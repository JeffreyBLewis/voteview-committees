#!/usr/bin/env python3
"""
Download House Resolution XML files for committee elections from the Congress.gov API.

Searches the 101st–119th Congresses for H.RES whose title contains the phrase
"standing committee" and an electing verb. This catches several title variants:

  "Electing Members to certain standing committees of the House of Representatives"
  "Electing a Member to certain standing committees of the House of Representatives"
  "Electing Members to a standing committee of the House of Representatives"
  "Electing Members to standing committees of the House of Representatives"
  "Electing Members to standing committees of the House"

Known resolutions that required the broader match:
  116th H.Res.46  – Democratic Judiciary assignments (singular title)
  118th H.Res.60  – Democratic Appropriations assignments (singular title)
  118th H.Res.71  – Democratic Judiciary + Oversight assignments (no "certain")
  118th H.Res.102 – Democratic Budget + Foreign Affairs ("of the House" only)
  118th H.Res.103 – Republican Budget (singular title)

Usage:
    CONGRESS_GOV_API_KEY=<key> python download_committee_elections.py

Output:
    committee_elections_xml/          directory of downloaded files
    committee_elections_xml/manifest.csv   log of every match found
"""

import os
import sys
import csv
import time
import requests
from pathlib import Path

API_KEY = os.environ.get("CONGRESS_GOV_API_KEY", "")
BASE_URL = "https://api.congress.gov/v3"
OUTPUT_DIR = Path("committee_elections_xml")

def is_committee_election_title(title: str) -> bool:
    """Return True if this H.RES title looks like a committee election resolution.

    The exact wording varies across Congresses:
      - singular vs. plural ("a standing committee" / "certain standing committees")
      - with or without "certain"
      - "of the House of Representatives" vs. "of the House"
    Matching on the invariant core ("lect" + "standing committee") catches all variants
    while excluding unrelated resolutions.
    """
    t = title.lower()
    return "lect" in t and "standing committee" in t


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


def iter_hres(congress):
    """Yield all H.RES bill summaries for a given congress, paginating automatically."""
    offset = 0
    limit = 250
    while True:
        data = api_get(f"/bill/{congress}/hres", {"offset": offset, "limit": limit})
        bills = data.get("bills", [])
        yield from bills
        total = data.get("pagination", {}).get("count", 0)
        offset += len(bills)
        if not bills or offset >= total:
            break
        time.sleep(0.15)


def get_text_url(congress, number):
    """Return (url, ext) for the best available text version, trying XML then HTML."""
    try:
        data = api_get(f"/bill/{congress}/hres/{number}/text")
    except RuntimeError as exc:
        print(f"    Warning: could not fetch text versions — {exc}")
        return None, None
    versions = data.get("textVersions", [])
    html_url = None
    for version in versions:
        for fmt in version.get("formats", []):
            t = fmt.get("type", "").upper()
            if "XML" in t:
                return fmt["url"], "xml"
            if "TEXT" in t and html_url is None:
                html_url = fmt["url"]
    if html_url:
        return html_url, "html"
    return None, None


def download(url, path):
    """Download a URL to a file. XML files on congress.gov don't require the API key."""
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
        for bill in iter_hres(congress):
            title = (bill.get("title") or "").strip()
            if not is_committee_election_title(title):
                continue

            number = bill["number"]
            found += 1
            print(f"  H.RES {number}: {title}")

            url, ext = get_text_url(congress, number)
            filename = OUTPUT_DIR / f"{congress}_hres{number}.{ext}" if ext else None

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
                print(f"    Already exists — skipping")
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
            print("  No matching H.RES found")

    # Write manifest CSV
    manifest_path = OUTPUT_DIR / "manifest.csv"
    fields = ["congress", "number", "title", "format", "url", "filename", "status"]
    with open(manifest_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(manifest_rows)

    # Summary
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
