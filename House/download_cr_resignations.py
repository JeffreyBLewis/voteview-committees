#!/usr/bin/env python3
"""
Scrape committee resignation letters from the Congressional Record via GovInfo.

Two passes:

  Pass 1 — Daily CREC collection (~5,900 packages, one per session day):
    Paginate through all packages, fetch granule lists, filter titles containing
    both "RESIGN" and "COMMITTEE", download matching HTML granules.

  Pass 2 — Bound CRECB collection (gap years 2003, 2004, 2008):
    The daily CREC has no article-level granules for these years. The bound
    edition (CRECB-YYYY-ptN) does, using the same title format and HTML access.

Coverage notes:
  - Daily CREC granule indexing is absent for 1997-98, 2003-04, 2008; Pass 2
    fills in 2003, 2004, and 2008 via the bound edition.
  - 1997-1998 bound granules are section-level only (PDF, no HTML) — not
    recoverable without PDF parsing.
  - Pre-1994 (101st-103rd Congress) is largely absent from GovInfo entirely.

Usage:
    CONGRESS_GOV_API_KEY=<key> python download_cr_resignations.py

Output:
    cr_resignations/          downloaded HTML files
    cr_resignations/manifest.csv
"""

import os
import sys
import csv
import time
import urllib.parse
import requests
from pathlib import Path

API_KEY = os.environ.get("CONGRESS_GOV_API_KEY", "")
GOVINFO_BASE = "https://api.govinfo.gov"
OUTPUT_DIR = Path("House/cr_resignations")

# Granule title must contain both of these (case-insensitive) to be a candidate
TITLE_KEYWORDS = ["RESIGN", "COMMITTEE"]


def govinfo_get(path, params=None, retries=5):
    """GET from GovInfo API with retry/backoff."""
    url = f"{GOVINFO_BASE}{path}"
    p = {"api_key": API_KEY, **(params or {})}
    for attempt in range(retries):
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
            if attempt == retries - 1:
                raise RuntimeError(f"GovInfo GET failed for {path}: {exc}") from exc
            time.sleep(2 ** attempt)


def iter_crec_packages():
    """Yield all CREC package dicts from the GovInfo collection, oldest-compatible order."""
    # The collection endpoint returns newest-first; we collect all and process them.
    offset_mark = "*"
    page_size = 100
    while True:
        data = govinfo_get(
            "/collections/CREC/1989-01-01T00:00:00Z",
            {"pageSize": page_size, "offsetMark": offset_mark},
        )
        packages = data.get("packages", [])
        yield from packages
        next_page = data.get("nextPage")
        if not next_page or not packages:
            break
        # Extract the offsetMark from the nextPage URL
        qs = urllib.parse.urlparse(next_page).query
        params = urllib.parse.parse_qs(qs)
        offset_mark = params.get("offsetMark", [None])[0]
        if not offset_mark:
            break
        time.sleep(0.1)


def get_matching_granules(pkg_id):
    """Return list of granule dicts whose titles contain RESIGN and COMMITTEE."""
    try:
        data = govinfo_get(
            f"/packages/{pkg_id}/granules",
            {"pageSize": 250, "offsetMark": "*"},
        )
    except RuntimeError as exc:
        print(f"    Warning: {exc}")
        return []
    matches = []
    for g in data.get("granules", []):
        title = (g.get("title") or "").upper()
        if all(kw in title for kw in TITLE_KEYWORDS):
            matches.append(g)
    return matches


def iter_crecb_gap_packages():
    """Yield (pkg_id, date_issued) for CRECB bound-edition gap-year packages.

    Covers 2003, 2004, and 2008 — years where the daily CREC has no
    article-level granules but the bound edition does (CRECB-YYYY-ptN).
    Stops iterating parts for a given year when a part returns 0 granules.
    """
    gap_years = [2003, 2004, 2008]
    for year in gap_years:
        for pt in range(1, 50):
            pkg_id = f"CRECB-{year}-pt{pt}"
            try:
                data = govinfo_get(
                    f"/packages/{pkg_id}/granules",
                    {"pageSize": 1, "offsetMark": "*"},
                )
            except RuntimeError:
                break
            if data.get("count", 0) == 0:
                break
            yield pkg_id, str(year)
            time.sleep(0.1)


