from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
import os
import sys
from dataclasses import asdict
from typing import Any

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

if __package__:
    from .cache import AgentSettings, LocalCache
else:  # pragma: no cover
    from cache import AgentSettings, LocalCache


class ServerClient:
    def __init__(self, cache: LocalCache, timeout: int = 20) -> None:
        self.cache = cache
        self.timeout = timeout

    def _headers(self, settings: AgentSettings) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        headers["X-Company-ID"] = str(int(getattr(settings, "company_id", 1) or 1))
        if settings.agent_key:
            headers["X-Agent-Key"] = settings.agent_key
        return headers

    def _request(self, method: str, url: str, payload: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> dict[str, Any]:
        body = None
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=body, method=method.upper())
        for key, value in (headers or {}).items():
            req.add_header(key, value)
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}

    def register(self, settings: AgentSettings) -> AgentSettings:
        url = f"{settings.server_url.rstrip('/')}/api/agent/register"
        payload = {"company_id": settings.company_id, "name": settings.name}
        data = self._request("POST", url, payload)
        settings.agent_id = int(data["agent_id"])
        settings.agent_key = data["agent_key"]
        settings.model_version = str(data.get("model_version", settings.model_version))
        settings.offline_mode = bool(data.get("offline_mode", settings.offline_mode))
        self.cache.save_agent_profile(settings)
        return settings

    def heartbeat(
        self,
        settings: AgentSettings,
        snapshot: dict[str, Any],
        online: bool,
        model_version: str,
        metrics: dict[str, Any] | None = None,
        inventory: list[dict[str, Any]] | None = None,
        logs: list[str] | None = None,
        alerts: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any] | None:
        if not settings.agent_id:
            return None
        url = f"{settings.server_url.rstrip('/')}/api/agent/heartbeat"
        payload = {
            "agent_id": settings.agent_id,
            "status": "online" if online else "offline",
            "model_version": model_version,
            "snapshot": snapshot,
            "metrics": metrics or {},
            "inventory": inventory or [],
            "logs": logs or [],
            "alerts": alerts or [],
            "company_id": settings.company_id,
            "agent_key": settings.agent_key,
        }
        return self._request("POST", url, payload, self._headers(settings))

    def sync(self, settings: AgentSettings) -> dict[str, Any]:
        if not settings.agent_id:
            raise ValueError("agent_id missing")
        url = f"{settings.server_url.rstrip('/')}/api/agent/{settings.agent_id}/sync"
        return self._request("GET", url, headers=self._headers(settings))

    def ack_command(self, settings: AgentSettings, queue_id: int, status: str, result: dict[str, Any] | None = None) -> dict[str, Any] | None:
        if not settings.agent_id:
            return None
        url = f"{settings.server_url.rstrip('/')}/api/agent/ack"
        payload = {
            "agent_id": settings.agent_id,
            "queue_id": queue_id,
            "status": status,
            "result": result or {},
        }
        return self._request("POST", url, payload, self._headers(settings))

    def fetch_and_apply_sync(self, settings: AgentSettings) -> dict[str, Any]:
        sync = self.sync(settings)
        model_bundle = sync.get("model_bundle")
        if isinstance(model_bundle, dict):
            version = str(model_bundle.get("version", settings.model_version))
            self.cache.save_model_bundle(version, model_bundle)
            settings.model_version = version
            self.cache.save_agent_profile(settings)
        workspace = sync.get("workspace")
        restored_targets: list[str] = []
        if isinstance(workspace, dict):
            local_targets = workspace.get("local_targets")
            if isinstance(local_targets, list):
                restored_targets.extend(str(item).strip() for item in local_targets if str(item).strip())
            settings.discovery_cidr = str(workspace.get("settings", {}).get("discovery_cidr") or settings.discovery_cidr or "")
        devices = sync.get("devices")
        if isinstance(devices, list):
            restored_targets.extend(
                str(item.get("mgmt_ip") or "").strip()
                for item in devices
                if isinstance(item, dict) and str(item.get("mgmt_ip") or "").strip()
            )
        deduped: list[str] = []
        seen: set[str] = set()
        for item in restored_targets:
            if item and item not in seen:
                seen.add(item)
                deduped.append(item)
        if deduped:
            settings.local_targets = deduped
            self.cache.save_local_targets(settings.local_targets)
            self.cache.save_agent_profile(settings)
        return sync

    def fetch_workspace(self, settings: AgentSettings) -> dict[str, Any]:
        if not settings.agent_id:
            raise ValueError("agent_id missing")
        url = f"{settings.server_url.rstrip('/')}/api/agent/workspace"
        return self._request("GET", url, headers=self._headers(settings))

    def save_workspace(self, settings: AgentSettings, payload: dict[str, Any]) -> dict[str, Any]:
        if not settings.agent_id:
            raise ValueError("agent_id missing")
        url = f"{settings.server_url.rstrip('/')}/api/agent/workspace"
        return self._request("POST", url, payload, self._headers(settings))

    def create_device(self, settings: AgentSettings, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{settings.server_url.rstrip('/')}/api/agent/device"
        headers = self._headers(settings)
        return self._request("POST", url, payload, headers)

    def delete_device(self, settings: AgentSettings, device_id: int | str) -> dict[str, Any]:
        url = f"{settings.server_url.rstrip('/')}/api/agent/device"
        headers = self._headers(settings)
        payload = {"mgmt_ip": str(device_id).strip()}
        return self._request("DELETE", url, payload, headers)

    def list_devices(self, settings: AgentSettings) -> dict[str, Any]:
        url = f"{settings.server_url.rstrip('/')}/api/devices"
        headers = self._headers(settings)
        headers["X-Company-ID"] = str(settings.company_id)
        return self._request("GET", url, headers=headers)

    def list_alerts(self, settings: AgentSettings) -> dict[str, Any]:
        url = f"{settings.server_url.rstrip('/')}/api/alerts"
        headers = self._headers(settings)
        headers["X-Company-ID"] = str(settings.company_id)
        return self._request("GET", url, headers=headers)

    def try_request(self, method: str, url: str, payload: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> tuple[bool, dict[str, Any] | str]:
        try:
            return True, self._request(method, url, payload, headers)
        except urllib.error.HTTPError as exc:
            try:
                return False, json.loads(exc.read().decode("utf-8"))
            except Exception:
                return False, f"HTTP {exc.code}"
        except Exception as exc:
            return False, str(exc)

    def sleep_backoff(self, seconds: float) -> None:
        time.sleep(seconds)
