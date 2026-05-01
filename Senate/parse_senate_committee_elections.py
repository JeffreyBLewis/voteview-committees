#!/usr/bin/env python3
"""
Parse Senate Resolution XML/HTML files in senate_committee_elections_xml/
into a CSV of committee appointments.

One row per member–committee assignment. Fields:
  congress          : Congress number
  date              : Date resolution was agreed to (YYYY-MM-DD)
  resolution        : Resolution identifier (e.g. S.Res.17)
  party_designation : "majority" or "minority" (from resolution title)
  committee         : Committee name, without "Committee on" prefix
  member            : Member name as printed in resolution
  rank              : Ordinal position within this resolution's committee list (1=first)
  role              : Chairman, Chairwoman, Ranking Member, Vice Chairman, Ex Officio, or ""

Usage:
    python3 parse_senate_committee_elections.py

Output: senate_elections.csv
"""

import csv
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

INPUT_DIR  = Path("senate_committee_elections_xml")
OUTPUT_CSV = Path("senate_elections.csv")

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_LOWER_WORDS = {"and", "the", "of", "on", "for", "in", "to", "by", "at", "a"}


def _smart_title(s):
    words = s.split()
    return " ".join(
        w.capitalize() if (i == 0 or w.lower() not in _LOWER_WORDS) else w.lower()
        for i, w in enumerate(words)
    )


def strip_committee_prefix(name):
    name = name.strip().rstrip(":")
    name = re.sub(r"Comittee|Commitee", "Committee", name)
    name = name.replace("’", "'").replace("‘", "'")
    m = re.match(r"Committee (?:on|of) (?:the |The )?(.+)", name, re.IGNORECASE)
    if m:
        result = m.group(1).strip()
        if result and result[0].islower():
            result = _smart_title(result)
        return result
    # Special committees that don't use "Committee on"
    for prefix in ("Select Committee", "Special Committee", "Joint ", "Standing "):
        if name.lower().startswith(prefix.lower()):
            return name
    return name


_ROLE_MAP = {
    "chairman":       "Chairman",
    "chairwoman":     "Chairwoman",
    "chair":          "Chair",
    "ranking":        "Ranking Member",
    "ranking member": "Ranking Member",
    "vice chairman":  "Vice Chairman",
    "vice chair":     "Vice Chair",
    "ex officio":     "Ex Officio",
    "exofficio":      "Ex Officio",
}

_TITLE_RE = re.compile(
    r"(?:Mr\.|Ms\.|Mrs\.|Miss\.|Dr\.)\s",
    re.IGNORECASE,
)


def parse_senate_member_text(text):
    """
    Parse a Senate committee member-list string into (member, role) pairs.

    Handles:
      "Ms. Klobuchar (Ranking), Mr. Bennet, Mr. Durbin"
      "Mr. Leahy (Chairman), Mr. Pryor, and Mr. Kerrey of Nebraska"
      "Mr. Reed (ex officio), Mr. Schumer (ex officio)"
    """
    if not text:
        return []
    text = re.sub(r"\s+", " ", text).strip().rstrip(".")

    # Normalize ", and Mr." → ", Mr."
    text = re.sub(
        r",?\s+and\s+(?=(?:Mr\.|Ms\.|Mrs\.|Miss\.|Dr\.)\s)",
        ", ",
        text,
        flags=re.IGNORECASE,
    )

    # Split on comma immediately before a title prefix
    parts = re.split(r",\s*(?=(?:Mr\.|Ms\.|Mrs\.|Miss\.|Dr\.)\s)", text)

    result = []
    for part in parts:
        part = re.sub(r"^\s*and\s+", "", part, flags=re.IGNORECASE).strip().rstrip(",;. ")
        if not part:
            continue

        # Extract parenthetical role at end of name
        role = ""
        m = re.search(r"\s*\(([^)]+)\)\s*$", part)
        if m:
            role_raw = m.group(1).strip()
            role = _ROLE_MAP.get(role_raw.lower(), role_raw)
            part = part[: m.start()].strip()

        if part:
            result.append((part, role))

    return result


def extract_party_designation(title):
    """Return 'majority', 'minority', or '' from the resolution title."""
    t = title.lower()
    if "majority" in t:
        return "majority"
    if "minority" in t:
        return "minority"
    return ""


# ---------------------------------------------------------------------------
# XML parser (109th–119th)
# ---------------------------------------------------------------------------