def download_granule_html(pkg_id, granule_id):
    """Fetch and return the HTML text of a granule."""
    url = f"{GOVINFO_BASE}/packages/{pkg_id}/granules/{granule_id}/htm"
    for attempt in range(4):
        try:
            r = requests.get(url, params={"api_key": API_KEY}, timeout=30)
            if r.status_code == 429:
                time.sleep(60 * (2 ** attempt))
                continue
            r.raise_for_status()
            return r.text
        except requests.RequestException as exc:
            if attempt == 3:
                raise RuntimeError(f"Download failed: {exc}") from exc
            time.sleep(2 ** attempt)


def process_granule(pkg_id, date_label, granule, manifest_rows):
    """Download one matching granule and append a row to manifest_rows."""
    granule_id = granule.get("granuleId", "")
    title = granule.get("title", "")
    print(f"\n  {date_label}  {granule_id}")
    print(f"  Title: {title}")

    filename = OUTPUT_DIR / f"{granule_id.replace('/', '_')}.html"
    row = {
        "date": date_label,
        "packageId": pkg_id,
        "granuleId": granule_id,
        "title": title,
        "filename": filename.name,
        "status": "",
    }

    if filename.exists():
        print("  Already downloaded — skipping")
        row["status"] = "exists"
    else:
        try:
            html = download_granule_html(pkg_id, granule_id)
            filename.write_text(html, encoding="utf-8")
            print(f"  Saved → {filename.name}")
            row["status"] = "downloaded"
        except Exception as exc:
            print(f"  Download failed: {exc}")
            row["status"] = f"error: {exc}"

    manifest_rows.append(row)
    time.sleep(0.2)


def main():
    if not API_KEY:
        sys.exit("Error: set the CONGRESS_GOV_API_KEY environment variable")

    OUTPUT_DIR.mkdir(exist_ok=True)
    manifest_rows = []

    # ------------------------------------------------------------------
    # Pass 1: daily CREC packages
    # ------------------------------------------------------------------
    print("Pass 1: scanning daily CREC packages…")
    pkg_count = 0

    for pkg in iter_crec_packages():
        pkg_id = pkg.get("packageId", "")
        date_issued = pkg.get("dateIssued", "")
        pkg_count += 1

        if pkg_count % 200 == 0:
            print(f"  …scanned {pkg_count} packages, {len(manifest_rows)} resignations found so far")

        for granule in get_matching_granules(pkg_id):
            process_granule(pkg_id, date_issued, granule, manifest_rows)

        time.sleep(0.12)

    print(f"\nPass 1 complete. Scanned {pkg_count} CREC packages.")

    # ------------------------------------------------------------------
    # Pass 2: bound CRECB packages for gap years 2003, 2004, 2008
    # ------------------------------------------------------------------
    print("\nPass 2: scanning bound CRECB packages for gap years (2003, 2004, 2008)…")
    crecb_pkg_count = 0

    for pkg_id, year_label in iter_crecb_gap_packages():
        crecb_pkg_count += 1
        for granule in get_matching_granules(pkg_id):
            process_granule(pkg_id, year_label, granule, manifest_rows)
        time.sleep(0.12)

    print(f"\nPass 2 complete. Scanned {crecb_pkg_count} CRECB packages.")

    # ------------------------------------------------------------------
    # Write manifest
    # ------------------------------------------------------------------
    manifest_path = OUTPUT_DIR / "manifest.csv"
    fields = ["date", "packageId", "granuleId", "title", "filename", "status"]
    with open(manifest_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(manifest_rows)

    downloaded = sum(1 for r in manifest_rows if r["status"] == "downloaded")
    existed = sum(1 for r in manifest_rows if r["status"] == "exists")
    errors = sum(1 for r in manifest_rows if r["status"].startswith("error"))

    print(f"\n{'=' * 56}")
    print(f"  Done.")
    print(f"  CREC packages scanned  : {pkg_count}")
    print(f"  CRECB packages scanned : {crecb_pkg_count}")
    print(f"  Resignation granules   : {len(manifest_rows)}")
    print(f"  Downloaded  : {downloaded}")
    print(f"  Already existed : {existed}")
    if errors:
        print(f"  Errors      : {errors}")
    print(f"  Manifest    : {manifest_path}")
    print(f"{'=' * 56}")


if __name__ == "__main__":
    main()
