from __future__ import annotations

import ipaddress
import json
import socket
import re
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Iterable

try:
    import requests
except Exception:  # pragma: no cover
    requests = None

try:
    import paramiko
except Exception:  # pragma: no cover
    paramiko = None

try:
    from pysnmp.hlapi import CommunityData, ContextData, ObjectIdentity, ObjectType, SnmpEngine, UdpTransportTarget, getCmd, nextCmd
except Exception:  # pragma: no cover
    try:
        from pysnmp.hlapi.v3arch import CommunityData, ContextData, ObjectIdentity, ObjectType, SnmpEngine, UdpTransportTarget, getCmd, nextCmd
    except Exception:  # pragma: no cover
        CommunityData = ContextData = ObjectIdentity = ObjectType = SnmpEngine = UdpTransportTarget = getCmd = nextCmd = None


def ping_host(host: str, timeout_ms: int = 800) -> dict[str, Any]:
    timeout_s = max(0.2, timeout_ms / 1000.0)
    started = perf_counter()
    ok = False
    output = "No TCP probe hit"
    for port in (443, 80, 22, 161, 8291, 23, 8080):
        try:
            with socket.create_connection((host, int(port)), timeout=timeout_s):
                ok = True
                output = f"TCP probe connected on port {port}"
                break
        except Exception:
            continue
    elapsed = round((perf_counter() - started) * 1000.0, 2) if ok else None
    return {
        "host": host,
        "reachable": ok,
        "latency_ms": elapsed,
        "ping_output": output,
    }


def tcp_probe(host: str, port: int, timeout: float = 0.6) -> dict[str, Any]:
    started = perf_counter()
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return {
                "port": int(port),
                "open": True,
                "latency_ms": round((perf_counter() - started) * 1000.0, 2),
            }
    except Exception as exc:
        return {
            "port": int(port),
            "open": False,
            "error": str(exc),
        }


@dataclass
class LocalProbeResult:
    host: str
    reachable: bool
    latency_ms: float | None
    tcp_open_ports: list[int]
    tcp_closed_ports: list[int]
    ping_output: str
    source: str = "local-agent"

    def as_dict(self) -> dict[str, Any]:
        return {
            "host": self.host,
            "reachable": self.reachable,
            "latency_ms": self.latency_ms,
            "tcp_open_ports": self.tcp_open_ports,
            "tcp_closed_ports": self.tcp_closed_ports,
            "ping_output": self.ping_output,
            "source": self.source,
        }


