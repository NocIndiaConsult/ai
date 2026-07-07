from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

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


def _host_key(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if "://" in raw:
        parsed = urlparse(raw)
        raw = parsed.hostname or raw
    if "/" in raw:
        raw = raw.split("/", 1)[0]
    if ":" in raw and raw.count(":") == 1:
        raw = raw.split(":", 1)[0]
    return raw.strip().lower()


def _build_html() -> str:
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Idea Agent</title>
  <style>
    :root{
      --bg:#050816; --panel:#0b1322; --line:rgba(255,255,255,.06); --text:#edf3ff; --muted:#8f9ebd;
      --blue:#6572ff; --purple:#8b5cf6; --good:#2de39c; --warn:#ffb84d; --bad:#ff6b7b;
      --shadow:0 20px 60px rgba(0,0,0,.32); --radius:18px;
      --cyan:#22d3ee; --pink:#ec4899; --orange:#f59e0b; --teal:#14b8a6;
    }
    *{box-sizing:border-box}
    html,body{height:100%}
    body{
      margin:0;font-family:Inter,Segoe UI,Roboto,Arial,sans-serif;color:var(--text);
      background:
        radial-gradient(circle at 10% 10%, rgba(96,112,255,.10), transparent 28%),
        radial-gradient(circle at 90% 0%, rgba(139,92,246,.09), transparent 25%),
        linear-gradient(180deg, #050812 0%, #070c18 100%);
      overflow:auto;
    }
    .shell{min-height:100vh;display:grid;grid-template-columns:236px 1fr}
    .side{display:flex;flex-direction:column;padding:20px 14px 14px;background:linear-gradient(180deg, rgba(7,11,22,.96), rgba(10,16,31,.98));border-right:1px solid rgba(255,255,255,.05)}
    .brand{display:flex;align-items:center;gap:14px;padding:4px 8px 22px}
    .logo{width:48px;height:48px;border-radius:16px;display:grid;place-items:center;background:linear-gradient(135deg,#7b62ff,#4660ff 58%,#1fd5ff);box-shadow:0 12px 30px rgba(76,95,255,.35);font-weight:900;font-size:18px}
    .brand h1{margin:0;font-size:19px;line-height:1.05;font-weight:800}
    .brand p{margin:4px 0 0;color:var(--muted);font-size:12px;letter-spacing:.01em}
    .nav{display:grid;gap:8px}
    .nav button{all:unset;cursor:pointer;display:flex;align-items:center;gap:14px;padding:12px 16px;border-radius:15px;color:var(--muted);font-size:14px;border:1px solid transparent}
    .nav button .navico{width:22px;height:22px;flex:0 0 auto;display:grid;place-items:center;font-size:15px;opacity:.9}
    .nav button .navlabel{flex:1}
    .nav button.active{background:linear-gradient(135deg, rgba(103,115,255,.97), rgba(136,94,246,.92));color:#fff;box-shadow:0 16px 30px rgba(70,81,210,.32)}
    .badge{min-width:24px;height:24px;border-radius:999px;display:grid;place-items:center;padding:0 8px;background:#6f52ff;color:#fff;font-size:11px;margin-left:auto}
    .sidepanel{margin-top:auto;padding-top:18px;display:grid;gap:14px}
    .profile{display:flex;align-items:center;gap:14px;padding:14px 12px;border-top:1px solid rgba(255,255,255,.06);border-bottom:1px solid rgba(255,255,255,.06)}
    .avatar{width:60px;height:60px;border-radius:50%;display:grid;place-items:center;background:radial-gradient(circle at 30% 30%, #74d8ff, #7b64ff 62%, #101826 100%);box-shadow:0 14px 32px rgba(106,83,255,.28);font-size:22px}
    .profile h4{margin:0;font-size:15px;font-weight:700}
    .profile p{margin:4px 0 0;color:var(--muted);font-size:12px;line-height:1.4}
    .dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--good);margin-right:6px;vertical-align:middle}
    .promo{padding:18px;border-radius:18px;background:linear-gradient(180deg, rgba(75,61,157,.30), rgba(28,25,67,.56));border:1px solid rgba(125,101,255,.24);box-shadow:0 18px 34px rgba(9,13,24,.34)}
    .promo h4{margin:0 0 8px;font-size:15px}
    .promo p{margin:0 0 14px;color:#d2dbff;font-size:12px;line-height:1.55}
    .main{padding:20px 22px 22px;overflow:auto;min-height:0}
    .main::-webkit-scrollbar{width:10px}
    .main::-webkit-scrollbar-thumb{background:rgba(140,154,214,.18);border-radius:20px;border:2px solid transparent;background-clip:content-box}
    .topbar{display:flex;justify-content:space-between;align-items:flex-start;gap:18px;margin-bottom:16px}
    .hero h2{margin:0;font-size:29px;line-height:1.15;font-weight:800;letter-spacing:-.02em}
    .hero p{margin:8px 0 0;color:var(--muted);font-size:14px}
    .top-actions{display:flex;align-items:center;gap:12px;flex-wrap:wrap;justify-content:flex-end}
    .searchbar{flex:1;max-width:720px;display:flex;align-items:center;gap:12px;padding:14px 18px;border-radius:16px;background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.06)}
    .searchbar input{all:unset;width:100%;color:var(--text);font-size:14px}
    .agent-pill{min-width:180px;padding:11px 15px;border-radius:14px;background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.06);text-align:left}
    .agent-pill .small{font-size:12px;color:var(--muted);display:block}
    .agent-pill .online{color:#3ef0a5;font-weight:700;font-size:14px;margin-top:4px}
    .iconbtn{width:46px;height:46px;border-radius:50%;display:grid;place-items:center;background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.06);color:#d8e2ff;position:relative}
    .iconbadge{position:absolute;top:-2px;right:-2px;width:18px;height:18px;border-radius:999px;background:#7a5dff;color:#fff;font-size:11px;display:grid;place-items:center;border:2px solid #070c18}
    .btn{all:unset;cursor:pointer;padding:12px 18px;border-radius:14px;color:#fff;font-weight:700;background:linear-gradient(135deg, #5362ff, #8c5af5);box-shadow:0 14px 28px rgba(98,95,255,.24);text-align:center;white-space:nowrap}
    .btn.secondary{background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.08);box-shadow:none;color:var(--text)}
    .btn.danger{background:rgba(255,80,80,.12);border:1px solid rgba(255,80,80,.35);box-shadow:none;color:#ff8a8a}
    .btn.danger:hover{background:rgba(255,80,80,.2)}
    .chip{padding:10px 14px;border-radius:999px;background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.06);color:#dce4ff;font-size:13px;line-height:1.1}
    .chip strong{display:block;color:#aab6d8;font-size:12px;font-weight:500;margin-bottom:4px}
    .grid6{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:14px;margin:18px 0 16px}
    .metric{position:relative;min-height:112px;padding:19px;border-radius:18px;background:linear-gradient(180deg, rgba(13,20,38,.96), rgba(10,16,31,.97));border:1px solid rgba(255,255,255,.06);box-shadow:var(--shadow);overflow:hidden}
    .metric.wide{min-height:132px}
    .metric-head{display:flex;align-items:center;gap:12px}
    .metric-ico{width:44px;height:44px;border-radius:50%;display:grid;place-items:center;background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.06);color:#8ba2ff;flex:0 0 auto;font-size:18px}
    .metric-ico.i-green{background:rgba(45,227,156,.14);color:var(--good);border-color:rgba(45,227,156,.25)}
    .metric-ico.i-blue{background:rgba(101,114,255,.16);color:#8fa0ff;border-color:rgba(101,114,255,.28)}
    .metric-ico.i-purple{background:rgba(139,92,246,.16);color:#c2a8ff;border-color:rgba(139,92,246,.28)}
    .metric-ico.i-orange{background:rgba(245,158,11,.16);color:var(--warn);border-color:rgba(245,158,11,.28)}
    .metric-ico.i-pink{background:rgba(236,72,153,.16);color:var(--pink);border-color:rgba(236,72,153,.28)}
    .metric-title{color:#a4b0cf;font-size:12px;letter-spacing:.02em}
    .metric-value{font-size:27px;line-height:1.1;font-weight:800;margin-top:10px;letter-spacing:-.03em}
    .metric-sub{color:#7cdcae;font-size:12px;margin-top:5px}
    .metric-sub.muted{color:var(--muted)}
    .layout{display:grid;grid-template-columns:minmax(0,1.42fr) minmax(330px,.93fr);gap:16px;margin-top:10px;align-items:start}
    .stack{display:grid;gap:14px}
    .card{background:linear-gradient(180deg, rgba(13,20,38,.96), rgba(10,16,31,.98));border:1px solid rgba(255,255,255,.06);border-radius:18px;padding:19px;box-shadow:var(--shadow)}
    .section-title{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;margin-bottom:14px}
    .section-title h3{margin:0;font-size:19px;letter-spacing:-.02em}
    .section-title span{color:var(--muted);font-size:12px}
    .subtle-row{display:flex;flex-wrap:wrap;gap:10px;justify-content:flex-end}
    .seg{padding:8px 12px;border-radius:12px;background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.05);color:#c9d4f0;font-size:12px}
    .seg.active{background:rgba(100,110,255,.22);border-color:rgba(100,110,255,.35)}
    .subgrid4{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}
    .subcard{padding:15px;border-radius:16px;background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.05);min-height:108px}
    .subcard .metric-title{font-size:12px}
    .subcard .metric-value{font-size:26px}
    .subcard .metric-sub{color:#7fe0b0}
    .topology-wrap{min-height:320px;border-radius:18px;border:1px solid rgba(255,255,255,.05);background:radial-gradient(circle at center, rgba(68,255,174,.06), transparent 45%), rgba(255,255,255,.02);padding:6px}
    .topology-wrap svg{width:100%;height:320px;display:block}
    .topo-link{stroke:rgba(120,235,190,.55);stroke-width:1.4;stroke-dasharray:4 4}
    .topo-link.offline{stroke:rgba(255,107,123,.45)}
    .topo-link.unknown{stroke:rgba(255,184,77,.4)}
    .topo-hub{fill:url(#topoHubGrad);stroke:rgba(255,255,255,.14)}
    .topo-node{stroke:rgba(255,255,255,.12);stroke-width:1}
    .topo-label{fill:#c7d2f0;font-size:10px;font-family:Inter,Segoe UI,Roboto,Arial,sans-serif}
    .legend{display:flex;gap:16px;align-items:center;flex-wrap:wrap;margin-top:10px}
    .legend span{display:inline-flex;align-items:center;gap:8px;color:var(--muted);font-size:12px}
    .legend i{display:inline-block;width:8px;height:8px;border-radius:50%}
    .alerts{border-left:1px solid rgba(98,255,195,.36);box-shadow:inset 3px 0 0 rgba(35,246,142,.14), var(--shadow)}
    .alert-ok{display:flex;align-items:center;gap:14px;padding:10px 0 0}
    .okmark{width:64px;height:64px;border-radius:50%;background:rgba(32,220,141,.10);display:grid;place-items:center;color:var(--good);font-size:26px;flex:0 0 auto}
    .assistant-box{min-height:172px;display:grid;gap:12px}
    .assistant-input{display:flex;align-items:center;gap:10px;padding:12px 12px;border-radius:14px;background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.05)}
    .assistant-input input{all:unset;flex:1;color:var(--text);font-size:14px}
    .assistant-input .send{width:54px;height:48px;border-radius:12px;display:grid;place-items:center;background:linear-gradient(135deg, #5b63ff, #8c59f5);font-size:18px}
    .quick-grid{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:10px}
    .quick{border-radius:14px;border:1px solid rgba(255,255,255,.05);background:rgba(255,255,255,.03);padding:16px 12px 14px;min-height:112px}
    .quick{cursor:pointer;transition:border-color .15s ease}
    .quick:hover{border-color:rgba(255,255,255,.16)}
    .quick .ico{width:36px;height:36px;border-radius:12px;display:grid;place-items:center;margin-bottom:12px;background:rgba(102,114,255,.12);color:#90a2ff;border:1px solid rgba(118,129,255,.18);font-size:16px}
    .quick .ico.q-pink{background:rgba(236,72,153,.14);color:var(--pink);border-color:rgba(236,72,153,.24)}
    .quick .ico.q-purple{background:rgba(139,92,246,.14);color:#c2a8ff;border-color:rgba(139,92,246,.24)}
    .quick .ico.q-cyan{background:rgba(34,211,238,.14);color:var(--cyan);border-color:rgba(34,211,238,.24)}
    .quick h4{margin:0;font-size:14px}
    .quick p{margin:8px 0 0;color:var(--muted);font-size:12px;line-height:1.45}
    .list{display:grid;gap:10px}
    .row{display:grid;grid-template-columns:1.1fr .55fr .55fr .65fr 1.3fr;gap:10px;padding:12px 14px;border-radius:16px;background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.05);font-size:13px;align-items:center}
    .small{color:var(--muted);font-size:12px;line-height:1.45}
    .pill{display:inline-flex;gap:8px;align-items:center;padding:8px 12px;border-radius:999px;border:1px solid rgba(255,255,255,.08);background:rgba(255,255,255,.03);font-size:12px;color:var(--muted)}
    .agent-grid{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:12px}
    .agent-card{padding:14px;border-radius:16px;background:linear-gradient(180deg, rgba(21,30,55,.96), rgba(10,16,31,.98));border:1px solid rgba(255,255,255,.06);box-shadow:var(--shadow);min-height:128px}
    .agent-card .head{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:12px}
    .agent-card .name{font-size:14px;font-weight:800;color:#f0f5ff}
    .agent-card .state{font-size:11px;color:#53f2a2;background:rgba(44,227,156,.12);border:1px solid rgba(44,227,156,.18);padding:4px 8px;border-radius:999px}
    .agent-card .note{font-size:12px;color:var(--muted);line-height:1.45;min-height:34px}
    .agent-card .bar{height:8px;border-radius:999px;background:rgba(255,255,255,.05);overflow:hidden;margin-top:12px}
    .agent-card .bar > span{display:block;height:100%;border-radius:inherit;background:linear-gradient(90deg,#4f7cff,#8b5cf6,#2de39c)}
    .agent-card .foot{display:flex;justify-content:space-between;margin-top:10px;font-size:11px;color:#9aacd1}
    .tabs{display:none;min-width:0}.tabs.active{display:block}
    .two{display:grid;grid-template-columns:1fr 1fr;gap:14px}
    .input{width:100%;padding:13px 14px;border-radius:14px;border:1px solid rgba(255,255,255,.07);background:#081020;color:var(--text);outline:none;font-size:14px}
    pre{white-space:pre-wrap;word-break:break-word;margin:0;font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:12px;line-height:1.5;color:#dbe7ff}
    .status-good{color:var(--good)} .status-warn{color:var(--warn)} .status-bad{color:var(--bad)}
    .main::-webkit-scrollbar{width:10px}.main::-webkit-scrollbar-thumb{background:rgba(140,154,214,.18);border-radius:20px;border:2px solid transparent;background-clip:content-box}
    .row.clickable{cursor:pointer;transition:background .12s ease,border-color .12s ease}
    .row.clickable:hover{background:rgba(255,255,255,.07);border-color:rgba(255,255,255,.14)}
    .status-dot{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:6px;vertical-align:middle}
    .status-dot.good{background:var(--good);box-shadow:0 0 8px rgba(45,227,156,.7)}
    .status-dot.bad{background:var(--bad);box-shadow:0 0 8px rgba(255,107,123,.7)}
    .status-dot.warn{background:var(--warn);box-shadow:0 0 8px rgba(255,184,77,.6)}
    .modal-overlay{display:none;position:fixed;inset:0;background:rgba(3,5,14,.72);backdrop-filter:blur(3px);z-index:999;align-items:flex-start;justify-content:center;padding:5vh 16px;overflow:auto}
    .modal-overlay.open{display:flex}
    .modal-card{width:min(760px,100%);background:linear-gradient(180deg, rgba(15,21,40,.98), rgba(9,13,26,.99));border:1px solid rgba(255,255,255,.08);border-radius:20px;box-shadow:var(--shadow);padding:22px;max-height:90vh;overflow:auto}
    .modal-head{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;margin-bottom:6px}
    .modal-head h3{margin:0;font-size:19px;font-weight:800}
    .modal-head p{margin:6px 0 0;color:var(--muted);font-size:12.5px}
    .modal-close{all:unset;cursor:pointer;color:var(--muted);font-size:20px;line-height:1;padding:4px 8px;border-radius:10px}
    .modal-close:hover{background:rgba(255,255,255,.08);color:var(--text)}
    .modal-meta{display:flex;flex-wrap:wrap;gap:8px;margin:14px 0}
    .iface-tabbar{display:flex;gap:6px;margin-top:12px;border-bottom:1px solid rgba(255,255,255,.08);padding-bottom:0}
    .iface-tab-btn{all:unset;cursor:pointer;padding:9px 14px;font-size:12.5px;font-weight:700;color:var(--muted);border-radius:10px 10px 0 0;border-bottom:2px solid transparent;transition:color .12s ease,border-color .12s ease,background .12s ease}
    .iface-tab-btn:hover{color:var(--text);background:rgba(255,255,255,.04)}
    .iface-tab-btn.active{color:#8fb2ff;border-bottom:2px solid #4f7cff;background:rgba(79,124,255,.08)}
    .iface-tab-btn .cnt{margin-left:6px;font-size:10.5px;color:var(--muted);background:rgba(255,255,255,.06);padding:1px 6px;border-radius:999px}
    .iface-panel{display:none}
    .iface-panel.active{display:block}
    .iface-empty{padding:16px 10px;color:var(--muted);font-size:12.5px;text-align:center}
    .iface-table{width:100%;border-collapse:collapse;margin-top:10px;font-size:13px}
    .iface-table th{text-align:left;color:var(--muted);font-weight:700;font-size:11px;text-transform:uppercase;letter-spacing:.04em;padding:8px 10px;border-bottom:1px solid rgba(255,255,255,.08)}
    .iface-table td{padding:9px 10px;border-bottom:1px solid rgba(255,255,255,.05)}
    .iface-table tr:last-child td{border-bottom:none}
    .iface-badge{display:inline-flex;align-items:center;gap:6px;padding:3px 9px;border-radius:999px;font-size:11.5px;font-weight:700}
    .iface-badge.up{background:rgba(45,227,156,.14);color:var(--good)}
    .iface-badge.down{background:rgba(255,107,123,.14);color:var(--bad)}
    .iface-badge.admin_down{background:rgba(255,184,77,.14);color:var(--warn)}
    .iface-badge.unknown{background:rgba(255,255,255,.08);color:var(--muted)}
    .modal-actions{display:flex;gap:10px;margin-top:16px}
    .onu-config{margin-top:18px;padding:16px;border-radius:18px;background:rgba(79,124,255,.06);border:1px solid rgba(79,124,255,.15)}
    .onu-config h4{margin:0 0 4px;font-size:15px}
    .onu-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;margin-top:12px}
    .onu-plan{margin-top:12px;padding:12px;border-radius:14px;background:rgba(0,0,0,.22);border:1px solid rgba(255,255,255,.07);max-height:220px;overflow:auto}
    .login-screen{position:fixed;inset:0;z-index:2000;display:grid;place-items:center;padding:24px;background:
      radial-gradient(circle at 20% 20%, rgba(101,114,255,.20), transparent 30%),
      radial-gradient(circle at 80% 10%, rgba(139,92,246,.18), transparent 32%),
      linear-gradient(180deg,#050812 0%,#070c18 100%)}
    .login-screen.hidden{display:none}
    .login-card{width:min(500px,100%);padding:28px;border-radius:26px;background:linear-gradient(180deg,rgba(13,20,38,.98),rgba(8,13,27,.99));border:1px solid rgba(255,255,255,.09);box-shadow:0 30px 90px rgba(0,0,0,.52)}
    .login-brand{display:flex;align-items:center;gap:14px;margin-bottom:22px}
    .login-brand h1{margin:0;font-size:24px;letter-spacing:-.03em}
    .login-brand p{margin:4px 0 0;color:var(--muted);font-size:13px}
    .login-grid{display:grid;gap:12px}
    .login-status{min-height:20px;color:#ffb4bd;font-size:12px;line-height:1.45}
    .login-demo{display:flex;justify-content:space-between;gap:10px;align-items:center;padding:10px 12px;border-radius:14px;background:rgba(101,114,255,.10);border:1px solid rgba(101,114,255,.20);color:#cfd8ff;font-size:12px}
    .shell.locked{display:none}
    @media (max-width: 1360px){.grid6{grid-template-columns:repeat(3,minmax(0,1fr))}.layout{grid-template-columns:1fr}.quick-grid{grid-template-columns:repeat(3,minmax(0,1fr))}}
    @media (max-width: 980px){body{overflow:auto}.shell{grid-template-columns:1fr}.side{border-right:none;border-bottom:1px solid rgba(255,255,255,.06)}.grid6{grid-template-columns:repeat(2,minmax(0,1fr))}.subgrid4,.quick-grid{grid-template-columns:1fr}.topbar{flex-direction:column}.top-actions{width:100%;justify-content:flex-start}.searchbar{max-width:none;width:100%}}
    @media (max-width: 760px){.grid6,.subgrid4,.quick-grid,.two{grid-template-columns:1fr}.row{grid-template-columns:1fr}.main{padding:16px}.side{padding:16px}.topology-wrap svg{height:260px}}
  </style>
</head>
<body>
  <div class="login-screen" id="loginScreen">
    <div class="login-card">
      <div class="login-brand">
        <div class="logo">IA</div>
        <div><h1>Idea Agent Login</h1><p>Server authenticated access for this company workspace</p></div>
      </div>
      <div class="login-grid">
        <input class="input" id="loginCompanyCode" placeholder="Company code (optional)"/>
        <input class="input" id="loginUsername" placeholder="Username"/>
        <input class="input" id="loginPassword" placeholder="Password" type="password" onkeydown="if(event.key==='Enter') loginAgent()"/>
        <div class="login-demo"><span>Demo login</span><strong>admin / admin</strong></div>
        <button class="btn" onclick="loginAgent()">Login & Open Agent</button>
        <div class="login-status" id="loginStatus"></div>
      </div>
    </div>
  </div>
  <div class="shell locked" id="appShell">
    <aside class="side">
      <div class="brand"><div class="logo">IA</div><div><h1>Idea Agent</h1><p>Network AI Assistant</p></div></div>
      <div class="nav" id="nav"></div>
      <div class="sidepanel">
        <div class="profile">
          <div class="avatar">🤖</div>
          <div><h4 id="profileName">Sagar</h4><p>Super Admin<br/><span class="dot"></span><span id="profileState">Online</span></p></div>
        </div>
        <div class="promo"><h4>AI Automation</h4><p>Smart automation and intelligent network management</p><div class="btn secondary" style="width:100%;text-align:center">Upgrade</div></div>
      </div>
    </aside>
    <main class="main">
      <div class="topbar">
        <div class="hero"><h2 id="pageTitle">Good evening, Sagar 👋</h2><p id="pageSubtitle">Your network is healthy and all systems are operational.</p></div>
        <div class="top-actions">
          <div class="searchbar"><span>⌕</span><input id="globalSearch" placeholder="Search devices, IPs, logs, alerts..." onkeydown="if(event.key==='Enter') runGlobalSearch(this.value)"/><span class="small">⌘ K</span></div>
          <div class="agent-pill"><span class="small">Server Sync</span><div class="online">● Online</div></div>
          <button class="btn" onclick="action('sync')">Sync Now</button>
          <div class="iconbtn">🔔<span class="iconbadge" id="bellBadge" style="display:none">0</span></div>
        </div>
      </div>
      <div class="grid6">
        <div class="metric"><div class="metric-head"><div class="metric-ico i-green">📶</div><div><div class="metric-title">Connection</div><div class="metric-value" id="mConnection">Online</div></div></div><div class="metric-sub muted" id="mConnectionSub">All systems operational</div></div>
        <div class="metric"><div class="metric-head"><div class="metric-ico i-blue">⟳</div><div><div class="metric-title">Last Sync</div><div class="metric-value" id="mLastSync">—</div></div></div><div class="metric-sub muted" id="mSyncSub">Auto sync enabled</div></div>
        <div class="metric"><div class="metric-head"><div class="metric-ico i-purple">▤</div><div><div class="metric-title">Queue</div><div class="metric-value" id="mQueue">0</div></div></div><div class="metric-sub muted">Commands queued</div></div>
        <div class="metric"><div class="metric-head"><div class="metric-ico i-orange">🧠</div><div><div class="metric-title">Model</div><div class="metric-value" id="mModel">v1</div></div></div><div class="metric-sub muted">AI Model Active</div></div>
        <div class="metric"><div class="metric-head"><div class="metric-ico i-green">🛡</div><div><div class="metric-title">Alerts</div><div class="metric-value" id="mAlerts">0</div></div></div><div class="metric-sub muted" id="mAlertsSub">No critical alerts</div></div>
        <div class="metric"><div class="metric-head"><div class="metric-ico i-purple">🎯</div><div><div class="metric-title">Targets</div><div class="metric-value" id="mTargets">0</div></div></div><div class="metric-sub muted">Hosts monitored</div></div>
      </div>
      <section class="tabs active" id="tab-dashboard">
        <div class="layout">
          <div class="stack">
            <div class="card">
              <div class="section-title"><div><h3>Network Overview</h3><span>Real-time network performance and statistics</span></div><div class="subtle-row"><div class="seg">1H</div><div class="seg">6H</div><div class="seg active">12H</div><div class="seg">1D</div><div class="seg">7D</div><div class="seg">30D</div></div></div>
              <div class="subgrid4">
                <div class="subcard"><div class="metric-title">Devices</div><div class="metric-value" id="mDevices">0</div><div class="metric-sub" id="mDevicesSub">Online: 0</div></div>
                <div class="subcard"><div class="metric-title">Avg Latency</div><div class="metric-value" id="mLatency">—</div><div class="metric-sub" id="mLatencySub">From live probes</div></div>
                <div class="subcard"><div class="metric-title">Reachability</div><div class="metric-value" id="mReachability">—</div><div class="metric-sub" id="mReachabilitySub">Reachable hosts</div></div>
                <div class="subcard"><div class="metric-title">Agent Uptime</div><div class="metric-value" id="mUptime">—</div><div class="metric-sub" id="mUptimeSub">Since process start</div></div>
              </div>
            </div>
            <div class="card">
              <div class="section-title"><div><h3>Network Topology</h3><span>Interactive network map</span></div><div class="subtle-row"><div class="seg active">Live</div></div></div>
              <div class="topology-wrap"><svg id="topologySvg" viewBox="0 0 760 320" preserveAspectRatio="xMidYMid meet"></svg></div>
              <div class="legend"><span><i style="background:var(--good)"></i>Online</span><span><i style="background:var(--warn)"></i>Saved / unknown</span><span><i style="background:var(--bad)"></i>Offline</span></div>
            </div>
            <div class="card">
              <div class="section-title"><div><h3>Quick Actions</h3><span>Common network tasks</span></div></div>
              <div class="quick-grid">
                <div class="quick" onclick="setTab('commands')"><div class="ico">⌘</div><h4>Run Command</h4><p>Execute commands on devices</p></div>
                <div class="quick" onclick="setTab('devices')"><div class="ico q-purple">⚙</div><h4>Bulk Config</h4><p>Update configuration on multiple devices</p></div>
                <div class="quick" onclick="action('heartbeat')"><div class="ico q-pink">❤</div><h4>Health Check</h4><p>Run network diagnostics</p></div>
                <div class="quick" onclick="setTab('settings')"><div class="ico">⬇</div><h4>Backup Config</h4><p>View device configuration snapshot</p></div>
                <div class="quick" onclick="action('poll')"><div class="ico q-cyan">◌</div><h4>Network Scan</h4><p>Discover new network devices</p></div>
                <div class="quick" onclick="setTab('reports')"><div class="ico q-purple">▦</div><h4>View Reports</h4><p>Generate network reports</p></div>
              </div>
            </div>
          </div>
          <div class="stack">
            <div class="card alerts">
              <div class="section-title"><div><h3>Critical Alerts</h3><span>View all</span></div></div>
              <div id="homeAlerts"><div class="alert-ok"><div class="okmark">✔</div><div><div style="font-size:18px;font-weight:800">All Clear! 🎉</div><div class="small">No critical alerts at the moment</div><div class="small">Your network is secure and healthy.</div></div></div></div>
            </div>
            <div class="card">
              <div class="section-title"><div><h3>AI Assistant</h3><span>Powered by advanced AI</span></div></div>
              <div class="assistant-box">
                <div class="assistant-input"><input id="assistantInput" placeholder="Ask me anything about your network..." onkeydown="if(event.key==='Enter') askAssistant(this.value)"/><div class="send" onclick="askAssistant(document.getElementById('assistantInput').value)">➤</div></div>
                <div class="flex">
                  <button class="btn secondary" onclick="askAssistant('offline devices')">Show me offline devices</button>
                  <button class="btn secondary" onclick="askAssistant('bandwidth usage')">Check bandwidth usage</button>
                  <button class="btn secondary" onclick="askAssistant('connectivity test')">Run connectivity test</button>
                  <button class="btn secondary" onclick="askAssistant('security status')">View security status</button>
                </div>
                <div class="pill" id="assistantAnswer" style="display:none;white-space:pre-wrap;line-height:1.5"></div>
              </div>
            </div>
            <div class="card"><div class="section-title"><div><h3>Recent Activity</h3><span>View all</span></div></div><div class="stack" id="historyList"></div></div>
          </div>
        </div>
      </section>
      <section class="tabs" id="tab-devices">
        <div class="card">
          <div class="section-title">
            <div><h3>Devices</h3><span>Local targets managed by this agent and synced to server</span></div>
            <button class="btn" onclick="openAddDeviceModal()">+ Add Device</button>
          </div>
          <div class="list" id="deviceList"></div>
        </div>
      </section>
      <section class="tabs" id="tab-diagnose">
        <div class="card">
          <div class="section-title"><div><h3>Diagnose</h3><span>Ping, traceroute and other connectivity checks from this PC</span></div></div>
          <div class="two" style="margin-bottom:12px">
            <select class="input" id="diagDeviceSelect" onchange="if(this.value) document.getElementById('diagTarget').value=this.value;">
              <option value="">Pick a saved device (optional)</option>
            </select>
            <input class="input" id="diagTarget" placeholder="IP address or hostname to test"/>
          </div>
          <div class="flex" style="margin-bottom:12px">
            <button class="btn" onclick="runDiagnostic('ping')">Ping</button>
            <button class="btn" onclick="runDiagnostic('tracert')">Traceroute</button>
            <button class="btn secondary" onclick="runDiagnostic('dns')">DNS Lookup</button>
            <button class="btn secondary" onclick="runDiagnostic('ports')">Port Check</button>
          </div>
          <div class="pill" id="diagStatus" style="margin-bottom:8px">Enter a target and run a test.</div>
          <div class="pill" id="diagIncomingStatus" style="margin-bottom:12px">Incoming ping capture: checking...</div>
          <div class="card" style="background:rgba(255,255,255,.03)"><pre id="diagOutput">-</pre></div>
        </div>
      </section>
      <section class="tabs" id="tab-topology"><div class="card"><div class="section-title"><div><h3>Topology</h3><span>Interactive network map</span></div></div><div class="stack"><div class="pill">Live topology view coming from network memory</div><div class="pill">Neighbor links and port mapping will populate here</div><div class="pill">Use the discovery list to seed topology auto-layout</div></div></div></section>
      <section class="tabs" id="tab-alerts"><div class="card"><div class="section-title"><div><h3>Alerts</h3><span>Open issues</span></div></div><div class="list" id="alertsList"></div></div></section>
      <section class="tabs" id="tab-ai">
        <div class="card" style="margin-bottom:14px">
          <div class="section-title"><div><h3>AI Team</h3><span>Five live agents working in real time</span></div><div class="pill">safe-first · live-sync · approve-only</div></div>
          <div class="agent-grid" id="agentGrid"></div>
        </div>
        <div class="two">
          <div class="card"><div class="section-title"><div><h3>Agent Log</h3><span>Live agent state</span></div></div><div class="stack" id="agentState"></div></div>
          <div class="card"><div class="section-title"><div><h3>Action History</h3><span>Local approvals</span></div></div><div class="stack" id="aiHistoryList"></div></div>
        </div>
      </section>
      <section class="tabs" id="tab-commands"><div class="card"><div class="section-title"><div><h3>Commands</h3><span>Approve-only execution workflow</span></div></div><div class="stack"><div class="pill">Queued command previews and approval gating will appear here</div><div class="pill">Future command packs stay visible for scoped execution</div></div></div></section>
      <section class="tabs" id="tab-automation"><div class="card"><div class="section-title"><div><h3>Automation</h3><span>Smart automation and policy control</span></div></div><div class="stack"><div class="pill">Safe remediation policies</div><div class="pill">Rollback-ready plan engine</div><div class="pill">Auto-remediation queue bindings</div></div></div></section>
      <section class="tabs" id="tab-reports"><div class="card"><div class="section-title"><div><h3>Reports</h3><span>Generate network reports</span></div></div><div class="stack"><div class="pill">Operational summaries</div><div class="pill">Health trends and incident reports</div><div class="pill">Exportable customer snapshots</div></div></div></section>
      <section class="tabs" id="tab-onprem"><div class="card"><div class="section-title"><div><h3>On-Prem Polling</h3><span>Local network scan</span></div></div><div class="stack"><div class="pill">Scan status: <strong id="scanStatus">Idle</strong></div><div class="pill">Targets count: <strong id="scanTargets">0</strong></div><div class="pill">Online hosts: <strong id="scanOnline">0</strong></div></div><div style="height:12px"></div><div class="list" id="inventoryList"></div></div></section>
      <section class="tabs" id="tab-settings"><div class="two"><div class="card"><div class="section-title"><div><h3>Settings</h3><span>Agent config</span></div></div><div class="stack"><input type="hidden" id="serverUrl"/><div class="pill">Cloud server is managed by provider and connected automatically.</div><input class="input" id="companyId" placeholder="Company ID"/><input class="input" id="agentNameInput" placeholder="Agent Name"/><input class="input" id="discoveryCidr" placeholder="Discovery CIDR"/><input class="input" id="localTargets" placeholder="Local targets (comma separated)"/><div class="flex"><button class="btn" onclick="saveSettings()">Save</button><button class="btn secondary" onclick="action('sync')">Resync</button></div></div></div><div class="card"><div class="section-title"><div><h3>Snapshot</h3><span>Local runtime</span></div></div><pre id="snapshotBox">-</pre></div></div></section>
    </main>
  </div>
  <div class="modal-overlay" id="deviceModal" onclick="if(event.target===this) closeDeviceModal()">
    <div class="modal-card">
      <div class="modal-head">
        <div>
          <h3 id="devModalTitle">Device</h3>
          <p id="devModalSubtitle">-</p>
        </div>
        <button class="modal-close" onclick="closeDeviceModal()">✕</button>
      </div>
      <div class="modal-meta" id="devModalMeta"></div>
      <div class="section-title"><div><h3 style="font-size:14px">Interfaces</h3><span id="devModalIfaceNote">Ports/interfaces reported by the device</span></div></div>
      <div class="iface-tabbar" id="ifaceTabbar">
        <button class="iface-tab-btn active" data-key="uplink" onclick="setIfaceTab('uplink')">Uplink<span class="cnt" id="ifaceCnt-uplink">0</span></button>
        <button class="iface-tab-btn" data-key="pon" onclick="setIfaceTab('pon')">PON Port<span class="cnt" id="ifaceCnt-pon">0</span></button>
        <button class="iface-tab-btn" data-key="onu" onclick="setIfaceTab('onu')">ONU<span class="cnt" id="ifaceCnt-onu">0</span></button>
        <button class="iface-tab-btn" data-key="vlan" onclick="setIfaceTab('vlan')">VLAN Interface<span class="cnt" id="ifaceCnt-vlan">0</span></button>
      </div>
      <div id="devModalIfaceWrap">
        <div class="iface-panel active" id="ifacePanel-uplink"><table class="iface-table"><thead><tr><th>Interface</th><th>Status</th><th>Rx bytes</th><th>Tx bytes</th></tr></thead><tbody id="devModalIfaceBody-uplink"></tbody></table><div class="iface-empty" id="ifaceEmpty-uplink" style="display:none">No uplink interfaces reported.</div></div>
        <div class="iface-panel" id="ifacePanel-pon"><table class="iface-table"><thead><tr><th>Interface</th><th>Status</th><th>Rx bytes</th><th>Tx bytes</th></tr></thead><tbody id="devModalIfaceBody-pon"></tbody></table><div class="iface-empty" id="ifaceEmpty-pon" style="display:none">No PON ports reported.</div></div>
        <div class="iface-panel" id="ifacePanel-onu"><table class="iface-table"><thead><tr><th>Interface</th><th>Status</th><th>Rx bytes</th><th>Tx bytes</th></tr></thead><tbody id="devModalIfaceBody-onu"></tbody></table><div class="iface-empty" id="ifaceEmpty-onu" style="display:none">No ONUs reported.</div></div>
        <div class="iface-panel" id="ifacePanel-vlan"><table class="iface-table"><thead><tr><th>Interface</th><th>Status</th><th>Rx bytes</th><th>Tx bytes</th></tr></thead><tbody id="devModalIfaceBody-vlan"></tbody></table><div class="iface-empty" id="ifaceEmpty-vlan" style="display:none">No VLAN interfaces reported.</div></div>
      </div>
      <div class="modal-actions">
        <button class="btn" onclick="refreshDeviceModal()">Refresh now</button>
        <button class="btn secondary" onclick="closeDeviceModal()">Close</button>
        <button class="btn danger" onclick="removeCurrentDevice()">Remove device</button>
      </div>
    </div>
  </div>
  <div class="modal-overlay" id="addDeviceModal" onclick="if(event.target===this) closeAddDeviceModal()">
    <div class="modal-card">
      <div class="modal-head">
        <div>
          <h3>Add Device</h3>
          <p>Add a MikroTik router, OLT, or any SNMP/SSH manageable device</p>
        </div>
        <button class="modal-close" onclick="closeAddDeviceModal()">✕</button>
      </div>
      <div class="stack">
        <input class="input" id="targetInput" placeholder="Target IP or hostname"/>
        <div class="two">
          <select class="input" id="vendorInput">
            <option value="">Vendor (auto)</option>
            <option>MicroTik</option><option>Cisco</option><option>Syrotech</option><option>TP-Link</option><option>HPE</option><option>Grandstream</option><option>DBC</option><option>Genexis</option>
          </select>
          <input class="input" id="modelInput" placeholder="Model (optional)"/>
        </div>
        <div class="two">
          <select class="input" id="protocolInput">
            <option value="auto">Protocol (auto)</option>
            <option value="snmp">SNMP</option><option value="ssh">SSH</option><option value="rest">REST</option><option value="netconf">NETCONF</option>
          </select>
          <input class="input" id="snmpCommunityInput" placeholder="SNMP community (default: public)"/>
        </div>
        <div class="two">
          <input class="input" id="usernameInput" placeholder="Username (optional, for SSH)"/>
          <input class="input" id="passwordInput" placeholder="Password (optional, for SSH)" type="password"/>
        </div>
        <div class="pill" id="deviceAddStatus">Ready to add a device.</div>
      </div>
      <div class="modal-actions">
        <button class="btn" onclick="submitAddDevice()">Add Device</button>
        <button class="btn secondary" onclick="closeAddDeviceModal()">Cancel</button>
      </div>
    </div>
  </div>
  <script>
    const navItems = [["dashboard","Dashboard","▦"],["devices","Devices","🖥"],["diagnose","Diagnose","🩺"],["topology","Topology","⎇"],["alerts","Alerts","🔔"],["ai","AI Assistant","✦"],["commands","Commands","⌘"],["automation","Automation","⚙"],["reports","Reports","▲"],["settings","Settings","⛭"]];
    const state = {tab:"dashboard",payload:{}};
    let lastMergedDevices = [];
    let authReady = false;
    function hostKey(value){
      let raw = String(value || "").trim();
      if (!raw) return "";
      try {
        if (raw.includes("://")) raw = new URL(raw).hostname || raw;
      } catch(e) {}
      if (raw.includes("/")) raw = raw.split("/")[0];
      if ((raw.match(/:/g) || []).length === 1) raw = raw.split(":")[0];
      return raw.trim().toLowerCase();
    }
    const nav = document.getElementById("nav");
    navItems.forEach(([key,label,icon]) => {
      const b = document.createElement("button");
      const i = document.createElement("span"); i.className = "navico"; i.textContent = icon;
      const t = document.createElement("span"); t.className = "navlabel"; t.textContent = label;
      const s = document.createElement("span"); s.className = "badge"; s.style.display = "none"; s.textContent = ""; s.id = "badge-" + key;
      b.appendChild(i); b.appendChild(t); b.appendChild(s); b.onclick = () => setTab(key); b.id = "nav-" + key; nav.appendChild(b);
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
        diagnose:["Diagnose","Ping, traceroute and connectivity checks"],
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
    function fmtDuration(seconds){
      if (seconds == null || isNaN(seconds)) return "—";
      const s = Math.floor(seconds);
      const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
      if (d > 0) return `${d}d ${h}h`;
      if (h > 0) return `${h}h ${m}m`;
      return `${m}m ${s % 60}s`;
    }
    function applyAuthGate(d){
      const loggedIn = !!(d.auth && d.auth.logged_in);
      authReady = loggedIn;
      const loginScreen = document.getElementById("loginScreen");
      const appShell = document.getElementById("appShell");
      if (loginScreen) loginScreen.classList.toggle("hidden", loggedIn);
      if (appShell) appShell.classList.toggle("locked", !loggedIn);
      const loginCompany = document.getElementById("loginCompanyCode");
      const loginUser = document.getElementById("loginUsername");
      if (loginCompany && !loginCompany.value) loginCompany.value = d.auth?.company_code || "";
      if (loginUser && !loginUser.value) loginUser.value = d.auth?.username || "admin";
      return loggedIn;
    }
    async function loginAgent(){
      const status = document.getElementById("loginStatus");
      const payload = {
        company_code: document.getElementById("loginCompanyCode").value.trim(),
        username: document.getElementById("loginUsername").value.trim(),
        password: document.getElementById("loginPassword").value
      };
      if (status) status.textContent = "Checking login with server...";
      try {
        const res = await fetch('/api/auth/login', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.ok === false) throw new Error(data.error || data.detail || `Login failed (${res.status})`);
        if (status) status.textContent = "Login successful. Opening agent...";
        setTimeout(load, 150);
      } catch (e) {
        if (status) status.textContent = e.message || String(e);
      }
    }
    async function logoutAgent(){
      await fetch('/api/auth/logout', {method:'POST'});
      authReady = false;
      await load();
    }
    function renderTopology(devices){
      const svg = document.getElementById("topologySvg");
      if (!svg) return;
      const W = 760, H = 320, cx = W / 2, cy = H / 2;
      const shown = devices.slice(0, 10);
      const n = shown.length;
      const parts = [`<defs><radialGradient id="topoHubGrad" cx="35%" cy="35%" r="65%"><stop offset="0%" stop-color="#63ffb9"/><stop offset="56%" stop-color="#1bbf7a"/><stop offset="100%" stop-color="#0d5b43"/></radialGradient></defs>`];
      if (!n) {
        parts.push(`<circle class="topo-hub" cx="${cx}" cy="${cy}" r="46"/><text class="topo-label" x="${cx}" y="${cy+5}" text-anchor="middle" font-size="12">Agent</text>`);
        parts.push(`<text class="topo-label" x="${cx}" y="${cy+30}" text-anchor="middle">No targets yet — add a device to populate the map</text>`);
        svg.innerHTML = parts.join("");
        return;
      }
      const radius = Math.min(W, H * 2) * 0.34;
      const nodePts = shown.map((item, i) => {
        const angle = (i / n) * Math.PI * 2 - Math.PI / 2;
        return { item, x: cx + radius * Math.cos(angle), y: cy + radius * Math.sin(angle) * 0.82 };
      });
      nodePts.forEach(p => {
        const cls = p.item.reachable === false ? "offline" : (p.item.reachable === true ? "" : "unknown");
        parts.push(`<line class="topo-link ${cls}" x1="${cx}" y1="${cy}" x2="${p.x}" y2="${p.y}"/>`);
      });
      parts.push(`<circle class="topo-hub" cx="${cx}" cy="${cy}" r="40"/><text class="topo-label" x="${cx}" y="${cy+4}" text-anchor="middle" font-size="12" font-weight="700" fill="#04231a">Agent</text>`);
      nodePts.forEach(p => {
        const color = p.item.reachable === false ? "#ff6b7b" : (p.item.reachable === true ? "#2de39c" : "#ffb84d");
        const label = String(p.item.host || "").slice(0, 16);
        parts.push(`<circle class="topo-node" cx="${p.x}" cy="${p.y}" r="16" fill="${color}" fill-opacity="0.85"/>`);
        const ty = p.y > cy ? p.y + 26 : p.y - 20;
        parts.push(`<text class="topo-label" x="${p.x}" y="${ty}" text-anchor="middle">${label}</text>`);
      });
      svg.innerHTML = parts.join("");
    }
    async function load(){
      const res = await fetch('/api/state');
      state.payload = await res.json();
      const d = state.payload;
      if (!applyAuthGate(d)) return;
      try {
        const remote = await fetch('/api/agent/devices').then(r => r.ok ? r.json() : null);
        if ((!d.inventory || !d.inventory.length) && remote && Array.isArray(remote.devices) && remote.devices.length) {
          d.local_devices = remote.devices.map(item => ({
            host: item.mgmt_ip || item.host,
            latency_ms: null,
            reachable: null,
            open_ports: [],
            interfaces: [],
            last_seen: item.last_seen || item.updated_at || null,
            type: item.device_type || item.vendor_family || item.vendor || 'device',
            source: 'server'
          }));
        }
      } catch (e) {}
      try {
        const remoteAlerts = await fetch('/api/agent/alerts').then(r => r.ok ? r.json() : null);
        if ((!d.alerts || !d.alerts.length) && remoteAlerts && Array.isArray(remoteAlerts.events)) {
          d.alerts = remoteAlerts.events.map(ev => ({
            id: ev.id,
            severity: ev.severity || ev.payload?.alert?.severity || 'info',
            event_type: ev.event_type || ev.payload?.alert?.issue || 'alert',
            host: ev.source || ev.payload?.device?.mgmt_ip || '-',
            status: ev.payload?.alert?.severity || ev.severity || '-',
            latency_ms: ev.payload?.alert?.latency_ms ?? ev.payload?.status?.icmp?.rtt_ms ?? '-',
            summary: ev.payload?.alert?.reason || ev.payload?.alert?.message || ev.payload?.alert?.recommendation || ev.payload?.summary || '-',
            payload: ev.payload || {},
          }));
        }
      } catch (e) {}
      const syncLabel = d.last_sync_at ? new Date(d.last_sync_at).toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'}) : "—";
      document.getElementById("mConnection").textContent = d.online ? "Online" : "Offline";
      document.getElementById("mConnectionSub").textContent = d.online ? "All systems operational" : (d.last_error || "Connection issue detected");
      document.getElementById("mLastSync").textContent = syncLabel;
      document.getElementById("mSyncSub").textContent = "Auto sync enabled";
      document.getElementById("mQueue").textContent = String(d.queue_depth ?? 0);
      document.getElementById("mModel").textContent = "v" + String(d.model_version || "1");
      const criticalAlertCount = (d.alerts || []).filter(i => (i.severity || '').toLowerCase() === 'critical' || String(i.event_type || '').startsWith('alert.')).length;
      document.getElementById("mAlerts").textContent = String(criticalAlertCount);
      document.getElementById("mAlertsSub").textContent = criticalAlertCount ? `${criticalAlertCount} critical` : "No critical alerts";
      document.getElementById("mTargets").textContent = String((d.local_targets || []).length || (d.inventory || []).length || 0);
      document.getElementById("mLatency").textContent = d.avg_latency_ms != null ? `${d.avg_latency_ms} ms` : "—";
      document.getElementById("mReachability").textContent = d.reachability_pct != null ? `${d.reachability_pct}%` : "—";
      document.getElementById("mUptime").textContent = fmtDuration(d.agent_uptime_seconds);
      document.getElementById("profileName").textContent = d.agent_name || "Sagar";
      document.getElementById("profileState").textContent = d.online ? "Online" : "Offline";
      document.getElementById("scanStatus").textContent = d.local_scan_status || "Idle";
      document.getElementById("scanTargets").textContent = (d.local_targets || []).length;
      document.getElementById("scanOnline").textContent = d.metrics?.reachable_hosts ?? 0;
      document.getElementById("serverUrl").value = d.server_url || "";
      document.getElementById("companyId").value = d.company_id || "";
      document.getElementById("agentNameInput").value = d.agent_name || "";
      document.getElementById("discoveryCidr").value = d.discovery_cidr || "";
      document.getElementById("localTargets").value = (d.local_targets || []).join(", ");
      document.getElementById("snapshotBox").textContent = JSON.stringify(d.snapshot || {}, null, 2);
      const diagIncoming = document.getElementById("diagIncomingStatus");
      if (diagIncoming) {
        const snap = d.snapshot || {};
        if (snap.incoming_ping_listener_active) {
          diagIncoming.textContent = "Incoming ping capture: active (devices pinging this PC, including over VPN, are auto-added)";
        } else if (snap.incoming_ping_listener_error) {
          diagIncoming.textContent = "Incoming ping capture: inactive - run the agent as Administrator/root to enable it";
        } else {
          diagIncoming.textContent = "Incoming ping capture: starting...";
        }
      }
      const overview = document.getElementById("historyList");
      overview.innerHTML = "";
      (d.recent_events || []).slice(0, 6).forEach(ev => {
        const item = document.createElement("div");
        item.className = "pill";
        item.textContent = `${ev.kind || "event"} · ${ev.created_at || "-"} · ${JSON.stringify(ev.payload || {}).slice(0, 120)}`;
        overview.appendChild(item);
      });
      const dev = document.getElementById("deviceList");
      dev.innerHTML = "";
      const liveInventory = new Map();
      (d.inventory || []).forEach(item => {
        const host = String(item.host || item.mgmt_ip || "").trim();
        const key = hostKey(host);
        if (key) liveInventory.set(key, item);
      });
      const targets = (d.local_devices || d.local_targets || []).map(item => typeof item === 'string' ? ({ host: item, source: "saved", reachable: null, latency_ms: null, open_ports: [], interfaces: [], last_seen: null, type: "saved target" }) : ({
        host: item.host || item.mgmt_ip,
        source: "saved",
        reachable: item.reachable ?? null,
        latency_ms: item.latency_ms ?? null,
        open_ports: item.open_ports || item.interfaces || [],
        interfaces: item.interfaces || item.open_ports || [],
        last_seen: item.last_seen || null,
        last_probed_at: item.last_probed_at || null,
        probe_error: item.probe_error || null,
        type: item.device_type || item.vendor_family || item.vendor || "saved target",
        vendor: item.vendor || "",
        vendor_family: item.vendor_family || "",
        model: item.model || "",
        protocol: item.protocol || item.access_protocol || "auto",
        access_protocol: item.access_protocol || "auto",
        username: item.username || "",
      }));
      // Merge saved devices with any matching live-inventory record for the
      // same host, so a device that has been added AND is being actively
      // polled shows its real online/offline state and interfaces instead
      // of the live data being silently discarded.
      const mergedDevices = [];
      const seenHosts = new Set();
      targets.forEach(item => {
        const host = String(item.host || "").trim();
        const key = hostKey(host);
        if (!key || seenHosts.has(key)) return;
        seenHosts.add(key);
        const live = liveInventory.get(key);
        if (live) {
          mergedDevices.push({
            ...item,
            source: "live",
            reachable: live.reachable ?? item.reachable ?? null,
            latency_ms: live.latency_ms ?? item.latency_ms ?? null,
            open_ports: Array.isArray(live.open_ports) ? live.open_ports : [],
            interfaces: Array.isArray(live.interfaces) ? live.interfaces : [],
            last_seen: live.last_seen || item.last_seen || null,
          });
        } else {
          mergedDevices.push(item);
        }
      });
      liveInventory.forEach((item, key) => {
        if (seenHosts.has(key)) return;
        seenHosts.add(key);
        mergedDevices.push({ ...item, host: item.host || item.mgmt_ip || key, source: "live" });
      });
      const totalDeviceCount = mergedDevices.length;
      const onlineDeviceCount = mergedDevices.filter(item => item.reachable === true).length;
      const mDevicesEl = document.getElementById("mDevices");
      const mDevicesSubEl = document.getElementById("mDevicesSub");
      if (mDevicesEl) mDevicesEl.textContent = String(totalDeviceCount);
      if (mDevicesSubEl) mDevicesSubEl.textContent = `Online: ${onlineDeviceCount}`;
      lastMergedDevices = mergedDevices;
      const deviceByHost = {};
      mergedDevices.forEach(item => { if (item.host) deviceByHost[String(item.host)] = item; });
      window.__deviceByHost = deviceByHost;
      const diagSelect = document.getElementById("diagDeviceSelect");
      if (diagSelect) {
        const prevValue = diagSelect.value;
        diagSelect.innerHTML = '<option value="">Pick a saved device (optional)</option>';
        mergedDevices.forEach(item => {
          const host = String(item.host || "").trim();
          if (!host) return;
          const opt = document.createElement("option");
          opt.value = host;
          opt.textContent = `${host}${item.type ? " · " + item.type : ""}`;
          diagSelect.appendChild(opt);
        });
        diagSelect.value = prevValue;
      }
      renderTopology(mergedDevices);
      mergedDevices.forEach(item => {
        const row = document.createElement("div");
        row.className = "row clickable";
        // Tri-state status: true -> online, false -> offline, null/undefined
        // (never probed yet) -> checking/unknown. This is the actual signal
        // the person needs right after adding a device.
        let reachClass = 'status-warn';
        let reachText = 'checking...';
        let dotClass = 'warn';
        if (item.reachable === true) { reachClass = 'status-good'; reachText = 'online'; dotClass = 'good'; }
        else if (item.reachable === false) { reachClass = 'status-bad'; reachText = 'offline'; dotClass = 'bad'; }
        const openPorts = Array.isArray(item.interfaces) && item.interfaces.length ? item.interfaces : (Array.isArray(item.open_ports) ? item.open_ports : []);
        const protocolText = item.protocol || item.access_protocol || 'auto';
        const statusBits = [
          protocolText ? `proto ${protocolText}` : '',
          item.vendor ? `vendor ${item.vendor}` : '',
          item.model ? `model ${item.model}` : '',
        ].filter(Boolean).join(' ? ');
        const ifaceCount = openPorts.length;
        const portText = ifaceCount ? `${ifaceCount} interface${ifaceCount === 1 ? '' : 's'} · click to view` : (item.reachable === null || item.reachable === undefined ? 'not probed yet' : 'no interface data');
        row.innerHTML = `<div><strong>${item.host}</strong><div class="small">${item.type || "device"} ? ${statusBits}</div></div>
          <div class="${reachClass}"><span class="status-dot ${dotClass}"></span>${reachText}</div>
          <div>${item.latency_ms ?? '-'}</div>
          <div>${portText}</div>
          <div class="small">${item.last_seen || item.probe_error || '-'}</div>`;
        row.onclick = () => openDeviceModal(item.host);
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
          <div>${item.port || item.status || '-'}</div>
          <div>${item.latency_ms ?? '-'}</div>
          <div>${item.summary || '-'}</div>`;
        alerts.appendChild(row);
      });
      if (!alerts.children.length) alerts.innerHTML = '<div class="small">No alerts right now.</div>';
      const liveAlerts = d.alerts || [];
      const home = document.getElementById("homeAlerts");
      if (home) {
        if (!liveAlerts.length) {
          home.innerHTML = '<div class="alert-ok"><div class="okmark">✔</div><div><div style="font-size:18px;font-weight:800">All Clear! 🎉</div><div class="small">No critical alerts at the moment</div><div class="small">Your network is secure and healthy.</div></div></div>';
        } else {
          home.innerHTML = "";
          liveAlerts.slice(0, 5).forEach(item => {
            const sev = String(item.severity || 'info').toLowerCase();
            const color = sev === 'critical' ? 'var(--bad)' : sev === 'warning' ? 'var(--warn)' : 'var(--blue)';
            const row = document.createElement("div");
            row.className = "row";
            row.style.borderLeft = `3px solid ${color}`;
            row.innerHTML = `<div><strong>${item.host || '-'}</strong><div class="small">${item.event_type || 'alert'} ? ${item.summary || ''}</div></div><div style="color:${color};text-transform:capitalize">${sev}</div>`;
            home.appendChild(row);
          });
        }
      }
      const bell = document.getElementById("bellBadge");
      if (bell) {
        if (liveAlerts.length) { bell.style.display = "grid"; bell.textContent = String(liveAlerts.length); }
        else { bell.style.display = "none"; }
      }
      const alertsBadge = document.getElementById("badge-alerts");
      if (alertsBadge) {
        if (liveAlerts.length) { alertsBadge.style.display = "inline-block"; alertsBadge.textContent = String(liveAlerts.length); }
        else { alertsBadge.style.display = "none"; }
      }
      const agents = document.getElementById("agentState");
      agents.innerHTML = "";
      (d.agents || []).forEach(item => {
        const row = document.createElement("div");
        row.className = "pill";
        row.textContent = `${item.name} · ${item.status} · ${item.note}`;
        agents.appendChild(row);
      });
      const agentGrid = document.getElementById("agentGrid");
      if (agentGrid) {
        agentGrid.innerHTML = "";
        (d.agents || []).forEach((item, idx) => {
          const card = document.createElement("div");
          card.className = "agent-card";
          const load = Number(item.load ?? (80 - idx * 10));
          card.innerHTML = `<div class="head"><div class="name">${item.name}</div><div class="state">${item.status || 'online'}</div></div><div class="note">${item.context || item.note || 'Realtime analysis active'}</div><div class="bar"><span style="width:${Math.max(12, Math.min(100, load))}%"></span></div><div class="foot"><span>${item.note || 'Live'}</span><span>Load ${Math.max(10, Math.min(100, load))}%</span></div>`;
          agentGrid.appendChild(card);
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
    async function action(kind){ await fetch('/api/action/' + kind, {method:'POST'}); setTimeout(load, 500); }
    async function runDiagnostic(kind){
      const target = document.getElementById("diagTarget").value.trim();
      const statusEl = document.getElementById("diagStatus");
      const outEl = document.getElementById("diagOutput");
      if (!target) { statusEl.textContent = "Enter an IP address or hostname first."; return; }
      const labels = {ping:"Pinging", tracert:"Tracing route to", dns:"Resolving", ports:"Checking ports on"};
      statusEl.textContent = `${labels[kind] || "Testing"} ${target}...`;
      outEl.textContent = "Running...";
      try {
        const res = await fetch('/api/diagnose', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({type: kind, target})});
        const result = await res.json();
        outEl.textContent = result.output || "No output.";
        statusEl.textContent = result.ok === false ? `Failed: ${result.error || "unknown error"}` : (result.success ? `${target}: success` : `${target}: no response`);
      } catch (e) {
        outEl.textContent = String(e);
        statusEl.textContent = "Diagnostic request failed.";
      }
    }
    function askAssistant(raw){
      const q = String(raw || "").toLowerCase().trim();
      const box = document.getElementById("assistantAnswer");
      if (!box) return;
      const d = state.payload || {};
      const inv = lastMergedDevices.length ? lastMergedDevices : (d.inventory || []);
      const targets = d.local_targets || [];
      const offline = inv.filter(i => i.reachable === false);
      let answer = "";
      if (!q) { box.style.display = "none"; return; }
      if (q.includes("offline")) {
        answer = offline.length
          ? `${offline.length} offline device(s): ${offline.map(i => i.host).join(", ")}`
          : "No devices are currently reporting offline.";
      } else if (q.includes("bandwidth")) {
        answer = "This agent doesn't collect an aggregate bandwidth counter yet — real signals available: " +
          `avg latency ${d.avg_latency_ms != null ? d.avg_latency_ms + " ms" : "n/a"}, ` +
          `reachability ${d.reachability_pct != null ? d.reachability_pct + "%" : "n/a"} across ${inv.length} probed host(s).`;
      } else if (q.includes("connectivity") || q.includes("test")) {
        answer = "Running a fresh poll of all targets now — results will update in a few seconds.";
        fetch('/api/action/poll', {method:'POST'}).then(() => setTimeout(load, 1500));
      } else if (q.includes("security") || q.includes("alert")) {
        const alerts = d.alerts || [];
        answer = alerts.length
          ? `${alerts.length} open alert(s). Most recent: ${alerts[0].event_type || 'alert'} on ${alerts[0].host || 'unknown host'} (${alerts[0].severity || 'info'}).`
          : "No open alerts. Network looks healthy.";
      } else if (q.includes("device")) {
        answer = `Tracking ${targets.length} saved target(s) and ${inv.length} live inventory record(s).`;
      } else {
        answer = `I can report on live agent data: devices (${targets.length}), alerts (${(d.alerts||[]).length}), queue depth (${d.queue_depth ?? 0}), reachability (${d.reachability_pct ?? '—'}%). Try asking about "offline devices" or "alerts".`;
      }
      box.style.display = "block";
      box.textContent = answer;
      document.getElementById("assistantInput").value = "";
    }
    function runGlobalSearch(term){
      const q = String(term || "").toLowerCase().trim();
      if (!q) return;
      setTab("devices");
      setTimeout(() => {
        document.querySelectorAll("#deviceList .row").forEach(row => {
          row.style.display = row.textContent.toLowerCase().includes(q) ? "" : "none";
        });
      }, 50);
    }
    let currentModalHost = null;
    let currentIfaceTab = 'uplink';
    function setIfaceTab(key){
      currentIfaceTab = key;
      document.querySelectorAll('#ifaceTabbar .iface-tab-btn').forEach(b => b.classList.toggle('active', b.dataset.key === key));
      document.querySelectorAll('.iface-panel').forEach(p => p.classList.toggle('active', p.id === 'ifacePanel-' + key));
    }
    function classifyIface(name){
      const n = String(name || '').toLowerCase();
      if (n.includes('vlan')) return 'vlan';
      if (n.includes('onu')) return 'onu';
      if (n.includes('pon') || n.includes('olt')) return 'pon';
      if (/^(uplink|wan|trunk|ge|gi|xge|xe|te|ether|fastethernet|eth)/.test(n)) return 'uplink';
      return 'uplink';
    }
    function ifaceBadgeClass(status){
      const s = String(status || '').toLowerCase();
      if (s === 'up') return 'up';
      if (s === 'down') return 'down';
      if (s === 'admin_down') return 'admin_down';
      return 'unknown';
    }
    function fmtBytes(n){
      const num = Number(n);
      if (!isFinite(num) || num <= 0) return '0 B';
      const units = ['B','KB','MB','GB','TB'];
      let i = 0; let v = num;
      while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
      return `${v.toFixed(v < 10 && i > 0 ? 1 : 0)} ${units[i]}`;
    }
    function renderDeviceModal(item){
      if (!item) return;
      const title = document.getElementById("devModalTitle");
      const subtitle = document.getElementById("devModalSubtitle");
      const meta = document.getElementById("devModalMeta");
      const note = document.getElementById("devModalIfaceNote");
      const groupKeys = ['uplink', 'pon', 'onu', 'vlan'];
      const bodies = {};
      const empties = {};
      groupKeys.forEach(k => {
        bodies[k] = document.getElementById('devModalIfaceBody-' + k);
        empties[k] = document.getElementById('ifaceEmpty-' + k);
        if (bodies[k]) bodies[k].innerHTML = '';
      });
      title.textContent = item.host || currentModalHost || 'Device';
      let statusLabel = 'checking...'; let statusColor = 'var(--warn)';
      if (item.reachable === true) { statusLabel = 'online'; statusColor = 'var(--good)'; }
      else if (item.reachable === false) { statusLabel = 'offline'; statusColor = 'var(--bad)'; }
      subtitle.innerHTML = `<span style="color:${statusColor};font-weight:700;text-transform:uppercase;font-size:11px;letter-spacing:.04em">${statusLabel}</span> · ${item.type || item.device_type || 'device'}${item.last_seen ? ' · last seen ' + item.last_seen : ''}`;
      const chips = [
        item.vendor ? `Vendor: ${item.vendor}` : '',
        item.model ? `Model: ${item.model}` : '',
        (item.protocol || item.access_protocol) ? `Protocol: ${item.protocol || item.access_protocol}` : '',
        item.latency_ms != null ? `Latency: ${item.latency_ms} ms` : '',
      ].filter(Boolean);
      meta.innerHTML = chips.map(c => `<span class="pill">${c}</span>`).join('');
      const ifaces = Array.isArray(item.interfaces) && item.interfaces.length ? item.interfaces : (Array.isArray(item.open_ports) ? item.open_ports : []);
      if (!ifaces.length) {
        note.textContent = item.probe_error ? `Could not read interfaces: ${item.probe_error}` : 'No interface data yet — click "Refresh now" to probe this device.';
      } else {
        note.textContent = `${ifaces.length} interface${ifaces.length === 1 ? '' : 's'} reported by the device`;
        ifaces.forEach(p => {
          const name = typeof p === 'object' ? (p.name || p.port || p.label || '-') : String(p);
          const status = typeof p === 'object' ? (p.status || '-') : '-';
          const rx = typeof p === 'object' ? (p['rx-byte'] ?? p.rx_byte ?? p.rx ?? 0) : 0;
          const tx = typeof p === 'object' ? (p['tx-byte'] ?? p.tx_byte ?? p.tx ?? 0) : 0;
          const group = (typeof p === 'object' && p.iface_group) ? p.iface_group : classifyIface(name);
          const key = groupKeys.includes(group) ? group : 'uplink';
          const tr = document.createElement('tr');
          tr.innerHTML = `<td><strong>${name}</strong></td><td><span class="iface-badge ${ifaceBadgeClass(status)}">${status}</span></td><td>${fmtBytes(rx)}</td><td>${fmtBytes(tx)}</td>`;
          if (bodies[key]) bodies[key].appendChild(tr);
        });
      }
      groupKeys.forEach(k => {
        const count = bodies[k] ? bodies[k].children.length : 0;
        const cntEl = document.getElementById('ifaceCnt-' + k);
        if (cntEl) cntEl.textContent = String(count);
        if (empties[k]) empties[k].style.display = count ? 'none' : '';
      });
      setIfaceTab(currentIfaceTab);
    }
    function openDeviceModal(host){
      if (!host) return;
      currentModalHost = host;
      currentIfaceTab = 'uplink';
      const item = (window.__deviceByHost || {})[host] || { host };
      renderDeviceModal(item);
      document.getElementById("deviceModal").classList.add("open");
      refreshDeviceModal();
    }
    function closeDeviceModal(){
      currentModalHost = null;
      document.getElementById("deviceModal").classList.remove("open");
    }
    async function refreshDeviceModal(){
      if (!currentModalHost) return;
      const host = currentModalHost;
      try {
        const r = await fetch('/api/agent/device/probe', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ host })
        });
        const data = await r.json().catch(() => ({}));
        if (currentModalHost !== host) return; // modal switched/closed while probing
        if (data && data.ok && data.device) {
          renderDeviceModal(data.device);
          if (window.__deviceByHost) window.__deviceByHost[host] = { ...window.__deviceByHost[host], ...data.device };
        }
      } catch (e) {
        // Keep showing whatever cached data we already rendered.
      }
      setTimeout(load, 300);
    }
    function openAddDeviceModal(){
      document.getElementById("targetInput").value = "";
      document.getElementById("vendorInput").value = "";
      document.getElementById("modelInput").value = "";
      document.getElementById("protocolInput").value = "auto";
      document.getElementById("snmpCommunityInput").value = "";
      document.getElementById("usernameInput").value = "";
      document.getElementById("passwordInput").value = "";
      const statusBox = document.getElementById("deviceAddStatus");
      if (statusBox) statusBox.textContent = "Ready to add a device.";
      document.getElementById("addDeviceModal").classList.add("open");
    }
    function closeAddDeviceModal(){
      document.getElementById("addDeviceModal").classList.remove("open");
    }
    async function submitAddDevice(){
      const host = document.getElementById("targetInput").value.trim();
      if (!host) return;
      const statusBox = document.getElementById("deviceAddStatus");
      if (statusBox) statusBox.textContent = `Adding ${host}...`;
      try {
        const r = await fetch('/api/agent/device', {
          method:'POST',
          headers:{'Content-Type':'application/json'},
          body:JSON.stringify({
            mgmt_ip: host,
            name: host,
            vendor: document.getElementById("vendorInput").value.trim() || 'Auto',
            vendor_family: (document.getElementById("vendorInput").value.trim() || 'generic').toLowerCase(),
            model: document.getElementById("modelInput").value.trim() || 'Auto',
            device_type: 'switch',
            access_protocol: document.getElementById("protocolInput").value.trim() || 'auto',
            snmp_community: document.getElementById("snmpCommunityInput").value.trim() || 'public',
            username: document.getElementById("usernameInput").value.trim() || null,
            password: document.getElementById("passwordInput").value.trim() || null
          })
        });
        const data = await r.json().catch(() => ({}));
        if (!r.ok || data.ok === false) throw new Error(data.error || `HTTP ${r.status}`);
        if (statusBox) {
          const reach = data && data.probe && data.probe.reachable ? "reachable" : "added";
          const proto = data && data.probe ? (data.probe.protocol || "auto") : "auto";
          statusBox.textContent = `${host} added ? ${reach} ? ${proto}`;
        }
      } catch (e) {
        if (statusBox) statusBox.textContent = `Could not add ${host}: ${e.message || e}`;
        return; // keep the popup open so the person can fix the input and retry
      }
      try {
        await fetch('/api/targets', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({host})});
      } catch (e) {}
      closeAddDeviceModal();
      setTimeout(load, 150);
    }
    async function removeCurrentDevice(){
      const host = currentModalHost;
      if (!host) return;
      if (!window.confirm(`Remove ${host} from Devices?`)) return;
      try {
        const r = await fetch('/api/agent/device', {
          method:'DELETE',
          headers:{'Content-Type':'application/json'},
          body:JSON.stringify({mgmt_ip: host})
        });
        const data = await r.json().catch(() => ({}));
        if (!r.ok || data.ok === false) throw new Error(data.error || `HTTP ${r.status}`);
      } catch (e) {}
      try {
        await fetch('/api/targets', {method:'DELETE', headers:{'Content-Type':'application/json'}, body:JSON.stringify({host})});
      } catch (e) {}
      closeDeviceModal();
      setTimeout(load, 150);
    }
    async function saveSettings(){ const payload = {server_url: document.getElementById("serverUrl").value.trim(), company_id: parseInt(document.getElementById("companyId").value || "1", 10), name: document.getElementById("agentNameInput").value.trim(), discovery_cidr: document.getElementById("discoveryCidr").value.trim(), local_targets: document.getElementById("localTargets").value.split(",").map(s=>s.trim()).filter(Boolean), local_devices: (state.payload.local_devices || [])}; await fetch('/api/settings', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)}); try { await fetch('/api/agent/workspace', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({local_targets: payload.local_targets, local_devices: payload.local_devices, settings:{discovery_cidr: payload.discovery_cidr}})}); } catch(e) {} setTimeout(load, 300); }
    setTab('dashboard'); load(); setInterval(load, 1000);
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
        self._started_at = time.time()

    def _snapshot(self) -> dict[str, Any]:
        return self.agent.build_snapshot()

    def _state_payload(self) -> dict[str, Any]:
        metrics = dict(self.agent.last_local_metrics or {})
        queue_depth = len(self.cache.pending_commands())
        target_count = len(getattr(self.settings, "local_targets", []) or [])
        reachable = int(metrics.get("reachable_hosts") or 0)
        unreachable = int(metrics.get("unreachable_hosts") or 0)
        alert_count = len(self.agent.last_local_alerts or [])
        inventory = list(self.agent.last_local_inventory or [])
        avg_latency = None
        latencies = [float(item.get("latency_ms")) for item in inventory if isinstance(item, dict) and item.get("latency_ms") is not None]
        if latencies:
            avg_latency = round(sum(latencies) / len(latencies), 1)
        inventory_by_host = {
            _host_key(item.get("host") or item.get("mgmt_ip")): item
            for item in inventory
            if isinstance(item, dict) and _host_key(item.get("host") or item.get("mgmt_ip"))
        }
        live_local_devices: list[dict[str, Any]] = []
        for item in list(getattr(self.settings, "local_devices", []) or []):
            if not isinstance(item, dict):
                continue
            host = str(item.get("host") or item.get("mgmt_ip") or "").strip()
            if not host:
                continue
            live = inventory_by_host.get(_host_key(host))
            base = {**item, "host": host, "mgmt_ip": host}
            if isinstance(live, dict):
                summary = live.get("summary") if isinstance(live.get("summary"), dict) else {}
                is_reachable = bool(live.get("reachable"))
                base["reachable"] = is_reachable
                base["latency_ms"] = live.get("latency_ms")
                base["protocol"] = live.get("protocol") or base.get("access_protocol")
                base["last_probed_at"] = live.get("observed_at") or base.get("last_probed_at")
                if is_reachable:
                    ports = summary.get("port_details") if isinstance(summary.get("port_details"), list) else []
                    base["open_ports"] = ports
                    base["interfaces"] = ports
                    base["summary"] = summary
                else:
                    base["open_ports"] = []
                    base["interfaces"] = []
                    base["summary"] = {"ports": 0, "port_details": [], "note": "Latest live poll says this device is unreachable."}
                    base["offline_reason"] = live.get("ping_output") or "Host unreachable from this agent"
            else:
                # Saved workspace data is not a live status. Never expose old
                # online/interface values from cache as current truth.
                base["reachable"] = None
                base["latency_ms"] = None
                base["open_ports"] = []
                base["interfaces"] = []
                base["summary"] = {"ports": 0, "port_details": [], "note": "Waiting for the next live local poll."}
            live_local_devices.append(base)
        critical_alerts = sum(1 for item in (self.agent.last_local_alerts or []) if str(item.get("severity") or "").lower() == "critical")
        total_probed = reachable + unreachable
        reachability_pct = round((reachable / total_probed) * 100, 1) if total_probed else None
        agent_uptime_seconds = round(time.time() - self._started_at, 1)
        return {
            "online": bool(self.agent.last_online_state),
            "server_url": self.settings.server_url,
            "company_id": self.settings.company_id,
            "agent_name": self.settings.name,
            "auth": {
                "logged_in": bool(getattr(self.settings, "auth_token", None)),
                "username": getattr(self.settings, "auth_username", None),
                "role": getattr(self.settings, "auth_role", None),
                "company_id": getattr(self.settings, "company_id", None),
                "company_code": getattr(self.settings, "company_code", None),
            },
            "model_version": self.settings.model_version,
            "queue_depth": queue_depth,
            "last_sync_at": self.agent.last_sync_at,
            "last_error": self.cache.get_last_error(),
            "local_targets": list(getattr(self.settings, "local_targets", []) or []),
            "local_devices": live_local_devices,
            "discovery_cidr": getattr(self.settings, "discovery_cidr", None),
            "last_discovery_at": self.agent.last_discovery_at,
            "model_cache_present": self.cache.latest_model_bundle() is not None,
            "snapshot": self._snapshot(),
            "metrics": metrics,
            "inventory": inventory,
            "alerts": list(self.agent.active_local_alerts or self.agent.last_local_alerts or []),
            "active_alerts": list(self.agent.active_local_alerts or []),
            "critical_alerts": critical_alerts,
            "avg_latency_ms": avg_latency,
            "reachability_pct": reachability_pct,
            "agent_uptime_seconds": agent_uptime_seconds,
            "last_inventory_count": len(inventory),
            "agents": [
                {"name": "Monitoring Agent", "status": "online", "note": f"{target_count} targets, {reachable} reachable", "load": max(10, min(100, 82 - unreachable * 10)), "context": "signal tracking"},
                {"name": "Diagnosis Agent", "status": "online", "note": f"{unreachable} unresolved host(s)", "load": max(10, min(100, 76 - max(0, unreachable - 1) * 8)), "context": "root cause analysis"},
                {"name": "Planning Agent", "status": "online", "note": f"{queue_depth} pending command(s)", "load": max(10, min(100, 68 - queue_depth * 2)), "context": "safe remediation"},
                {"name": "Verification Agent", "status": "online", "note": f"{critical_alerts} critical alert(s)", "load": max(10, min(100, 60 + (1 if reachable else 0) * 8)), "context": "policy gate"},
                {"name": "Execution Agent", "status": "online", "note": f"{alert_count} alert feed item(s)", "load": max(10, min(100, 48 + (1 if self.agent.last_online_state else 0) * 12)), "context": "remediation path"},
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
                if parsed.path == "/api/agent/devices":
                    try:
                        _json_response(self, parent.client.list_devices(parent.settings))
                    except Exception as exc:
                        _json_response(self, {"error": str(exc), "devices": []})
                    return
                if parsed.path == "/api/agent/alerts":
                    try:
                        _json_response(self, parent.client.list_alerts(parent.settings))
                    except Exception as exc:
                        _json_response(self, {"error": str(exc), "events": []})
                    return
                _json_response(self, {"error": "not found"}, 404)
            def do_POST(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                length = int(self.headers.get("Content-Length", "0") or "0")
                body = self.rfile.read(length).decode("utf-8") if length else "{}"
                data = json.loads(body or "{}")
                if parsed.path == "/api/auth/login":
                    try:
                        username = str(data.get("username") or "").strip()
                        password = str(data.get("password") or "")
                        server_url = str(data.get("server_url") or parent.settings.server_url or "").strip().rstrip("/")
                        company_code = str(data.get("company_code") or "").strip()
                        if not server_url:
                            _json_response(self, {"ok": False, "error": "Server URL is required"}, 400); return
                        if not username or not password:
                            _json_response(self, {"ok": False, "error": "Username and password are required"}, 400); return
                        login_result = parent.client.login(parent.settings, username, password, company_code or None, server_url)
                        if not login_result.get("success"):
                            _json_response(self, {"ok": False, "error": login_result.get("detail") or "Login failed"}, 401); return
                        parent.settings.server_url = server_url
                        parent.settings.company_id = int(login_result.get("company_id") or parent.settings.company_id or 1)
                        parent.settings.auth_token = str(login_result.get("token") or "")
                        parent.settings.auth_username = str(login_result.get("username") or username)
                        parent.settings.auth_role = str(login_result.get("role") or "user")
                        parent.settings.company_code = company_code or None
                        parent.cache.save_agent_profile(parent.settings)
                        registration_warning = None
                        try:
                            if not parent.settings.agent_id or not parent.settings.agent_key:
                                parent.client.register(parent.settings)
                        except Exception as exc:
                            registration_warning = str(exc)
                        try:
                            parent.agent.request_sync_once()
                        except Exception:
                            pass
                        _json_response(self, {"ok": True, "auth": parent._state_payload().get("auth"), "registration_warning": registration_warning}); return
                    except Exception as exc:
                        _json_response(self, {"ok": False, "error": str(exc)}, 401); return
                if parsed.path == "/api/auth/logout":
                    parent.settings.auth_token = None
                    parent.settings.auth_username = None
                    parent.settings.auth_role = None
                    parent.cache.save_agent_profile(parent.settings)
                    _json_response(self, {"ok": True}); return
                if parsed.path == "/api/action/sync":
                    parent.agent.request_sync_once(); _json_response(self, {"ok": True}); return
                if parsed.path == "/api/action/heartbeat":
                    parent.agent.request_heartbeat_once(); _json_response(self, {"ok": True}); return
                if parsed.path == "/api/action/poll":
                    parent.agent.request_local_poll_once(); _json_response(self, {"ok": True}); return
                if parsed.path == "/api/diagnose":
                    target = str(data.get("target") or "").strip()
                    kind = str(data.get("type") or "ping").strip().lower()
                    if not target:
                        _json_response(self, {"ok": False, "error": "target is required"}, 400); return
                    try:
                        result = parent.agent.poller.diagnose(kind, target)
                        _json_response(self, {"ok": True, "type": kind, **result}); return
                    except Exception as exc:
                        _json_response(self, {"ok": False, "error": str(exc)}); return
                if parsed.path == "/api/settings":
                    parent.settings.server_url = data.get("server_url") or parent.settings.server_url
                    parent.settings.company_id = int(data.get("company_id") or parent.settings.company_id)
                    parent.settings.name = data.get("name") or parent.settings.name
                    parent.settings.discovery_cidr = data.get("discovery_cidr") or parent.settings.discovery_cidr
                    targets = data.get("local_targets")
                    if isinstance(targets, list):
                        parent.settings.local_targets = [str(v) for v in targets if str(v).strip()]
                    devices = data.get("local_devices")
                    if isinstance(devices, list):
                        cleaned_devices = []
                        for item in devices:
                            if not isinstance(item, dict):
                                continue
                            host = str(item.get("host") or item.get("mgmt_ip") or "").strip()
                            if not host:
                                continue
                            cleaned_devices.append({**item, "host": host, "mgmt_ip": host})
                        parent.settings.local_devices = cleaned_devices
                        parent.cache.save_local_devices(cleaned_devices)
                    parent.cache.save_agent_profile(parent.settings); _json_response(self, {"ok": True}); return
                if parsed.path == "/api/agent/device/probe":
                    try:
                        host = str(data.get("mgmt_ip") or data.get("host") or "").strip()
                        if not host:
                            _json_response(self, {"ok": False, "error": "host is required"}, 400); return
                        devices = parent.cache.load_local_devices()
                        device_record = next(
                            (item for item in devices if str(item.get("host") or item.get("mgmt_ip") or "").strip() == host),
                            None,
                        ) or {"host": host, "mgmt_ip": host}
                        probe = parent.agent.poller.probe_device(device_record)
                        probe_summary = probe.get("summary") if isinstance(probe, dict) and isinstance(probe.get("summary"), dict) else {}
                        is_reachable = bool(probe.get("reachable")) if isinstance(probe, dict) else False
                        device_record["reachable"] = is_reachable
                        device_record["latency_ms"] = probe.get("latency_ms") if isinstance(probe, dict) else None
                        device_record["protocol"] = (probe.get("protocol") if isinstance(probe, dict) else None) or device_record.get("access_protocol")
                        device_record["open_ports"] = probe_summary.get("port_details") if is_reachable else []
                        device_record["interfaces"] = device_record["open_ports"]
                        device_record["summary"] = probe_summary if is_reachable else {"ports": 0, "port_details": [], "note": "Latest local probe says this device is unreachable."}
                        from datetime import datetime, timezone
                        now_iso = datetime.now(timezone.utc).isoformat()
                        device_record["last_probed_at"] = now_iso
                        if device_record["reachable"]:
                            device_record["last_seen"] = now_iso
                        parent.cache.add_local_device(device_record)
                        parent.settings.local_devices = parent.cache.load_local_devices()
                        if isinstance(probe, dict):
                            live_probe = {**probe, "host": host, "mgmt_ip": host}
                            current_inventory = [
                                item for item in list(parent.agent.last_local_inventory or [])
                                if _host_key(item.get("host") or item.get("mgmt_ip")) != _host_key(host)
                            ]
                            current_inventory.insert(0, live_probe)
                            parent.agent.last_local_inventory = current_inventory
                        _json_response(self, {"ok": True, "host": host, "probe": probe, "device": device_record}); return
                    except Exception as exc:
                        _json_response(self, {"ok": False, "error": str(exc)}); return
                if parsed.path == "/api/agent/device":
                    try:
                        host = str(data.get("mgmt_ip") or data.get("host") or "").strip()
                        created = {}
                        probe = None
                        device_record = None
                        if host:
                            device_record = {
                                "host": host,
                                "mgmt_ip": host,
                                "name": str(data.get("name") or host).strip() or host,
                                "vendor": str(data.get("vendor") or "").strip(),
                                "vendor_family": str(data.get("vendor_family") or "").strip().lower(),
                                "model": str(data.get("model") or "").strip(),
                                "device_type": str(data.get("device_type") or "switch").strip().lower(),
                                "access_protocol": str(data.get("access_protocol") or "auto").strip().lower(),
                                "username": data.get("username"),
                                "password": data.get("password"),
                                "snmp_community": data.get("snmp_community"),
                                "location": data.get("location"),
                            }
                            try:
                                probe = parent.agent.poller.probe_device(device_record)
                                if isinstance(probe, dict):
                                    summary = probe.get("summary") if isinstance(probe.get("summary"), dict) else {}
                                    is_reachable = bool(probe.get("reachable"))
                                    device_record["reachable"] = is_reachable
                                    device_record["latency_ms"] = probe.get("latency_ms")
                                    device_record["open_ports"] = summary.get("port_details") if is_reachable else []
                                    device_record["interfaces"] = device_record["open_ports"]
                                    device_record["summary"] = summary if is_reachable else {"ports": 0, "port_details": [], "note": "Latest local probe says this device is unreachable."}
                                    device_record["last_seen"] = probe.get("last_seen") or probe.get("observed_at") or None
                                    device_record["protocol"] = probe.get("protocol") or device_record["access_protocol"]
                                    device_record["device_type"] = probe.get("device_type") or device_record["device_type"]
                            except Exception as exc:
                                device_record["probe_error"] = str(exc)
                            parent.cache.add_local_device(device_record)
                            parent.settings.local_devices = parent.cache.load_local_devices()
                            parent.settings.local_targets = parent.cache.load_local_targets()
                            if isinstance(probe, dict):
                                live_probe = {**probe, "host": host, "mgmt_ip": host}
                                current_inventory = [
                                    item for item in list(parent.agent.last_local_inventory or [])
                                    if _host_key(item.get("host") or item.get("mgmt_ip")) != _host_key(host)
                                ]
                                current_inventory.insert(0, live_probe)
                                parent.agent.last_local_inventory = current_inventory
                            parent.cache.save_agent_profile(parent.settings)
                        try:
                            created = parent.client.create_device(parent.settings, data)
                        except Exception as exc:
                            created = {"ok": False, "error": str(exc), "synced": False}
                        try:
                            parent.client.save_workspace(
                                parent.settings,
                                {
                                    "local_targets": parent.settings.local_targets,
                                    "local_devices": parent.settings.local_devices,
                                    "settings": {"discovery_cidr": parent.settings.discovery_cidr},
                                },
                            )
                        except Exception:
                            pass
                        _json_response(self, {"ok": True, "device": created, "probe": probe, "local_device": device_record if host else None}); return
                    except Exception as exc:
                        _json_response(self, {"ok": False, "error": str(exc)}); return
                if parsed.path == "/api/targets":
                    host = str(data.get("host") or "").strip()
                    if host:
                        parent.cache.add_local_target(host)
                        parent.settings.local_targets = parent.cache.load_local_targets()
                        parent.cache.save_agent_profile(parent.settings)
                        try:
                            parent.client.save_workspace(
                                parent.settings,
                                {
                                    "local_targets": parent.settings.local_targets,
                                    "settings": {"discovery_cidr": parent.settings.discovery_cidr},
                                },
                            )
                        except Exception:
                            pass
                    _json_response(self, {"ok": True}); return
                if parsed.path == "/api/targets/delete":
                    host = str(data.get("host") or "").strip()
                    if host:
                        parent.cache.remove_local_device(host)
                        parent.settings.local_targets = parent.cache.load_local_targets()
                        parent.settings.local_devices = parent.cache.load_local_devices()
                        parent.cache.save_agent_profile(parent.settings)
                        try:
                            parent.client.save_workspace(
                                parent.settings,
                                {
                                    "local_targets": parent.settings.local_targets,
                                    "local_devices": parent.settings.local_devices,
                                    "settings": {"discovery_cidr": parent.settings.discovery_cidr},
                                },
                            )
                        except Exception:
                            pass
                    _json_response(self, {"ok": True}); return
                if parsed.path == "/api/agent/device":
                    try:
                        host = str(data.get("mgmt_ip") or data.get("host") or "").strip()
                        deleted = parent.client.delete_device(parent.settings, host) if host else {}
                        if host:
                            parent.cache.remove_local_device(host)
                            parent.settings.local_targets = parent.cache.load_local_targets()
                            parent.settings.local_devices = parent.cache.load_local_devices()
                            parent.cache.save_agent_profile(parent.settings)
                        _json_response(self, {"ok": True, "device": deleted}); return
                    except Exception as exc:
                        _json_response(self, {"ok": False, "error": str(exc)}); return
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
                        try:
                            parent.client.save_workspace(
                                parent.settings,
                                {
                                    "local_targets": parent.settings.local_targets,
                                    "settings": {"discovery_cidr": parent.settings.discovery_cidr},
                                },
                            )
                        except Exception:
                            pass
                    _json_response(self, {"ok": True}); return
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
                webview.create_window("Idea Agent", url, width=1600, height=1024, resizable=True, min_size=(1280, 820))
                try:
                    webview.start(debug=False, http_server=True, gui="edgechromium")
                except TypeError:
                    webview.start(debug=False, http_server=True)
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
