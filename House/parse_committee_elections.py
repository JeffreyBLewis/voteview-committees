#!/usr/bin/env python3
"""
Parse House Resolution XML/HTML files in committee_elections_xml/ into a CSV.

One row per member–committee assignment. Fields:
  congress     : Congress number
  date         : Date resolution was agreed to (YYYY-MM-DD)
  resolution   : Resolution identifier (e.g. H.Res.7)
  committee    : Committee name, without "Committee on" prefix
  member       : Member name as printed in resolution
  rank         : Ordinal position in this resolution's committee list (1 = first listed)
  role         : "Chair", "Chairman", "Chairwoman", or "" for regular members
  rank_after   : "Mr. X" if resolution specifies member ranks after X, else ""

Usage:
    python parse_committee_elections.py

Output: house_elections.csv
"""

import csv
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

INPUT_DIR = Path("committee_elections_xml")
OUTPUT_CSV = Path("house_elections.csv")


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_LOWER_WORDS = {"and", "the", "of", "on", "for", "in", "to", "by", "at", "a"}


def _smart_title(s):
    """Title-case s but keep common function words lowercase (except first word)."""
    words = s.split()
    return " ".join(
        w.capitalize() if (i == 0 or w.lower() not in _LOWER_WORDS) else w.lower()
        for i, w in enumerate(words)
    )


def strip_committee_prefix(name):
    """Return committee name without leading 'Committee on/of (the) '.
    Title-cases the result when the source was all-lowercase."""
    # Normalize common OCR/typo variants in the source documents
    name = name.strip().rstrip(":")
    name = re.sub(r"Comittee|Commitee", "Committee", name)
    # Normalize curly/smart apostrophes to straight apostrophe
    name = name.replace("’", "'").replace("‘", "'")
    m = re.match(r"Committee (?:on|of) (?:the |The )?(.+)", name, re.IGNORECASE)
    if m:
        result = m.group(1).strip()
        # Normalize case only when source was all-lowercase
        if result and result[0].islower():
            result = _smart_title(result)
        return result
    return name


def parse_member_text(text, is_ranked_section=False):
    """
    Parse a committee member-list string into a list of (member, role, rank_after).

    Handles all observed formats:
      • "Mr. X, Chair."                     → [(X, Chair, "")]
      • "Mr. X, Chairman; Mr. Y, Mr. Z."    → [(X, Chairman, ""), (Y, "", ""), (Z, "", "")]
      • "Mr. X (to rank immediately after Mr. Y)." → [(X, "", "Mr. Y")]
      • "Mr. X to rank after Mr. Y."        → [(X, "", "Mr. Y")]
      • "Mr. X, after Mr. Y."  (ranked §)   → [(X, "", "Mr. Y")]
      • "Mr. X and Ms. Y."                  → [(X, "", ""), (Y, "", "")]
    """
    if not text:
        return []

    # Collapse internal whitespace (handles multi-line HTML text)
    text = re.sub(r"\s+", " ", text).strip().rstrip(".")

    # ------------------------------------------------------------------
    # Ranked-section format: "Name, after Other" or just "Name"
    # ------------------------------------------------------------------
    if is_ranked_section:
        m = re.match(r"(.+?),\s+after\s+(.+)$", text, re.IGNORECASE)
        if m:
            return [(m.group(1).strip().rstrip(",").strip(), "", m.group(2).strip())]
        return [(text.strip().rstrip(",").strip(), "", "")]

    # ------------------------------------------------------------------
    # Replace parenthetical rank notes with placeholders
    # ------------------------------------------------------------------
    rank_store: list[str] = []

    def _store(m):
        rank_store.append(m.group(1).strip())
        return f"__RNKAFTER{len(rank_store) - 1}__"

    text = re.sub(r"\(to rank (?:immediately )?after ([^)]+)\)", _store, text)

    # Convert "Name, Chair. Name2 …" → "Name, Chair; Name2 …" so the
    # semicolon split below correctly separates the two entries.
    text = re.sub(
        r"(,\s*Chair(?:man|woman|person)?)\.\s+(?=(?:Mr\.|Ms\.|Mrs\.|Miss\.|Dr\.)\s)",
        r"\1; ",
        text,
        flags=re.IGNORECASE,
    )

    # ------------------------------------------------------------------
    # Inline "Name to rank [immediately] after Other" (no parens)
    # These appear as full entries; handle before splitting on "; "
    # ------------------------------------------------------------------
    inline_rank_entries = []
    remainder_parts = []
    for chunk in re.split(r";\s*", text):
        chunk = chunk.strip()
        ir = re.search(r"\bto rank (?:immediately )?after (.+)$", chunk, re.IGNORECASE)
        if ir:
            name = chunk[: ir.start()].strip().rstrip(",").strip()
            rank_after = ir.group(1).strip()
            inline_rank_entries.append((chunk, name, rank_after))
        else:
            remainder_parts.append(chunk)

    entries = []

    # Process non-inline-rank chunks (split by "; ")
    for part in remainder_parts:
        part = re.sub(r"^\s*and\s+", "", part, flags=re.IGNORECASE).strip()
        if not part:
            continue

        # Check for Chair/Chairman/Chairwoman role at the end
        role_m = re.search(
            r",\s*(Chair(?:man|woman|person)?)\s*$", part, re.IGNORECASE
        )
        if role_m:
            role = role_m.group(1)
            name = part[: role_m.start()].strip()
            name, rank_after = _extract_rank_placeholder(name, rank_store)
            entries.append((name, role, rank_after))
            continue

        # No role — split on ", " only where followed by a member title.
        # Normalise ", and Mr." / " and Mr." → ", Mr." before splitting.
        part = re.sub(
            r",?\s+and\s+(?=(?:Mr\.|Ms\.|Mrs\.|Miss\.|Dr\.)\s)",
            ", ",
            part,
            flags=re.IGNORECASE,
        )
        subparts = re.split(r",\s*(?=(?:Mr\.|Ms\.|Mrs\.|Miss\.|Dr\.)\s)", part)
        for sp in subparts:
            sp = re.sub(r"^\s*and\s+", "", sp, flags=re.IGNORECASE).strip()
            if sp:
                sp, rank_after = _extract_rank_placeholder(sp, rank_store)
                entries.append((sp, "", rank_after))

    # Insert inline-rank entries at the position they were found in the original text
    # (We'll just append them; order within the full list is preserved by original split order)
    for _chunk, name, rank_after in inline_rank_entries:
        entries.append((name, "", rank_after))

    # Safety: strip any unconsumed placeholders
    cleaned = []
    for member, role, rank_after in entries:
        member = re.sub(r"__RNKAFTER\d+__", "", member).strip()
        cleaned.append((member, role, rank_after))
    return cleaned


