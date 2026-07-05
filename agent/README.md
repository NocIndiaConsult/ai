# Windows Agent

This folder contains the Windows edge agent that runs on the customer site PC.

What it does:
- Registers with the main SaaS server
- Sends heartbeat and sync snapshots
- Caches model bundles and events locally
- Queues commands while offline
- Replays pending commands when the server comes back
- Shows a desktop dashboard with:
  - Dashboard
  - Alerts
  - AI Team
  - On-Prem polling
  - Settings

Run locally:

```powershell
python agent\main.py --server http://127.0.0.1:8000 --company-id 1 --name site-agent
```

Run headless:

```powershell
python agent\main.py --server http://127.0.0.1:8000 --company-id 1 --name site-agent --headless
```

Build Windows EXE:

```powershell
.\agent\build.ps1
```

The generated binary will be a single-file Windows app suitable for customer deployment.
