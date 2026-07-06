from __future__ import annotations

import argparse
import importlib
import os
import platform
import re
import socket
import importlib.util
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Any

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

def _load_local_module(name: str):
    module_name = f"agent_{name}"
    if module_name in sys.modules:
        return sys.modules[module_name]
    if name in sys.modules:
        return sys.modules[name]
    import_candidates = [f"agent.{name}", name]
    last_error: Exception | None = None
    for candidate in import_candidates:
        try:
            module = importlib.import_module(candidate)
            sys.modules[module_name] = module
            sys.modules[name] = module
            return module
        except Exception as exc:
            last_error = exc
            continue
    candidates = [os.path.join(SCRIPT_DIR, f"{name}.py")]
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        bundle_root = os.path.abspath(sys._MEIPASS)  # type: ignore[attr-defined]
        candidates.extend(
            [
                os.path.join(bundle_root, f"{name}.py"),
                os.path.join(bundle_root, "agent", f"{name}.py"),
                os.path.join(bundle_root, f"{name}.pyc"),
                os.path.join(bundle_root, "agent", f"{name}.pyc"),
            ]
        )
    else:
        candidates.append(os.path.join(SCRIPT_DIR, "agent", f"{name}.py"))
    for path in candidates:
        if os.path.exists(path):
            spec_name = module_name
            if path.endswith(".pyc"):
                spec = importlib.util.spec_from_file_location(spec_name, path)
            else:
                spec = importlib.util.spec_from_file_location(spec_name, path)
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = module
                sys.modules[name] = module
                try:
                    spec.loader.exec_module(module)
                    return module
                except Exception as exc:
                    last_error = exc
    raise ModuleNotFoundError(f"Could not load local module: {name}: {last_error!r}")


_cache = _load_local_module("cache")
_client = _load_local_module("client")
_polling = _load_local_module("local_polling")
_ui = _load_local_module("webui2")

AgentSettings = _cache.AgentSettings
LocalCache = _cache.LocalCache
ServerClient = _client.ServerClient
LocalNetworkPoller = _polling.LocalNetworkPoller
IncomingPingListener = _polling.IncomingPingListener
AgentUI = _ui.AgentUI


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def hostname() -> str:
    try:
        return socket.gethostname()
    except Exception:
        return "unknown-host"