def _extract_rank_placeholder(text, rank_store):
    """Replace a \x00RN\x00 placeholder in text, returning (cleaned_text, rank_after)."""
    m = re.search(r"__RNKAFTER(\d+)__", text)
    if m:
        rank_after = rank_store[int(m.group(1))]
        text = re.sub(r"__RNKAFTER\d+__", "", text).strip()
        return text, rank_after
    return text, ""


# ---------------------------------------------------------------------------
# XML parser
# ---------------------------------------------------------------------------

def parse_xml_file(path):
    """Return list of row dicts from an XML H.RES file."""
    content = path.read_text(encoding="utf-8", errors="replace")
    # Strip DOCTYPE and processing instructions that confuse ElementTree
    content = re.sub(r"<!DOCTYPE[^>]*>", "", content)
    content = re.sub(r"<\?[^?]*\?>", "", content)

    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        print(f"  XML parse error {path.name}: {exc}")
        return []

    # Date: prefer action-date/@date attribute (YYYYMMDD)
    date_str = ""
    action_date = root.find(".//action-date")
    if action_date is not None:
        d = action_date.get("date", "")
        if re.fullmatch(r"\d{8}", d):
            date_str = f"{d[:4]}-{d[4:6]}-{d[6:]}"
        elif action_date.text:
            try:
                date_str = datetime.strptime(action_date.text.strip(), "%B %d, %Y").strftime(
                    "%Y-%m-%d"
                )
            except ValueError:
                pass
    if not date_str:
        dc = root.find(".//{http://purl.org/dc/elements/1.1/}date")
        if dc is not None:
            date_str = (dc.text or "").strip()

    # Congress and resolution number from filename: "110_hres7"
    m = re.match(r"(\d+)_hres(\d+)", path.stem)
    congress = int(m.group(1)) if m else ""
    resolution = f"H.Res.{m.group(2)}" if m else ""

    rows = []

    for section in root.findall(".//section"):
        # Determine section type from introductory text element
        intro = section.find("text")
        intro_text = ("".join(intro.itertext()) if intro is not None else "").lower()
        is_ranked = "ranked as follows" in intro_text or (
            "rank" in intro_text and "elected" not in intro_text
        )

        # Support two XML schemas:
        #   old: <committee-appointment-paragraph><header>…<text>…
        #   new (119th+): <paragraph><enum>…<header>…<text>…
        paras = section.findall(".//committee-appointment-paragraph")
        if not paras:
            paras = [p for p in section.findall(".//paragraph")
                     if p.find("header") is not None]

        for para in paras:
            header = para.find("header")
            if header is None:
                continue

            # Committee name: prefer <committee-name> child, else full header text.
            # Collapse internal whitespace — some headers span multiple XML lines.
            cn_el = header.find(".//committee-name")
            if cn_el is not None:
                committee_raw = "".join(cn_el.itertext())
            else:
                committee_raw = "".join(header.itertext())
            committee_raw = re.sub(r"\s+", " ", committee_raw)
            committee = strip_committee_prefix(committee_raw)

            text_el = para.find("text")
            if text_el is None:
                continue
            member_text = "".join(text_el.itertext())

            for rank, (member, role, rank_after) in enumerate(
                parse_member_text(member_text, is_ranked_section=is_ranked), 1
            ):
                rows.append(
                    {
                        "congress": congress,
                        "date": date_str,
                        "resolution": resolution,
                        "committee": committee,
                        "member": member,
                        "rank": rank,
                        "role": role,
                        "rank_after": rank_after,
                    }
                )

    return rows


