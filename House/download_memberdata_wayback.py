#!/usr/bin/env python3
"""
Download every archived version of https://clerk.house.gov/xml/lists/MemberData.xml
from the Wayback Machine using waybackpy.

Usage:
    pip install waybackpy requests
    python3 download_memberdata_wayback.py
"""

import os
import time
import argparse
import requests
from pathlib import Path

try:
    import waybackpy
    from waybackpy import WaybackMachineCDXServerAPI
except ImportError:
    raise SystemExit("Install waybackpy first:  pip install waybackpy")

TARGET_URL = "https://clerk.house.gov/xml/lists/MemberData.xml"
USER_AGENT = "Mozilla/5.0 (research bot; jeffreybyronlewis@gmail.com)"
OUTPUT_DIR = Path("MemberData_snapshots")


def fetch_snapshot(archive_url: str, dest_path: Path, session: requests.Session, retries: int = 3) -> bool:
    for attempt in range(1, retries + 1):
        try:
            resp = session.get(archive_url, timeout=60)
            resp.raise_for_status()
            dest_path.write_bytes(resp.content)
            return True
        except requests.RequestException as exc:
            print(f"    attempt {attempt}/{retries} failed: {exc}")
            if attempt < retries:
                time.sleep(5 * attempt)
    return False


def main():
    parser = argparse.ArgumentParser(description="Download all Wayback snapshots of MemberData.xml")
    parser.add_argument("--output", default=str(OUTPUT_DIR), help="Directory to write files into")
    parser.add_argument("--delay", type=float, default=1.0, help="Seconds to wait between downloads (default 1)")
    parser.add_argument("--skip-existing", action="store_true", default=True, help="Skip already-downloaded files")
    parser.add_argument("--no-skip-existing", dest="skip_existing", action="store_false")
    args = parser.parse_args()

    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Querying CDX API for all snapshots of:\n  {TARGET_URL}\n")
    cdx = WaybackMachineCDXServerAPI(
        url=TARGET_URL,
        user_agent=USER_AGENT,
        # Return only successful captures (HTTP 200) of the exact URL
        filters=["statuscode:200"],
        # Collapse on digest so we skip byte-identical copies
        # Comment out the next line to get every single snapshot including duplicates
        collapses=["digest"],
    )

    snapshots = list(cdx.snapshots())
    print(f"Found {len(snapshots)} unique snapshots.\n")

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    ok = skip = fail = 0
    for i, snap in enumerate(snapshots, 1):
        timestamp = snap.timestamp          # e.g. "20050301120000"
        archive_url = snap.archive_url      # https://web.archive.org/web/<ts>/<original>
        dest = out_dir / f"MemberData_{timestamp}.xml"

        print(f"[{i:4d}/{len(snapshots)}] {timestamp}", end="  ")

        if args.skip_existing and dest.exists() and dest.stat().st_size > 0:
            print("already exists, skipping.")
            skip += 1
            continue

        print(f"downloading ...", end=" ", flush=True)
        success = fetch_snapshot(archive_url, dest, session)
        if success:
            size_kb = dest.stat().st_size / 1024
            print(f"OK ({size_kb:.1f} KB)")
            ok += 1
        else:
            print("FAILED")
            fail += 1

        time.sleep(args.delay)

    print(f"\nDone. Downloaded: {ok}  Skipped: {skip}  Failed: {fail}")
    print(f"Files are in: {out_dir.resolve()}")


if __name__ == "__main__":
    main()
