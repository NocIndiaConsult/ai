"""
cleanup_auto_devices.py
------------------------
One-time helper to remove devices that were auto-added by the old
ping-sweep / incoming-ping discovery feature (which is now removed from
the agent), leaving only devices you added manually.

WHY THIS IS NEEDED
------------------
The agent's cache.py never actually persisted the "discovered" /
"discovered_via" flag on a device (save_local_devices() only keeps a
fixed whitelist of fields, and those two were not in it). So there is
no 100% certain marker left in the database to say "this one was
auto-added". Instead this script uses a very reliable pattern that the
old auto-add code always produced and a manually-added real device
almost never has all three of at once:

    device_type      == "host"
    access_protocol  == "auto"
    name             == host   (i.e. you never gave it a real name)

HOW TO USE
----------
1. Copy this file next to your agent's main.py / cache.py (same
   "agent" folder), or anywhere - it only needs cache.py importable.
2. Run it:  python cleanup_auto_devices.py
3. It will print every device currently stored, mark the ones that
   look auto-added, and ask for confirmation before deleting anything.
4. If a device you actually care about gets flagged by mistake, just
   answer "n" when asked, or edit KEEP_HOSTS below and re-run.

Nothing is deleted until you type "yes" at the prompt.
"""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from cache import LocalCache  # noqa: E402

# If you know for sure some hosts are real devices even though they match
# the "looks auto-added" pattern, list them here and they will never be
# deleted, no matter what.
KEEP_HOSTS: set[str] = set()


def looks_auto_added(device: dict) -> bool:
    host = str(device.get("host") or device.get("mgmt_ip") or "").strip()
    name = str(device.get("name") or "").strip()
    device_type = str(device.get("device_type") or "").strip().lower()
    access_protocol = str(device.get("access_protocol") or "").strip().lower()
    return (
        device_type == "host"
        and access_protocol == "auto"
        and name == host
        and host not in KEEP_HOSTS
    )


def main() -> None:
    cache = LocalCache()
    devices = cache.load_local_devices()

    if not devices:
        print(f"No devices found in {cache.db_path}. Nothing to do.")
        return

    print(f"Database: {cache.db_path}")
    print(f"Total devices currently stored: {len(devices)}\n")

    auto_like: list[dict] = []
    manual_like: list[dict] = []
    for device in devices:
        (auto_like if looks_auto_added(device) else manual_like).append(device)

    print("Devices that LOOK auto-added (will be removed if you confirm):")
    if auto_like:
        for device in auto_like:
            print(f"  - {device.get('host')}  (name={device.get('name')}, type={device.get('device_type')}, protocol={device.get('access_protocol')})")
    else:
        print("  (none found)")

    print("\nDevices that look manually added / configured (will be KEPT):")
    if manual_like:
        for device in manual_like:
            print(f"  - {device.get('host')}  (name={device.get('name')}, type={device.get('device_type')}, protocol={device.get('access_protocol')})")
    else:
        print("  (none found)")

    if not auto_like:
        print("\nNothing matches the auto-added pattern. No changes made.")
        return

    print(f"\n{len(auto_like)} device(s) will be permanently removed from local_devices and local_targets.")
    answer = input("Type 'yes' to proceed, anything else to cancel: ").strip().lower()
    if answer != "yes":
        print("Cancelled. No changes made.")
        return

    for device in auto_like:
        host = str(device.get("host") or device.get("mgmt_ip") or "").strip()
        if host:
            cache.remove_local_device(host)
            cache.remove_local_target(host)

    remaining = cache.load_local_devices()
    print(f"\nDone. Removed {len(auto_like)} device(s). {len(remaining)} device(s) remain:")
    for device in remaining:
        print(f"  - {device.get('host')}  (name={device.get('name')})")


if __name__ == "__main__":
    main()
