#!/usr/bin/env python3
"""
Download Senate Resolution XML files for committee appointments from the Congress.gov API.

Searches the 101st–119th Congresses for S.RES that assign majority or minority party
membership to Senate committees. The title language changed across eras:

  112th–119th: "A resolution to constitute the [majority/minority] party's membership
                on certain committees for the One Hundred [Nth] Congress..."
  104th–110th: "A resolution making [majority/minority] party appointments to
                [certain] Senate committees for the [Nth] Congress"
  101st–102nd: "A resolution to make [majority/minority] party appointments to
                Senate Committees under paragraph..."

All share: ("majority party" OR "minority party") AND "committee" in the title.

Usage:
    CONGRESS_GOV_API_KEY=<key> python download_senate_committee_elections.py

Output:
    senate_committee_elections_xml/          downloaded files
    senate_committee_elections_xml/manifest.csv
"""

import os
import sys
import csv
import time
import requests
from pathlib import Path

API_KEY = os.environ.get("CONGRESS_GOV_API_KEY", "")
BASE_URL = "https://api.congress.gov/v3"
OUTPUT_DIR = Path("senate_committee_elections_xml")


def is_committee_appointment(title):
    """Return True if the title is a Senate committee appointment resolution.

    Two title patterns observed across Congresses:
      Standard  : "…constitute the majority/minority party's membership on certain committees…"
      Alternative: "Making majority/minority party appointments for the Nth Congress"
                   (used in 113th and 114th — no "committee" in title)
    """
    t = title.lower()
    has_party = "majority party" in t or "minority party" in t
    has_committee = "committee" in t
    has_appointment = "appointment" in t
    # Exclude spending/expenditure authorizations that mention committees
    is_expenditure = "authoriz" in t and "expenditure" in t
    return has_party and (has_committee or has_appointment) and not is_expenditure


def api_get(path, params=None):
    """GET from Congress.gov API, return parsed JSON. Retries on transient errors."""
    url = f"{BASE_URL}{path}"
    p = {"api_key": API_KEY, "format": "json", **(params or {})}
    for attempt in range(5):
        try:
            r = requests.get(url, params=p, timeout=30)
            if r.status_code == 429:
                wait = 60 * (2 ** attempt)
                print(f"    Rate limited — waiting {wait}s", flush=True)
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r.json()
        except requests.RequestException as exc:
            if attempt == 4:
                raise RuntimeError(f"API request failed for {path}: {exc}") from exc
            time.sleep(2 ** attempt)


def iter_sres(congress):
    """Yield all S.RES bill summaries for a given congress, paginating automatically."""
    offset = 0
    limit = 250
    while True:
        data = api_get(f"/bill/{congress}/sres", {"offset": offset, "limit": limit})
        bills = data.get("bills", [])
        yield from bills
        total = data.get("pagination", {}).get("count", 0)
        offset += len(bills)
        if not bills or offset >= total:
            break
        time.sleep(0.15)


def get_text_url(congress, number):
    """Return (url, ext) for the best available text version, trying XML then HTML.

    Prefers the final enacted version ('Agreed to Senate') over earlier stages.
    """
    try:
        data = api_get(f"/bill/{congress}/sres/{number}/text")
    except RuntimeError as exc:
        print(f"    Warning: could not fetch text versions — {exc}")
        return None, None
    versions = data.get("textVersions", [])
    preferred_order = ["Agreed to Senate", "Engrossed in Senate", "Introduced in Senate"]

    # Try XML first across preferred version order
    for preferred in preferred_order:
        for version in versions:
            if version.get("type") == preferred:
                for fmt in version.get("formats", []):
                    if "XML" in fmt.get("type", "").upper():
                        return fmt["url"], "xml"
    # Any version with XML
    for version in versions:
        for fmt in version.get("formats", []):
            if "XML" in fmt.get("type", "").upper():
                return fmt["url"], "xml"

    # Fall back to HTML across preferred version order
    for preferred in preferred_order:
        for version in versions:
            if version.get("type") == preferred:
                for fmt in version.get("formats", []):
                    if "TEXT" in fmt.get("type", "").upper():
                        return fmt["url"], "html"
    # Any version with HTML
    for version in versions:
        for fmt in version.get("formats", []):
            if "TEXT" in fmt.get("type", "").upper():
                return fmt["url"], "html"

    return None, None


def download(url, path):
    """Download a URL to a file."""
    r = requests.get(url, timeout=120)
    r.raise_for_status()
    path.write_bytes(r.content)


def congress_years(congress):
    start = 1789 + (congress - 1) * 2
    return f"{start}–{start + 1}"


def main():
    if not API_KEY:
        sys.exit("Error: set the CONGRESS_GOV_API_KEY environment variable")

    OUTPUT_DIR.mkdir(exist_ok=True)
    manifest_rows = []

    for congress in range(101, 120):
        print(f"\n{'=' * 56}")
        print(f"  {congress}th Congress  ({congress_years(congress)})")
        print(f"{'=' * 56}")

        found = 0
        for bill in iter_sres(congress):
            title = (bill.get("title") or "").strip()
            if not is_committee_appointment(title):
                continue

            number = bill["number"]
            found += 1
            print(f"  S.RES {number}: {title}")

            url, ext = get_text_url(congress, number)
            filename = OUTPUT_DIR / f"{congress}_sres{number}.{ext}" if ext else None

            row = {
                "congress": congress,
                "number": number,
                "title": title,
                "format": ext or "",
                "url": url or "",
                "filename": filename.name if filename else "",
                "status": "",
            }

            if not url:
                print("    No text version available")
                row["status"] = "no_text"
            elif filename.exists():
                print("    Already exists — skipping")
                row["status"] = "exists"
            else:
                try:
                    download(url, filename)
                    size_kb = filename.stat().st_size // 1024
                    print(f"    Saved → {filename.name}  ({size_kb} KB)")
                    row["status"] = "downloaded"
                except Exception as exc:
                    print(f"    Download failed: {exc}")
                    row["status"] = f"error: {exc}"

            manifest_rows.append(row)
            time.sleep(0.3)

        if found == 0:
            print("  No matching S.RES found")

    # Write manifest CSV
    manifest_path = OUTPUT_DIR / "manifest.csv"
    fields = ["congress", "number", "title", "format", "url", "filename", "status"]
    with open(manifest_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(manifest_rows)

    downloaded = [r for r in manifest_rows if r["status"] == "downloaded"]
    errors = sum(1 for r in manifest_rows if r["status"].startswith("error"))
    print(f"\n{'=' * 56}")
    print(f"  Done.")
    print(f"  Downloaded (XML) : {sum(1 for r in downloaded if r['format'] == 'xml')}")
    print(f"  Downloaded (HTML): {sum(1 for r in downloaded if r['format'] == 'html')}")
    print(f"  Already existed  : {sum(1 for r in manifest_rows if r['status'] == 'exists')}")
    print(f"  No text available: {sum(1 for r in manifest_rows if r['status'] == 'no_text')}")
    if errors:
        print(f"  Errors           : {errors}")
    print(f"  Manifest         : {manifest_path}")
    print(f"{'=' * 56}")


if __name__ == "__main__":
    main()
