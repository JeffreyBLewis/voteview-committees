#!/usr/bin/env python3
"""
Parse committee resignation HTML files from the Congressional Record into a CSV.

Reads every .html file in cr_resignations/ (produced by download_cr_resignations.py)
and extracts:
  - date          : date the resignation appeared in the CR (YYYY-MM-DD)
  - congress      : Congress number derived from the date
  - member_name   : resigning member's name (from the letter signature)
  - committees    : committee(s) resigned from, semicolon-separated
  - cr_citation   : human-readable CR reference (Volume, Number/Part, Page)
  - uri           : public GovInfo URL for the granule HTML
  - granule_id    : GovInfo granule identifier
  - package_id    : GovInfo package identifier

Output: resignations.csv

Usage:
    python parse_cr_resignations.py
"""

import csv
import re
from datetime import datetime
from pathlib import Path

INPUT_DIR = Path("cr_resignations")
OUTPUT_CSV = Path("resignations.csv")

MONTHS = {m: i for i, m in enumerate(
    ["January","February","March","April","May","June",
     "July","August","September","October","November","December"], 1)}


def congress_from_date(dt):
    return (dt.year - 1789) // 2 + 1


def strip_tags(html):
    return re.sub(r"<[^>]+>", "", html)


def extract_date(raw, is_crecb):
    """Return a datetime parsed from the CR header or letter body, or None."""
    if not is_crecb:
        # [Congressional Record Volume N, Number N (Weekday, Month D, YYYY)]
        m = re.search(
            r"\((?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),\s+"
            r"(\w+ \d{1,2}, \d{4})\)",
            raw,
        )
        if m:
            try:
                return datetime.strptime(m.group(1), "%B %d, %Y")
            except ValueError:
                pass

    # Fall back: letter date line "Washington, DC, Month D, YYYY."
    m = re.search(
        r"Washington,\s+D\.?C\.?,\s+(\w+\s+\d{1,2},\s*\d{4})",
        raw,
    )
    if m:
        try:
            return datetime.strptime(m.group(1).strip(), "%B %d, %Y")
        except ValueError:
            pass

    return None


def extract_cr_fields(raw, is_crecb):
    """Return (volume, number_or_part, page, citation_str)."""
    volume = number = page = ""

    m = re.search(r"Volume (\d+)", raw)
    if m:
        volume = m.group(1)

    if is_crecb:
        m = re.search(r"Part (\d+)", raw)
        if m:
            number = f"Part {m.group(1)}"
    else:
        m = re.search(r"Number (\d+)", raw)
        if m:
            number = m.group(1)

    m = re.search(r"\[Pages?\s+([\w\d\-]+)\]", raw)
    if m:
        page = m.group(1)

    if is_crecb:
        citation = f"Cong. Rec. (Bound) Vol. {volume} {number}, p. {page}"
    else:
        citation = f"Cong. Rec. Vol. {volume}, No. {number}, p. {page}"

    return volume, number, page, citation


def extract_heading(raw):
    """Return the all-caps heading block (may span multiple lines) that contains COMMITTEE."""
    # The heading appears after the GPO notice line and before "The SPEAKER"
    # It is a block of text in all-caps (possibly indented)
    gpo_end = raw.find("www.gpo.gov")
    if gpo_end == -1:
        gpo_end = 0
    speaker_pos = raw.find("The SPEAKER", gpo_end)
    if speaker_pos == -1:
        speaker_pos = len(raw)

    segment = raw[gpo_end:speaker_pos]

    # Find lines that are all-caps (ignoring punctuation/spaces) and non-empty
    heading_lines = []
    in_heading = False
    for line in segment.splitlines():
        stripped = line.strip()
        if not stripped:
            if in_heading:
                break
            continue
        # All-caps test: remove punctuation/spaces and check
        alpha = re.sub(r"[^A-Za-z]", "", stripped)
        if alpha and alpha == alpha.upper():
            heading_lines.append(stripped)
            in_heading = True
        elif in_heading:
            break

    return " ".join(heading_lines)


