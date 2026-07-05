from __future__ import annotations

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

try:
    import webview  # type: ignore
except Exception:  # pragma: no cover
    webview = None
import webbrowser

if __package__:
    from .cache import AgentSettings, LocalCache
    from .client import ServerClient
else:  # pragma: no cover
    from cache import AgentSettings, LocalCache
    from client import ServerClient


def _json_response(handler: BaseHTTPRequestHandler, payload: dict[str, Any], status: int = 200) -> None:
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(raw)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(raw)


def _text_response(handler: BaseHTTPRequestHandler, html: str, status: int = 200) -> None:
    raw = html.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(raw)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(raw)


def _build_html() -> str:
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Idea Agent</title>
  <style>
    :root{
      --bg:#060b16; --panel:#0b1324; --panel2:#0f1a31; --line:#1a2945; --text:#edf3ff;
      --muted:#8fa4cb; --accent:#6f7dff; --accent2:#8b5cf6; --good:#38d39f; --warn:#ffb347; --bad:#ff5f6d;
    }
    *{box-sizing:border-box}
    body{margin:0;font-family:Inter,Segoe UI,Arial,sans-serif;background:
      radial-gradient(circle at top left, rgba(111,125,255,.18), transparent 35%),
      radial-gradient(circle at top right, rgba(139,92,246,.16), transparent 28%),
      linear-gradient(180deg, #050812 0%, #09111f 100%);
      color:var(--text);
    }
    .shell{display:grid;grid-template-columns:240px 1fr;min-height:100vh}
    .side{background:linear-gradient(180deg, rgba(7,12,24,.99), rgba(9,16,32,.98));border-right:1px solid rgba(255,255,255,.06);padding:16px 14px 18px;display:flex;flex-direction:column;gap:14px}
    .brand{display:flex;align-items:center;gap:12px;padding:6px 4px 14px}
    .logo{width:40px;height:40px;border-radius:14px;background:radial-gradient(circle at 30% 30%, #8d7bff, #4b5cff 60%, #2e7cff);display:grid;place-items:center;font-weight:800;box-shadow:0 12px 35px rgba(111,125,255,.35)}
    .brand h1{margin:0;font-size:17px;line-height:1.1}
    .brand p{margin:2px 0 0;color:var(--muted);font-size:11px}
    .nav{display:grid;gap:7px}
    .nav button{all:unset;cursor:pointer;padding:14px 14px;border-radius:14px;background:transparent;color:var(--muted);border:1px solid transparent;display:flex;align-items:center;justify-content:space-between;font-size:14px}
    .nav button.active{background:linear-gradient(135deg, rgba(111,125,255,.92), rgba(139,92,246,.92));color:#fff;border-color:rgba(111,125,255,.25);box-shadow:0 14px 26px rgba(65,77,200,.32)}
    .badge{padding:3px 9px;border-radius:999px;background:rgba(255,255,255,.12);color:#fff;font-size:12px}
    .sidepanel{margin-top:auto;border-top:1px solid rgba(255,255,255,.06);padding-top:14px;display:grid;gap:14px}
    .profile{display:flex;align-items:center;gap:12px;padding:10px 8px}
    .avatar{width:54px;height:54px;border-radius:50%;background:radial-gradient(circle at 35% 35%, #6ee7ff, #7c3aed 68%, #0b1220);display:grid;place-items:center;box-shadow:0 10px 26px rgba(124,58,237,.35);font-size:20px}
    .profile h4{margin:0;font-size:15px}
    .profile p{margin:2px 0 0;color:var(--muted);font-size:12px}
    .dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--good);margin-right:6px;vertical-align:middle}
    .promo{padding:16px;border-radius:18px;background:linear-gradient(180deg, rgba(55,65,190,.22), rgba(53,35,118,.38));border:1px solid rgba(133, 95, 255, .24);box-shadow:0 18px 40px rgba(9,15,28,.35)}
    .promo h4{margin:0 0 8px;font-size:14px}
    .promo p{margin:0 0 14px;color:#cfd9ff;font-size:12px;line-height:1.55}
    .main{padding:18px 22px 22px}
    .topbar{display:flex;justify-content:space-between;align-items:flex-start;gap:18px;margin-bottom:16px}
    .hero h2{margin:0;font-size:28px;font-weight:800}
    .hero p{margin:8px 0 0;color:var(--muted);font-size:15px}
    .chips{display:flex;align-items:center;gap:12px;flex-wrap:wrap;justify-content:flex-end}
    .chip{padding:10px 14px;border-radius:999px;background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.06);color:#dfe7ff;font-size:13px}
    .active-chip{background:rgba(111,125,255,.18);border-color:rgba(111,125,255,.32)}
    .chip strong{color:#fff}
    .grid4{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:14px;margin:18px 0}
    .card{background:linear-gradient(180deg, rgba(13,20,38,.95), rgba(10,16,31,.96));border:1px solid rgba(255,255,255,.06);border-radius:18px;padding:16px 18px;box-shadow:0 16px 40px rgba(2,6,16,.35)}
    .metric-title{color:#a5b3d3;font-size:13px;letter-spacing:.02em}
    .metric-value{font-size:28px;font-weight:800;margin-top:8px}
    .metric-sub{color:#7ce6c2;font-size:12px;margin-top:6px}
    .layout{display:grid;grid-template-columns:1.5fr .88fr;gap:16px;margin-top:12px}
    .section-title{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px}
    .section-title h3{margin:0;font-size:18px}
    .section-title span{color:var(--muted);font-size:12px}
    .list{display:grid;gap:10px}
    .row{display:grid;grid-template-columns:1.1fr .55fr .55fr .65fr 1.3fr;gap:10px;padding:12px 14px;border-radius:16px;background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.05);font-size:13px;align-items:center}
    .row.head{background:transparent;border:none;color:var(--muted);font-size:12px;padding-top:0}
    .status-good{color:var(--good)} .status-warn{color:var(--warn)} .status-bad{color:var(--bad)}
    .two{display:grid;grid-template-columns:1fr 1fr;gap:14px}
    .pill{display:inline-flex;gap:8px;align-items:center;padding:7px 12px;border-radius:999px;border:1px solid var(--line);background:rgba(255,255,255,.03);font-size:12px;color:var(--muted)}
    .btn{all:unset;cursor:pointer;padding:12px 16px;border-radius:14px;background:linear-gradient(135deg,var(--accent),var(--accent2));color:white;font-weight:700;box-shadow:0 14px 30px rgba(111,125,255,.24);text-align:center}
    .btn.secondary{background:rgba(255,255,255,.05);box-shadow:none;border:1px solid var(--line);color:var(--text)}
    .tabs{display:none}
    .tabs.active{display:block}
    .stack{display:grid;gap:14px}
    .small{color:var(--muted);font-size:12px}
    .input{width:100%;padding:12px 14px;border-radius:14px;border:1px solid var(--line);background:#081020;color:var(--text);outline:none}
    .flex{display:flex;gap:10px;flex-wrap:wrap}
    pre{white-space:pre-wrap;word-break:break-word;margin:0;font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:12px;color:#dbe7ff}
    @media (max-width: 1100px){.shell{grid-template-columns:1fr}.side{border-right:none;border-bottom:1px solid var(--line)}.grid4,.layout,.two{grid-template-columns:1fr 1fr}.row{grid-template-columns:1fr 1fr}}
    @media (max-width: 760px){.grid4,.layout,.two{grid-template-columns:1fr}.topbar{flex-direction:column;align-items:flex-start}.row{grid-template-columns:1fr}.main{padding:18px}.side{padding:18px}}
  </style>
</head>
<body>
  <div class="shell">
    <aside class="side">
      <div class="brand">
        <div class="logo">IA</div>
        <div>
          <h1>Idea Agent</h1>
          <p>Network AI Assistant</p>
        </div>
      </div>
      <div class="nav" id="nav"></div>
      <div class="sidepanel">
        <div class="card">
          <div class="metric-title">Agent</div>
          <div class="metric-value" id="mode">Online</div>
          <div class="small" id="agentName">Super Admin</div>
        </div>
        <div class="profile">
          <div class="avatar">🤖</div>
          <div>
            <h4 id="agentName2">Sagar</h4>
            <p>Super Admin<br/><span class="dot"></span><span id="mode2">Online</span></p>
          </div>
        </div>
        <div class="promo">
          <h4>AI Automation</h4>
          <p>Smart automation and intelligent network management</p>
          <div class="btn secondary" style="width:100%;text-align:center">Upgrade</div>
        </div>
      </div>
    </aside>
    <main class="main">
      <div class="topbar">
        <div class="hero">
          <h2 id="pageTitle">Good evening, Sagar 👋</h2>
          <p id="pageSubtitle">Your network is healthy and all systems are operational.</p>
        </div>
        <div class="chips">
          <div class="chip"><strong>Agent Status</strong><br/><span id="connChip">Online</span></div>
          <div class="chip"><strong>Sync</strong><br/><span id="syncChip">Last sync: -</span></div>
          <div class="chip"><strong>Queue</strong><br/><span id="queueChip">0</span></div>
          <div class="btn" onclick="action('sync')" style="padding:14px 22px">Sync Now</div>
          <div class="chip">🔔 <span id="alertBubble">3</span></div>
        </div>
      </div>
      <div class="grid4">
        <div class="card"><div class="metric-title">Connection</div><div class="metric-value" id="mTargets">Online</div><div class="metric-sub">All systems operational</div></div>
        <div class="card"><div class="metric-title">Last Sync</div><div class="metric-value" id="mReachable">2m ago</div><div class="metric-sub">Auto sync enabled</div></div>
        <div class="card"><div class="metric-title">Queue</div><div class="metric-value" id="mAlerts">0</div><div class="metric-sub">Commands queued</div></div>
        <div class="card"><div class="metric-title">Model</div><div class="metric-value" id="mModel">v1.6</div><div class="metric-sub">AI Model Active</div></div>
        <div class="card"><div class="metric-title">Alerts</div><div class="metric-value" id="mCritical">0</div><div class="metric-sub">No critical alerts</div></div>
        <div class="card"><div class="metric-title">Targets</div><div class="metric-value" id="mTargets2">1</div><div class="metric-sub">Hosts monitored</div></div>
      </div>
      <section class="tabs active" id="tab-dashboard">
        <div class="layout">
          <div class="stack">
            <div class="card">
              <div class="section-title"><h3>Network Overview</h3><span>Real-time network performance and statistics</span></div>
              <div class="flex" style="justify-content:flex-end;margin:-6px 0 10px">
                <div class="chip">1H</div><div class="chip">6H</div><div class="chip active-chip">12H</div><div class="chip">1D</div><div class="chip">7D</div><div class="chip">30D</div>
              </div>
              <div class="grid4" style="grid-template-columns:repeat(4,minmax(0,1fr));margin-top:8px">
                <div class="card"><div class="metric-title">Devices</div><div class="metric-value" id="mDevices">24</div><div class="metric-sub">Online: 22</div></div>
                <div class="card"><div class="metric-title">Bandwidth</div><div class="metric-value">1.2 Gbps</div><div class="metric-sub">↑ 12%  0.5</div></div>
                <div class="card"><div class="metric-title">Latency</div><div class="metric-value">18 ms</div><div class="metric-sub">↓ 8%</div></div>
                <div class="card"><div class="metric-title">Uptime</div><div class="metric-value">99.9%</div><div class="metric-sub">Excellent</div></div>
              </div>
            </div>
            <div class="card">
              <div class="section-title"><h3>Network Topology</h3><span>Interactive network map</span></div>
              <div class="stack" id="overviewCards"></div>
            </div>
            <div class="card">
              <div class="section-title"><h3>Quick Actions</h3><span>Common network tasks</span></div>
              <div class="grid4" style="grid-template-columns:repeat(6,minmax(0,1fr));margin-top:8px">
                <div class="card"><div class="metric-title">Run Command</div><div class="small">Execute commands on devices</div></div>
                <div class="card"><div class="metric-title">Bulk Config</div><div class="small">Update configuration on multiple devices</div></div>
                <div class="card"><div class="metric-title">Health Check</div><div class="small">Run network diagnostics</div></div>
                <div class="card"><div class="metric-title">Backup Config</div><div class="small">Backup device configurations</div></div>
                <div class="card"><div class="metric-title">Network Scan</div><div class="small">Discover new network devices</div></div>
                <div class="card"><div class="metric-title">View Reports</div><div class="small">Generate network reports</div></div>
              </div>
            </div>
          </div>
          <div class="stack">
            <div class="card">
              <div class="section-title"><h3>Critical Alerts</h3><span>View All</span></div>
              <div class="stack">
                <div class="pill">All Clear! 🎉</div>
                <div class="small">No critical alerts at the moment</div>
                <div class="small">Your network is secure and healthy.</div>
              </div>
            </div>
            <div class="card">
              <div class="section-title"><h3>AI Assistant</h3><span>Powered by Advanced AI</span></div>
              <div class="stack">
                <input class="input" id="assistantInput" placeholder="Ask me anything about your network..."/>
                <div class="flex">
                  <button class="btn secondary">Show me offline devices</button>
                  <button class="btn secondary">Check bandwidth usage</button>
                  <button class="btn secondary">Run connectivity test</button>
                  <button class="btn secondary">View security status</button>
                </div>
              </div>
            </div>
            <div class="card">
              <div class="section-title"><h3>Recent Activity</h3><span>View All</span></div>
              <div class="stack" id="historyList"></div>
            </div>
          </div>
        </div>
      </section>
      <section class="tabs" id="tab-devices">
        <div class="card">
          <div class="section-title"><h3>Devices</h3><span>Local targets managed by this agent</span></div>
          <div class="flex" style="margin-bottom:12px">
            <input class="input" id="targetInput" placeholder="Add target IP or hostname"/>
            <button class="btn" onclick="addTarget()">Add Target</button>
            <button class="btn secondary" onclick="removeTarget()">Remove Target</button>
          </div>
          <div class="list" id="deviceList"></div>
        </div>
      </section>
      <section class="tabs" id="tab-topology">
        <div class="card">
          <div class="section-title"><h3>Topology</h3><span>Interactive network map</span></div>
          <div class="stack">
            <div class="pill">Live topology view coming from network memory</div>
            <div class="pill">Neighbor links and port mapping will populate here</div>
            <div class="pill">Use the discovery list to seed topology auto-layout</div>
          </div>
        </div>
      </section>
      <section class="tabs" id="tab-alerts">
        <div class="card">
          <div class="section-title"><h3>Alerts</h3><span>Open issues</span></div>
          <div class="list" id="alertsList"></div>
        </div>
      </section>
      <section class="tabs" id="tab-ai">
        <div class="two">
          <div class="card"><div class="section-title"><h3>AI Team</h3><span>Live agent state</span></div><div class="stack" id="agentState"></div></div>
          <div class="card"><div class="section-title"><h3>Action History</h3><span>Local approvals</span></div><div class="stack" id="aiHistoryList"></div></div>
        </div>
      </section>
      <section class="tabs" id="tab-commands">
        <div class="card">
          <div class="section-title"><h3>Commands</h3><span>Approve-only execution workflow</span></div>
          <div class="stack">
            <div class="pill">Queued command previews and approval gating will appear here</div>
            <div class="pill">Future command packs stay visible for scoped execution</div>
          </div>
        </div>
      </section>
      <section class="tabs" id="tab-automation">
        <div class="card">
          <div class="section-title"><h3>Automation</h3><span>Smart automation and policy control</span></div>
          <div class="stack">
            <div class="pill">Safe remediation policies</div>
            <div class="pill">Rollback-ready plan engine</div>
            <div class="pill">Auto-remediation queue bindings</div>
          </div>
        </div>
      </section>
      <section class="tabs" id="tab-reports">
        <div class="card">
          <div class="section-title"><h3>Reports</h3><span>Generate network reports</span></div>
          <div class="stack">
            <div class="pill">Operational summaries</div>
            <div class="pill">Health trends and incident reports</div>
            <div class="pill">Exportable customer snapshots</div>
          </div>
        </div>
      </section>
      <section class="tabs" id="tab-onprem">
        <div class="card">
          <div class="section-title"><h3>On-Prem Polling</h3><span>Local network scan</span></div>
          <div class="stack">
            <div class="pill">Scan status: <strong id="scanStatus">Idle</strong></div>
            <div class="pill">Targets count: <strong id="scanTargets">0</strong></div>
            <div class="pill">Online hosts: <strong id="scanOnline">0</strong></div>
          </div>
          <div style="height:12px"></div>
          <div class="list" id="inventoryList"></div>
        </div>
      </section>
      <section class="tabs" id="tab-settings">
        <div class="two">
          <div class="card">
            <div class="section-title"><h3>Settings</h3><span>Agent config</span></div>
            <div class="stack">
              <input class="input" id="serverUrl" placeholder="Server URL"/>
              <input class="input" id="companyId" placeholder="Company ID"/>
              <input class="input" id="agentName" placeholder="Agent Name"/>
              <input class="input" id="discoveryCidr" placeholder="Discovery CIDR"/>
              <input class="input" id="localTargets" placeholder="Local targets (comma separated)"/>
              <div class="flex">
                <button class="btn" onclick="saveSettings()">Save</button>
                <button class="btn secondary" onclick="action('sync')">Resync</button>
              </div>
            </div>
          </div>
          <div class="card">
            <div class="section-title"><h3>Snapshot</h3><span>Local runtime</span></div>
            <pre id="snapshotBox">-</pre>
          </div>
        </div>
      </section>
    </main>
  </div>
  <script>
    const navItems = [
      ["dashboard","Dashboard",""],
      ["devices","Devices",""],
      ["topology","Topology",""],
      ["alerts","Alerts",""],
      ["ai","AI Assistant",""],
      ["commands","Commands",""],
      ["automation","Automation",""],
      ["reports","Reports",""],
      ["settings","Settings",""]
    ];
    const state = { tab: "dashboard", payload: {} };
    const nav = document.getElementById("nav");
    navItems.forEach(([key,label,badge]) => {
      const b = document.createElement("button");
      b.textContent = label;
      const s = document.createElement("span");
      s.className = "badge";
      s.textContent = badge;
      if (!badge) s.style.display = "none";
      b.appendChild(s);
      b.onclick = () => setTab(key);
      b.id = "nav-" + key;
      nav.appendChild(b);
    });
    function setTab(tab){
      state.tab = tab;
      navItems.forEach(([key]) => {
        document.getElementById("nav-" + key).classList.toggle("active", key === tab);
        document.getElementById("tab-" + key).classList.toggle("active", key === tab);
      });
      const titles = {
        dashboard:["Good evening, Sagar 👋","Your network is healthy and all systems are operational."],
        devices:["Devices","Add and manage local targets"],
        topology:["Topology","Interactive network map"],
        alerts:["Alerts","Open issues and queue"],
        ai:["AI Assistant","Powered by advanced AI"],
        commands:["Commands","Execute and review actions"],
        automation:["Automation","Smart automation and policy control"],
        reports:["Reports","Generate network reports"],
        settings:["Settings","Agent and server config"]
      };
      document.getElementById("pageTitle").textContent = titles[tab][0];
      document.getElementById("pageSubtitle").textContent = titles[tab][1];
    }
    async function load(){
      const res = await fetch('/api/state');
      state.payload = await res.json();
      const d = state.payload;
      document.getElementById("connChip").textContent = d.online ? "Connected" : "Disconnected";
      document.getElementById("syncChip").textContent = "Last sync: " + (d.last_sync_at || "-");
      document.getElementById("queueChip").textContent = "Queue: " + (d.queue_depth || 0);
      document.getElementById("mTargets").textContent = d.online ? "Online" : "Offline";
      document.getElementById("mReachable").textContent = d.last_sync_at ? new Date(d.last_sync_at).toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'}) : "-";
      document.getElementById("mAlerts").textContent = (d.queue_depth || 0);
      document.getElementById("mModel").textContent = d.model_version || "-";
      document.getElementById("mCritical").textContent = (d.alerts || []).filter(i => (i.severity || '').toLowerCase() === 'critical').length;
      document.getElementById("mTargets2").textContent = (d.metrics?.target_count ?? 0) || 1;
      document.getElementById("agentName").textContent = d.agent_name || "-";
      document.getElementById("agentName2").textContent = d.agent_name || "-";
      document.getElementById("mode").textContent = d.online ? "ready" : "offline";
      document.getElementById("mode2").textContent = d.online ? "Online" : "Offline";
      document.getElementById("cidr").textContent = d.discovery_cidr || "-";
      document.getElementById("discoveryAt").textContent = d.last_discovery_at || "-";
      document.getElementById("cachePresent").textContent = d.model_cache_present ? "yes" : "no";
      document.getElementById("lastError").textContent = d.last_error || "-";
      document.getElementById("scanStatus").textContent = d.local_scan_status || "Idle";
      document.getElementById("scanTargets").textContent = (d.local_targets || []).length;
      document.getElementById("scanOnline").textContent = d.metrics?.reachable_hosts ?? 0;
      document.getElementById("serverUrl").value = d.server_url || "";
      document.getElementById("companyId").value = d.company_id || "";
      document.getElementById("agentName").value = d.agent_name || "";
      document.getElementById("discoveryCidr").value = d.discovery_cidr || "";
      document.getElementById("localTargets").value = (d.local_targets || []).join(", ");
      document.getElementById("snapshotBox").textContent = JSON.stringify(d.snapshot || {}, null, 2);

      const overview = document.getElementById("overviewCards");
      overview.innerHTML = "";
      (d.recent_events || []).slice(0, 5).forEach(ev => {
        const item = document.createElement("div");
        item.className = "pill";
        item.textContent = `${ev.kind || "event"} · ${ev.created_at || "-"} · ${JSON.stringify(ev.payload || {}).slice(0, 140)}`;
        overview.appendChild(item);
      });

      const dev = document.getElementById("deviceList");
      dev.innerHTML = "";
      (d.inventory || []).forEach(item => {
        const row = document.createElement("div");
        row.className = "row";
        row.innerHTML = `<div><strong>${item.host}</strong><div class="small">${item.type || "host"}</div></div>
          <div class="${item.reachable ? 'status-good' : 'status-bad'}">${item.reachable ? 'online' : 'offline'}</div>
          <div>${item.latency_ms ?? '-'}</div>
          <div>${(item.open_ports || []).join(', ') || '-'}</div>
          <div class="small">${item.last_seen || '-'}</div>`;
        dev.appendChild(row);
      });
      if (!dev.children.length) dev.innerHTML = '<div class="small">No targets yet.</div>';

      const alerts = document.getElementById("alertsList");
      alerts.innerHTML = "";
      (d.alerts || []).forEach(item => {
        const row = document.createElement("div");
        row.className = "row";
        row.innerHTML = `<div><strong>${item.severity || 'info'}</strong><div class="small">${item.event_type || '-'}</div></div>
          <div>${item.host || '-'}</div>
          <div>${item.status || '-'}</div>
          <div>${item.latency_ms ?? '-'}</div>
          <div>${item.summary || '-'}</div>`;
        alerts.appendChild(row);
      });
      if (!alerts.children.length) alerts.innerHTML = '<div class="small">No alerts right now.</div>';

      const agents = document.getElementById("agentState");
      agents.innerHTML = "";
      (d.agents || []).forEach(item => {
        const row = document.createElement("div");
        row.className = "pill";
        row.textContent = `${item.name} · ${item.status} · ${item.note}`;
        agents.appendChild(row);
      });

      const hist = document.getElementById("historyList");
      if (hist) {
        hist.innerHTML = "";
        (d.history || []).slice(0, 5).forEach(item => {
          const row = document.createElement("div");
          row.className = "pill";
          row.textContent = `${item.time} · ${item.action} · ${item.target} · ${item.status}`;
          hist.appendChild(row);
        });
      }
      const histAi = document.getElementById("aiHistoryList");
      histAi.innerHTML = "";
      (d.history || []).forEach(item => {
        const row = document.createElement("div");
        row.className = "pill";
        row.textContent = `${item.time} · ${item.action} · ${item.target} · ${item.status}`;
        histAi.appendChild(row);
      });

      const inv = document.getElementById("inventoryList");
      inv.innerHTML = "";
      (d.inventory || []).forEach(item => {
        const row = document.createElement("div");
        row.className = "row";
        row.innerHTML = `<div><strong>${item.host}</strong><div class="small">${item.ping_output || 'local probe'}</div></div>
          <div>${item.reachable ? 'online' : 'offline'}</div>
          <div>${item.latency_ms ?? '-'}</div>
          <div>${(item.open_ports || []).join(', ') || '-'}</div>
          <div>${item.last_seen || '-'}</div>`;
        inv.appendChild(row);
      });
      if (!inv.children.length) inv.innerHTML = '<div class="small">No discovery inventory.</div>';
    }
    async function action(kind){
      await fetch('/api/action/' + kind, {method:'POST'});
      setTimeout(load, 500);
    }
    async function addTarget(){
      const host = document.getElementById("targetInput").value.trim();
      if (!host) return;
      await fetch('/api/targets', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({host})});
      document.getElementById("targetInput").value = "";
      setTimeout(load, 300);
    }
    async function removeTarget(){
      const host = document.getElementById("targetInput").value.trim();
      if (!host) return;
      await fetch('/api/targets', {method:'DELETE', headers:{'Content-Type':'application/json'}, body:JSON.stringify({host})});
      document.getElementById("targetInput").value = "";
      setTimeout(load, 300);
    }
    async function saveSettings(){
      const payload = {
        server_url: document.getElementById("serverUrl").value.trim(),
        company_id: parseInt(document.getElementById("companyId").value || "1", 10),
        name: document.getElementById("agentName").value.trim(),
        discovery_cidr: document.getElementById("discoveryCidr").value.trim(),
        local_targets: document.getElementById("localTargets").value.split(",").map(s=>s.trim()).filter(Boolean)
      };
      await fetch('/api/settings', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
      setTimeout(load, 300);
    }
    setTab('dashboard');
    load();
    setInterval(load, 2500);
  </script>
</body>
</html>"""


class AgentUI:
    def __init__(self, agent: Any) -> None:
        self.agent = agent
        self.cache: LocalCache = agent.cache
        self.client: ServerClient = agent.client
        self.settings: AgentSettings = agent.settings
        self.httpd: ThreadingHTTPServer | None = None
        self.server_thread: threading.Thread | None = None
        self.port = 8765

    def _snapshot(self) -> dict[str, Any]:
        return self.agent.build_snapshot()

    def _state_payload(self) -> dict[str, Any]:
        return {
            "online": bool(self.agent.last_online_state),
            "server_url": self.settings.server_url,
            "company_id": self.settings.company_id,
            "agent_name": self.settings.name,
            "model_version": self.settings.model_version,
            "queue_depth": len(self.cache.pending_commands()),
            "last_sync_at": self.agent.last_sync_at,
            "last_error": self.cache.get_last_error(),
            "local_targets": list(getattr(self.settings, "local_targets", []) or []),
            "discovery_cidr": getattr(self.settings, "discovery_cidr", None),
            "last_discovery_at": self.agent.last_discovery_at,
            "model_cache_present": self.cache.latest_model_bundle() is not None,
            "snapshot": self._snapshot(),
            "metrics": self.agent.last_local_metrics,
            "inventory": self.agent.last_local_inventory,
            "alerts": self.agent.last_local_alerts,
            "agents": [
                {"name": "Monitoring Agent", "status": "online", "note": "tracking signals"},
                {"name": "Diagnosis Agent", "status": "online", "note": "root cause analysis"},
                {"name": "Planning Agent", "status": "online", "note": "safe remediation"},
                {"name": "Verification Agent", "status": "online", "note": "policy gate"},
                {"name": "Execution Agent", "status": "online", "note": "approve-only workflow"},
            ],
            "history": [
                {
                    "time": item.get("created_at"),
                    "action": item.get("kind"),
                    "target": item.get("payload", {}).get("host") or item.get("payload", {}).get("device") or "-",
                    "status": item.get("payload", {}).get("status", "ok"),
                }
                for item in self.cache.recent_events(12)
            ],
            "recent_events": self.cache.recent_events(6),
        }

    def _serve(self) -> None:
        parent = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
                return

            def do_GET(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                if parsed.path == "/" or parsed.path == "/index.html":
                    _text_response(self, _build_html())
                    return
                if parsed.path == "/api/state":
                    _json_response(self, parent._state_payload())
                    return
                _json_response(self, {"error": "not found"}, 404)

            def do_POST(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                length = int(self.headers.get("Content-Length", "0") or "0")
                body = self.rfile.read(length).decode("utf-8") if length else "{}"
                data = json.loads(body or "{}")
                if parsed.path == "/api/action/sync":
                    parent.agent.request_sync_once()
                    _json_response(self, {"ok": True})
                    return
                if parsed.path == "/api/action/heartbeat":
                    parent.agent.request_heartbeat_once()
                    _json_response(self, {"ok": True})
                    return
                if parsed.path == "/api/action/poll":
                    parent.agent.request_local_poll_once()
                    _json_response(self, {"ok": True})
                    return
                if parsed.path == "/api/settings":
                    parent.settings.server_url = data.get("server_url") or parent.settings.server_url
                    parent.settings.company_id = int(data.get("company_id") or parent.settings.company_id)
                    parent.settings.name = data.get("name") or parent.settings.name
                    parent.settings.discovery_cidr = data.get("discovery_cidr") or parent.settings.discovery_cidr
                    targets = data.get("local_targets")
                    if isinstance(targets, list):
                        parent.settings.local_targets = [str(v) for v in targets if str(v).strip()]
                    parent.cache.save_agent_profile(parent.settings)
                    _json_response(self, {"ok": True})
                    return
                if parsed.path == "/api/targets":
                    host = str(data.get("host") or "").strip()
                    if host:
                        parent.cache.add_local_target(host)
                        parent.settings.local_targets = parent.cache.load_local_targets()
                        parent.cache.save_agent_profile(parent.settings)
                    _json_response(self, {"ok": True})
                    return
                if parsed.path == "/api/targets/delete":
                    host = str(data.get("host") or "").strip()
                    if host:
                        parent.cache.remove_local_target(host)
                        parent.settings.local_targets = parent.cache.load_local_targets()
                        parent.cache.save_agent_profile(parent.settings)
                    _json_response(self, {"ok": True})
                    return
                _json_response(self, {"error": "not found"}, 404)

            def do_DELETE(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                length = int(self.headers.get("Content-Length", "0") or "0")
                body = self.rfile.read(length).decode("utf-8") if length else "{}"
                data = json.loads(body or "{}")
                if parsed.path == "/api/targets":
                    host = str(data.get("host") or "").strip()
                    if host:
                        parent.cache.remove_local_target(host)
                        parent.settings.local_targets = parent.cache.load_local_targets()
                        parent.cache.save_agent_profile(parent.settings)
                    _json_response(self, {"ok": True})
                    return
                _json_response(self, {"error": "not found"}, 404)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        self.httpd.daemon_threads = True
        self.server_thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.server_thread.start()

    def mainloop(self) -> None:
        self._serve()
        url = f"http://127.0.0.1:{self.port}/"
        if webview is not None:
            try:
                window = webview.create_window(
                    "Idea Agent",
                    url,
                    width=1600,
                    height=1024,
                    resizable=True,
                    min_size=(1280, 820),
                )

                def _run() -> None:
                    try:
                        webview.start(debug=False, http_server=True, gui="edgechromium")
                    except TypeError:
                        webview.start(debug=False, http_server=True)

                _run()
            except Exception:
                try:
                    webbrowser.open(url)
                except Exception:
                    pass
        else:
            try:
                webbrowser.open(url)
            except Exception:
                pass
            try:
                while self.agent.running:
                    time.sleep(0.5)
            except KeyboardInterrupt:
                self.agent.running = False
        if self.httpd:
            self.httpd.shutdown()
