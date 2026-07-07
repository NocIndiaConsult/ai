from __future__ import annotations

import ipaddress
import json
import platform
import socket
import re
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Callable, Iterable

_SNMP_LOCK = threading.Lock()

try:
    import requests
except Exception:  # pragma: no cover
    requests = None

try:
    import paramiko
except Exception:  # pragma: no cover
    paramiko = None

try:
    import psutil  # used to enumerate every network adapter (incl. VPN tunnel adapters)
except Exception:  # pragma: no cover
    psutil = None

import asyncio

try:
    # pysnmp 6.x/7.x (current PyPI releases) - async-only hlapi.
    from pysnmp.hlapi.v3arch.asyncio import (
        CommunityData,
        ContextData,
        ObjectIdentity,
        ObjectType,
        SnmpEngine,
        UdpTransportTarget,
        get_cmd,
        walk_cmd,
    )
    SNMP_MODE = "asyncio"
    getCmd = nextCmd = None
except Exception:  # pragma: no cover
    try:
        # pysnmp <6 - classic synchronous hlapi (only importable on Python <3.12).
        from pysnmp.hlapi import CommunityData, ContextData, ObjectIdentity, ObjectType, SnmpEngine, UdpTransportTarget, getCmd, nextCmd
        SNMP_MODE = "sync"
        get_cmd = walk_cmd = None
    except Exception:  # pragma: no cover
        CommunityData = ContextData = ObjectIdentity = ObjectType = SnmpEngine = UdpTransportTarget = None
        getCmd = nextCmd = get_cmd = walk_cmd = None
        SNMP_MODE = None


async def _snmp_get_async(host: str, community: str, oid: str, timeout: float, retries: int, mp_model: int = 1) -> list[tuple[str, str]] | None:
    transport = await UdpTransportTarget.create((host, 161), timeout=timeout, retries=retries)
    error_indication, error_status, _error_index, var_binds = await get_cmd(
        SnmpEngine(),
        CommunityData(community, mpModel=mp_model),
        transport,
        ContextData(),
        ObjectType(ObjectIdentity(oid)),
    )
    if error_indication or error_status:
        return None
    return [(str(o), str(v)) for o, v in var_binds]


async def _snmp_walk_async(host: str, community: str, oid: str, timeout: float, retries: int, mp_model: int = 1) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    transport = await UdpTransportTarget.create((host, 161), timeout=timeout, retries=retries)
    async for error_indication, error_status, _error_index, var_binds in walk_cmd(
        SnmpEngine(),
        CommunityData(community, mpModel=mp_model),
        transport,
        ContextData(),
        ObjectType(ObjectIdentity(oid)),
        lexicographicMode=False,
    ):
        if error_indication or error_status:
            break
        for oid_obj, value_obj in var_binds:
            full_oid = str(oid_obj)
            if not full_oid.startswith(oid):
                continue
            rows.append((full_oid[len(oid):].lstrip("."), str(value_obj)))
    return rows


def ping_host(host: str, timeout_ms: int = 1200) -> dict[str, Any]:
    """Fast reachability check.

    Previously this tried 7 TCP ports one after another (up to ~5-6s of
    pure waiting on a fully offline host) and only then fell back to an
    ICMP ping. On a poll cycle with several offline devices that serial
    wait added up to tens of seconds per cycle, which is what made the
    dashboard feel "hung" and made status updates (and alerts) lag far
    behind real device state. TCP ports are raced in parallel; ICMP runs
    only after TCP fails so Windows does not spawn ping.exe on every
    successful TCP probe.
    """
    timeout_s = max(0.15, timeout_ms / 1000.0)
    started = perf_counter()
    ports = (443, 80, 22, 161, 8291, 23, 8080)

    def _try_port(port: int) -> tuple[int, bool]:
        try:
            with socket.create_connection((host, int(port)), timeout=timeout_s):
                return port, True
        except Exception:
            return port, False

    ok = False
    output = "No TCP probe hit"
    with ThreadPoolExecutor(max_workers=len(ports)) as pool:
        futures = {pool.submit(_try_port, p): p for p in ports}
        for future in as_completed(futures):
            port, hit = future.result()
            if hit:
                ok = True
                output = f"TCP probe connected on port {port}"
                break
    if not ok:
        # VPN/tunnel paths often expose no management TCP port to this PC
        # while still being reachable by ICMP. Run ping only as fallback.
        diag = run_ping_diagnostic(host, count=1, timeout_ms=timeout_ms)
        if diag.get("success"):
            ok = True
            output = str(diag.get("output") or "ICMP ping replied")
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


def _is_windows() -> bool:
    return platform.system().lower().startswith("win")


def _subprocess_hidden_kwargs() -> dict[str, Any]:
    if not _is_windows():
        return {}
    kwargs: dict[str, Any] = {}
    try:
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0
        kwargs["startupinfo"] = startupinfo
    except Exception:
        pass
    try:
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    except Exception:
        pass
    return kwargs


def _safe_target(target: str) -> str:
    """Keep only characters valid in a hostname/IP so user input can never be
    used to inject extra shell/CLI arguments into the ping/tracert command."""
    value = str(target or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9.:_-]{1,255}", value or ""):
        return ""
    return value