def extract_committees(heading, raw):
    """Return a list of committee names from the heading or letter body."""
    raw_heading = heading.upper()

    # Pattern 1: singular "COMMITTEE ON X" — use lookahead so "COMMITTEE ON" is not consumed
    hits = re.findall(
        r"COMMITTEE ON\s+((?:THE\s+)?[A-Z][A-Z\s',/\-\.]+?)"
        r"(?=\s+AND\s+(?:THE\s+)?COMMITTEE\s+ON"
        r"|\s+AND\s+AS\s+MEMBER"
        r"|\s+AND\s+AS\s+CHAIR"
        r"|\s*$)",
        raw_heading,
    )
    if hits:
        return [_clean_committee(h) for h in hits]

    # Pattern 2: "THE COMMITTEE ON X"
    hits = re.findall(r"THE COMMITTEE ON\s+([A-Z][A-Z\s',/\-\.]+?)(?:\s+AND|\s*$)", raw_heading)
    if hits:
        return [_clean_committee(h) for h in hits]

    # Pattern 3: plural "COMMITTEES ON X, Y, AND Z"
    m = re.search(r"COMMITTEES ON\s+([A-Z][A-Z\s',/\-\.]+?)(?:\s*$)", raw_heading)
    if m:
        raw_list = m.group(1)
        # Split on ", AND " first (serial comma), then plain ", "
        parts = re.split(r",\s*AND\s+", raw_list, maxsplit=1)
        result = []
        for part in parts:
            for item in re.split(r",\s*", part):
                item = re.sub(r"^\s*AND\s+", "", item.strip())
                if item:
                    result.append(_clean_committee(item))
        if result:
            return result

    # Pattern 4: "JOINT ECONOMIC COMMITTEE" or other "[WORDS] COMMITTEE" not covered above
    m = re.search(r"(?:JOINT\s+ECONOMIC|PERMANENT\s+SELECT)\s+COMMITTEE", raw_heading)
    if m:
        return [_clean_committee(m.group(0))]

    # Fallback: scan text body for "Committee on X"
    hits = re.findall(r"Committee on ([A-Za-z][A-Za-z\s',/\-\.]{3,50}?)(?::|,|\.|$| and )", raw)
    seen, result = set(), []
    for h in hits:
        h = h.strip().rstrip(",.:;")
        if h and h not in seen:
            seen.add(h)
            result.append(_clean_committee(h.upper()))
    return result[:5]


def _clean_committee(name):
    """Normalise a committee name: title-case and strip leading 'The '."""
    name = name.strip().title()
    if name.lower().startswith("the "):
        name = name[4:]
    return name


_EXCLUDE_NAME = ["SPEAKER", "CONGRESS", "WASHINGTON", "HOUSE", "REPRESENTATIVE",
                 "COMMITTEE", "SINCERELY", "RESPECTFULLY"]

_NAME_WORD = r"[A-Z][a-zA-Z\.\-]*"
_NAME_PAT = rf"({_NAME_WORD}(?:\s+{_NAME_WORD}){{1,4}})"

_SALUTATIONS = (
    r"(?:Sincerely|Respectfully|Very truly yours|Yours truly"
    r"|Best [Rr]egards|Most sincerely|For God and Country)"
)


def _looks_like_name(candidate):
    words = candidate.split()
    if not (2 <= len(words) <= 6):
        return False
    if not all(w[0].isupper() for w in words if w):
        return False
    upper = candidate.upper()
    return not any(x in upper for x in _EXCLUDE_NAME)


def _normalize_name(s):
    return " ".join(s.split()).rstrip(".,;")


