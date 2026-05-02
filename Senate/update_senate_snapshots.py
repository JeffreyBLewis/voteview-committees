#!/usr/bin/env python3
"""
Fetch current Senate committee rosters and senator directory from senate.gov
and save as new timestamped snapshots — but only when the content has changed.

Sources
-------
  Committee rosters : https://www.senate.gov/general/committee_membership/
                        committee_memberships_{CODE}.xml
  Senator directory : https://www.senate.gov/general/contact_information/
                        senators_cfm.xml

Committee codes are auto-discovered from existing files in SenateCommittees_snapshots/.
The SenatorData XML contains a <last_updated> timestamp that changes on every
fetch; that field is stripped before comparing so it does not trigger false saves.

Output
------
  SenateCommittees_snapshots/{CODE}_{YYYYMMDDHHMMSS}.xml   (when changed)
  SenatorData_snapshots/SenatorData_{YYYYMMDDHHMMSS}.xml   (when changed)
"""

import re
import sys
import time
import requests
from datetime import datetime
from pathlib import Path

COMM_DIR    = Path("SenateCommittees_snapshots")
SENATOR_DIR = Path("SenatorData_snapshots")

COMM_URL    = ("https://www.senate.gov/general/committee_membership/"
               "committee_memberships_{code}.xml")
SENATOR_URL = ("https://www.senate.gov/general/contact_information/"
               "senators_cfm.xml")

_LAST_UPDATED = re.compile(rb"<last_updated>[^<]*</last_updated>", re.IGNORECASE)


def normalize(raw: bytes) -> bytes:
    """Remove volatile <last_updated> so it doesn't trigger false-positive saves."""
    return _LAST_UPDATED.sub(b"", raw)


def fetch(url: str) -> bytes:
    """GET url; return raw bytes. Raises on HTTP or network error."""
    r = requests.get(url, timeout=30, headers={"User-Agent": "VoteView-updater/1.0"})
    r.raise_for_status()
    if not r.content:
        raise ValueError("Empty response")
    # Sanity-check: should be XML
    if not r.content.lstrip().startswith(b"<"):
        raise ValueError(f"Response does not look like XML (starts with {r.content[:40]!r})")
    return r.content


def latest_snapshot(directory: Path, prefix: str):
    """Return the most recent existing snapshot Path for this prefix, or None."""
    candidates = sorted(directory.glob(f"{prefix}_*.xml"))
    return candidates[-1] if candidates else None


def save_if_changed(raw: bytes, directory: Path, prefix: str) -> str:
    """
    Compare raw (normalized) against the latest snapshot.
    Save a new file and return 'saved' if different; return 'unchanged' if same.
    """
    prev = latest_snapshot(directory, prefix)
    if prev is not None and normalize(raw) == normalize(prev.read_bytes()):
        return "unchanged"

    ts  = datetime.now().strftime("%Y%m%d%H%M%S")
    out = directory / f"{prefix}_{ts}.xml"
    out.write_bytes(raw)
    return "saved"


def discover_codes() -> list:
    """Sorted list of unique committee codes found in SenateCommittees_snapshots/."""
    codes = set()
    for path in COMM_DIR.glob("*.xml"):
        m = re.match(r"^([A-Z]+)_\d{14}\.xml$", path.name)
        if m:
            codes.add(m.group(1))
    return sorted(codes)


def main():
    codes = discover_codes()
    if not codes:
        sys.exit(f"No committee snapshot files found in {COMM_DIR}")

    print(f"Fetching {len(codes)} committee rosters + senator directory …\n")

    saved = unchanged = errors = 0

    for code in codes:
        url = COMM_URL.format(code=code)
        try:
            raw = fetch(url)
        except Exception as exc:
            print(f"  {code:<6}  ERROR  {exc}")
            errors += 1
            time.sleep(0.3)
            continue

        status = save_if_changed(raw, COMM_DIR, code)
        marker = "✓ saved" if status == "saved" else "  —"
        print(f"  {code:<6}  {marker}")
        if status == "saved":
            saved += 1
        else:
            unchanged += 1
        time.sleep(0.3)

    print()
    try:
        raw = fetch(SENATOR_URL)
        status = save_if_changed(raw, SENATOR_DIR, "SenatorData")
        marker = "✓ saved" if status == "saved" else "  —"
        print(f"  {'SenatorData':<14}  {marker}")
        if status == "saved":
            saved += 1
        else:
            unchanged += 1
    except Exception as exc:
        print(f"  {'SenatorData':<14}  ERROR  {exc}")
        errors += 1

    print(f"\n  Saved: {saved}   Unchanged: {unchanged}   Errors: {errors}")
    if saved:
        Path(".roster_changed").touch()


if __name__ == "__main__":
    main()
