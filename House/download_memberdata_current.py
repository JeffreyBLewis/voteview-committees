#!/usr/bin/env python3
"""
Download the current House Member Data XML from the Clerk of the House
and save it to MemberData_snapshots/ only if the content has changed
from the most recent snapshot already on disk.

The publish-date attribute is excluded from the change comparison so
that metadata-only refreshes do not generate duplicate files.

Usage:
    python3 download_memberdata_current.py

Output:
    MemberData_snapshots/MemberData_YYYYMMDDHHMMSS.xml  (only when changed)
"""

import hashlib
import re
import sys
import requests
from datetime import datetime, timezone
from pathlib import Path

SOURCE_URL = "https://clerk.house.gov/xml/lists/MemberData.xml"
OUTPUT_DIR = Path("MemberData_snapshots")
USER_AGENT = "Mozilla/5.0 (research bot; jeffreybyronlewis@gmail.com)"


def fetch_current() -> bytes:
    resp = requests.get(SOURCE_URL, headers={"User-Agent": USER_AGENT}, timeout=60)
    resp.raise_for_status()
    return resp.content


def content_hash(raw: bytes) -> str:
    """SHA-256 of XML content with publish-date stripped (avoids false positives)."""
    text = raw.decode("utf-8", errors="replace")
    text = re.sub(r'\s*publish-date="[^"]*"', "", text)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def latest_snapshot(output_dir: Path):
    """Return (path, hash) for the most recent MemberData_*.xml, or (None, None)."""
    files = sorted(output_dir.glob("MemberData_*.xml"))
    if not files:
        return None, None
    latest = files[-1]
    return latest, content_hash(latest.read_bytes())


def main():
    OUTPUT_DIR.mkdir(exist_ok=True)

    print(f"Fetching {SOURCE_URL} …", flush=True)
    try:
        raw = fetch_current()
    except requests.RequestException as exc:
        sys.exit(f"Download failed: {exc}")

    new_hash = content_hash(raw)
    latest_path, latest_hash = latest_snapshot(OUTPUT_DIR)

    if latest_path:
        print(f"Most recent snapshot : {latest_path.name}")
        if new_hash == latest_hash:
            print("No change detected — snapshot not saved.")
            return
        print("Change detected — saving new snapshot.")
    else:
        print("No existing snapshots found — saving.")

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    dest = OUTPUT_DIR / f"MemberData_{timestamp}.xml"
    dest.write_bytes(raw)
    print(f"Saved → {dest.name}  ({len(raw) / 1024:.1f} KB)")


if __name__ == "__main__":
    main()