class WindowsAgent:
    def __init__(self, settings: AgentSettings) -> None:
        self.cache = LocalCache()
        self.settings = settings
        self.client = ServerClient(self.cache)
        self.poller = LocalNetworkPoller(
            common_ports=getattr(settings, "common_ports", None),
            max_discovery_hosts=getattr(settings, "max_discovery_hosts", 32),
        )
        self.running = True
        self.last_sync_at: str | None = None
        self.last_online_state: bool | None = None
        self.model_bundle: dict[str, Any] | None = None
        self.last_local_inventory: list[dict[str, Any]] = []
        self.last_local_metrics: dict[str, Any] = {}
        self.last_local_alerts: list[dict[str, Any]] = []
        self.active_local_alerts: list[dict[str, Any]] = []
        self.local_devices: list[dict[str, Any]] = []
        self._iface_stats_history: dict[str, dict[str, dict[str, Any]]] = {}
        self._global_mac_location: dict[str, dict[str, Any]] = {}
        self._global_mac_flap_counts: dict[str, int] = {}
        self.last_discovery_at: str | None = None
        self._server_devices_restored = False
        self._device_lock = threading.Lock()
        self._recent_incoming_pings: dict[str, float] = {}
        self.incoming_ping_listener = IncomingPingListener(on_ping=self._handle_incoming_ping)
        if not getattr(self.settings, "local_targets", None):
            self.settings.local_targets = self.cache.load_local_targets()
        if not getattr(self.settings, "local_devices", None):
            self.settings.local_devices = self.cache.load_local_devices()
        self.local_devices = list(getattr(self.settings, "local_devices", []) or [])
        self._sync_now = threading.Event()
        self._heartbeat_now = threading.Event()
        self._poll_now = threading.Event()
        self.cache.save_agent_profile(settings)

    def ensure_registered(self) -> None:
        if self.settings.agent_id and self.settings.agent_key:
            return
        self.settings = self.client.register(self.settings)

    def _restore_devices_from_server(self) -> None:
        """Pull this company's saved device list from the server and merge it
        into local storage. Devices on the server are keyed by company_id
        (not by this machine's agent_id), so this brings them back after a
        reinstall wipes the local cache folder (~/.idea-agent)."""
        try:
            response = self.client.list_devices(self.settings)
        except Exception as exc:
            self.cache.add_event("device_restore_error", {"error": str(exc), "at": utc_now()})
            return
        devices = response.get("devices") if isinstance(response, dict) else None
        if not isinstance(devices, list) or not devices:
            return
        existing_hosts = {
            str(item.get("host") or item.get("mgmt_ip") or "").strip()
            for item in self.cache.load_local_devices()
        }
        restored = 0
        for item in devices:
            if not isinstance(item, dict):
                continue
            host = str(item.get("mgmt_ip") or item.get("host") or "").strip()
            if not host or host in existing_hosts:
                continue
            record = {
                "host": host,
                "mgmt_ip": host,
                "name": str(item.get("name") or host).strip() or host,
                "vendor": str(item.get("vendor") or "").strip(),
                "vendor_family": str(item.get("vendor_family") or "").strip().lower(),
                "model": str(item.get("model") or "").strip(),
                "device_type": str(item.get("device_type") or "switch").strip().lower(),
                "access_protocol": str(item.get("access_protocol") or "auto").strip().lower(),
                "username": item.get("username"),
                "password": item.get("password"),
                "snmp_community": item.get("snmp_community"),
                "location": item.get("location"),
            }
            self.cache.add_local_device(record)
            existing_hosts.add(host)
            restored += 1
        if restored:
            self.settings.local_devices = self.cache.load_local_devices()
            self.settings.local_targets = self.cache.load_local_targets()
            self.cache.save_agent_profile(self.settings)
        self.cache.add_event("device_restore", {"restored": restored, "at": utc_now()})

    def build_snapshot(self) -> dict[str, Any]:
        latest = self.cache.latest_model_bundle()
        snapshot: dict[str, Any] = {
            "hostname": hostname(),
            "platform": platform.platform(),
            "python": platform.python_version(),
            "agent_time": utc_now(),
            "local_mode": not bool(self.last_online_state),
            "cached_model_version": self.settings.model_version,
            "queue_depth": len(self.cache.pending_commands()),
            "model_cache_present": latest is not None,
            "recent_events": self.cache.recent_events(5),
            "last_error": self.cache.get_last_error(),
            "local_targets": list(getattr(self.settings, "local_targets", []) or []),
            "local_devices": list(getattr(self.settings, "local_devices", []) or []),
            "local_inventory_count": len(self.last_local_inventory),
            "local_alert_count": len(self.active_local_alerts),
            "local_discovery_at": self.last_discovery_at,
            "incoming_ping_listener_active": bool(self.incoming_ping_listener.active),
            "incoming_ping_listener_error": self.incoming_ping_listener.last_error,
        }
        if latest:
            snapshot["model_bundle_version"] = latest.get("model_version")
            snapshot["model_bundle_fetched_at"] = latest.get("fetched_at")
        return snapshot

    def _handle_incoming_ping(self, src_ip: str) -> None:
        """Called from the IncomingPingListener background thread whenever a
        device - on the LAN or reachable only through a VPN tunnel - sends a
        ping to this PC. Auto-registers it as a device the same way the
        outward discovery scan does, so devices this agent could never have
        guessed the IP of (a remote site behind a VPN) still get added."""
        src_ip = str(src_ip or "").strip()
        if not src_ip or src_ip.startswith("127.") or src_ip == "0.0.0.0":
            return
        now = time.time()
        last_seen = self._recent_incoming_pings.get(src_ip)
        self._recent_incoming_pings[src_ip] = now
        if last_seen and now - last_seen < 30:
            return  # already handled recently, avoid hammering the DB on ping floods
        with self._device_lock:
            known_hosts = {
                str(item.get("host") or item.get("mgmt_ip") or "").strip()
                for item in self.cache.load_local_devices()
            }
            if src_ip in known_hosts:
                return
            self.cache.add_local_device(
                {
                    "host": src_ip,
                    "name": src_ip,
                    "device_type": "host",
                    "access_protocol": "auto",
                    "discovered": True,
                    "discovered_via": "incoming_ping",
                    "discovered_at": utc_now(),
                }
            )
            self.settings.local_devices = self.cache.load_local_devices()
            self.settings.local_targets = self.cache.load_local_targets()
            self.local_devices = self.settings.local_devices
            self.cache.save_agent_profile(self.settings)
        self.cache.add_event("device_auto_discovered", {"host": src_ip, "via": "incoming_ping", "at": utc_now()})

    def _run_local_poll_once(self, refresh_discovery: bool = True) -> None:
        if not getattr(self.settings, "poll_enabled", True):
            return
        self.local_devices = list(getattr(self.settings, "local_devices", []) or self.cache.load_local_devices() or [])
        targets = list(getattr(self.settings, "local_targets", []) or [])
        cidr = getattr(self.settings, "discovery_cidr", None)
        extra_cidrs: list[str] = []
        if getattr(self.settings, "discovery_enabled", True) and not cidr:
            # No CIDR configured by hand yet - work out this PC's own LAN so
            # any device that answers a ping/probe from this machine is found
            # automatically, without the user having to type a subnet in.
            detected_cidr = self.poller.detect_local_cidr()
            if detected_cidr:
                cidr = detected_cidr
                self.settings.discovery_cidr = detected_cidr
                self.cache.save_agent_profile(self.settings)
        if getattr(self.settings, "discovery_enabled", True):
            # Also sweep every OTHER local subnet this PC is a member of
            # (e.g. a VPN tunnel adapter's subnet), so devices reachable only
            # over a VPN can be found by an outward scan too, not just when
            # they happen to ping this PC first.
            extra_cidrs = [c for c in self.poller.detect_all_local_cidrs() if c != cidr]
        if refresh_discovery and getattr(self.settings, "discovery_enabled", True):
            targets = self.poller.discover_targets(cidr, targets)
            for extra_cidr in extra_cidrs:
                for host in self.poller.discover_targets(extra_cidr, []):
                    if host not in targets:
                        targets.append(host)
            self.last_discovery_at = utc_now()
        known_hosts = {
            str(item.get("host") or item.get("mgmt_ip") or "").strip()
            for item in self.local_devices
            if isinstance(item, dict)
        }
        # Hosts that showed up from the ping sweep but are not saved devices
        # yet - if they answer, they get auto-added below.
        newly_seen_hosts = [
            str(host).strip()
            for host in targets
            if isinstance(host, str) and str(host).strip() and str(host).strip() not in known_hosts
        ]
        results: list[dict[str, Any]] = []
        alerts: list[dict[str, Any]] = []
        metrics: dict[str, Any] = {
            "target_count": len(targets),
            "discovery_cidr": cidr,
            "poll_interval_seconds": getattr(self.settings, "poll_interval_seconds", 20),
        }
        poll_inputs: list[Any] = list(self.local_devices) + [{"host": host} for host in newly_seen_hosts]
        if not poll_inputs:
            poll_inputs = [{"host": host} for host in targets]
        auto_added = 0
        for result in self.poller.scan(cidr=None, manual_targets=poll_inputs):
            results.append(result)
            host = str(result.get("host") or "").strip()
            if host and host in newly_seen_hosts and result.get("reachable"):
                # This device replied to a ping/probe from this PC for the
                # first time - register it automatically so it shows up
                # under Devices without any manual step.
                with self._device_lock:
                    self.cache.add_local_device(
                        {
                            "host": host,
                            "name": host,
                            "device_type": "host",
                            "access_protocol": "auto",
                            "discovered": True,
                            "discovered_via": "ping_sweep",
                            "discovered_at": utc_now(),
                        }
                    )
                known_hosts.add(host)
                auto_added += 1
            if result.get("alerts"):
                alerts.extend(result.get("alerts") or [])
            elif not result.get("reachable"):
                alerts.append(
                    {
                        "event_type": "local.connectivity_loss",
                        "severity": "warning",
                        "host": result.get("host"),
                        "summary": f"{result.get('host')} unreachable",
                        "reachability": result.get("reachable"),
                        "latency_ms": result.get("latency_ms"),
                    }
                )
            alerts.extend(self._detect_device_health_alerts(str(result.get("host") or ""), result))
        if auto_added:
            self.local_devices = self.cache.load_local_devices()
            self.settings.local_devices = self.local_devices
            targets = self.cache.load_local_targets()
            self.settings.local_targets = targets
            self.cache.save_agent_profile(self.settings)
            self.cache.add_event("device_auto_discovered", {"count": auto_added, "at": utc_now()})
        alerts.extend(self._detect_network_loop_alerts(results))
        self.last_local_inventory = results
        self.cache.save_local_targets(targets)
        self.cache.save_local_devices(self.local_devices)
        self.last_local_alerts = alerts
        self.active_local_alerts = self._merge_local_alerts(self.active_local_alerts, alerts, results)
        self.last_local_metrics = metrics | {
            "reachable_hosts": sum(1 for item in results if item.get("reachable")),
            "unreachable_hosts": sum(1 for item in results if not item.get("reachable")),
        }
        self.cache.add_event(
            "local_poll",
            {
                "metrics": self.last_local_metrics,
                "inventory": results,
                "alerts": alerts,
                "at": utc_now(),
            },
        )
        if self.active_local_alerts:
            self.cache.add_event("local_alert", {"alerts": self.active_local_alerts, "at": utc_now()})
        self._heartbeat_once()

    def _detect_device_health_alerts(self, host: str, result: dict[str, Any]) -> list[dict[str, Any]]:
        alerts: list[dict[str, Any]] = []
        summary = result.get("summary")
        if not host or not isinstance(summary, dict):
            return alerts
        now_ts = time.time()

        # CPU threshold
        cpu = summary.get("cpu")
        if isinstance(cpu, (int, float)) and cpu >= 90:
            alerts.append({
                "event_type": "device.cpu_high",
                "severity": "critical" if cpu >= 95 else "warning",
                "host": host,
                "summary": f"{host} CPU usage at {cpu:.0f}%",
            })

        # Memory threshold (needs both free and total to compute a real percentage)
        mem = summary.get("memory")
        mem_total = summary.get("memory_total")
        if isinstance(mem, (int, float)) and isinstance(mem_total, (int, float)) and mem_total > 0:
            free_pct = (mem / mem_total) * 100
            if free_pct <= 10:
                alerts.append({
                    "event_type": "device.memory_low",
                    "severity": "critical" if free_pct <= 5 else "warning",
                    "host": host,
                    "summary": f"{host} free memory at {free_pct:.0f}%",
                })

        # Temperature threshold
        temperature = summary.get("temperature")
        temp_val = None
        try:
            if temperature is not None:
                match = re.search(r"-?\d+(?:\.\d+)?", str(temperature))
                temp_val = float(match.group(0)) if match else None
        except Exception:
            temp_val = None
        if temp_val is not None and temp_val >= 70:
            alerts.append({
                "event_type": "device.temperature_high",
                "severity": "critical",
                "host": host,
                "summary": f"{host} temperature at {temp_val:.0f}C",
            })

        # Interface flapping + unusual bandwidth (compare against last poll)
        prev_iface = self._iface_stats_history.get(host, {})
        curr_iface: dict[str, dict[str, Any]] = {}
        for row in summary.get("port_details") or []:
            if not isinstance(row, dict):
                continue
            name = str(row.get("name") or "").strip()
            if not name:
                continue
            try:
                rx = float(row.get("rx-byte") or 0)
                tx = float(row.get("tx-byte") or 0)
            except Exception:
                rx = tx = 0.0
            try:
                link_downs = int(float(row.get("link-downs") or 0))
            except Exception:
                link_downs = 0
            port_status = str(row.get("status") or "").strip().lower() or "unknown"
            entry: dict[str, Any] = {"rx": rx, "tx": tx, "ts": now_ts, "link_downs": link_downs, "status": port_status}
            prev = prev_iface.get(name)
            if prev:
                # Real-time port up/down transition alert (compares this poll vs previous poll)
                # "down" = cable/link operationally down, "admin_down" = interface disabled (e.g. via WinBox/CLI)
                prev_status = str(prev.get("status") or "").strip().lower() or "unknown"
                was_up = prev_status == "up"
                is_up = port_status == "up"
                was_down = prev_status in {"down", "admin_down"}
                is_down = port_status in {"down", "admin_down"}
                if is_down and not was_down:
                    reason = "disabled" if port_status == "admin_down" else "link down"
                    alerts.append({
                        "event_type": "port.down",
                        "severity": "critical",
                        "host": host, "port": name,
                        "summary": f"{host} port {name} went DOWN ({reason})",
                        "status": port_status,
                    })
                elif is_up and was_down:
                    alerts.append({
                        "event_type": "port.up",
                        "severity": "info",
                        "host": host, "port": name,
                        "summary": f"{host} port {name} came back UP",
                        "status": "up",
                    })
                elapsed = max(now_ts - float(prev.get("ts", now_ts)), 1.0)
                rate_bps = max(0.0, ((rx - float(prev.get("rx", rx))) + (tx - float(prev.get("tx", tx)))) * 8 / elapsed)
                baseline = prev.get("rate_bps")
                if baseline and baseline > 1_000_000 and rate_bps > baseline * 5:
                    alerts.append({
                        "event_type": "device.unusual_traffic",
                        "severity": "warning",
                        "host": host, "port": name,
                        "summary": f"{host} {name} traffic spiked to {rate_bps / 1_000_000:.1f} Mbps",
                    })
                elif rate_bps > 800_000_000:
                    alerts.append({
                        "event_type": "device.unusual_traffic",
                        "severity": "critical",
                        "host": host, "port": name,
                        "summary": f"{host} {name} sustained traffic at {rate_bps / 1_000_000:.0f} Mbps",
                    })
                entry["rate_bps"] = rate_bps
                downs_delta = link_downs - int(prev.get("link_downs", link_downs))
                if downs_delta >= 3:
                    alerts.append({
                        "event_type": "device.interface_flapping",
                        "severity": "critical",
                        "host": host, "port": name,
                        "summary": f"{host} {name} flapped {downs_delta}x since last check, possible loop or bad cable",
                    })
            curr_iface[name] = entry
        self._iface_stats_history[host] = curr_iface

        # Catch-all: surface any probe-level error as its own alert
        if result.get("probe_error"):
            alerts.append({
                "event_type": "device.probe_error",
                "severity": "warning",
                "host": host,
                "summary": f"{host} probe error: {result.get('probe_error')}",
            })
        return alerts

    def _detect_network_loop_alerts(self, results: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Correlates MAC sightings across every switch polled in this same cycle
        to pinpoint exactly which switch(es)/port(s) a loop is coming from,
        instead of raising the same generic alert on all 10 switches at once."""
        alerts: list[dict[str, Any]] = []
        now_ts = time.time()

        # Collect every (host, interface) a MAC was seen on, in this single poll pass
        sightings: dict[str, list[dict[str, str]]] = {}
        for result in results:
            host = str(result.get("host") or "").strip()
            summary = result.get("summary") or {}
            if not host or not isinstance(summary, dict):
                continue
            for entry in summary.get("mac_table") or []:
                if not isinstance(entry, dict):
                    continue
                mac = str(entry.get("mac") or "").strip()
                iface = str(entry.get("interface") or "").strip()
                if not mac or not iface:
                    continue
                sightings.setdefault(mac, []).append({"host": host, "interface": iface})

        # Strongest signal: same MAC seen in 2+ places within the SAME scan.
        # A MAC can only physically exist on one port at a time, so this
        # pinpoints the exact switch/port pair forming the loop.
        confirmed_macs: set[str] = set()
        for mac, locs in sightings.items():
            unique_locs = list({(l["host"], l["interface"]): l for l in locs}.values())
            if len(unique_locs) <= 1:
                continue
            confirmed_macs.add(mac)
            switches_involved = sorted({l["host"] for l in unique_locs})
            where = " <-> ".join(f"{l['host']}:{l['interface']}" for l in unique_locs)
            alerts.append({
                "event_type": "network.loop_confirmed",
                "severity": "critical",
                "host": switches_involved[0],
                "port": unique_locs[0]["interface"],
                "summary": f"Loop pinpointed at {where} - MAC {mac} seen in multiple places in one scan",
                "switches": switches_involved,
                "locations": unique_locs,
                "mac": mac,
            })

        # Weaker but earlier signal: MAC keeps moving poll-over-poll. Tracks
        # whether it moved to a different SWITCH (loop spans multiple
        # switches - both ends of the redundant link get named) or just a
        # different port on the same switch (self-loop on one device).
        for mac, locs in sightings.items():
            if mac in confirmed_macs:
                continue
            current = locs[0]
            prev = self._global_mac_location.get(mac)
            self._global_mac_location[mac] = {"host": current["host"], "interface": current["interface"], "ts": now_ts}
            if not prev:
                continue
            moved = prev.get("host") != current["host"] or prev.get("interface") != current["interface"]
            if moved and (now_ts - float(prev.get("ts", 0))) < 90:
                self._global_mac_flap_counts[mac] = self._global_mac_flap_counts.get(mac, 0) + 1
            else:
                self._global_mac_flap_counts[mac] = max(0, self._global_mac_flap_counts.get(mac, 0) - 1)
            if self._global_mac_flap_counts.get(mac, 0) >= 3:
                cross_switch = prev.get("host") != current["host"]
                if cross_switch:
                    alerts.append({
                        "event_type": "network.loop_suspected",
                        "severity": "critical",
                        "host": current["host"],
                        "summary": f"MAC {mac} bouncing between {prev.get('host')}:{prev.get('interface')} and {current['host']}:{current['interface']} - check the link between these two switches",
                        "switches": sorted({str(prev.get("host")), current["host"]}),
                        "mac": mac,
                    })
                else:
                    alerts.append({
                        "event_type": "device.mac_flapping",
                        "severity": "warning",
                        "host": current["host"],
                        "summary": f"{current['host']}: MAC {mac} flapping between its own ports {prev.get('interface')} and {current['interface']} - likely a self-loop cable on this switch",
                        "switches": [current["host"]],
                        "mac": mac,
                    })
        return alerts

    def _alert_key(self, alert: dict[str, Any]) -> str:
        host = str(alert.get("host") or alert.get("device") or alert.get("mgmt_ip") or "").strip()
        event_type = str(alert.get("event_type") or alert.get("issue") or alert.get("kind") or "alert").strip()
        port = str(alert.get("port") or alert.get("port_name") or "").strip()
        if not port:
            down_ports = alert.get("down_ports")
            if isinstance(down_ports, list) and down_ports:
                port = ",".join(str(p).strip() for p in down_ports if str(p).strip())
        return f"{host}|{event_type}|{port}"

    def _merge_local_alerts(self, existing: list[dict[str, Any]], fresh: list[dict[str, Any]], results: list[dict[str, Any]]) -> list[dict[str, Any]]:
        active: dict[str, dict[str, Any]] = {}
        for item in existing:
            key = self._alert_key(item)
            if key:
                active[key] = {**item, "resolved": bool(item.get("resolved"))}
        down_map: dict[str, set[str]] = {}
        for result in results:
            host = str(result.get("host") or "").strip()
            summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
            down_ports = summary.get("port_details") if isinstance(summary, dict) else []
            ports_down: set[str] = set()
            if isinstance(down_ports, list):
                for row in down_ports:
                    if not isinstance(row, dict):
                        continue
                    status = str(row.get("status") or "").lower()
                    name = str(row.get("name") or row.get("port") or "").strip()
                    if name and status in {"down", "admin_down"}:
                        ports_down.add(name)
            if ports_down:
                down_map[host] = ports_down
        for alert in fresh:
            key = self._alert_key(alert)
            if not key:
                continue
            current = {**alert, "resolved": False, "last_seen": utc_now()}
            active[key] = current
        for key, item in list(active.items()):
            host = str(item.get("host") or "").strip()
            port = str(item.get("port") or "").strip()
            if host and port:
                live_ports = down_map.get(host, set())
                if port not in live_ports:
                    item = {**item, "resolved": True, "resolved_at": utc_now()}
                    active[key] = item
            elif host and host in down_map and down_map[host]:
                item = {**item, "resolved": False}
                active[key] = item
        merged: list[dict[str, Any]] = []
        for item in active.values():
            if not item.get("resolved"):
                merged.append(item)
        return merged

    def _sync_once(self) -> None:
        self.ensure_registered()
        if not self._server_devices_restored:
            self._restore_devices_from_server()
            self._server_devices_restored = True
        sync = self.client.fetch_and_apply_sync(self.settings)
        self.model_bundle = sync.get("model_bundle")
        self.last_sync_at = utc_now()
        self.cache.add_event("sync", {"sync": sync, "at": self.last_sync_at})
        self.last_online_state = True
        for cmd in sync.get("commands", []) or []:
            self.cache.enqueue_incoming_command(
                int(cmd.get("id") or 0),
                str(cmd.get("type") or "sync"),
                dict(cmd.get("payload") or {}),
            )
        self.flush_queue()

    def _heartbeat_once(self) -> None:
        self.ensure_registered()
        snapshot = self.build_snapshot()
        self.last_online_state = True
        response = self.client.heartbeat(
            self.settings,
            snapshot=snapshot,
            online=True,
            model_version=self.settings.model_version,
            metrics=self.last_local_metrics,
            inventory=self.last_local_inventory,
            logs=[item.get("ping_output", "") for item in self.last_local_inventory if item.get("ping_output")],
            alerts=self.last_local_alerts,
        )
        self.cache.add_event("heartbeat", {"response": response or {}, "snapshot": snapshot, "at": utc_now()})
        for cmd in (response or {}).get("commands", []) or []:
            self.cache.enqueue_incoming_command(
                int(cmd.get("id") or 0),
                str(cmd.get("type") or "sync"),
                dict(cmd.get("payload") or {}),
            )
        self.flush_queue()

    def sync_loop(self) -> None:
        backoff = 3.0
        while self.running:
            try:
                if self._sync_now.is_set():
                    self._sync_now.clear()
                    self._sync_once()
                else:
                    self._sync_once()
                backoff = 3.0
            except Exception as exc:
                self.last_online_state = False
                self.cache.set_last_error(str(exc))
                self.cache.add_event("sync_error", {"error": str(exc), "at": utc_now()})
                backoff = min(backoff * 1.5, 60.0)
            self._wait_or_stop(backoff)

    def heartbeat_loop(self) -> None:
        while self.running:
            try:
                if self._heartbeat_now.is_set():
                    self._heartbeat_now.clear()
                self._heartbeat_once()
            except Exception as exc:
                self.last_online_state = False
                self.cache.set_last_error(str(exc))
                self.cache.add_event("heartbeat_error", {"error": str(exc), "at": utc_now()})
            self._wait_or_stop(15)

    def local_poll_loop(self) -> None:
        interval = max(10, int(getattr(self.settings, "poll_interval_seconds", 20) or 20))
        discovery_interval = max(30, int(getattr(self.settings, "discovery_interval_seconds", 60) or 60))
        last_discovery = 0.0
        while self.running:
            try:
                if self._poll_now.is_set():
                    self._poll_now.clear()
                do_discovery = getattr(self.settings, "discovery_enabled", True) and (time.time() - last_discovery >= discovery_interval)
                if do_discovery:
                    self._run_local_poll_once(refresh_discovery=True)
                    last_discovery = time.time()
                elif getattr(self.settings, "poll_enabled", True):
                    self._run_local_poll_once(refresh_discovery=False)
            except Exception as exc:
                self.cache.set_last_error(str(exc))
                self.cache.add_event("local_poll_error", {"error": str(exc), "at": utc_now()})
            self._wait_or_stop(interval)

    def _wait_or_stop(self, seconds: float) -> None:
        deadline = time.time() + seconds
        while self.running and time.time() < deadline:
            time.sleep(0.25)

    def request_sync_once(self) -> None:
        self._sync_now.set()

    def request_heartbeat_once(self) -> None:
        self._heartbeat_now.set()

    def request_local_poll_once(self) -> None:
        self._poll_now.set()

    def flush_queue(self) -> None:
        for cmd in self.cache.pending_commands():
            payload = cmd["payload"]
            self._execute_local_command(cmd["command_type"], payload)
            try:
                ack = self.client.ack_command(self.settings, int(cmd["id"]), "acked", {"local": True, "payload": payload})
                self.cache.mark_command_status(int(cmd["id"]), "acked", ack or {"local": True})
            except Exception as exc:
                self.cache.mark_command_status(int(cmd["id"]), "failed", {"error": str(exc)})

    def approve_incoming_command(self, command_id: int) -> None:
        item = next((row for row in self.cache.pending_incoming_commands() if int(row["id"]) == int(command_id)), None)
        if not item:
            return
        payload = item.get("payload", {})
        self.cache.add_event(
            "incoming_command_approved",
            {"command_id": command_id, "command_type": item.get("command_type"), "payload": payload, "at": utc_now()},
        )
        try:
            if self.settings.agent_id:
                self.client.ack_command(self.settings, int(item.get("server_command_id") or 0), "approved", {"approved": True, "payload": payload})
        finally:
            self.cache.update_incoming_command_status(command_id, "approved", {"approved": True})

    def reject_incoming_command(self, command_id: int, reason: str = "rejected by local operator") -> None:
        item = next((row for row in self.cache.pending_incoming_commands() if int(row["id"]) == int(command_id)), None)
        if not item:
            return
        self.cache.add_event(
            "incoming_command_rejected",
            {"command_id": command_id, "command_type": item.get("command_type"), "reason": reason, "at": utc_now()},
        )
        try:
            if self.settings.agent_id:
                self.client.ack_command(self.settings, int(item.get("server_command_id") or 0), "rejected", {"reason": reason})
        finally:
            self.cache.update_incoming_command_status(command_id, "rejected", {"reason": reason})

    def _execute_local_command(self, command_type: str, payload: dict[str, Any]) -> None:
        self.cache.add_event("command_executed", {"command_type": command_type, "payload": payload, "at": utc_now()})

    def start(self, ui: bool = True) -> None:
        threads = [
            threading.Thread(target=self.sync_loop, daemon=True),
            threading.Thread(target=self.heartbeat_loop, daemon=True),
            threading.Thread(target=self.local_poll_loop, daemon=True),
        ]
        for thread in threads:
            thread.start()
        try:
            self.incoming_ping_listener.start()
        except Exception as exc:
            self.cache.add_event("incoming_ping_listener_error", {"error": str(exc), "at": utc_now()})
        if ui:
            app = AgentUI(self)
            try:
                app.mainloop()
            finally:
                self.running = False
        else:
            try:
                while self.running:
                    time.sleep(1)
            except KeyboardInterrupt:
                self.running = False


def load_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Idea Agent Windows Client")
    parser.add_argument("--server", required=False, default=os.environ.get("IDEA_AGENT_SERVER", "http://127.0.0.1:8000"))
    parser.add_argument("--company-id", required=False, type=int, default=int(os.environ.get("IDEA_AGENT_COMPANY_ID", "1")))
    parser.add_argument("--name", required=False, default=os.environ.get("IDEA_AGENT_NAME", hostname()))
    parser.add_argument("--agent-id", required=False, type=int, default=None)
    parser.add_argument("--agent-key", required=False, default=None)
    parser.add_argument("--headless", action="store_true", help="Run without the desktop UI")
    return parser.parse_args()


def load_settings(args: argparse.Namespace) -> AgentSettings:
    cache = LocalCache()
    profile = cache.load_agent_profile()
    settings = profile or AgentSettings(
        server_url=args.server,
        company_id=args.company_id,
        name=args.name,
        agent_id=args.agent_id,
        agent_key=args.agent_key,
    )
    settings.server_url = args.server or settings.server_url
    if args.agent_id is not None:
        settings.agent_id = args.agent_id
    if args.agent_key:
        settings.agent_key = args.agent_key
    if args.name:
        settings.name = args.name
    if args.company_id:
        settings.company_id = args.company_id
    cache.save_agent_profile(settings)
    return settings


def main() -> None:
    args = load_args()
    settings = load_settings(args)
    agent = WindowsAgent(settings)
    agent.start(ui=not args.headless)


if __name__ == "__main__":
    main()