class LocalNetworkPoller:
    def __init__(self, common_ports: Iterable[int] | None = None, max_discovery_hosts: int = 32) -> None:
        self.common_ports = [int(p) for p in (common_ports or [22, 23, 80, 443, 161, 8291, 8080])]
        self.max_discovery_hosts = max(1, int(max_discovery_hosts))

    def _norm(self, value: Any) -> str:
        return str(value or "").strip()

    def _device_protocol(self, device: dict[str, Any] | str | None) -> str:
        if isinstance(device, dict):
            proto = self._norm(device.get("access_protocol") or device.get("protocol") or "auto").lower()
            vendor_family = self._norm(device.get("vendor_family") or device.get("vendor") or "").lower()
            model = self._norm(device.get("model") or "").lower()
            if proto == "auto":
                if any(tag in " ".join([vendor_family, model]) for tag in ("mikrotik", "routeros")):
                    return "rest"
                if any(tag in " ".join([vendor_family, model]) for tag in ("snmp", "olt", "cisco", "hpe", "tp-link", "d-link", "dbc", "syrotech", "genexis", "grandstream")):
                    return "snmp"
                if self._norm(device.get("username")) and self._norm(device.get("password")):
                    return "ssh"
                return "rest"
            return proto
        return "auto"

    def _port_scan(self, host: str, ports: Iterable[int] | None = None) -> list[int]:
        open_ports: list[int] = []
        for port in ports or self.common_ports:
            try:
                with socket.create_connection((host, int(port)), timeout=0.5):
                    open_ports.append(int(port))
            except Exception:
                continue
        return open_ports

    def _device_host(self, device: dict[str, Any] | str) -> str:
        if isinstance(device, dict):
            return self._norm(device.get("host") or device.get("mgmt_ip") or device.get("ip"))
        return self._norm(device)

    def _ssh_command(self, host: str, username: str, password: str, command: str, timeout: float = 6.0) -> str:
        if paramiko is None:
            raise RuntimeError("paramiko not available")
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(hostname=host, username=username, password=password, timeout=timeout, banner_timeout=timeout, auth_timeout=timeout)
        try:
            _, stdout, stderr = client.exec_command(command, timeout=timeout)
            output = stdout.read().decode("utf-8", errors="ignore")
            error = stderr.read().decode("utf-8", errors="ignore")
            return (output or error or "").strip()
        finally:
            client.close()

    def _rest_get(self, host: str, path: str, username: str | None = None, password: str | None = None, timeout: float = 6.0) -> Any:
        if requests is None:
            raise RuntimeError("requests not available")
        url = f"http://{host}{path}"
        auth = (username, password) if username and password else None
        response = requests.get(url, auth=auth, timeout=timeout, verify=False)
        response.raise_for_status()
        try:
            return response.json()
        except Exception:
            return response.text

    def _mikrotik_rest_snapshot(self, device: dict[str, Any]) -> dict[str, Any]:
        host = self._device_host(device)
        username = self._norm(device.get("username"))
        password = self._norm(device.get("password"))
        summary = {}
        interfaces = []
        resource = {}
        reachable = False
        try:
            resource = self._rest_get(host, "/rest/system/resource", username, password)
            reachable = True
        except Exception:
            resource = {}
        try:
            interfaces = self._rest_get(host, "/rest/interface", username, password)
            reachable = True
        except Exception:
            try:
                interfaces = self._rest_get(host, "/rest/interface/print", username, password)
                reachable = True
            except Exception:
                interfaces = []
        if isinstance(interfaces, dict):
            interfaces = interfaces.get("result") or interfaces.get("data") or []
        normalized: list[dict[str, Any]] = []
        down_ports: list[str] = []
        for row in interfaces if isinstance(interfaces, list) else []:
            if not isinstance(row, dict):
                continue
            name = self._norm(row.get("name") or row.get("interface") or row.get("port"))
            if not name:
                continue
            status = "unknown"
            raw = json.dumps(row, default=str)
            disabled = str(row.get("disabled") or "").lower() in {"true", "yes", "1"}
            running = str(row.get("running") or "").lower() in {"true", "yes", "1"}
            if disabled:
                status = "admin_down"
            elif running:
                status = "up"
            elif any(str(row.get(key) or "").strip() not in {"", "0", "0.0"} for key in ("last-link-up-time", "last-link-down-time", "link-downs", "rx-byte", "tx-byte", "rx-packet", "tx-packet")):
                status = "down"
            normalized.append(
                {
                    "name": name,
                    "status": status,
                    "admin_status": "down" if disabled else "up",
                    "oper_status": "up" if running else "down",
                    "speed": row.get("actual-mtu") or row.get("mtu") or row.get("rate") or row.get("speed"),
                    "rx-byte": row.get("rx-byte") or row.get("rx_bytes"),
                    "tx-byte": row.get("tx-byte") or row.get("tx_bytes"),
                    "rx-packet": row.get("rx-packet") or row.get("rx_packets"),
                    "tx-packet": row.get("tx-packet") or row.get("tx_packets"),
                    "link-downs": row.get("link-downs") or row.get("link_downs"),
                    "last-link-up-time": row.get("last-link-up-time") or row.get("last_link_up_time"),
                    "last-link-down-time": row.get("last-link-down-time") or row.get("last_link_down_time"),
                    "raw": raw,
                    "type": row.get("type") or "ether",
                    "running": running,
                }
            )
            if status in {"down", "admin_down"}:
                down_ports.append(name)
        cpu = None
        mem = None
        uptime = None
        temperature = None
        serial = None
        mac = None
        if isinstance(resource, dict):
            uptime = resource.get("uptime")
            cpu = resource.get("cpu-load") or resource.get("cpu")
            mem = resource.get("free-memory") or resource.get("memory")
            temperature = resource.get("temperature")
            serial = resource.get("board-name") or resource.get("serial-number")
            mac = resource.get("board-identity") or resource.get("base-mac")
        summary = {
            "ports": len(normalized),
            "port_details": normalized,
            "ports_up": sum(1 for row in normalized if row.get("status") == "up"),
            "ports_down": len([p for p in down_ports if p]),
            "ports_admin_down": len([row for row in normalized if row.get("status") == "admin_down"]),
            "cpu": float(re.search(r"-?\d+(?:\.\d+)?", str(cpu)).group(0)) if cpu is not None and re.search(r"-?\d+(?:\.\d+)?", str(cpu)) else 0.0,
            "memory": float(re.search(r"-?\d+(?:\.\d+)?", str(mem)).group(0)) if mem is not None and re.search(r"-?\d+(?:\.\d+)?", str(mem)) else 0.0,
            "uptime": uptime,
            "temperature": temperature,
            "serial_number": serial,
            "mac_address": mac,
            "note": "MikroTik REST telemetry snapshot.",
        }
        alerts = []
        for port_name in down_ports:
            alerts.append(
                {
                    "event_type": "device.port_down",
                    "severity": "critical",
                    "host": host,
                    "port": port_name,
                    "summary": f"{host} {port_name} down",
                    "down_ports": [port_name],
                }
            )
        return {"host": host, "reachable": reachable, "latency_ms": None, "ping_output": "mikrotik-rest", "device_type": "mikrotik", "protocol": "rest", "summary": summary, "alerts": alerts}

    def _ssh_snapshot(self, device: dict[str, Any]) -> dict[str, Any]:
        host = self._device_host(device)
        username = self._norm(device.get("username"))
        password = self._norm(device.get("password"))
        commands = [
            " /interface print detail without-paging",
            " /system resource print without-paging",
        ]
        output = ""
        reachable = False
        for command in commands:
            try:
                output = self._ssh_command(host, username, password, command)
                if output:
                    reachable = True
                    break
            except Exception:
                continue
        ports = []
        down_ports = []
        for line in output.splitlines():
            line = line.strip()
            if not line or "name=" not in line:
                continue
            match = re.search(r'name=([^ ]+)', line)
            if not match:
                continue
            name = match.group(1).strip().strip('"')
            status = "up" if "running=true" in line.lower() else "down" if "running=false" in line.lower() or "disabled=true" in line.lower() else "unknown"
            ports.append({"name": name, "status": status, "raw": line, "running": "running=true" in line.lower()})
            if status in {"down", "admin_down"}:
                down_ports.append(name)
        alerts = []
        for port_name in down_ports:
            alerts.append(
                {
                    "event_type": "device.port_down",
                    "severity": "critical",
                    "host": host,
                    "port": port_name,
                    "summary": f"{host} {port_name} down",
                    "down_ports": [port_name],
                }
            )
        return {"host": host, "reachable": reachable or bool(output), "latency_ms": None, "ping_output": "ssh-snapshot", "device_type": "switch", "protocol": "ssh", "summary": {"ports": len(ports), "port_details": ports, "ports_down": len(down_ports), "ports_up": len(ports) - len(down_ports), "note": "SSH snapshot collected."}, "alerts": alerts}

    def _snmp_snapshot(self, device: dict[str, Any]) -> dict[str, Any]:
        host = self._device_host(device)
        if getCmd is None:
            return self.probe_host(host)
        community = self._norm(device.get("snmp_community") or "public")
        ports: list[dict[str, Any]] = []
        down_ports: list[str] = []
        reachable = False
        try:
            # sysName only first to validate SNMP
            iterator = getCmd(
                SnmpEngine(),
                CommunityData(community, mpModel=0),
                UdpTransportTarget((host, 161), timeout=2, retries=1),
                ContextData(),
                ObjectType(ObjectIdentity("1.3.6.1.2.1.1.5.0")),
            )
            for error_indication, error_status, error_index, var_binds in iterator:
                if error_indication or error_status:
                    break
                reachable = True
            iterator = nextCmd(
                SnmpEngine(),
                CommunityData(community, mpModel=0),
                UdpTransportTarget((host, 161), timeout=2, retries=1),
                ContextData(),
                ObjectType(ObjectIdentity("1.3.6.1.2.1.2.2.1.2")),
                lexicographicMode=False,
            )
            for error_indication, error_status, error_index, var_binds in iterator:
                if error_indication or error_status:
                    break
                for var_bind in var_binds:
                    text = str(var_bind)
                    if "=" not in text:
                        continue
                    _, value = text.split("=", 1)
                    name = value.strip().strip('"')
                    if not name:
                        continue
                    ports.append({"name": name, "status": "unknown", "raw": text})
                    reachable = True
        except Exception:
            pass
        alerts = []
        return {"host": host, "reachable": reachable, "latency_ms": None, "ping_output": "snmp-snapshot", "device_type": "switch", "protocol": "snmp", "summary": {"ports": len(ports), "port_details": ports, "ports_down": len(down_ports), "ports_up": len(ports), "note": "SNMP snapshot collected."}, "alerts": alerts}

    def probe_host(self, host: str) -> dict[str, Any]:
        ping = ping_host(host)
        open_ports: list[int] = []
        closed_ports: list[int] = []
        for port in self.common_ports:
            probe = tcp_probe(host, port)
            if probe.get("open"):
                open_ports.append(port)
            else:
                closed_ports.append(port)
        if not ping["reachable"] and open_ports:
            ping["reachable"] = True
            ping["latency_ms"] = min((probe.get("latency_ms") for probe in (tcp_probe(host, p) for p in open_ports) if probe.get("latency_ms") is not None), default=None)
            ping["ping_output"] = f"Reachable via TCP probe; open ports: {', '.join(str(p) for p in open_ports[:6])}"
        result = LocalProbeResult(
            host=host,
            reachable=bool(ping["reachable"]),
            latency_ms=ping["latency_ms"],
            tcp_open_ports=open_ports,
            tcp_closed_ports=closed_ports,
            ping_output=str(ping["ping_output"] or ""),
        )
        payload = result.as_dict()
        payload["tcp_probe_count"] = len(self.common_ports)
        payload["local_status"] = "online" if payload["reachable"] else "offline"
        return payload

    def probe_device(self, device: dict[str, Any] | str) -> dict[str, Any]:
        host = self._device_host(device)
        if not host:
            return {"host": "", "reachable": False, "latency_ms": None, "ping_output": "invalid host", "alerts": []}
        if isinstance(device, dict):
            reach = self.probe_host(host)
            protocol = self._device_protocol(device)
            if protocol == "auto":
                scan_ports = self._port_scan(host, ports=[80, 443, 8080, 8728, 8729, 161, 22, 8291])
                if any(port in scan_ports for port in (80, 443, 8080, 8728, 8729)):
                    protocol = "rest"
                elif 161 in scan_ports or self._norm(device.get("snmp_community")):
                    protocol = "snmp"
                elif 22 in scan_ports or (self._norm(device.get("username")) and self._norm(device.get("password"))):
                    protocol = "ssh"
                else:
                    protocol = "rest" if any(tag in " ".join([self._norm(device.get("vendor_family")), self._norm(device.get("vendor")), self._norm(device.get("model"))]).lower() for tag in ("mikrotik", "routeros")) else "snmp"
            if protocol == "rest":
                snap = self._mikrotik_rest_snapshot(device)
                snap["reachable"] = bool(snap.get("reachable")) or bool(reach.get("reachable"))
                snap["latency_ms"] = reach.get("latency_ms")
                snap["ping_output"] = reach.get("ping_output") or snap.get("ping_output")
                return snap
            if protocol == "snmp":
                snap = self._snmp_snapshot(device)
                if snap.get("summary", {}).get("ports", 0):
                    snap["latency_ms"] = reach.get("latency_ms")
                    snap["ping_output"] = reach.get("ping_output") or snap.get("ping_output")
                    return snap
            if protocol == "ssh":
                snap = self._ssh_snapshot(device)
                snap["latency_ms"] = reach.get("latency_ms")
                snap["ping_output"] = reach.get("ping_output") or snap.get("ping_output")
                return snap
        return self.probe_host(host)

    def discover_targets(self, cidr: str | None, manual_targets: Iterable[str | dict[str, Any]] | None = None) -> list[str | dict[str, Any]]:
        targets: list[str | dict[str, Any]] = []
        for host in manual_targets or []:
            if isinstance(host, dict):
                value = self._device_host(host)
                if value and not any(self._device_host(item) == value if isinstance(item, dict) else str(item).strip() == value for item in targets):
                    targets.append(host)
            else:
                host = str(host).strip()
                if host and host not in targets:
                    targets.append(host)
        if cidr:
            try:
                network = ipaddress.ip_network(cidr, strict=False)
                for candidate in network.hosts():
                    if len(targets) >= self.max_discovery_hosts:
                        break
                    value = str(candidate)
                    if not any(self._device_host(item) == value if isinstance(item, dict) else str(item).strip() == value for item in targets):
                        targets.append(value)
            except Exception:
                pass
        return targets[: self.max_discovery_hosts]

    def scan(self, cidr: str | None, manual_targets: Iterable[str | dict[str, Any]] | None = None) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for host in self.discover_targets(cidr, manual_targets):
            if isinstance(host, dict):
                results.append(self.probe_device(host))
            else:
                results.append(self.probe_host(host))
        return results