def run_ping_diagnostic(target: str, count: int = 4, timeout_ms: int = 1000) -> dict[str, Any]:
    """Run a real ICMP ping (Windows: ping.exe, Linux/Mac: ping) against a
    device so a technician can see live round-trip results, not just a
    reachability flag."""
    host = _safe_target(target)
    if not host:
        return {"target": target, "success": False, "output": "Invalid host/IP.", "command": ""}
    count = max(1, min(int(count or 4), 10))
    timeout_ms = max(200, min(int(timeout_ms or 1000), 5000))
    if _is_windows():
        cmd = ["ping", "-n", str(count), "-w", str(timeout_ms), host]
    else:
        cmd = ["ping", "-c", str(count), "-W", str(max(1, timeout_ms // 1000)), host]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=(count * timeout_ms / 1000.0) + 8,
            **_subprocess_hidden_kwargs(),
        )
        output = ((proc.stdout or "") + (proc.stderr or "")).strip()
        success = proc.returncode == 0
    except FileNotFoundError:
        output = "ping utility not found on this system."
        success = False
    except subprocess.TimeoutExpired:
        output = "Ping timed out."
        success = False
    except Exception as exc:
        output = f"Ping failed: {exc}"
        success = False
    return {"target": host, "success": success, "output": output or "No output.", "command": " ".join(cmd)}


def run_traceroute_diagnostic(target: str, max_hops: int = 30) -> dict[str, Any]:
    """Run tracert (Windows) / traceroute (Linux/Mac) so a technician can see
    every hop between this PC and the destination device."""
    host = _safe_target(target)
    if not host:
        return {"target": target, "success": False, "output": "Invalid host/IP.", "command": ""}
    max_hops = max(1, min(int(max_hops or 30), 64))
    if _is_windows():
        cmd = ["tracert", "-d", "-h", str(max_hops), "-w", "1000", host]
    else:
        cmd = ["traceroute", "-m", str(max_hops), host]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=max_hops * 2 + 15,
            **_subprocess_hidden_kwargs(),
        )
        output = ((proc.stdout or "") + (proc.stderr or "")).strip()
        success = proc.returncode == 0
    except FileNotFoundError:
        output = "traceroute/tracert utility not found on this system."
        success = False
    except subprocess.TimeoutExpired:
        output = "Traceroute timed out."
        success = False
    except Exception as exc:
        output = f"Traceroute failed: {exc}"
        success = False
    return {"target": host, "success": success, "output": output or "No output.", "command": " ".join(cmd)}


def run_dns_lookup(target: str) -> dict[str, Any]:
    host = _safe_target(target)
    if not host:
        return {"target": target, "success": False, "output": "Invalid host/IP."}
    try:
        info = socket.gethostbyname_ex(host)
        name, aliases, addrs = info
        lines = [f"Name: {name}"]
        if aliases:
            lines.append(f"Aliases: {', '.join(aliases)}")
        lines.append(f"Addresses: {', '.join(addrs)}")
        return {"target": host, "success": True, "output": "\n".join(lines)}
    except Exception as exc:
        return {"target": host, "success": False, "output": f"DNS lookup failed: {exc}"}


def run_port_check(target: str, ports: Iterable[int] | None = None) -> dict[str, Any]:
    host = _safe_target(target)
    if not host:
        return {"target": target, "success": False, "output": "Invalid host/IP."}
    check_ports = [int(p) for p in (ports or [22, 23, 80, 443, 161, 8080, 8291, 3389])]
    lines = []
    any_open = False
    for port in check_ports:
        probe = tcp_probe(host, port)
        if probe.get("open"):
            any_open = True
            lines.append(f"Port {port}: OPEN ({probe.get('latency_ms')} ms)")
        else:
            lines.append(f"Port {port}: closed/filtered")
    return {"target": host, "success": any_open, "output": "\n".join(lines)}


def list_local_ipv4_addresses() -> list[str]:
    """Every IPv4 address currently bound to this PC's network adapters -
    not just the one used for outbound internet traffic. This is what makes
    it possible to notice a VPN tunnel adapter (OpenVPN/WireGuard/IPSec/etc.)
    in addition to the normal LAN NIC."""
    addrs: set[str] = set()
    if psutil is not None:
        try:
            for iface_addrs in psutil.net_if_addrs().values():
                for addr in iface_addrs:
                    if getattr(addr, "family", None) == socket.AF_INET and addr.address and not addr.address.startswith("127."):
                        addrs.add(addr.address)
        except Exception:
            pass
    if not addrs:
        try:
            _, _, ip_list = socket.gethostbyname_ex(socket.gethostname())
            for ip in ip_list:
                if ip and not ip.startswith("127."):
                    addrs.add(ip)
        except Exception:
            pass
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(("8.8.8.8", 80))
            default_ip = probe.getsockname()[0]
            if default_ip and not default_ip.startswith("127."):
                addrs.add(default_ip)
        finally:
            probe.close()
    except Exception:
        pass
    return sorted(addrs)


def detect_local_cidr() -> str | None:
    """Guess this PC's own LAN /24 so devices that answer a ping from this
    machine can be auto-discovered even when no discovery CIDR is configured."""
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(("8.8.8.8", 80))
            local_ip = probe.getsockname()[0]
        finally:
            probe.close()
        if not local_ip or local_ip.startswith("127."):
            return None
        parts = local_ip.split(".")
        if len(parts) != 4:
            return None
        return f"{parts[0]}.{parts[1]}.{parts[2]}.0/24"
    except Exception:
        return None


def detect_all_local_cidrs() -> list[str]:
    """Guess a /24 for every network adapter on this PC, including a VPN
    tunnel adapter, so an outward discovery scan can also cover the VPN
    side of the network (useful for split-tunnel VPNs where the VPN subnet
    is small enough and directly routable)."""
    cidrs: list[str] = []
    for ip in list_local_ipv4_addresses():
        parts = ip.split(".")
        if len(parts) == 4:
            cidr = f"{parts[0]}.{parts[1]}.{parts[2]}.0/24"
            if cidr not in cidrs:
                cidrs.append(cidr)
    return cidrs


class IncomingPingListener:
    """Watches for ICMP Echo Request ("ping") packets arriving at this PC on
    any interface - including a VPN tunnel adapter - and reports the source
    IP of every device that pings this machine.

    This covers the case an outward subnet scan cannot: a device that is far
    away, only reachable *through* a VPN, whose IP this agent has no way of
    guessing in advance. The moment that device pings this PC, its source IP
    is captured here and can be auto-added as a discovered device.

    Needs administrator/root privileges (raw sockets). If that isn't
    available, `last_error` is set and the rest of the agent keeps working
    normally - this is a best-effort extra signal, not a hard requirement.
    """

    def __init__(self, on_ping: "Callable[[str], None]") -> None:
        self._on_ping = on_ping
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self.active = False
        self.last_error: str | None = None

    def start(self) -> None:
        if self._threads:
            return
        self._stop.clear()
        if _is_windows():
            bind_ips = list_local_ipv4_addresses() or ["0.0.0.0"]
            for ip in bind_ips:
                thread = threading.Thread(target=self._run_windows, args=(ip,), daemon=True)
                thread.start()
                self._threads.append(thread)
        else:
            thread = threading.Thread(target=self._run_posix, daemon=True)
            thread.start()
            self._threads.append(thread)

    def stop(self) -> None:
        self._stop.set()

    def _handle_packet(self, packet: bytes) -> None:
        try:
            if len(packet) < 20:
                return
            ihl = (packet[0] & 0x0F) * 4
            protocol = packet[9]
            if protocol != 1 or len(packet) < ihl + 8:
                return
            icmp_type = packet[ihl]
            if icmp_type != 8:  # Echo Request only - ignore replies/other ICMP
                return
            src_ip = socket.inet_ntoa(packet[12:16])
            self._on_ping(src_ip)
        except Exception:
            pass

    def _run_windows(self, bind_ip: str) -> None:
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_IP)
            sock.bind((bind_ip, 0))
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_HDRINCL, 1)
            sock.ioctl(socket.SIO_RCVALL, socket.RCVALL_ON)
            sock.settimeout(1.0)
            self.active = True
            while not self._stop.is_set():
                try:
                    packet, _addr = sock.recvfrom(65565)
                except socket.timeout:
                    continue
                except Exception:
                    break
                self._handle_packet(packet)
        except Exception as exc:
            self.last_error = f"{bind_ip}: {exc}"
        finally:
            if sock is not None:
                try:
                    sock.ioctl(socket.SIO_RCVALL, socket.RCVALL_OFF)
                except Exception:
                    pass
                try:
                    sock.close()
                except Exception:
                    pass

    def _run_posix(self) -> None:
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_ICMP)
            sock.settimeout(1.0)
            self.active = True
            while not self._stop.is_set():
                try:
                    packet, _addr = sock.recvfrom(65565)
                except socket.timeout:
                    continue
                except Exception:
                    break
                self._handle_packet(packet)
        except PermissionError as exc:
            self.last_error = f"permission denied - needs root/CAP_NET_RAW: {exc}"
        except Exception as exc:
            self.last_error = str(exc)
        finally:
            if sock is not None:
                try:
                    sock.close()
                except Exception:
                    pass


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
        # Cache of host+community -> working SNMP mpModel (1=v2c, 0=v1).
        # Many DBC/Syrotech (and other budget) OLTs only implement SNMPv1 and
        # simply drop v2c GET/GETBULK requests with no response at all, which
        # looks identical to "unreachable" unless we also try v1.
        self._snmp_version_cache: dict[str, int] = {}

    def _norm(self, value: Any) -> str:
        return str(value or "").strip()

    def _device_protocol(self, device: dict[str, Any] | str | None) -> str:
        if isinstance(device, dict):
            proto = self._norm(device.get("access_protocol") or device.get("protocol") or "auto").lower()
            vendor_family = self._norm(device.get("vendor_family") or device.get("vendor") or "").lower()
            model = self._norm(device.get("model") or "").lower()
            has_creds = bool(self._norm(device.get("username")) and self._norm(device.get("password")))
            vendor_model = " ".join([vendor_family, model])
            if proto == "auto":
                if any(tag in vendor_model for tag in ("mikrotik", "routeros")):
                    return "rest"
                if "cisco" in vendor_model:
                    return "ssh_cisco" if has_creds else "snmp"
                if any(tag in vendor_model for tag in ("hpe", "aruba", "comware")):
                    return "ssh_hpe" if has_creds else "snmp"
                # DBC and Syrotech GPON/EPON OLTs are VSOL-based rebrands with
                # their own quirks (SNMP daemon off by default, factory
                # community strings of "public"/"private", Cisco-style CLI
                # over SSH/telnet) - handled by a dedicated hybrid path below
                # instead of the generic SNMP-only path other vendors use.
                if any(tag in vendor_model for tag in ("dbc", "syrotech")):
                    return "dbc_syrotech"
                # TP-Link, D-Link, Grandstream, Genexis and other OLT/switch
                # vendors: standard SNMP (IF-MIB/BRIDGE-MIB) works broadly
                # here without needing a vendor-specific CLI parser.
                if any(tag in vendor_model for tag in ("snmp", "olt", "tp-link", "d-link", "genexis", "grandstream")):
                    return "snmp"
                if has_creds:
                    return "ssh"
                return "snmp"
            return proto
        return "auto"

    def _port_scan(self, host: str, ports: Iterable[int] | None = None) -> list[int]:
        # Was a sequential loop (up to len(ports) * 0.5s in the worst case,
        # e.g. ~4s for 8 ports on an unreachable host). Probing all ports
        # concurrently keeps this to ~0.5s regardless of how many ports are
        # checked, which matters since this runs once per "auto" protocol
        # device on every poll cycle.
        port_list = list(ports or self.common_ports)
        if not port_list:
            return []
        open_ports: list[int] = []
        with ThreadPoolExecutor(max_workers=len(port_list)) as pool:
            futures = {pool.submit(self._is_port_open, host, port, 0.5): port for port in port_list}
            for future in as_completed(futures):
                port = futures[future]
                try:
                    if future.result():
                        open_ports.append(int(port))
                except Exception:
                    continue
        return open_ports

    @staticmethod
    def _is_port_open(host: str, port: int, timeout: float) -> bool:
        try:
            with socket.create_connection((host, int(port)), timeout=timeout):
                return True
        except Exception:
            return False

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
        mac_table: list[dict[str, Any]] = []
        try:
            hosts_data = self._rest_get(host, "/rest/interface/bridge/host", username, password)
            if isinstance(hosts_data, dict):
                hosts_data = hosts_data.get("result") or hosts_data.get("data") or []
            for row in hosts_data if isinstance(hosts_data, list) else []:
                if not isinstance(row, dict):
                    continue
                mac_addr = self._norm(row.get("mac-address") or row.get("mac_address"))
                iface = self._norm(row.get("on-interface") or row.get("interface") or row.get("bridge"))
                if mac_addr and iface:
                    mac_table.append({"mac": mac_addr.lower(), "interface": iface})
        except Exception:
            mac_table = []
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
        mem_total = None
        uptime = None
        temperature = None
        serial = None
        mac = None
        if isinstance(resource, dict):
            uptime = resource.get("uptime")
            cpu = resource.get("cpu-load") or resource.get("cpu")
            mem = resource.get("free-memory") or resource.get("memory")
            mem_total = resource.get("total-memory")
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
            "memory_total": float(re.search(r"-?\d+(?:\.\d+)?", str(mem_total)).group(0)) if mem_total is not None and re.search(r"-?\d+(?:\.\d+)?", str(mem_total)) else None,
            "uptime": uptime,
            "temperature": temperature,
            "serial_number": serial,
            "mac_address": mac,
            "mac_table": mac_table,
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
        mac_table: list[dict[str, Any]] = []
        try:
            mac_output = self._ssh_command(host, username, password, " /interface bridge host print without-paging")
            for mline in mac_output.splitlines():
                mline = mline.strip()
                if not mline or "mac-address=" not in mline:
                    continue
                mac_match = re.search(r'mac-address=([0-9A-Fa-f:]+)', mline)
                iface_match = re.search(r'(?:on-interface|interface)=([^ ]+)', mline)
                if mac_match and iface_match:
                    mac_table.append({"mac": mac_match.group(1).strip().lower(), "interface": iface_match.group(1).strip().strip('"')})
        except Exception:
            mac_table = []
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
        return {"host": host, "reachable": reachable or bool(output), "latency_ms": None, "ping_output": "ssh-snapshot", "device_type": "switch", "protocol": "ssh", "summary": {"ports": len(ports), "port_details": ports, "ports_down": len(down_ports), "ports_up": len(ports) - len(down_ports), "mac_table": mac_table, "note": "SSH snapshot collected."}, "alerts": alerts}

    def build_onu_config_plan(self, device: dict[str, Any], payload: dict[str, Any]) -> list[str]:
        host = self._device_host(device)
        vendor = " ".join(
            [
                str(device.get("vendor") or ""),
                str(device.get("vendor_family") or ""),
                str(device.get("model") or ""),
            ]
        ).lower()
        onu_serial = str(payload.get("onu_serial") or "").strip()
        pon_port = str(payload.get("pon_port") or "").strip()
        service_name = str(payload.get("service_name") or onu_serial or "ONU-SERVICE").strip()
        vlan = str(payload.get("vlan") or "1").strip()
        bandwidth = str(payload.get("bandwidth_mbps") or "100").strip()
        wan_mode = str(payload.get("wan_mode") or "bridge").strip().lower()
        pppoe_user = str(payload.get("pppoe_username") or "").strip()
        pppoe_pass = str(payload.get("pppoe_password") or "").strip()
        ssid = str(payload.get("ssid") or "").strip()
        wifi_pass = str(payload.get("wifi_password") or "").strip()
        if not onu_serial:
            raise ValueError("ONU serial is required")
        if not pon_port:
            raise ValueError("PON port is required")

        # Vendor CLIs differ, but most VSOL/BDCOM/Syrotech-style OLTs follow
        # profile -> bind -> service/vlan concepts. Keep the plan explicit so
        # the operator can verify before Apply writes anything.
        if any(key in vendor for key in ("syrotech", "dbc", "bdcom", "vsol", "optilink", "netlink")):
            commands = [
                "enable",
                "configure terminal",
                f"interface pon {pon_port}",
                f"onu {onu_serial}",
                f"service-profile {service_name}",
                f"vlan {vlan}",
                f"bandwidth {bandwidth}",
                f"wan mode {wan_mode}",
            ]
            if wan_mode == "pppoe" and pppoe_user:
                commands.append(f"pppoe username {pppoe_user}")
            if wan_mode == "pppoe" and pppoe_pass:
                commands.append(f"pppoe password {pppoe_pass}")
            if ssid:
                commands.append(f"wifi ssid {ssid}")
            if wifi_pass:
                commands.append(f"wifi password {wifi_pass}")
            commands.extend(["exit", "write memory"])
            return commands
        return [
            f"# Generic ONU config plan for {host}",
            f"onu {onu_serial}",
            f"pon-port {pon_port}",
            f"service {service_name}",
            f"vlan {vlan}",
            f"bandwidth {bandwidth}",
            f"wan mode {wan_mode}",
            *( [f"wifi ssid {ssid}"] if ssid else [] ),
            *( [f"wifi password {wifi_pass}"] if wifi_pass else [] ),
        ]

    def configure_onu(self, device: dict[str, Any], payload: dict[str, Any], apply: bool = False) -> dict[str, Any]:
        host = self._device_host(device)
        commands = self.build_onu_config_plan(device, payload)
        if not apply:
            return {
                "ok": True,
                "mode": "dry_run",
                "host": host,
                "message": "Dry run only. No configuration was written to the OLT.",
                "commands": commands,
            }
        username = self._norm(device.get("username"))
        password = self._norm(device.get("password"))
        if not username:
            return {
                "ok": False,
                "mode": "apply",
                "host": host,
                "message": "SSH username/password required before applying ONU configuration.",
                "commands": commands,
            }
        output_chunks: list[str] = []
        try:
            for command in commands:
                if command.strip().startswith("#"):
                    continue
                output_chunks.append(f"$ {command}\n")
                output_chunks.append(self._ssh_command(host, username, password, command, timeout=8) or "ok\n")
            return {
                "ok": True,
                "mode": "apply",
                "host": host,
                "message": "ONU configuration commands sent to OLT.",
                "commands": commands,
                "output": "".join(output_chunks)[-6000:],
            }
        except Exception as exc:
            return {
                "ok": False,
                "mode": "apply",
                "host": host,
                "message": str(exc),
                "commands": commands,
                "output": "".join(output_chunks)[-6000:],
            }

    def _snmp_cache_key(self, host: str, community: str) -> str:
        return f"{host}|{community}"

    def _snmp_versions_to_try(self, host: str, community: str) -> list[int]:
        """Try SNMPv2c first (mpModel=1), then fall back to SNMPv1 (mpModel=0).
        A lot of DBC/Syrotech and other low-cost GPON OLTs never got v2c
        (GETBULK) support in their firmware and just silently drop those
        packets, which previously looked exactly like the OLT being offline.
        Once we learn which version a host actually answers on, we remember
        it so we don't pay the extra round-trip on every subsequent walk."""
        key = self._snmp_cache_key(host, community)
        cached = self._snmp_version_cache.get(key)
        if cached is not None:
            return [cached]
        return [1, 0]

    def _snmp_walk(self, host: str, community: str, oid: str, timeout: float = 2.0, retries: int = 1) -> list[tuple[str, str]]:
        """Walks an SNMP subtree, returning (index_suffix, value) pairs so rows
        can be correlated across separate walks (e.g. matching ifIndex between
        ifDescr / ifOperStatus / ifInOctets). IF-MIB and BRIDGE-MIB are
        standard MIBs supported by virtually every manageable switch, router,
        and OLT regardless of vendor, which is what makes this path work
        across Cisco, HPE, TP-Link, D-Link, Grandstream, and unbranded gear
        without a vendor-specific parser."""
        key = self._snmp_cache_key(host, community)
        # pysnmp's asyncio transport is not reliably thread-safe on Windows.
        # Other probes may run in parallel, but SNMP walks/gets are serialized
        # so OLT ifTable walks do not randomly come back empty.
        with _SNMP_LOCK:
            for mp_model in self._snmp_versions_to_try(host, community):
                rows: list[tuple[str, str]] = []
                if SNMP_MODE == "asyncio":
                    try:
                        rows = asyncio.run(_snmp_walk_async(host, community, oid, timeout, retries, mp_model))
                    except Exception:
                        rows = []
                elif SNMP_MODE == "sync" and nextCmd is not None:
                    try:
                        iterator = nextCmd(
                            SnmpEngine(),
                            CommunityData(community, mpModel=mp_model),
                            UdpTransportTarget((host, 161), timeout=timeout, retries=retries),
                            ContextData(),
                            ObjectType(ObjectIdentity(oid)),
                            lexicographicMode=False,
                        )
                        for error_indication, error_status, error_index, var_binds in iterator:
                            if error_indication or error_status:
                                break
                            for var_bind in var_binds:
                                oid_obj, value_obj = var_bind
                                full_oid = str(oid_obj)
                                if not full_oid.startswith(oid):
                                    continue
                                rows.append((full_oid[len(oid):].lstrip("."), str(value_obj)))
                    except Exception:
                        rows = []
                if rows:
                    self._snmp_version_cache[key] = mp_model
                    return rows
        return []

    def _snmp_get_ok(self, host: str, community: str, oid: str, timeout: float = 2.0, retries: int = 1) -> bool:
        key = self._snmp_cache_key(host, community)
        with _SNMP_LOCK:
            for mp_model in self._snmp_versions_to_try(host, community):
                ok = False
                if SNMP_MODE == "asyncio":
                    try:
                        result = asyncio.run(_snmp_get_async(host, community, oid, timeout, retries, mp_model))
                        ok = result is not None
                    except Exception:
                        ok = False
                elif SNMP_MODE == "sync" and getCmd is not None:
                    try:
                        iterator = getCmd(
                            SnmpEngine(),
                            CommunityData(community, mpModel=mp_model),
                            UdpTransportTarget((host, 161), timeout=timeout, retries=retries),
                            ContextData(),
                            ObjectType(ObjectIdentity(oid)),
                        )
                        for error_indication, error_status, error_index, var_binds in iterator:
                            ok = not error_indication and not error_status
                            break
                    except Exception:
                        ok = False
                if ok:
                    self._snmp_version_cache[key] = mp_model
                    return True
        return False

    def _snmp_interface_table(self, host: str, community: str) -> dict[str, dict[str, Any]]:
        table: dict[str, dict[str, Any]] = {}
        for idx, name in self._snmp_walk(host, community, "1.3.6.1.2.1.2.2.1.2"):
            table.setdefault(idx, {})["name"] = name.strip('"')
        for idx, val in self._snmp_walk(host, community, "1.3.6.1.2.1.2.2.1.8"):
            table.setdefault(idx, {})["oper_status"] = val
        for idx, val in self._snmp_walk(host, community, "1.3.6.1.2.1.2.2.1.7"):
            table.setdefault(idx, {})["admin_status"] = val
        for idx, val in self._snmp_walk(host, community, "1.3.6.1.2.1.2.2.1.10"):
            table.setdefault(idx, {})["in_octets"] = val
        for idx, val in self._snmp_walk(host, community, "1.3.6.1.2.1.2.2.1.16"):
            table.setdefault(idx, {})["out_octets"] = val
        return table

    def _snmp_mac_table(self, host: str, community: str, if_table: dict[str, dict[str, Any]]) -> list[dict[str, str]]:
        # BRIDGE-MIB: bridge port -> ifIndex, then the FDB table (indexed by
        # the MAC's own 6 octets) gives MAC -> bridge port.
        port_to_ifindex: dict[str, str] = dict(self._snmp_walk(host, community, "1.3.6.1.2.1.17.1.4.1.2"))
        mac_entries: list[dict[str, str]] = []
        for suffix, port_val in self._snmp_walk(host, community, "1.3.6.1.2.1.17.4.3.1.2"):
            octets = suffix.split(".")
            if len(octets) != 6:
                continue
            try:
                mac = ":".join(f"{int(o):02x}" for o in octets)
            except Exception:
                continue
            ifindex = port_to_ifindex.get(port_val.strip())
            iface_name = if_table.get(ifindex, {}).get("name") if ifindex else None
            if mac and iface_name:
                mac_entries.append({"mac": mac.lower(), "interface": iface_name})
        return mac_entries

    def _snmp_snapshot(self, device: dict[str, Any]) -> dict[str, Any]:
        host = self._device_host(device)
        if SNMP_MODE is None:
            return self.probe_host(host)
        community = self._norm(device.get("snmp_community") or "public")
        reachable = self._snmp_get_ok(host, community, "1.3.6.1.2.1.1.5.0")

        if_table = self._snmp_interface_table(host, community) if reachable else {}
        status_map = {"1": "up", "2": "down", "3": "testing", "4": "unknown", "5": "dormant", "6": "not_present", "7": "lower_layer_down"}
        port_details: list[dict[str, Any]] = []
        down_ports: list[str] = []
        for idx, row in if_table.items():
            name = row.get("name") or f"if{idx}"
            oper = status_map.get(str(row.get("oper_status")), "unknown")
            admin = status_map.get(str(row.get("admin_status")), "unknown")
            status = "admin_down" if admin == "down" else oper
            port_details.append({
                "name": name,
                "status": status,
                "rx-byte": row.get("in_octets") or 0,
                "tx-byte": row.get("out_octets") or 0,
            })
            if status in {"down", "admin_down"}:
                down_ports.append(name)

        mac_table = self._snmp_mac_table(host, community, if_table) if reachable else []

        # Best-effort CPU via HOST-RESOURCES-MIB, which many (not all) vendors
        # expose regardless of platform - harmless no-op if unsupported.
        cpu_val = None
        if reachable:
            for _, val in self._snmp_walk(host, community, "1.3.6.1.2.1.25.3.3.1.2"):
                try:
                    cpu_val = float(val)
                    break
                except Exception:
                    continue

        alerts: list[dict[str, Any]] = []
        for name in down_ports:
            alerts.append({
                "event_type": "device.port_down",
                "severity": "critical",
                "host": host,
                "port": name,
                "summary": f"{host} port {name} is down",
            })

        summary: dict[str, Any] = {
            "ports": len(port_details),
            "port_details": port_details,
            "ports_up": sum(1 for p in port_details if p.get("status") == "up"),
            "ports_down": len(down_ports),
            "ports_admin_down": len([p for p in port_details if p.get("status") == "admin_down"]),
            "mac_table": mac_table,
            "note": "SNMP snapshot via standard IF-MIB/BRIDGE-MIB - vendor agnostic.",
        }
        if cpu_val is not None:
            summary["cpu"] = cpu_val
        return {
            "host": host,
            "reachable": reachable,
            "latency_ms": None,
            "ping_output": "snmp-snapshot",
            "device_type": "switch",
            "protocol": "snmp",
            "summary": summary,
            "alerts": alerts,
        }

    def _dbc_syrotech_community_candidates(self, device: dict[str, Any]) -> list[str]:
        """DBC/Syrotech OLTs are VSOL-based firmware, shipped from the factory
        with SNMP fully disabled ('snmp-server start' has to be run on the
        OLT's own CLI before it will answer anything) and with two default
        community strings once it is turned on: 'public' (read-only) and
        'private' (read-write). If the person didn't override the community,
        try both defaults instead of only 'public'."""
        configured = self._norm(device.get("snmp_community"))
        candidates = [configured] if configured else []
        for fallback in ("public", "private"):
            if fallback not in candidates:
                candidates.append(fallback)
        return candidates

    def _dbc_syrotech_snmp_snapshot(self, device: dict[str, Any]) -> dict[str, Any]:
        host = self._device_host(device)
        last_snap: dict[str, Any] | None = None
        for community in self._dbc_syrotech_community_candidates(device):
            device_with_community = dict(device)
            device_with_community["snmp_community"] = community
            snap = self._snmp_snapshot(device_with_community)
            last_snap = snap
            if snap.get("reachable"):
                snap["summary"] = snap.get("summary") or {}
                snap["summary"]["snmp_community_used"] = community
                return snap
        return last_snap or {"host": host, "reachable": False, "latency_ms": None, "ping_output": "snmp-snapshot", "summary": {}}

    _DBC_SYROTECH_CLI_COMMANDS = [
        "show interface gigabitethernet status",
        "show interface brief",
        "show interface gpon-olt status",
        "show interface",
    ]

    # Matches Cisco-style port lines such as:
    #   GigabitEthernet0/1   up      up      1000    full
    #   gpon-olt_0/1        enable   up
    _CLI_IFACE_LINE_RE = re.compile(
        r"^(?P<name>(?:gigabitethernet|gpon-olt|gpon-onu|epon-olt|fastethernet|ge|gi)\S*)\s+"
        r"(?P<admin>up|down|enable|disable)\s+(?P<oper>up|down)\b",
        re.IGNORECASE,
    )

    def _dbc_syrotech_cli_interfaces(self, device: dict[str, Any]) -> tuple[list[dict[str, Any]], str | None]:
        """Best-effort parser for the Cisco-style CLI these OLTs expose over
        SSH (confirmed by vendor config dumps showing 'interface
        gigabitethernet 0/x' blocks and 'crypto key generate rsa', i.e. SSH
        management is present on this platform). Different firmware builds
        word their 'show interface' output slightly differently, so several
        candidate commands/patterns are tried; if none match, this returns
        an empty list rather than guessing."""
        host = self._device_host(device)
        username = self._norm(device.get("username"))
        password = self._norm(device.get("password"))
        if not (username and password):
            return [], "no SSH username/password saved for this device"
        last_error: str | None = None
        for command in self._DBC_SYROTECH_CLI_COMMANDS:
            try:
                output = self._ssh_command(host, username, password, command)
            except Exception as exc:
                last_error = str(exc)
                continue
            if not output:
                continue
            ports: list[dict[str, Any]] = []
            for line in output.splitlines():
                match = self._CLI_IFACE_LINE_RE.match(line.strip())
                if not match:
                    continue
                admin = match.group("admin").lower()
                oper = match.group("oper").lower()
                status = "admin_down" if admin in ("down", "disable") else oper
                ports.append({"name": match.group("name"), "status": status, "rx-byte": 0, "tx-byte": 0})
            if ports:
                return ports, None
        return [], last_error or "CLI did not return a recognizable interface table"

    def _dbc_syrotech_snapshot(self, device: dict[str, Any]) -> dict[str, Any]:
        host = self._device_host(device)
        snap = self._dbc_syrotech_snmp_snapshot(device)
        summary = snap.setdefault("summary", {})
        if snap.get("reachable") and summary.get("ports", 0):
            summary["note"] = (
                f"DBC/Syrotech OLT: SNMP responded using community "
                f"'{summary.get('snmp_community_used', 'public')}'."
            )
            return snap
        # SNMP gave nothing usable (either the OLT genuinely isn't reachable,
        # or - very common on these OLTs - the SNMP daemon was never turned
        # on with 'snmp-server start' on the OLT itself). Fall back to the
        # Cisco-style CLI over SSH, which these OLTs support out of the box.
        cli_ports, cli_error = self._dbc_syrotech_cli_interfaces(device)
        if cli_ports:
            down_ports = [p["name"] for p in cli_ports if p["status"] in ("down", "admin_down")]
            snap["reachable"] = True
            snap["protocol"] = "ssh"
            summary["ports"] = len(cli_ports)
            summary["port_details"] = cli_ports
            summary["ports_up"] = sum(1 for p in cli_ports if p["status"] == "up")
            summary["ports_down"] = len(down_ports)
            summary["ports_admin_down"] = len([p for p in cli_ports if p["status"] == "admin_down"])
            summary["note"] = "DBC/Syrotech OLT: SNMP unavailable, interfaces read via SSH CLI instead."
            snap["alerts"] = [
                {
                    "event_type": "device.port_down",
                    "severity": "critical",
                    "host": host,
                    "port": name,
                    "summary": f"{host} port {name} is down",
                }
                for name in down_ports
            ]
            return snap
        # Neither SNMP nor CLI produced interfaces - give a precise, actionable
        # reason instead of a bare "offline", since in practice this is almost
        # always one of a small number of causes on this OLT platform.
        community_tried = ", ".join(self._dbc_syrotech_community_candidates(device))
        reasons = [
            f"SNMP: no response using community/ies '{community_tried}' (tried SNMPv2c and v1).",
            "This platform ships with SNMP OFF by default - log into the OLT CLI (SSH/telnet/web) "
            "and run: snmp-server start, then snmp-server community public ro (and/or "
            "snmp-server community private rw), and make sure the community here matches.",
        ]
        if cli_error:
            reasons.append(f"SSH CLI fallback also failed: {cli_error}.")
        else:
            reasons.append("SSH CLI fallback was not attempted (no username/password saved for this device).")
        summary["note"] = " ".join(reasons)
        return snap

    def _cisco_ssh_snapshot(self, device: dict[str, Any]) -> dict[str, Any]:
        # SNMP already gives vendor-agnostic ports/traffic/mac-table; SSH here
        # only adds what SNMP can't reliably get on IOS: CPU and temperature.
        snap = self._snmp_snapshot(device)
        host = self._device_host(device)
        username = self._norm(device.get("username"))
        password = self._norm(device.get("password"))
        if username and password:
            try:
                cpu_out = self._ssh_command(host, username, password, "show processes cpu | include five")
                match = re.search(r"five minutes:\s*(\d+)%", cpu_out)
                if match:
                    snap.setdefault("summary", {})["cpu"] = float(match.group(1))
            except Exception:
                pass
            try:
                env_out = self._ssh_command(host, username, password, "show env temperature")
                match = re.search(r"(-?\d+(?:\.\d+)?)\s*(?:C|Celsius|degrees)", env_out, re.IGNORECASE)
                if match:
                    snap.setdefault("summary", {})["temperature"] = match.group(1)
            except Exception:
                pass
        snap["protocol"] = "ssh"
        snap.setdefault("summary", {})["note"] = "Cisco: SNMP (ports/traffic/MAC) + SSH (CPU/temperature) hybrid."
        return snap

    def _hpe_ssh_snapshot(self, device: dict[str, Any]) -> dict[str, Any]:
        # Same hybrid approach for HPE Comware/ArubaOS-CX gear.
        snap = self._snmp_snapshot(device)
        host = self._device_host(device)
        username = self._norm(device.get("username"))
        password = self._norm(device.get("password"))
        if username and password:
            try:
                cpu_out = self._ssh_command(host, username, password, "display cpu-usage")
                match = re.search(r"CPU usage:\s*(\d+)%", cpu_out) or re.search(r"(\d+)%", cpu_out)
                if match:
                    snap.setdefault("summary", {})["cpu"] = float(match.group(1))
            except Exception:
                pass
            try:
                env_out = self._ssh_command(host, username, password, "display environment")
                match = re.search(r"Temperature.*?(-?\d+(?:\.\d+)?)", env_out, re.IGNORECASE | re.DOTALL)
                if match:
                    snap.setdefault("summary", {})["temperature"] = match.group(1)
            except Exception:
                pass
        snap["protocol"] = "ssh"
        snap.setdefault("summary", {})["note"] = "HPE: SNMP (ports/traffic/MAC) + SSH (CPU/temperature) hybrid."
        return snap

    def probe_host(self, host: str) -> dict[str, Any]:
        ping = ping_host(host)
        open_ports: list[int] = []
        closed_ports: list[int] = []
        probe_results: dict[int, dict[str, Any]] = {}
        with ThreadPoolExecutor(max_workers=max(1, len(self.common_ports))) as pool:
            futures = {pool.submit(tcp_probe, host, port): port for port in self.common_ports}
            for future in as_completed(futures):
                port = futures[future]
                try:
                    probe = future.result()
                except Exception as exc:
                    probe = {"port": port, "open": False, "error": str(exc)}
                probe_results[port] = probe
                if probe.get("open"):
                    open_ports.append(port)
                else:
                    closed_ports.append(port)
        if not ping["reachable"] and open_ports:
            ping["reachable"] = True
            ping["latency_ms"] = min(
                (probe_results[p].get("latency_ms") for p in open_ports if probe_results[p].get("latency_ms") is not None),
                default=None,
            )
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
            if not reach.get("reachable"):
                # Fast-fail path: the device does not even answer ping/TCP,
                # so there is no point spending 6-20+ seconds trying
                # SNMP/SSH/REST (each with its own multi-second timeout) on
                # a host that is simply down. This was the main reason a
                # handful of offline devices could stall an entire poll
                # cycle and delay status updates for every other device by
                # many minutes. Report it as offline immediately instead.
                return {
                    "host": host,
                    "reachable": False,
                    "latency_ms": reach.get("latency_ms"),
                    "protocol": self._device_protocol(device),
                    "ping_output": reach.get("ping_output") or "Host unreachable from this agent",
                    "summary": {
                        "ports": 0,
                        "port_details": [],
                        "note": "Device did not respond to ping/TCP; skipped SNMP/SSH/REST probe to keep the poll cycle fast.",
                    },
                    "alerts": [],
                }
            protocol = self._device_protocol(device)
            if protocol == "auto":
                scan_ports = self._port_scan(host, ports=[80, 443, 8080, 8728, 8729, 161, 22, 8291])
                vendor_model_text = " ".join([self._norm(device.get("vendor_family")), self._norm(device.get("vendor")), self._norm(device.get("model"))]).lower()
                if any(tag in vendor_model_text for tag in ("mikrotik", "routeros")):
                    protocol = "rest"
                elif any(tag in vendor_model_text for tag in ("dbc", "syrotech")):
                    protocol = "dbc_syrotech"
                elif any(port in scan_ports for port in (80, 443, 8080, 8728, 8729)):
                    protocol = "rest"
                elif 161 in scan_ports or self._norm(device.get("snmp_community")):
                    protocol = "snmp"
                elif 22 in scan_ports or (self._norm(device.get("username")) and self._norm(device.get("password"))):
                    protocol = "ssh"
                else:
                    protocol = "snmp"
            if protocol == "rest":
                snap = self._mikrotik_rest_snapshot(device)
                snap["reachable"] = bool(snap.get("reachable")) or bool(reach.get("reachable"))
                snap["latency_ms"] = reach.get("latency_ms")
                snap["ping_output"] = reach.get("ping_output") or snap.get("ping_output")
                return snap
            if protocol == "ssh_cisco":
                snap = self._cisco_ssh_snapshot(device)
                snap["reachable"] = bool(snap.get("reachable")) or bool(reach.get("reachable"))
                snap["latency_ms"] = reach.get("latency_ms")
                snap["ping_output"] = reach.get("ping_output") or snap.get("ping_output")
                return snap
            if protocol == "ssh_hpe":
                snap = self._hpe_ssh_snapshot(device)
                snap["reachable"] = bool(snap.get("reachable")) or bool(reach.get("reachable"))
                snap["latency_ms"] = reach.get("latency_ms")
                snap["ping_output"] = reach.get("ping_output") or snap.get("ping_output")
                return snap
            if protocol == "dbc_syrotech":
                snap = self._dbc_syrotech_snapshot(device)
                snap["latency_ms"] = reach.get("latency_ms")
                snap["ping_output"] = reach.get("ping_output") or snap.get("ping_output")
                if not snap.get("reachable") and reach.get("reachable"):
                    # Host answers ping/TCP but neither SNMP nor SSH CLI
                    # produced anything - still surface it as reachable so it
                    # doesn't look like a dead OLT, the note already explains
                    # the likely cause (SNMP off, wrong creds, etc).
                    snap["reachable"] = True
                return snap
            if protocol == "snmp":
                snap = self._snmp_snapshot(device)
                snap["latency_ms"] = reach.get("latency_ms")
                snap["ping_output"] = reach.get("ping_output") or snap.get("ping_output")
                if snap.get("reachable"):
                    # SNMP answered. Return it as-is even if ifTable came
                    # back with 0 ports (e.g. an OLT that exposes its PON/GE
                    # ports under vendor-enterprise OIDs instead of the
                    # standard ifTable) rather than silently discarding a
                    # valid response and reporting "offline".
                    if not snap.get("summary", {}).get("ports", 0):
                        snap.setdefault("summary", {})["note"] = (
                            "SNMP responded (device is online) but the standard IF-MIB ifTable "
                            "returned no interfaces. This OLT model likely exposes its PON/GE "
                            "ports under a vendor-specific enterprise MIB instead of the standard "
                            "one - it needs a vendor-specific OID set, not a real connectivity issue."
                        )
                    return snap
                if reach.get("reachable"):
                    # Host answers ping/TCP but SNMP (v2c and v1) got no
                    # response at all - almost always a wrong community
                    # string, SNMP disabled on the OLT's management
                    # VLAN/IP, or an ACL blocking this agent's IP; not the
                    # OLT actually being offline.
                    snap["reachable"] = True
                    community = self._norm(device.get("snmp_community") or "public")
                    snap.setdefault("summary", {})["note"] = (
                        f"Device is reachable on the network but did not respond to SNMP "
                        f"(tried v2c and v1) using community '{community}'. Check that SNMP is "
                        f"enabled on the OLT's management VLAN/IP, the community string matches "
                        f"exactly (case-sensitive), and no ACL is blocking this agent's IP."
                    )
                    return snap
            if protocol == "ssh":
                snap = self._ssh_snapshot(device)
                snap["latency_ms"] = reach.get("latency_ms")
                snap["ping_output"] = reach.get("ping_output") or snap.get("ping_output")
                return snap
        return self.probe_host(host)

    def detect_local_cidr(self) -> str | None:
        return detect_local_cidr()

    def detect_all_local_cidrs(self) -> list[str]:
        return detect_all_local_cidrs()

    def diagnose(self, kind: str, target: str, **options: Any) -> dict[str, Any]:
        """Run a single named diagnostic (ping / traceroute / dns / ports) for
        the Diagnose tab in the UI."""
        kind = (kind or "").strip().lower()
        if kind == "ping":
            return run_ping_diagnostic(target, count=options.get("count", 4), timeout_ms=options.get("timeout_ms", 1000))
        if kind in ("tracert", "traceroute"):
            return run_traceroute_diagnostic(target, max_hops=options.get("max_hops", 30))
        if kind in ("dns", "nslookup"):
            return run_dns_lookup(target)
        if kind in ("ports", "portcheck", "port_check"):
            return run_port_check(target, ports=options.get("ports"))
        return {"target": target, "success": False, "output": f"Unknown diagnostic type: {kind}"}

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

    def _safe_probe(self, host: str | dict[str, Any]) -> dict[str, Any]:
        target_host = self._device_host(host) if isinstance(host, dict) else str(host)
        try:
            if isinstance(host, dict):
                return self.probe_device(host)
            return self.probe_host(host)
        except Exception as exc:
            # Never let one bad device (SNMP/SSH/REST crash, DNS error, etc.)
            # take down the whole scan - every other host in this cycle must
            # still get probed and reported.
            return {
                "host": target_host,
                "reachable": False,
                "latency_ms": None,
                "ping_output": "probe error",
                "probe_error": str(exc),
                "alerts": [],
            }

    def scan(self, cidr: str | None, manual_targets: Iterable[str | dict[str, Any]] | None = None) -> list[dict[str, Any]]:
        targets = list(self.discover_targets(cidr, manual_targets))
        if not targets:
            return []
        results: list[dict[str, Any]] = [None] * len(targets)  # type: ignore[list-item]
        # Raised from 32 -> 64. With the fast-fail path above, most devices
        # (especially offline ones) finish in ~1-2s, so a larger pool lets
        # bigger device lists complete a full cycle sooner without waiting
        # in batches behind a small worker count.
        max_workers = min(64, max(1, len(targets)))
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(self._safe_probe, host): idx for idx, host in enumerate(targets)}
            for future in as_completed(futures):
                idx = futures[future]
                try:
                    results[idx] = future.result()
                except Exception as exc:
                    host = targets[idx]
                    target_host = self._device_host(host) if isinstance(host, dict) else str(host)
                    results[idx] = {
                        "host": target_host,
                        "reachable": False,
                        "latency_ms": None,
                        "ping_output": "probe error",
                        "probe_error": str(exc),
                        "alerts": [],
                    }
        return results
