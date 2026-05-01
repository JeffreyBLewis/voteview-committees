#!/usr/bin/env python3
"""
Download every archived version of all Senate committee membership XML files
from the Wayback Machine using waybackpy.

Source URL pattern:
    https://www.senate.gov/general/committee_membership/committee_memberships_<CODE>.xml

The CDX API is queried with prefix matching so every committee code ever archived
is discovered automatically — no hard-coded committee list needed.

Usage:
    pip install waybackpy requests
    python3 download_senate_committees_wayback.py
"""

import re
import time
import argparse
import requests
from pathlib import Path
from collections import defaultdict

try:
    from waybackpy import WaybackMachineCDXServerAPI
except ImportError:
    raise SystemExit("Install waybackpy first:  pip install waybackpy")

URL_PREFIX  = "https://www.senate.gov/general/committee_membership/committee_memberships_"
USER_AGENT  = "Mozilla/5.0 (research bot; jeffreybyronlewis@gmail.com)"
OUTPUT_DIR  = Path("SenateCommittees_snapshots")
CODE_RE     = re.compile(r"committee_memberships_([A-Z0-9]+)\.xml", re.IGNORECASE)


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
    parser = argparse.ArgumentParser(
        description="Download all Wayback snapshots of Senate committee membership XMLs"
    )
    parser.add_argument("--output", default=str(OUTPUT_DIR), help="Directory to write files into")
    parser.add_argument("--delay", type=float, default=1.0, help="Seconds to wait between downloads (default 1)")
    parser.add_argument("--skip-existing", action="store_true", default=True, help="Skip already-downloaded files")
    parser.add_argument("--no-skip-existing", dest="skip_existing", action="store_false")
    args = parser.parse_args()

    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Querying CDX API for all snapshots matching:\n  {URL_PREFIX}*.xml\n")
    cdx = WaybackMachineCDXServerAPI(
        url=URL_PREFIX,
        user_agent=USER_AGENT,
        filters=["statuscode:200"],
        collapses=["digest"],
        match_type="prefix",
    )

    snapshots = list(cdx.snapshots())
    print(f"Found {len(snapshots)} unique snapshots across all committee codes.\n")

    # Group by committee code for a summary
    by_code = defaultdict(int)
    for snap in snapshots:
        m = CODE_RE.search(snap.original)
        if m:
            by_code[m.group(1).upper()] += 1
    print("Committee codes found:")
    for code in sorted(by_code):
        print(f"  {code}: {by_code[code]} snapshots")
    print()

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    ok = skip = fail = 0
    for i, snap in enumerate(snapshots, 1):
        m = CODE_RE.search(snap.original)
        if not m:
            print(f"[{i:4d}/{len(snapshots)}] skipping unrecognized URL: {snap.original}")
            continue

        committee_code = m.group(1).upper()
        timestamp      = snap.timestamp
        archive_url    = snap.archive_url
        dest = out_dir / f"{committee_code}_{timestamp}.xml"

        print(f"[{i:4d}/{len(snapshots)}] {committee_code} {timestamp}", end="  ")

        if args.skip_existing and dest.exists() and dest.stat().st_size > 0:
            print("already exists, skipping.")
            skip += 1
            continue

        print("downloading ...", end=" ", flush=True)
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