def parse_xml_file(path):
    content = path.read_text(encoding="utf-8", errors="replace")
    content = re.sub(r"<!DOCTYPE[^>]*>", "", content)
    content = re.sub(r"<\?[^?]*\?>", "", content)

    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        print(f"  XML parse error {path.name}: {exc}")
        return []

    # Date — prefer text content ("February 12, 2014") over the date attribute
    # because the attribute is sometimes populated with a stale/wrong value.
    date_str = ""
    action_date = root.find(".//action-date")
    if action_date is not None:
        if action_date.text:
            try:
                date_str = datetime.strptime(
                    action_date.text.strip(), "%B %d, %Y"
                ).strftime("%Y-%m-%d")
            except ValueError:
                pass
        if not date_str:
            d = action_date.get("date", "")
            if re.fullmatch(r"\d{8}", d):
                date_str = f"{d[:4]}-{d[4:6]}-{d[6:]}"
    if not date_str:
        dc = root.find(".//{http://purl.org/dc/elements/1.1/}date")
        if dc is not None:
            date_str = (dc.text or "").strip()

    # Party designation from title
    party_designation = ""
    title_el = root.find(".//official-title")
    if title_el is not None:
        party_designation = extract_party_designation("".join(title_el.itertext()))
    if not party_designation:
        dc_title = root.find(".//{http://purl.org/dc/elements/1.1/}title")
        if dc_title is not None:
            party_designation = extract_party_designation(dc_title.text or "")

    m = re.match(r"(\d+)_sres(\d+)", path.stem)
    congress   = int(m.group(1)) if m else ""
    resolution = f"S.Res.{m.group(2)}" if m else ""

    rows = []
    for section in root.findall(".//section"):
        # Support both <committee-appointment-paragraph> and plain <paragraph>
        paras = section.findall(".//committee-appointment-paragraph")
        if not paras:
            paras = [p for p in section.findall(".//paragraph")
                     if p.find("header") is not None]

        for para in paras:
            header = para.find("header")
            if header is None:
                continue
            cn_el = header.find(".//committee-name")
            committee_raw = "".join(
                (cn_el if cn_el is not None else header).itertext()
            )
            committee_raw = re.sub(r"\s+", " ", committee_raw)
            committee = strip_committee_prefix(committee_raw)

            text_el = para.find("text")
            if text_el is None:
                continue
            member_text = "".join(text_el.itertext())

            for rank, (member, role) in enumerate(
                parse_senate_member_text(member_text), 1
            ):
                rows.append({
                    "congress":           congress,
                    "date":               date_str,
                    "resolution":         resolution,
                    "party_designation":  party_designation,
                    "committee":          committee,
                    "member":             member,
                    "rank":               rank,
                    "role":               role,
                })

    return rows


# ---------------------------------------------------------------------------
# HTML parser (101st–108th)
# ---------------------------------------------------------------------------

def parse_html_file(path):
    raw  = path.read_text(encoding="utf-8", errors="replace")
    text = re.sub(r"<[^>]+>", "", raw)

    m = re.match(r"(\d+)_sres(\d+)", path.stem)
    congress   = int(m.group(1)) if m else ""
    resolution = f"S.Res.{m.group(2)}" if m else ""

    # Party designation from title text
    party_designation = extract_party_designation(text[:500])

    # Date from header
    date_str = ""
    dm = re.search(
        r"(January|February|March|April|May|June|July|August|September"
        r"|October|November|December)\s+\d{1,2}.*?,\s*\d{4}",
        text,
    )
    if dm:
        # Strip "(legislative day, ...)" parentheticals then parse
        clean_date = re.sub(r"\s*\(.*?\)", "", dm.group(0)).strip()
        for fmt in ("%B %d, %Y", "%B  %d, %Y"):
            try:
                date_str = datetime.strptime(clean_date, fmt).strftime("%Y-%m-%d")
                break
            except ValueError:
                pass

    flat = re.sub(r"\s+", " ", text)

    # Match "Committee on X: members." or "COMMITTEE ON X: members."
    committee_re = re.compile(
        r"(?:\(\d+\)\s+)?(Committee on [^:.\-]+?)\s*(?::\s*|\.\-\-\s*)"
        r"((?:(?!(?:\(\d+\)\s+)?Committee on|Attest|<all>).)+)",
        re.IGNORECASE,
    )

    rows = []
    for cm in committee_re.finditer(flat):
        committee = strip_committee_prefix(cm.group(1))
        member_text = cm.group(2).strip().rstrip(".")

        for rank, (member, role) in enumerate(
            parse_senate_member_text(member_text), 1
        ):
            rows.append({
                "congress":           congress,
                "date":               date_str,
                "resolution":         resolution,
                "party_designation":  party_designation,
                "committee":          committee,
                "member":             member,
                "rank":               rank,
                "role":               role,
            })

    return rows


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    xml_files  = sorted(INPUT_DIR.glob("*.xml"))
    html_files = sorted(INPUT_DIR.glob("*.html"))
    all_files  = xml_files + html_files

    if not all_files:
        print(f"No files found in {INPUT_DIR}")
        return

    all_rows = []
    errors   = []

    for path in all_files:
        try:
            rows = parse_xml_file(path) if path.suffix == ".xml" else parse_html_file(path)
            all_rows.extend(rows)
        except Exception as exc:
            errors.append((path.name, str(exc)))

    all_rows.sort(key=lambda r: (str(r["date"]), str(r["congress"]), r["resolution"]))

    fields = ["congress", "date", "resolution", "party_designation",
              "committee", "member", "rank", "role"]
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(all_rows)

    print(f"Parsed {len(all_rows):,} member-committee records from {len(all_files)} files")
    print(f"Output → {OUTPUT_CSV}")

    missing_member = sum(1 for r in all_rows if not r["member"])
    missing_date   = sum(1 for r in all_rows if not r["date"])
    with_role      = sum(1 for r in all_rows if r["role"])
    maj            = sum(1 for r in all_rows if r["party_designation"] == "majority")
    min_           = sum(1 for r in all_rows if r["party_designation"] == "minority")
    print(f"  Majority-party rows : {maj:,}")
    print(f"  Minority-party rows : {min_:,}")
    print(f"  With role           : {with_role:,}")
    print(f"  Missing member name : {missing_member}")
    print(f"  Missing date        : {missing_date}")
    if errors:
        print(f"  Parse errors        : {len(errors)}")
        for name, msg in errors[:5]:
            print(f"    {name}: {msg}")


if __name__ == "__main__":
    main()
