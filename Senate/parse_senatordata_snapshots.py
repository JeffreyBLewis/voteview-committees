#!/usr/bin/env python3
"""
Parse all SenatorData_<timestamp>.xml snapshots into a single CSV.

Source: https://www.senate.gov/general/contact_information/senators_cfm.xml
Each row = one senator in one snapshot.

Usage:
    python3 parse_senatordata_snapshots.py
    python3 parse_senatordata_snapshots.py --input SenatorData_snapshots --output senatordata.csv
"""

import csv
import re
import argparse
import xml.etree.ElementTree as ET
from pathlib import Path
from datetime import datetime

SNAPSHOT_DIR = Path("SenatorData_snapshots")
OUTPUT_CSV   = Path("senate_memberdata.csv")

FIELDNAMES = [
    # snapshot metadata
    "snapshot_timestamp",
    "snapshot_date",
    # senator fields
    "bioguide_id",
    "last_name",
    "first_name",
    "party",
    "state",
    "address",
    "phone",
    "email",
    "website",
    "class",
    "leadership_title",
]


def txt(element, tag, default=""):
    """Return stripped text of a child tag, or default if missing/empty."""
    el = element.find(tag)
    if el is None:
        return default
    return (el.text or "").strip()


def parse_file(path: Path):
    """Yield one dict per senator from a single snapshot XML file."""
    timestamp = re.search(r"SenatorData_(\d+)\.xml", path.name)
    if not timestamp:
        return
    ts = timestamp.group(1)
    snap_date = datetime.strptime(ts[:8], "%Y%m%d").strftime("%Y-%m-%d")

    try:
        tree = ET.parse(path)
    except ET.ParseError as exc:
        print(f"  PARSE ERROR in {path.name}: {exc}")
        return

    root = tree.getroot()

    # senators_cfm.xml wraps members in <member> elements at the root level
    # or under a <contact_information> root — handle both
    members = root.findall("member")
    if not members:
        members = root.findall(".//member")

    for member in members:
        yield {
            "snapshot_timestamp": ts,
            "snapshot_date":      snap_date,
            "bioguide_id":        txt(member, "bioguide_id"),
            "last_name":          txt(member, "last_name"),
            "first_name":         txt(member, "first_name"),
            "party":              txt(member, "party"),
            "state":              txt(member, "state"),
            "address":            txt(member, "address"),
            "phone":              txt(member, "phone"),
            "email":              txt(member, "email"),
            "website":            txt(member, "website"),
            "class":              txt(member, "class"),
            "leadership_title":   txt(member, "leadership_title"),
        }


def main():
    parser = argparse.ArgumentParser(description="Parse SenatorData XML snapshots to CSV")
    parser.add_argument("--input",  default=str(SNAPSHOT_DIR), help="Directory of XML snapshots")
    parser.add_argument("--output", default=str(OUTPUT_CSV),   help="Output CSV path")
    args = parser.parse_args()

    in_dir  = Path(args.input)
    out_csv = Path(args.output)

    xml_files = sorted(in_dir.glob("SenatorData_*.xml"))
    if not xml_files:
        raise SystemExit(f"No SenatorData_*.xml files found in {in_dir}")

    print(f"Parsing {len(xml_files)} snapshot files -> {out_csv}\n")

    total_rows = 0
    with out_csv.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        writer.writeheader()

        for i, path in enumerate(xml_files, 1):
            rows = list(parse_file(path))
            writer.writerows(rows)
            total_rows += len(rows)
            print(f"  [{i:4d}/{len(xml_files)}] {path.name}  -> {len(rows)} senators")

    print(f"\nWrote {total_rows:,} rows to {out_csv.resolve()}")


if __name__ == "__main__":
    main()
