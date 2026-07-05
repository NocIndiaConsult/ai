from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class AgentSettings:
    server_url: str
    company_id: int
    name: str
    agent_id: int | None = None
    agent_key: str | None = None
    model_version: str = "1"
    offline_mode: bool = True
    site_name: str | None = None
    poll_enabled: bool = True
    discovery_enabled: bool = True
    discovery_cidr: str | None = None
    local_targets: list[str] = field(default_factory=list)
    local_devices: list[dict[str, Any]] = field(default_factory=list)
    poll_interval_seconds: int = 20
    discovery_interval_seconds: int = 60
    max_discovery_hosts: int = 32
    common_ports: list[int] = field(default_factory=lambda: [22, 23, 80, 443, 161, 8291, 8080])


class LocalCache:
    def __init__(self, root: Path | None = None) -> None:
        base = root or (Path.home() / ".idea-agent")
        self.root = base
        self.root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "agent.db"
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS outbound_queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    command_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'queued',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS incoming_commands (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    server_command_id INTEGER,
                    command_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS model_cache (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    model_version TEXT NOT NULL,
                    bundle_json TEXT NOT NULL,
                    fetched_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )

    def save_setting(self, key: str, value: Any) -> None:
        text = json.dumps(value)
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO settings(key, value) VALUES (?, ?)",
                (key, text),
            )

    def get_setting(self, key: str, default: Any | None = None) -> Any:
        with self._connect() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        if not row:
            return default
        try:
            return json.loads(row["value"])
        except Exception:
            return default

    def save_agent_profile(self, settings: AgentSettings) -> None:
        self.save_setting("profile", settings.__dict__)

    def load_agent_profile(self) -> AgentSettings | None:
        raw = self.get_setting("profile")
        if not raw:
            return None
        return AgentSettings(**raw)

    def enqueue_command(self, command_type: str, payload: dict[str, Any]) -> int:
        now = _utc_now()
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO outbound_queue(command_type, payload_json, status, created_at, updated_at)
                VALUES (?, ?, 'queued', ?, ?)
                """,
                (command_type, json.dumps(payload), now, now),
            )
            return int(cur.lastrowid)

    def enqueue_incoming_command(self, server_command_id: int | None, command_type: str, payload: dict[str, Any]) -> int:
        now = _utc_now()
        with self._connect() as conn:
            if server_command_id is not None:
                existing = conn.execute(
                    "SELECT id FROM incoming_commands WHERE server_command_id = ? AND status = 'pending' LIMIT 1",
                    (int(server_command_id),),
                ).fetchone()
                if existing:
                    return int(existing["id"])
            cur = conn.execute(
                """
                INSERT INTO incoming_commands(server_command_id, command_type, payload_json, status, created_at, updated_at)
                VALUES (?, ?, ?, 'pending', ?, ?)
                """,
                (server_command_id, command_type, json.dumps(payload), now, now),
            )
            return int(cur.lastrowid)

    def incoming_commands(self, status: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT id, server_command_id, command_type, payload_json, status, created_at, updated_at FROM incoming_commands"
        params: tuple[Any, ...] = ()
        if status:
            sql += " WHERE status = ?"
            params = (status,)
        sql += " ORDER BY id DESC"
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [
            {
                "id": row["id"],
                "server_command_id": row["server_command_id"],
                "command_type": row["command_type"],
                "payload": json.loads(row["payload_json"]),
                "status": row["status"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

    def update_incoming_command_status(self, command_id: int, status: str, result: dict[str, Any] | None = None) -> None:
        now = _utc_now()
        with self._connect() as conn:
            conn.execute(
                "UPDATE incoming_commands SET status = ?, updated_at = ? WHERE id = ?",
                (status, now, command_id),
            )
            if result is not None:
                conn.execute(
                    "INSERT INTO events(kind, payload_json, created_at) VALUES (?, ?, ?)",
                    ("incoming_command_result", json.dumps({"command_id": command_id, "result": result}), now),
                )

    def pending_commands(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, command_type, payload_json, status, created_at, updated_at FROM outbound_queue WHERE status != 'acked' ORDER BY id ASC"
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            result.append(
                {
                    "id": row["id"],
                    "command_type": row["command_type"],
                    "payload": json.loads(row["payload_json"]),
                    "status": row["status"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                }
            )
        return result

    def pending_incoming_commands(self) -> list[dict[str, Any]]:
        return self.incoming_commands("pending")

    def mark_command_status(self, queue_id: int, status: str, result: dict[str, Any] | None = None) -> None:
        now = _utc_now()
        with self._connect() as conn:
            conn.execute(
                "UPDATE outbound_queue SET status = ?, updated_at = ? WHERE id = ?",
                (status, now, queue_id),
            )
            if result is not None:
                conn.execute(
                    """
                    INSERT INTO events(kind, payload_json, created_at)
                    VALUES (?, ?, ?)
                    """,
                    ("command_result", json.dumps({"queue_id": queue_id, "result": result}), now),
                )

    def save_model_bundle(self, model_version: str, bundle: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO model_cache(model_version, bundle_json, fetched_at)
                VALUES (?, ?, ?)
                """,
                (model_version, json.dumps(bundle), _utc_now()),
            )
            conn.execute(
                "INSERT OR REPLACE INTO settings(key, value) VALUES (?, ?)",
                ("model_version", json.dumps(model_version)),
            )

    def latest_model_bundle(self) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT model_version, bundle_json, fetched_at FROM model_cache ORDER BY id DESC LIMIT 1"
            ).fetchone()
        if not row:
            return None
        return {
            "model_version": row["model_version"],
            "bundle": json.loads(row["bundle_json"]),
            "fetched_at": row["fetched_at"],
        }

    def add_event(self, kind: str, payload: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO events(kind, payload_json, created_at) VALUES (?, ?, ?)",
                (kind, json.dumps(payload), _utc_now()),
            )

    def recent_events(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT kind, payload_json, created_at FROM events ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {"kind": row["kind"], "payload": json.loads(row["payload_json"]), "created_at": row["created_at"]}
            for row in rows
        ]

    def set_last_error(self, error: str) -> None:
        self.save_setting("last_error", {"error": error, "at": _utc_now()})

    def get_last_error(self) -> dict[str, Any] | None:
        return self.get_setting("last_error")

    def save_local_targets(self, targets: list[str]) -> None:
        cleaned = []
        for target in targets:
            value = str(target).strip()
            if value and value not in cleaned:
                cleaned.append(value)
        self.save_setting("local_targets", cleaned)

    def load_local_targets(self) -> list[str]:
        raw = self.get_setting("local_targets", [])
        if isinstance(raw, list):
            cleaned: list[str] = []
            for item in raw:
                if isinstance(item, dict):
                    host = str(item.get("host") or item.get("mgmt_ip") or "").strip()
                    if host and host not in cleaned:
                        cleaned.append(host)
                else:
                    value = str(item).strip()
                    if value and value not in cleaned:
                        cleaned.append(value)
            return cleaned
        return []

    def load_local_devices(self) -> list[dict[str, Any]]:
        raw = self.get_setting("local_devices", [])
        if not isinstance(raw, list):
            return []
        devices: list[dict[str, Any]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            host = str(item.get("host") or item.get("mgmt_ip") or "").strip()
            if not host:
                continue
            devices.append(
                {
                    "host": host,
                    "mgmt_ip": host,
                    "name": str(item.get("name") or host).strip(),
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
            )
        return devices

    def save_local_devices(self, devices: list[dict[str, Any]]) -> None:
        cleaned: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in devices:
            if not isinstance(item, dict):
                continue
            host = str(item.get("host") or item.get("mgmt_ip") or "").strip()
            if not host or host in seen:
                continue
            seen.add(host)
            cleaned.append(
                {
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
            )
        self.save_setting("local_devices", cleaned)

    def add_local_target(self, target: str) -> list[str]:
        targets = self.load_local_targets()
        value = str(target).strip()
        if value and value not in targets:
            targets.append(value)
        self.save_local_targets(targets)
        self.add_event("device_target_added", {"target": value})
        return targets

    def add_local_device(self, device: dict[str, Any]) -> list[dict[str, Any]]:
        devices = self.load_local_devices()
        host = str(device.get("host") or device.get("mgmt_ip") or "").strip()
        if not host:
            return devices
        merged = []
        replaced = False
        for item in devices:
            if str(item.get("host") or item.get("mgmt_ip") or "").strip() == host:
                merged.append({**item, **device, "host": host, "mgmt_ip": host})
                replaced = True
            else:
                merged.append(item)
        if not replaced:
            merged.append({**device, "host": host, "mgmt_ip": host})
        self.save_local_devices(merged)
        self.save_local_targets([item["host"] for item in merged if item.get("host")])
        self.add_event("device_target_added", {"target": host, "device": device})
        return merged

    def remove_local_target(self, target: str) -> list[str]:
        targets = [item for item in self.load_local_targets() if item != str(target).strip()]
        self.save_local_targets(targets)
        self.add_event("device_target_removed", {"target": str(target).strip()})
        return targets

    def remove_local_device(self, target: str) -> list[dict[str, Any]]:
        host = str(target).strip()
        devices = [item for item in self.load_local_devices() if str(item.get("host") or item.get("mgmt_ip") or "").strip() != host]
        self.save_local_devices(devices)
        self.save_local_targets([item["host"] for item in devices if item.get("host")])
        self.add_event("device_target_removed", {"target": host})
        return devices

    def save_action_history(self, action: dict[str, Any]) -> None:
        self.add_event("action_history", action)

    def action_history(self, limit: int = 50) -> list[dict[str, Any]]:
        return [
            item
            for item in self.recent_events(limit)
            if item.get("kind") in {"action_history", "command_result", "local_poll", "local_alert", "sync", "heartbeat"}
        ]
