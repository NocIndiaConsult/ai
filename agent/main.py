from __future__ import annotations

import argparse
import importlib
import os
import platform
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
        self.last_discovery_at: str | None = None
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
        }
        if latest:
            snapshot["model_bundle_version"] = latest.get("model_version")
            snapshot["model_bundle_fetched_at"] = latest.get("fetched_at")
        return snapshot

    def _run_local_poll_once(self, refresh_discovery: bool = True) -> None:
        if not getattr(self.settings, "poll_enabled", True):
            return
        self.local_devices = list(getattr(self.settings, "local_devices", []) or self.cache.load_local_devices() or [])
        targets = list(getattr(self.settings, "local_targets", []) or [])
        cidr = getattr(self.settings, "discovery_cidr", None)
        if refresh_discovery and getattr(self.settings, "discovery_enabled", True):
            targets = self.poller.discover_targets(cidr, targets)
            self.last_discovery_at = utc_now()
        results: list[dict[str, Any]] = []
        alerts: list[dict[str, Any]] = []
        metrics: dict[str, Any] = {
            "target_count": len(targets),
            "discovery_cidr": cidr,
            "poll_interval_seconds": getattr(self.settings, "poll_interval_seconds", 20),
        }
        poll_inputs = self.local_devices or [{"host": host} for host in targets]
        for result in self.poller.scan(cidr=None, manual_targets=poll_inputs):
            results.append(result)
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
