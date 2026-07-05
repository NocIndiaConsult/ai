from __future__ import annotations

import tkinter as tk
import os
import sys
from datetime import datetime
from tkinter import ttk
from typing import Any

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

if __package__:
    from .cache import AgentSettings, LocalCache
    from .client import ServerClient
else:  # pragma: no cover
    from cache import AgentSettings, LocalCache
    from client import ServerClient


def _short(text: Any, limit: int = 96) -> str:
    value = str(text or "").strip()
    if len(value) <= limit:
        return value
    return value[: limit - 1] + "..."


def _fmt_time(value: str | None) -> str:
    if not value:
        return "-"
    return str(value).replace("T", " ")[:19]


class AgentUI(tk.Tk):
    def __init__(self, agent: Any) -> None:
        super().__init__()
        self.agent = agent
        self.cache: LocalCache = agent.cache
        self.client: ServerClient = agent.client
        self.settings: AgentSettings = agent.settings

        self.title("Idea Agent")
        self.geometry("1380x860")
        self.minsize(1180, 760)
        self.configure(bg="#07101d")

        self._setup_style()
        self._build_shell()
        self._refresh_ui()
        self.after(1200, self._tick)

    def _setup_style(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("TNotebook", background="#07101d", borderwidth=0)
        style.configure("TNotebook.Tab", background="#0f1a2d", foreground="#b9c7e6", padding=(18, 10), borderwidth=0)
        style.map("TNotebook.Tab", background=[("selected", "#1c2e50")], foreground=[("selected", "#ffffff")])
        style.configure("Card.TFrame", background="#0f1a2d", relief="flat")
        style.configure("Section.TFrame", background="#0a1322")
        style.configure("TLabel", background="#07101d", foreground="#d9e4fb", font=("Segoe UI", 10))
        style.configure("Title.TLabel", background="#07101d", foreground="#ffffff", font=("Segoe UI Semibold", 22))
        style.configure("SubTitle.TLabel", background="#07101d", foreground="#8ea1c7", font=("Segoe UI", 10))
        style.configure("CardTitle.TLabel", background="#0f1a2d", foreground="#9fb4d9", font=("Segoe UI Semibold", 10))
        style.configure("CardValue.TLabel", background="#0f1a2d", foreground="#ffffff", font=("Segoe UI Semibold", 24))
        style.configure("CardSmall.TLabel", background="#0f1a2d", foreground="#8ea1c7", font=("Segoe UI", 9))
        style.configure("TButton", padding=(14, 9), background="#345dff", foreground="#ffffff", borderwidth=0)
        style.map("TButton", background=[("active", "#4c73ff")], relief=[("pressed", "flat"), ("active", "flat")])
        style.configure("Treeview", background="#0a1322", fieldbackground="#0a1322", foreground="#e8f0ff", rowheight=30, borderwidth=0)
        style.configure("Treeview.Heading", background="#12223c", foreground="#ffffff", padding=10, relief="flat")
        style.map("Treeview", background=[("selected", "#214172")], foreground=[("selected", "#ffffff")])

    def _build_shell(self) -> None:
        top = tk.Frame(self, bg="#07101d")
        top.pack(fill="x", padx=24, pady=(20, 14))
        tk.Label(top, text="IDEA AGENT", bg="#07101d", fg="#ffffff", font=("Segoe UI Semibold", 26)).pack(anchor="w")
        tk.Label(
            top,
            text="Local edge agent for customer networks • sync, alerts, AI team, safe command queue",
            bg="#08111f",
            fg="#93a8c8",
            font=("Segoe UI", 10),
        ).pack(anchor="w", pady=(4, 0))

        summary = tk.Frame(self, bg="#07101d")
        summary.pack(fill="x", padx=24, pady=(4, 16))
        self.status_var = tk.StringVar(value="Disconnected")
        self.sync_var = tk.StringVar(value="Last sync: -")
        self.queue_var = tk.StringVar(value="Queued commands: 0")
        self.model_var = tk.StringVar(value="Model: -")
        self.alerts_var = tk.StringVar(value="Critical alerts: 0")
        self.inventory_var = tk.StringVar(value="Local hosts: 0")

        for idx, (title, var) in enumerate(
            [
                ("Connection", self.status_var),
                ("Sync", self.sync_var),
                ("Queue", self.queue_var),
                ("Model", self.model_var),
                ("Alerts", self.alerts_var),
                ("Targets", self.inventory_var),
            ]
        ):
            card = tk.Frame(summary, bg="#0f1a2d", bd=0, highlightthickness=1, highlightbackground="#20304d")
            card.grid(row=0, column=idx, sticky="nsew", padx=(0 if idx == 0 else 12, 0), ipadx=12, ipady=14)
            summary.grid_columnconfigure(idx, weight=1)
            tk.Label(card, text=title.upper(), bg="#0f1a2d", fg="#91a5cb", font=("Segoe UI", 8, "bold")).pack(anchor="w")
            tk.Label(card, textvariable=var, bg="#0f1a2d", fg="#ffffff", font=("Segoe UI Semibold", 17)).pack(anchor="w", pady=(6, 0))

        body = tk.Frame(self, bg="#07101d")
        body.pack(fill="both", expand=True, padx=24, pady=(0, 20))

        left = tk.Frame(body, bg="#07101d", width=250)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)
        right = tk.Frame(body, bg="#07101d")
        right.pack(side="right", fill="both", expand=True, padx=(18, 0))

        actions = tk.Frame(left, bg="#0f1a2d", bd=0, highlightthickness=1, highlightbackground="#20304d")
        actions.pack(fill="x", pady=(0, 14))
        tk.Label(actions, text="QUICK ACTIONS", bg="#0f1a2d", fg="#ffffff", font=("Segoe UI Semibold", 14)).pack(anchor="w", padx=14, pady=(12, 6))
        ttk.Button(actions, text="Sync Now", command=self.manual_sync).pack(fill="x", padx=14, pady=6)
        ttk.Button(actions, text="Heartbeat", command=self.manual_heartbeat).pack(fill="x", padx=14, pady=6)
        ttk.Button(actions, text="Run Local Poll", command=self.run_local_poll).pack(fill="x", padx=14, pady=6)
        ttk.Button(actions, text="Open Cache", command=self.open_cache_summary).pack(fill="x", padx=14, pady=(6, 14))

        nav = tk.Frame(left, bg="#0f1a2d", bd=0, highlightthickness=1, highlightbackground="#20304d")
        nav.pack(fill="x", pady=(0, 14))
        tk.Label(nav, text="SECTIONS", bg="#0f1a2d", fg="#ffffff", font=("Segoe UI Semibold", 14)).pack(anchor="w", padx=14, pady=(12, 6))

        notebook = ttk.Notebook(right)
        notebook.pack(fill="both", expand=True)

        self.dashboard_tab = tk.Frame(notebook, bg="#08111f")
        self.devices_tab = tk.Frame(notebook, bg="#08111f")
        self.alerts_tab = tk.Frame(notebook, bg="#08111f")
        self.ai_tab = tk.Frame(notebook, bg="#08111f")
        self.onprem_tab = tk.Frame(notebook, bg="#08111f")
        self.settings_tab = tk.Frame(notebook, bg="#08111f")
        notebook.add(self.dashboard_tab, text="Dashboard")
        notebook.add(self.devices_tab, text="Devices")
        notebook.add(self.alerts_tab, text="Alerts")
        notebook.add(self.ai_tab, text="AI Team")
        notebook.add(self.onprem_tab, text="On-Prem")
        notebook.add(self.settings_tab, text="Settings")
        self._notebook = notebook

        for idx, label in enumerate(["Dashboard", "Devices", "Alerts", "AI Team", "On-Prem", "Settings"]):
            btn = tk.Button(
                nav,
                text=label,
                anchor="w",
                bg="#111c2f",
                fg="#dbe7ff",
                activebackground="#1b2c49",
                activeforeground="#ffffff",
                relief="flat",
                bd=0,
                padx=12,
                pady=10,
                command=lambda i=idx: notebook.select(i),
            )
            btn.pack(fill="x", padx=14, pady=5)

        self._build_dashboard()
        self._build_devices()
        self._build_alerts()
        self._build_ai_team()
        self._build_onprem()
        self._build_settings()

    def _current_incoming(self) -> list[dict[str, Any]]:
        return self.cache.pending_incoming_commands()

    def _section(self, parent: tk.Widget, title: str, subtitle: str | None = None) -> tk.Frame:
        frame = tk.Frame(parent, bg="#0f1a2d", bd=0, highlightthickness=1, highlightbackground="#20304d")
        header = tk.Frame(frame, bg="#0f1a2d")
        header.pack(fill="x", padx=14, pady=(12, 4))
        tk.Label(header, text=title, bg="#0f1a2d", fg="#ffffff", font=("Segoe UI Semibold", 14)).pack(anchor="w")
        if subtitle:
            tk.Label(header, text=subtitle, bg="#0f1a2d", fg="#8ea6c9", font=("Segoe UI", 9)).pack(anchor="w", pady=(2, 0))
        return frame

    def _build_dashboard(self) -> None:
        top = self._section(self.dashboard_tab, "Overview", "Agent runtime and network sync")
        top.pack(fill="x", padx=14, pady=14)
        self.dashboard_text = tk.Text(top, height=12, bg="#0a1322", fg="#dbe7ff", insertbackground="#ffffff", relief="flat", wrap="word", font=("Consolas", 10))
        self.dashboard_text.pack(fill="both", expand=True, padx=14, pady=(0, 14))

    def _build_devices(self) -> None:
        top = self._section(self.devices_tab, "Devices", "Local targets managed by this agent")
        top.pack(fill="both", expand=True, padx=14, pady=14)
        controls = tk.Frame(top, bg="#0f1a2d")
        controls.pack(fill="x", padx=14, pady=(0, 10))
        self.device_target_var = tk.StringVar(value="")
        tk.Entry(controls, textvariable=self.device_target_var, bg="#0a1322", fg="#ffffff", insertbackground="#ffffff", relief="flat", width=40).pack(side="left", padx=(0, 8))
        ttk.Button(controls, text="Add Target", command=self.add_target).pack(side="left", padx=4)
        ttk.Button(controls, text="Remove Target", command=self.remove_target).pack(side="left", padx=4)
        ttk.Button(controls, text="Refresh", command=self._refresh_ui).pack(side="left", padx=4)
        ttk.Button(controls, text="Run Local Poll", command=self.run_local_poll).pack(side="left", padx=4)
        cols = ("target", "state", "latency", "open_ports", "last_seen")
        self.device_tree = ttk.Treeview(top, columns=cols, show="headings", height=12)
        for col, width in [("target", 220), ("state", 100), ("latency", 110), ("open_ports", 340), ("last_seen", 180)]:
            self.device_tree.heading(col, text=col.replace("_", " ").title())
            self.device_tree.column(col, width=width, anchor="w")
        self.device_tree.pack(fill="both", expand=True, padx=14, pady=(0, 14))
        pending = self._section(top, "Pending Approvals", "Server commands waiting for local approval")
        pending.pack(fill="both", expand=True, padx=14, pady=(0, 14))
        pending_cols = ("command_id", "type", "target", "status", "created_at")
        self.pending_tree = ttk.Treeview(pending, columns=pending_cols, show="headings", height=8)
        for col, width in [("command_id", 100), ("type", 180), ("target", 220), ("status", 120), ("created_at", 180)]:
            self.pending_tree.heading(col, text=col.replace("_", " ").title())
            self.pending_tree.column(col, width=width, anchor="w")
        self.pending_tree.pack(fill="both", expand=True, padx=14, pady=(0, 14))
        pending_actions = tk.Frame(pending, bg="#0f1a2d")
        pending_actions.pack(fill="x", padx=14, pady=(0, 14))
        ttk.Button(pending_actions, text="Approve Selected", command=self.approve_selected_command).pack(side="left", padx=4)
        ttk.Button(pending_actions, text="Reject Selected", command=self.reject_selected_command).pack(side="left", padx=4)
        hist = self._section(top, "Action History", "Local changes and approvals on this agent")
        hist.pack(fill="both", expand=True, padx=14, pady=(0, 14))
        hist_cols = ("time", "command_id", "action", "target", "status")
        self.history_tree = ttk.Treeview(hist, columns=hist_cols, show="headings", height=8)
        for col, width in [("time", 170), ("command_id", 100), ("action", 170), ("target", 220), ("status", 120)]:
            self.history_tree.heading(col, text=col.title())
            self.history_tree.column(col, width=width, anchor="w")
        self.history_tree.pack(fill="both", expand=True, padx=14, pady=(0, 14))
        approvals = tk.Frame(hist, bg="#0f1a2d")
        approvals.pack(fill="x", padx=14, pady=(0, 14))
        ttk.Button(approvals, text="Approve Selected", command=self.approve_selected_command).pack(side="left", padx=4)
        ttk.Button(approvals, text="Reject Selected", command=self.reject_selected_command).pack(side="left", padx=4)

    def _build_alerts(self) -> None:
        top = self._section(self.alerts_tab, "Alerts", "Open issues and queued actions from the server")
        top.pack(fill="both", expand=True, padx=14, pady=14)
        cols = ("time", "severity", "issue", "source", "summary")
        self.alert_tree = ttk.Treeview(top, columns=cols, show="headings", height=12)
        for col, width in [("time", 160), ("severity", 90), ("issue", 140), ("source", 160), ("summary", 520)]:
            self.alert_tree.heading(col, text=col.title())
            self.alert_tree.column(col, width=width, anchor="w")
        self.alert_tree.pack(fill="both", expand=True, padx=14, pady=(0, 14))

    def _build_ai_team(self) -> None:
        container = tk.Frame(self.ai_tab, bg="#07101d")
        container.pack(fill="both", expand=True, padx=14, pady=14)
        self.agent_cards: list[tk.Frame] = []
        agents = [
            ("Monitoring Agent", "tracks anomalies and live signals"),
            ("Diagnosis Agent", "roots faults from telemetry"),
            ("Planning Agent", "builds safe remediation plan"),
            ("Verification Agent", "checks policy and risk gate"),
            ("Execution Agent", "applies approved actions"),
        ]
        for idx, (name, desc) in enumerate(agents):
            card = tk.Frame(container, bg="#0f1a2d", bd=0, highlightthickness=1, highlightbackground="#20304d")
            card.grid(row=idx // 2, column=idx % 2, sticky="nsew", padx=8, pady=8)
            container.grid_rowconfigure(idx // 2, weight=1)
            container.grid_columnconfigure(idx % 2, weight=1)
            tk.Label(card, text=name, bg="#0f1a2d", fg="#ffffff", font=("Segoe UI Semibold", 14)).pack(anchor="w", padx=14, pady=(12, 2))
            tk.Label(card, text=desc, bg="#0f1a2d", fg="#8ea6c9", font=("Segoe UI", 9)).pack(anchor="w", padx=14)
            status = tk.StringVar(value="idle")
            load = tk.StringVar(value="Load 0%")
            pulse = tk.StringVar(value="Waiting for sync")
            tk.Label(card, textvariable=status, bg="#0f1a2d", fg="#78d9a2", font=("Segoe UI Semibold", 12)).pack(anchor="w", padx=14, pady=(10, 0))
            tk.Label(card, textvariable=load, bg="#0f1a2d", fg="#dbe7ff", font=("Segoe UI", 10)).pack(anchor="w", padx=14, pady=(2, 0))
            tk.Label(card, textvariable=pulse, bg="#0f1a2d", fg="#9fb4d9", font=("Segoe UI", 9), wraplength=360, justify="left").pack(anchor="w", padx=14, pady=(2, 14))
            self.agent_cards.append(card)
            card._agent_status = status  # type: ignore[attr-defined]
            card._agent_load = load  # type: ignore[attr-defined]
            card._agent_pulse = pulse  # type: ignore[attr-defined]

    def _build_onprem(self) -> None:
        top = self._section(self.onprem_tab, "On-Prem Polling", "Local discovery and host probing from this agent PC")
        top.pack(fill="both", expand=True, padx=14, pady=14)
        self.local_scan_var = tk.StringVar(value="Idle")
        self.local_targets_count_var = tk.StringVar(value="Targets: 0")
        self.local_online_count_var = tk.StringVar(value="Online: 0")
        header = tk.Frame(top, bg="#0f1a2d")
        header.pack(fill="x", padx=14, pady=(0, 10))
        for idx, (label, var) in enumerate(
            [
                ("Scan Status", self.local_scan_var),
                ("Targets", self.local_targets_count_var),
                ("Online", self.local_online_count_var),
            ]
        ):
            box = tk.Frame(header, bg="#0a1322", bd=0, highlightthickness=1, highlightbackground="#20304d")
            box.grid(row=0, column=idx, sticky="nsew", padx=6, pady=6, ipadx=10, ipady=10)
            header.grid_columnconfigure(idx, weight=1)
            tk.Label(box, text=label, bg="#0a1322", fg="#9fb4d9", font=("Segoe UI", 9, "bold")).pack(anchor="w")
            tk.Label(box, textvariable=var, bg="#0a1322", fg="#ffffff", font=("Segoe UI Semibold", 16)).pack(anchor="w", pady=(4, 0))
        ttk.Button(top, text="Run Local Poll Now", command=self.run_local_poll).pack(anchor="w", padx=14, pady=(0, 10))
        cols = ("host", "reachability", "latency", "open_ports", "status")
        self.local_tree = ttk.Treeview(top, columns=cols, show="headings", height=12)
        for col, width in [("host", 220), ("reachability", 100), ("latency", 110), ("open_ports", 240), ("status", 120)]:
            self.local_tree.heading(col, text=col.replace("_", " ").title())
            self.local_tree.column(col, width=width, anchor="w")
        self.local_tree.pack(fill="both", expand=True, padx=14, pady=(0, 14))

    def _build_settings(self) -> None:
        frame = self._section(self.settings_tab, "Settings", "Local agent registration and sync endpoint")
        frame.pack(fill="x", padx=14, pady=14)
        form = tk.Frame(frame, bg="#0f1a2d")
        form.pack(fill="x", padx=14, pady=(0, 14))
        self.server_var = tk.StringVar(value=self.settings.server_url)
        self.company_var = tk.StringVar(value=str(self.settings.company_id))
        self.name_var = tk.StringVar(value=self.settings.name)
        self.agent_id_var = tk.StringVar(value=str(self.settings.agent_id or ""))
        self.agent_key_var = tk.StringVar(value=self.settings.agent_key or "")
        self.model_version_var = tk.StringVar(value=self.settings.model_version)
        self.site_name_var = tk.StringVar(value=self.settings.site_name or "")
        self.discovery_cidr_var = tk.StringVar(value=self.settings.discovery_cidr or "")
        self.local_targets_var = tk.StringVar(value=", ".join(self.settings.local_targets or []))
        self.poll_enabled_var = tk.BooleanVar(value=bool(self.settings.poll_enabled))
        self.discovery_enabled_var = tk.BooleanVar(value=bool(self.settings.discovery_enabled))
        self.error_string_var = tk.StringVar(value="-")
        rows = [
            ("Server URL", self.server_var),
            ("Company ID", self.company_var),
            ("Agent Name", self.name_var),
            ("Agent ID", self.agent_id_var),
            ("Agent Key", self.agent_key_var),
            ("Model Version", self.model_version_var),
            ("Site Name", self.site_name_var),
            ("Discovery CIDR", self.discovery_cidr_var),
            ("Local Targets", self.local_targets_var),
        ]
        for idx, (label, var) in enumerate(rows):
            tk.Label(form, text=label, bg="#0f1a2d", fg="#9fb4d9", font=("Segoe UI", 10)).grid(row=idx, column=0, sticky="w", pady=5, padx=(0, 12))
            tk.Entry(form, textvariable=var, bg="#0a1322", fg="#ffffff", insertbackground="#ffffff", relief="flat", width=52).grid(row=idx, column=1, sticky="ew", pady=5)
        chk_row = len(rows)
        tk.Checkbutton(form, text="Enable local polling", variable=self.poll_enabled_var, bg="#0f1a2d", fg="#dbe7ff", activebackground="#0f1a2d", activeforeground="#ffffff", selectcolor="#0a1322").grid(row=chk_row, column=1, sticky="w", pady=(10, 2))
        tk.Checkbutton(form, text="Enable discovery scans", variable=self.discovery_enabled_var, bg="#0f1a2d", fg="#dbe7ff", activebackground="#0f1a2d", activeforeground="#ffffff", selectcolor="#0a1322").grid(row=chk_row + 1, column=1, sticky="w", pady=2)
        form.grid_columnconfigure(1, weight=1)
        btns = tk.Frame(frame, bg="#0f1a2d")
        btns.pack(fill="x", padx=14, pady=(0, 14))
        ttk.Button(btns, text="Save", command=self.save_settings).pack(side="left")
        ttk.Button(btns, text="Apply & Sync", command=self.manual_sync).pack(side="left", padx=8)
        ttk.Button(btns, text="Reconnect", command=self.reconnect).pack(side="left")
        tk.Label(frame, textvariable=self.error_string_var, bg="#0f1a2d", fg="#ff8a8a", font=("Segoe UI", 9)).pack(anchor="w", padx=14, pady=(0, 14))

    def _render_dashboard(self) -> None:
        self.dashboard_text.delete("1.0", "end")
        events = self.cache.recent_events(12)
        lines = [
            f"Server: {self.settings.server_url}",
            f"Agent: {self.settings.name} ({self.settings.agent_id or 'unregistered'})",
            f"Company ID: {self.settings.company_id}",
            f"Last sync: {self.agent.last_sync_at or '-'}",
            f"Last online: {self.agent.last_online_state}",
            f"Pending commands: {len(self.cache.pending_commands())}",
            f"Cached model: {self.settings.model_version}",
            f"Last error: {self.cache.get_last_error() or '-'}",
            f"Local poll hosts: {len(self.agent.last_local_inventory)}",
            "",
            "Recent events:",
        ]
        for item in events[:8]:
            payload = item.get("payload", {})
            lines.append(f"- {item.get('kind')} @ {_fmt_time(item.get('created_at'))} :: {_short(payload, 140)}")
        self.dashboard_text.insert("1.0", "\n".join(lines))

    def _render_alerts(self) -> None:
        for row in self.alert_tree.get_children():
            self.alert_tree.delete(row)
        for event in self.cache.recent_events(80):
            if event.get("kind") not in {"sync_error", "heartbeat_error", "command_result", "sync", "local_alert"}:
                continue
            payload = event.get("payload", {})
            self.alert_tree.insert(
                "",
                "end",
                values=(
                    _fmt_time(event.get("created_at")),
                    str(payload.get("severity") or payload.get("status") or "info"),
                    str(payload.get("kind") or event.get("kind")),
                    str(payload.get("source") or payload.get("server") or self.settings.name),
                    _short(payload, 120),
                ),
            )

    def _render_ai_team(self) -> None:
        events = self.cache.recent_events(30)
        for idx, card in enumerate(self.agent_cards):
            status = getattr(card, "_agent_status")
            load = getattr(card, "_agent_load")
            pulse = getattr(card, "_agent_pulse")
            last_event = events[idx] if idx < len(events) else {}
            kind = last_event.get("kind") or "idle"
            payload = last_event.get("payload", {})
            status.set("online" if self.agent.last_online_state else "offline")
            load.set(f"Load {min(95, 20 + idx * 12)}%")
            pulse.set(f"{kind}: {_short(payload, 96)}")

    def _render_onprem(self) -> None:
        results = list(self.agent.last_local_inventory or [])
        self.local_scan_var.set("Scanning" if results else "Idle")
        self.local_targets_count_var.set(f"Targets: {len(results)}")
        self.local_online_count_var.set(f"Online: {sum(1 for item in results if item.get('reachable'))}")
        for row in self.device_tree.get_children():
            self.device_tree.delete(row)
        for row in self.pending_tree.get_children():
            self.pending_tree.delete(row)
        for row in self.local_tree.get_children():
            self.local_tree.delete(row)
        for item in self._current_incoming():
            payload = item.get("payload", {})
            target = payload.get("target") or payload.get("host") or payload.get("device") or "-"
            self.pending_tree.insert(
                "",
                "end",
                values=(
                    item.get("id"),
                    item.get("command_type", "-"),
                    target,
                    item.get("status", "-"),
                    _fmt_time(item.get("created_at")),
                ),
            )
        for item in results[:100]:
            target = item.get("host", "-")
            self.device_tree.insert(
                "",
                "end",
                values=(
                    target,
                    "online" if item.get("reachable") else "offline",
                    f"{item.get('latency_ms', '-') } ms" if item.get("latency_ms") is not None else "-",
                    ", ".join(f"tcp/{p}" for p in item.get("tcp_open_ports", [])[:6]) or "-",
                    _fmt_time(self.agent.last_discovery_at),
                ),
            )
            self.local_tree.insert(
                "",
                "end",
                values=(
                    target,
                    "online" if item.get("reachable") else "offline",
                    f"{item.get('latency_ms', '-') } ms" if item.get("latency_ms") is not None else "-",
                    ", ".join(f"tcp/{p}" for p in item.get("tcp_open_ports", [])[:6]) or "-",
                    item.get("local_status", "-"),
                ),
            )
        for row in self.history_tree.get_children():
            self.history_tree.delete(row)
        for item in self.cache.action_history(50):
            payload = item.get("payload", {})
            self.history_tree.insert(
                "",
                "end",
                values=(
                    _fmt_time(item.get("created_at")),
                    str(payload.get("command_id") or payload.get("queue_id") or "-"),
                    str(payload.get("action") or payload.get("command_type") or item.get("kind")),
                    str(payload.get("target") or payload.get("host") or payload.get("command_id") or "-"),
                    str(payload.get("status") or payload.get("reason") or "recorded"),
                ),
            )

    def _refresh_ui(self) -> None:
        online = self.agent.last_online_state
        self.status_var.set("Online" if online else "Offline")
        self.sync_var.set(f"Last sync: {_fmt_time(self.agent.last_sync_at)}")
        self.queue_var.set(f"Queued commands: {len(self.cache.pending_commands())}")
        self.alerts_var.set(
            f"Critical alerts: {sum(1 for item in self.cache.recent_events(60) if str((item.get('payload') or {}).get('severity', '')).lower() == 'critical')}"
        )
        self.inventory_var.set(f"Local hosts: {len(self.agent.last_local_inventory)}")
        bundle = self.cache.latest_model_bundle()
        if bundle:
            self.model_var.set(f"Model: {bundle.get('model_version', '-')}")
        else:
            self.model_var.set(f"Model: {self.settings.model_version}")
        last_error = self.cache.get_last_error()
        self.error_string_var.set(_short(last_error.get("error") if isinstance(last_error, dict) else "-", 160) if last_error else "-")
        self._render_dashboard()
        self._render_alerts()
        self._render_ai_team()
        self._render_onprem()

    def _tick(self) -> None:
        try:
            self._refresh_ui()
        finally:
            self.after(2000, self._tick)

    def save_settings(self) -> None:
        self.settings.server_url = self.server_var.get().strip() or self.settings.server_url
        try:
            self.settings.company_id = int(self.company_var.get().strip() or self.settings.company_id)
        except Exception:
            pass
        self.settings.name = self.name_var.get().strip() or self.settings.name
        try:
            agent_id_text = self.agent_id_var.get().strip()
            self.settings.agent_id = int(agent_id_text) if agent_id_text else None
        except Exception:
            self.settings.agent_id = None
        self.settings.agent_key = self.agent_key_var.get().strip() or None
        self.settings.model_version = self.model_version_var.get().strip() or self.settings.model_version
        self.settings.site_name = self.site_name_var.get().strip() or None
        self.settings.discovery_cidr = self.discovery_cidr_var.get().strip() or None
        self.settings.local_targets = [item.strip() for item in self.local_targets_var.get().split(",") if item.strip()]
        self.settings.poll_enabled = bool(self.poll_enabled_var.get())
        self.settings.discovery_enabled = bool(self.discovery_enabled_var.get())
        self.cache.save_local_targets(self.settings.local_targets)
        self.cache.save_agent_profile(self.settings)
        self.error_string_var.set("Saved local agent settings")

    def reconnect(self) -> None:
        self.agent.running = False
        self.cache.add_event("ui_action", {"action": "reconnect", "at": datetime.utcnow().isoformat()})
        self.destroy()
        self.agent.running = True

    def manual_sync(self) -> None:
        self.cache.add_event("ui_action", {"action": "sync", "at": datetime.utcnow().isoformat()})
        self.agent.request_sync_once()

    def manual_heartbeat(self) -> None:
        self.cache.add_event("ui_action", {"action": "heartbeat", "at": datetime.utcnow().isoformat()})
        self.agent.request_heartbeat_once()

    def run_local_poll(self) -> None:
        self.cache.add_event("ui_action", {"action": "local_poll", "at": datetime.utcnow().isoformat()})
        self.agent.request_local_poll_once()

    def approve_selected_command(self) -> None:
        selected = self.pending_tree.selection()
        if not selected:
            return
        values = self.pending_tree.item(selected[0], "values")
        if not values:
            return
        try:
            command_id = int(values[1])
        except Exception:
            return
        self.agent.approve_incoming_command(command_id)
        self.cache.add_event("ui_action", {"action": "approve_command", "command_id": command_id, "at": datetime.utcnow().isoformat()})
        self._refresh_ui()

    def reject_selected_command(self) -> None:
        selected = self.pending_tree.selection()
        if not selected:
            return
        values = self.pending_tree.item(selected[0], "values")
        if not values:
            return
        try:
            command_id = int(values[1])
        except Exception:
            return
        self.agent.reject_incoming_command(command_id)
        self.cache.add_event("ui_action", {"action": "reject_command", "command_id": command_id, "at": datetime.utcnow().isoformat()})
        self._refresh_ui()

    def add_target(self) -> None:
        target = self.device_target_var.get().strip()
        if not target:
            return
        targets = self.cache.add_local_target(target)
        self.settings.local_targets = targets
        self.cache.save_agent_profile(self.settings)
        self.device_target_var.set("")
        self.cache.save_action_history({"action": "add_target", "target": target, "at": datetime.utcnow().isoformat()})
        self._refresh_ui()

    def remove_target(self) -> None:
        target = self.device_target_var.get().strip()
        if not target:
            selected = self.device_tree.selection()
            if selected:
                values = self.device_tree.item(selected[0], "values")
                target = values[0] if values else ""
        if not target:
            return
        targets = self.cache.remove_local_target(target)
        self.settings.local_targets = targets
        self.cache.save_agent_profile(self.settings)
        self.cache.save_action_history({"action": "remove_target", "target": target, "at": datetime.utcnow().isoformat()})
        self._refresh_ui()

    def open_cache_summary(self) -> None:
        summary = {
            "profile": self.settings.__dict__,
            "events": self.cache.recent_events(20),
            "queue": self.cache.pending_commands(),
            "model": self.cache.latest_model_bundle(),
        }
        self.cache.add_event("ui_summary", {"summary": summary, "at": datetime.utcnow().isoformat()})
        self.error_string_var.set("Cache summary recorded locally")