def extract_member_name(raw):
    """Return the resigning member's name from the letter signature."""
    # Strip backtick-quoted nicknames: W.J. ``Billy'' Tauzin → W.J. Tauzin
    normalized = re.sub(r"``[^']*''", " ", raw)

    # Strategy 1: name (optional district) immediately before "Member of Congress"
    # or equivalent title. Name may end with comma, period, or nothing.
    for text in (normalized, raw):
        m = re.search(
            rf"\n[ \t]{{3,}}(?:Congressman\s+)?{_NAME_PAT}"
            r"(?:\s+\([A-Z]{2}-\d+\))?[,.]?[ \t]*\n"
            r"[ \t]+(?:Member of Congress\.?|Member,\s+[A-Z]|U\.?S\.?\s+Congressman)",
            text,
        )
        if m:
            return _normalize_name(m.group(1))

    # Strategy 2: name after common closing salutation, at any indentation ≥3 spaces.
    # Name may end with comma, period, or nothing.
    for text in (normalized, raw):
        m = re.search(
            rf"{_SALUTATIONS},\s*\n"
            rf"(?:\s*\n)*"
            rf"[ \t]{{3,}}(?:Congressman\s+)?{_NAME_PAT}[,.]?(?:\s*\n|$)",
            text,
        )
        if m:
            candidate = _normalize_name(m.group(1))
            if _looks_like_name(candidate):
                return candidate

    # Strategy 3: heavily indented title-case line ending in comma
    for line in raw.splitlines():
        if len(line) > 25 and line[:20].strip() == "" and line.strip().endswith(","):
            candidate = line.strip().rstrip(",")
            if _looks_like_name(candidate):
                return candidate

    # Strategy 4: heavily indented title-case line ending in period, near end of doc
    lines = raw.splitlines()
    for i, line in enumerate(lines):
        if len(line) > 25 and line[:20].strip() == "" and line.strip().endswith("."):
            candidate = line.strip().rstrip(".")
            if _looks_like_name(candidate) and i >= len(lines) * 0.6:
                return candidate

    return ""


def parse_file(path):
    html = path.read_text(encoding="utf-8", errors="replace")
    raw = strip_tags(html)

    stem = path.stem
    is_crecb = stem.startswith("CRECB-")

    # Package / granule IDs
    if is_crecb:
        m = re.match(r"(CRECB-\d{4}-pt\d+)", stem)
    else:
        m = re.match(r"(CREC-\d{4}-\d{2}-\d{2})", stem)
    package_id = m.group(1) if m else stem
    granule_id = stem

    uri = f"https://www.govinfo.gov/content/pkg/{package_id}/html/{granule_id}.htm"

    date = extract_date(raw, is_crecb)
    volume, number, page, cr_citation = extract_cr_fields(raw, is_crecb)
    heading = extract_heading(raw)
    committees = extract_committees(heading, raw)
    member_name = extract_member_name(raw)

    return {
        "date": date.strftime("%Y-%m-%d") if date else "",
        "congress": congress_from_date(date) if date else "",
        "member_name": member_name,
        "committees": "; ".join(committees),
        "cr_citation": cr_citation,
        "uri": uri,
        "granule_id": granule_id,
        "package_id": package_id,
    }


def main():
    html_files = [p for p in sorted(INPUT_DIR.glob("*.html")) if p.name != "manifest.csv"]
    if not html_files:
        print(f"No HTML files found in {INPUT_DIR}")
        return

    rows = []
    errors = []
    for path in html_files:
        try:
            rows.append(parse_file(path))
        except Exception as exc:
            errors.append((path.name, str(exc)))

    rows.sort(key=lambda r: r["date"])

    fields = ["date", "congress", "member_name", "committees",
              "cr_citation", "uri", "granule_id", "package_id"]
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Parsed {len(rows)} records → {OUTPUT_CSV}")

    missing_name  = sum(1 for r in rows if not r["member_name"])
    missing_comm  = sum(1 for r in rows if not r["committees"])
    missing_date  = sum(1 for r in rows if not r["date"])
    print(f"  Missing name       : {missing_name}")
    print(f"  Missing committee  : {missing_comm}")
    print(f"  Missing date       : {missing_date}")
    if errors:
        print(f"  Parse errors       : {len(errors)}")
        for name, msg in errors[:5]:
            print(f"    {name}: {msg}")


if __name__ == "__main__":
    main()
