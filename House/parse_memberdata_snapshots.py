#!/usr/bin/env python3
"""
Parse all MemberData_<timestamp>.xml snapshots into a single CSV.

Each row = one member × one committee in one snapshot.
Subcommittees belonging to that committee are pipe-delimited in the
'subcommittees' column. Matching is by the first two characters of the
committee code (e.g. subcomcode "II10" belongs to committee "II00").

Usage:
    python3 parse_memberdata_snapshots.py
    python3 parse_memberdata_snapshots.py --input MemberData_snapshots --output memberdata.csv
"""

import csv
import json
import re
import argparse
import xml.etree.ElementTree as ET
from pathlib import Path
from datetime import datetime

SNAPSHOT_DIR  = Path("MemberData_snapshots")
OUTPUT_CSV    = Path("house_committee_roster_data.csv")
COMMITTEE_CODES_FILE = Path(__file__).parent / "house_committee_codes.json"


def load_committee_names() -> dict:
    if COMMITTEE_CODES_FILE.exists():
        return json.loads(COMMITTEE_CODES_FILE.read_text())
    return {}

FIELDNAMES = [
    # snapshot metadata
    "snapshot_timestamp",
    "snapshot_date",
    # congress-level metadata
    "congress_num",
    "session",
    "majority",
    "minority",
    # member fields
    "statedistrict",
    "bioguide_id",
    "namelist",
    "lastname",
    "firstname",
    "middlename",
    "suffix",
    "courtesy",
    "party",
    "caucus",
    "state_postal",
    "state_fullname",
    "district",
    "prior_congress",
    "official_name",
    "formal_name",
    "townname",
    "office_building",
    "office_room",
    "office_zip",
    "phone",
    "elected_date",
    "sworn_date",
    # committee fields (one row per committee)
    "committee_code",        # e.g. "II00"
    "committee_name",        # e.g. "Committee on Natural Resources"
    "committee_rank",
    "committee_leadership",
    # subcommittees of this committee only (pipe-delimited)
    "subcommittees",         # e.g. "II10:rank=2|II24:rank=1:Chairman"
]


def txt(element, tag, default=""):
    el = element.find(tag)
    if el is None:
        return default
    return (el.text or "").strip()


def parse_file(path: Path, committee_names: dict = {}):
    """Yield one dict per (member, committee) from a single snapshot XML file."""
    timestamp = re.search(r"MemberData_(\d+)\.xml", path.name)
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

    ti = root.find("title-info")
    if ti is None:
        ti = ET.Element("title-info")
    congress_num = txt(ti, "congress-num")
    session      = txt(ti, "session")
    majority     = txt(ti, "majority")
    minority     = txt(ti, "minority")

    members_el = root.find("members")
    if members_el is None:
        return

    for member in members_el.findall("member"):
        mi = member.find("member-info")
        if mi is None:
            mi = ET.Element("member-info")

        state_el       = mi.find("state")
        state_postal   = (state_el.get("postal-code") or "").strip() if state_el is not None else ""
        state_fullname = txt(state_el, "state-fullname") if state_el is not None else ""

        elected_el   = mi.find("elected-date")
        sworn_el     = mi.find("sworn-date")
        elected_date = (elected_el.get("date") or "").strip() if elected_el is not None else ""
        sworn_date   = (sworn_el.get("date")   or "").strip() if sworn_el   is not None else ""

        member_base = {
            "snapshot_timestamp": ts,
            "snapshot_date":      snap_date,
            "congress_num":       congress_num,
            "session":            session,
            "majority":           majority,
            "minority":           minority,
            "statedistrict":      txt(member, "statedistrict"),
            "bioguide_id":        txt(mi, "bioguideID"),
            "namelist":           txt(mi, "namelist"),
            "lastname":           txt(mi, "lastname"),
            "firstname":          txt(mi, "firstname"),
            "middlename":         txt(mi, "middlename"),
            "suffix":             txt(mi, "suffix"),
            "courtesy":           txt(mi, "courtesy"),
            "party":              txt(mi, "party"),
            "caucus":             txt(mi, "caucus"),
            "state_postal":       state_postal,
            "state_fullname":     state_fullname,
            "district":           txt(mi, "district"),
            "prior_congress":     txt(mi, "prior-congress"),
            "official_name":      txt(mi, "official-name"),
            "formal_name":        txt(mi, "formal-name"),
            "townname":           txt(mi, "townname"),
            "office_building":    txt(mi, "office-building"),
            "office_room":        txt(mi, "office-room"),
            "office_zip":         txt(mi, "office-zip"),
            "phone":              txt(mi, "phone"),
            "elected_date":       elected_date,
            "sworn_date":         sworn_date,
        }

        ca = member.find("committee-assignments")
        if ca is None:
            continue

        # Index subcommittees by their two-character parent prefix
        sub_by_prefix = {}
        for s in ca.findall("subcommittee"):
            code = s.get("subcomcode", "")
            rank = s.get("rank", "")
            lead = s.get("leadership", "")
            entry = f"{code}:rank={rank}" + (f":{lead}" if lead else "")
            sub_by_prefix.setdefault(code[:2], []).append(entry)

        for c in ca.findall("committee"):
            comcode  = c.get("comcode", "")
            rank     = c.get("rank", "")
            lead     = c.get("leadership", "")
            prefix   = comcode[:2]
            subs     = sub_by_prefix.get(prefix, [])

            yield {
                **member_base,
                "committee_code":      comcode,
                "committee_name":      committee_names.get(comcode, ""),
                "committee_rank":      rank,
                "committee_leadership": lead,
                "subcommittees":       "|".join(subs),
            }


def main():
    parser = argparse.ArgumentParser(description="Parse MemberData XML snapshots to CSV")
    parser.add_argument("--input",  default=str(SNAPSHOT_DIR), help="Directory of XML snapshots")
    parser.add_argument("--output", default=str(OUTPUT_CSV),   help="Output CSV path")
    args = parser.parse_args()

    in_dir  = Path(args.input)
    out_csv = Path(args.output)

    xml_files = sorted(in_dir.glob("MemberData_*.xml"))
    if not xml_files:
        raise SystemExit(f"No MemberData_*.xml files found in {in_dir}")

    committee_names = load_committee_names()
    print(f"Loaded {len(committee_names)} committee name mappings.")
    print(f"Parsing {len(xml_files)} snapshot files -> {out_csv}\n")

    total_rows = 0
    with out_csv.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        writer.writeheader()

        for i, path in enumerate(xml_files, 1):
            rows = list(parse_file(path, committee_names))
            writer.writerows(rows)
            total_rows += len(rows)
            print(f"  [{i:4d}/{len(xml_files)}] {path.name}  -> {len(rows)} committee-member pairs")

    print(f"\nWrote {total_rows:,} rows to {out_csv.resolve()}")


if __name__ == "__main__":
    main()