# ---------------------------------------------------------------------------
# HTML parser
# ---------------------------------------------------------------------------

_MONTHS = {m: i for i, m in enumerate(
    ["January","February","March","April","May","June",
     "July","August","September","October","November","December"], 1
)}


def parse_html_file(path):
    """Return list of row dicts from a plain-text HTML H.RES file."""
    raw = path.read_text(encoding="utf-8", errors="replace")
    text = re.sub(r"<[^>]+>", "", raw)

    m = re.match(r"(\d+)_hres(\d+)", path.stem)
    congress = int(m.group(1)) if m else ""
    resolution = f"H.Res.{m.group(2)}" if m else ""

    # Date
    date_str = ""
    dm = re.search(
        r"(January|February|March|April|May|June|July|August|September"
        r"|October|November|December)\s+\d{1,2},\s*\d{4}",
        text,
    )
    if dm:
        try:
            date_str = datetime.strptime(dm.group(0), "%B %d, %Y").strftime("%Y-%m-%d")
        except ValueError:
            pass

    # Collapse all whitespace into single spaces, then split on "Committee on"
    # to get one chunk per committee entry.
    flat = re.sub(r"\s+", " ", text)

    # Two HTML formats observed:
    #   Standard  : "Committee on X: members."
    #   113th H.7 : "(N) Committee on X.--members."
    # The separator alternation handles both.
    committee_re = re.compile(
        r"(?:\(\d+\)\s+)?(Committee on [^:.\-]+?)\s*(?::\s*|\.\-\-\s*)"
        r"((?:(?!(?:\(\d+\)\s+)?Committee on|Attest).)+)",
        re.IGNORECASE,
    )

    rows = []
    for cm in committee_re.finditer(flat):
        committee = strip_committee_prefix(cm.group(1))
        member_text = cm.group(2).strip().rstrip(".")

        for rank, (member, role, rank_after) in enumerate(
            parse_member_text(member_text, is_ranked_section=False), 1
        ):
            rows.append(
                {
                    "congress": congress,
                    "date": date_str,
                    "resolution": resolution,
                    "committee": committee,
                    "member": member,
                    "rank": rank,
                    "role": role,
                    "rank_after": rank_after,
                }
            )

    return rows


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    xml_files = sorted(INPUT_DIR.glob("*.xml"))
    html_files = sorted(INPUT_DIR.glob("*.html"))
    all_files = xml_files + html_files

    if not all_files:
        print(f"No files found in {INPUT_DIR}")
        return

    all_rows = []
    errors = []

    for path in all_files:
        try:
            if path.suffix == ".xml":
                rows = parse_xml_file(path)
            else:
                rows = parse_html_file(path)
            all_rows.extend(rows)
        except Exception as exc:
            errors.append((path.name, str(exc)))

    all_rows.sort(key=lambda r: (str(r["date"]), str(r["congress"]), r["resolution"], r["committee"], r["member"]))

    fields = ["congress", "date", "resolution", "committee", "member", "rank", "role", "rank_after"]
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(all_rows)

    print(f"Parsed {len(all_rows):,} member-committee records from {len(all_files)} files")
    print(f"Output → {OUTPUT_CSV}")

    missing_member = sum(1 for r in all_rows if not r["member"])
    missing_date   = sum(1 for r in all_rows if not r["date"])
    with_role      = sum(1 for r in all_rows if r["role"])
    with_rank_after = sum(1 for r in all_rows if r["rank_after"])
    print(f"  With role (Chair/Chairman)  : {with_role:,}")
    print(f"  With rank_after             : {with_rank_after:,}")
    print(f"  Missing member name         : {missing_member}")
    print(f"  Missing date                : {missing_date}")
    if errors:
        print(f"  Parse errors                : {len(errors)}")
        for name, msg in errors[:5]:
            print(f"    {name}: {msg}")


if __name__ == "__main__":
    main()
