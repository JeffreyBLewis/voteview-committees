#!/usr/bin/env python3
"""
Parse all Senate committee membership XML snapshots into a single CSV.

Source: SenateCommittees_snapshots/<CODE>_<timestamp>.xml
Each row = one senator × one committee in one snapshot.
Subcommittee assignments within that committee are pipe-delimited in
the 'subcommittees' column (e.g. "SSAF13:Ranking|SSAF01:Member").

Usage:
    python3 parse_senate_committee_snapshots.py
    python3 parse_senate_committee_snapshots.py --input SenateCommittees_snapshots --output senate_committees.csv
"""

import csv
import re
import argparse
import xml.etree.ElementTree as ET
from pathlib import Path
from datetime import datetime
from collections import defaultdict

SNAPSHOT_DIR = Path("SenateCommittees_snapshots")
OUTPUT_CSV   = Path("senate_committee_memberships.csv")

FILE_RE = re.compile(r"^([A-Z0-9]+)_(\d+)\.xml$", re.IGNORECASE)

FIELDNAMES = [
    # snapshot metadata
    "snapshot_timestamp",
    "snapshot_date",
    # committee metadata
    "committee_code",        # from filename, e.g. "SSAF"
    "majority_party",
    "committee_name",
    "committee_full_code",   # from XML, e.g. "SSAF00"
    # member fields
    "first_name",
    "last_name",
    "state",
    "party",
    "position",              # full-committee role: Chairman, Ranking, Member, Ex Officio, etc.
    # subcommittees of this committee (pipe-delimited)
    "subcommittees",         # e.g. "SSAF13:Ranking|SSAF01:Member"
]


def txt(element, tag, default=""):
    el = element.find(tag)
    if el is None:
        return default
    return (el.text or "").strip()


def member_key(member_el):
    """Return a (last, first, state) tuple to match a senator across the full committee and subcommittees."""
    name_el = member_el.find("name")
    last  = txt(name_el, "last")  if name_el is not None else ""
    first = txt(name_el, "first") if name_el is not None else ""
    state = txt(member_el, "state")
    return (last.lower(), first.lower(), state.lower())


def parse_file(path: Path):
    """Yield one dict per (senator, committee) from a single snapshot XML file."""
    m = FILE_RE.match(path.name)
    if not m:
        return
    committee_code = m.group(1).upper()
    ts             = m.group(2)
    snap_date      = datetime.strptime(ts[:8], "%Y%m%d").strftime("%Y-%m-%d")

    try:
        tree = ET.parse(path)
    except ET.ParseError as exc:
        print(f"  PARSE ERROR in {path.name}: {exc}")
        return

    root = tree.getroot()
    committees_el = root.find("committees")
    if committees_el is None:
        committees_el = root

    majority_party      = txt(committees_el, "majority_party")
    committee_name      = txt(committees_el, "committee_name")
    committee_full_code = txt(committees_el, "committee_code")

    # Collect full-committee members: key -> {fields}
    full_members = {}
    members_el = committees_el.find("members")
    if members_el is not None:
        for member in members_el.findall("member"):
            name_el = member.find("name")
            first = txt(name_el, "first") if name_el is not None else ""
            last  = txt(name_el, "last")  if name_el is not None else ""
            key = member_key(member)
            full_members[key] = {
                "first_name": first,
                "last_name":  last,
                "state":      txt(member, "state"),
                "party":      txt(member, "party"),
                "position":   txt(member, "position"),
            }

    # Collect subcommittee assignments: key -> [pipe-delimited entries]
    sub_assignments = defaultdict(list)
    for sub in committees_el.findall("subcommittee"):
        sub_code = txt(sub, "committee_code")
        sub_members_el = sub.find("members")
        if sub_members_el is None:
            continue
        for member in sub_members_el.findall("member"):
            key      = member_key(member)
            position = txt(member, "position")
            entry    = f"{sub_code}:{position}" if position else sub_code
            sub_assignments[key].append(entry)

            # If this senator is on a subcommittee but not the full committee, add them
            if key not in full_members:
                name_el = member.find("name")
                first = txt(name_el, "first") if name_el is not None else ""
                last  = txt(name_el, "last")  if name_el is not None else ""
                full_members[key] = {
                    "first_name": first,
                    "last_name":  last,
                    "state":      txt(member, "state"),
                    "party":      txt(member, "party"),
                    "position":   "",
                }

    for key, fields in full_members.items():
        yield {
            "snapshot_timestamp":  ts,
            "snapshot_date":       snap_date,
            "committee_code":      committee_code,
            "majority_party":      majority_party,
            "committee_name":      committee_name,
            "committee_full_code": committee_full_code,
            **fields,
            "subcommittees": "|".join(sub_assignments.get(key, [])),
        }


def main():
    parser = argparse.ArgumentParser(description="Parse Senate committee XML snapshots to CSV")
    parser.add_argument("--input",  default=str(SNAPSHOT_DIR), help="Directory of XML snapshots")
    parser.add_argument("--output", default=str(OUTPUT_CSV),   help="Output CSV path")
    args = parser.parse_args()

    in_dir  = Path(args.input)
    out_csv = Path(args.output)

    xml_files = sorted(in_dir.glob("*.xml"))
    if not xml_files:
        raise SystemExit(f"No XML files found in {in_dir}")

    print(f"Parsing {len(xml_files)} snapshot files -> {out_csv}\n")

    total_rows = 0
    with out_csv.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        writer.writeheader()

        for i, path in enumerate(xml_files, 1):
            rows = list(parse_file(path))
            writer.writerows(rows)
            total_rows += len(rows)
            print(f"  [{i:4d}/{len(xml_files)}] {path.name}  -> {len(rows)} committee-member pairs")

    print(f"\nWrote {total_rows:,} rows to {out_csv.resolve()}")


if __name__ == "__main__":
    main()
