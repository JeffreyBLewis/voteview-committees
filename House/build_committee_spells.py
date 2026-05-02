#!/usr/bin/env python3
"""
Build house_committee_spells.csv — one row per member × committee × congress.

Sources
-------
  MemberData_snapshots/MemberData_*.xml   member info and committee assignments
  house_elections.csv                      resolution numbers and dates
  house_resignations.csv                   CR citations and resignation dates
  house_committee_codes.json               committee code → full name

Logic
-----
  Spells are derived from roster snapshots: a spell exists for every
  (bioguide, comcode, congress) tuple observed in at least one snapshot.

  start_date: resolution date from house_elections.csv if a match is found;
              otherwise the date of the first snapshot in that congress.

  end_date:   resignation date from house_resignations.csv if a match is found;
              otherwise the last day of the congress (for completed congresses);
              blank for the current congress.

  Committee rank and leadership role are taken from the last snapshot within
  the congress so they reflect the member's final position.

Output: house_committee_spells.csv
"""

import csv
import gzip
import json
import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

SNAPSHOT_DIR  = Path("MemberData_snapshots")
COMM_CODES_FILE = Path("house_committee_codes.json")
ELECTIONS_CSV   = Path("house_elections.csv")
RESIGNATIONS_CSV = Path("house_resignations.csv")
OUTPUT_CSV      = Path("house_committee_spells.csv")

# Inclusive start, exclusive end (day after last session) for each congress.
# End date is None for the current (ongoing) congress.
CONGRESS_DATES = {
    114: (date(2015, 1,  6), date(2017, 1,  3)),
    115: (date(2017, 1,  3), date(2019, 1,  3)),
    116: (date(2019, 1,  3), date(2021, 1,  3)),
    117: (date(2021, 1,  3), date(2023, 1,  3)),
    118: (date(2023, 1,  3), date(2025, 1,  3)),
    119: (date(2025, 1,  3), None),
}

OUTPUT_FIELDS = [
    "congress", "start_date", "start_date_imputed", "end_date", "departure_reason",
    "bioguide_id", "member_name", "state", "district", "party",
    "committee_name", "committee_code", "committee_rank", "role",
    "resolution", "resolution_date",
    "cr_citation", "resignation_date",
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def snapshot_date(path: Path) -> date:
    m = re.search(r"(\d{14})", path.name)
    if not m:
        raise ValueError(f"Cannot parse timestamp from {path.name}")
    return datetime.strptime(m.group(1), "%Y%m%d%H%M%S").date()


def date_to_congress(d: date):
    for cong in range(119, 113, -1):
        if d >= CONGRESS_DATES[cong][0]:
            return cong
    return None


_COMM_PREFIX = re.compile(r"^Committee (?:on|of)(?: the| The)?\s+", re.IGNORECASE)
_SMART_APOS  = re.compile(r"[''`]")
_WS          = re.compile(r"\s+")

_COMM_ALIASES = {
    "oversight and accountability":      "oversight and government reform",
    "oversight and reform":              "oversight and government reform",
    "education and the workforce":       "education and labor",
    "education and workforce":           "education and labor",
    "veterans affairs":                  "veterans' affairs",
}


def strip_comm_prefix(s: str) -> str:
    s = _SMART_APOS.sub("'", s.strip().rstrip(":"))
    s = _WS.sub(" ", s)
    return _COMM_PREFIX.sub("", s).strip()


def norm_comm(s: str) -> str:
    s = strip_comm_prefix(s).lower()
    s = _SMART_APOS.sub("'", s)
    s = _WS.sub(" ", s).strip()
    return _COMM_ALIASES.get(s, s)


_TITLE_PREFIX = re.compile(r"^(Mr\.|Ms\.|Mrs\.|Miss\.|Dr\.)\s+", re.IGNORECASE)
_PARENTHETICAL = re.compile(r"\(.*?\)")
_OF_STATE = re.compile(r"\s+of\s+\S.*$", re.IGNORECASE)


def lastname_variants(name: str) -> set[str]:
    """Return a set of lower-case name tokens useful for fuzzy matching."""
    name = _TITLE_PREFIX.sub("", name.strip())
    name = _PARENTHETICAL.sub("", name)
    name = _OF_STATE.sub("", name)
    name = name.strip().rstrip(".,")
    parts = name.split()
    if not parts:
        return set()
    variants = {parts[-1].lower(), name.lower()}
    if len(parts) >= 2:
        variants.add(" ".join(parts[-2:]).lower())
    return variants


# ---------------------------------------------------------------------------
# Load committee codes
# ---------------------------------------------------------------------------

def load_comm_codes() -> dict[str, str]:
    with open(COMM_CODES_FILE, encoding="utf-8") as f:
        raw = json.load(f)
    # Return code → short name (committee prefix stripped)
    return {code: strip_comm_prefix(name) for code, name in raw.items()}


# ---------------------------------------------------------------------------
# Parse roster snapshots
# ---------------------------------------------------------------------------

def parse_snapshots():
    """
    Returns
    -------
    member_info : dict[bioguide_id, dict]
        Latest known info for each member.
    obs : list[tuple]
        (snapshot_date, congress, bioguide, comcode, rank_int, leadership_str)
    """
    member_info: dict[str, dict] = {}
    obs: list[tuple] = []

    files = sorted(SNAPSHOT_DIR.glob("MemberData_*.xml.gz"))
    print(f"  Parsing {len(files)} snapshots …", flush=True)

    for path in files:
        snap_date = snapshot_date(path)
        congress = date_to_congress(snap_date)
        if congress is None:
            continue

        try:
            with gzip.open(path, "rb") as fh:
                tree = ET.parse(fh)
        except ET.ParseError as exc:
            print(f"    XML error {path.name}: {exc}")
            continue

        root = tree.getroot()

        for member in root.findall(".//member"):
            mi = member.find("member-info")
            if mi is None:
                continue

            bio = (mi.findtext("bioguideID") or "").strip()
            if not bio:
                continue

            state_el = mi.find("state")
            state = state_el.get("postal-code", "") if state_el is not None else ""

            info = {
                "bioguide_id":   bio,
                "member_name":   (mi.findtext("official-name") or "").strip(),
                "lastname":      (mi.findtext("lastname") or "").strip().lower(),
                "state":         state,
                "district":      (mi.findtext("district") or "").strip(),
                "party":         (mi.findtext("party") or "").strip(),
            }
            # Keep the most recent record for each bioguide
            if bio not in member_info or snap_date >= member_info[bio]["_snap"]:
                member_info[bio] = {**info, "_snap": snap_date}

            ca = member.find("committee-assignments")
            if ca is None:
                continue
            for c in ca.findall("committee"):
                comcode = c.get("comcode", "").strip()
                if not comcode:
                    continue
                try:
                    rank = int(c.get("rank") or 0)
                except ValueError:
                    rank = 0
                leadership = (c.get("leadership") or "").strip()
                obs.append((snap_date, congress, bio, comcode, rank, leadership))

    return member_info, obs


# ---------------------------------------------------------------------------
# Load elections
# ---------------------------------------------------------------------------

def load_elections():
    """
    Returns a lookup:
        elec[(congress, lastname_variant, comm_norm)] = {resolution, resolution_date, role}

    Multiple variants per row (single last token, last two tokens, full stripped name).
    When multiple resolutions match, the one with the earlier date is preferred.
    """
    elec: dict[tuple, dict] = {}
    with open(ELECTIONS_CSV, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                cong = int(row["congress"])
            except ValueError:
                continue
            comm = norm_comm(row["committee"])
            rec = {
                "resolution":      row["resolution"],
                "resolution_date": row["date"],
                "role":            row.get("role", ""),
            }
            for ln in lastname_variants(row["member"]):
                key = (cong, ln, comm)
                existing = elec.get(key)
                if existing is None or row["date"] < existing["resolution_date"]:
                    elec[key] = rec
    return elec


# ---------------------------------------------------------------------------
# Load predecessor info (chamber departures: death, resignation, expulsion)
# ---------------------------------------------------------------------------

def load_predecessor_info():
    """
    Parse MemberData snapshots for <predecessor-info> elements.
    Returns pred_info[(bioguide_id, congress)] = {vacate_date, cause}

    Each replacement member's XML record includes the bioguide_id, vacate date,
    and departure cause (D=died, R=resigned, E=expelled) for the member they replaced.
    The congress is derived from the vacate_date, not the snapshot date.
    """
    pred_info: dict[tuple, dict] = {}

    for path in sorted(SNAPSHOT_DIR.glob("MemberData_*.xml.gz")):
        try:
            with gzip.open(path, "rb") as fh:
                root = ET.parse(fh).getroot()
        except ET.ParseError:
            continue

        for member in root.findall(".//member"):
            pi = member.find("predecessor-info")
            if pi is None:
                continue

            cause = pi.get("cause", "")
            bio   = (pi.findtext("pred-memindex") or "").strip()
            vd_el = pi.find("pred-vacate-date")
            if vd_el is None or not bio:
                continue

            vacate_d = None
            attr = vd_el.get("date", "")
            if re.fullmatch(r"\d{8}", attr):
                vacate_d = datetime.strptime(attr, "%Y%m%d").date()
            else:
                for fmt in ("%B %d, %Y", "%B  %d, %Y", "%Y-%m-%d"):
                    try:
                        vacate_d = datetime.strptime((vd_el.text or "").strip(), fmt).date()
                        break
                    except ValueError:
                        pass
            if vacate_d is None:
                continue

            cong = date_to_congress(vacate_d)
            if cong is None:
                continue

            key = (bio, cong)
            existing = pred_info.get(key)
            if existing is None or vacate_d > existing["vacate_date"]:
                pred_info[key] = {"vacate_date": vacate_d, "cause": cause}

    return pred_info


# ---------------------------------------------------------------------------
# Load resignations
# ---------------------------------------------------------------------------

def load_resignations():
    """
    Returns a lookup:
        resign[(congress, lastname_variant, comm_norm)] = {cr_citation, resignation_date}
    """
    resign: dict[tuple, dict] = {}
    with open(RESIGNATIONS_CSV, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                cong = int(row["congress"])
            except ValueError:
                continue
            rec = {
                "cr_citation":      row.get("cr_citation", ""),
                "resignation_date": row.get("date", ""),
            }
            comms_raw = row.get("committees", "")
            comms = [norm_comm(c) for c in comms_raw.split(";") if c.strip()]
            for ln in lastname_variants(row["member_name"]):
                # Index against each committee listed in the resignation
                for comm in comms:
                    key = (cong, ln, comm)
                    existing = resign.get(key)
                    if existing is None or row["date"] < existing["resignation_date"]:
                        resign[key] = rec
                # Also index without a specific committee so we can fallback
                key_any = (cong, ln, "")
                if key_any not in resign:
                    resign[key_any] = {**rec, "_comms": comms}
    return resign


# ---------------------------------------------------------------------------
# Build spells
# ---------------------------------------------------------------------------

def build_spells(member_info, obs, elec, resign, comm_codes, pred_info):
    """
    Aggregate snapshot observations into one spell per (bioguide, comcode, congress),
    then enrich with election and resignation data.
    """
    # Group observations: (bioguide, comcode, congress) → list of (snap_date, rank, leadership)
    groups: dict[tuple, list] = defaultdict(list)
    for snap_date, congress, bio, comcode, rank, leadership in obs:
        groups[(bio, comcode, congress)].append((snap_date, rank, leadership))

    spells = []

    for (bio, comcode, congress), entries in groups.items():
        entries.sort()  # by snap_date

        first_snap = entries[0][0]
        last_snap  = entries[-1][0]
        # Use last snapshot's rank and leadership
        _, last_rank, last_leadership = entries[-1]

        m_info = member_info.get(bio, {})
        lastname = m_info.get("lastname", "")
        comm_full = comm_codes.get(comcode, comcode)
        comm_short = norm_comm(comm_full)

        # ── Election lookup ──────────────────────────────────────────────
        elec_rec = None
        for ln in lastname_variants(lastname):
            key = (congress, ln, comm_short)
            if key in elec:
                elec_rec = elec[key]
                break

        resolution      = elec_rec["resolution"]      if elec_rec else ""
        resolution_date = elec_rec["resolution_date"] if elec_rec else ""
        role            = (last_leadership or
                           (elec_rec["role"] if elec_rec else ""))

        # Start date: prefer resolution date; fall back to first snapshot
        if resolution_date:
            start_date = resolution_date
            start_date_imputed = False
        else:
            start_date = first_snap.isoformat()
            start_date_imputed = True

        # ── Resignation lookup ────────────────────────────────────────────
        resign_rec = None
        for ln in lastname_variants(lastname):
            key = (congress, ln, comm_short)
            if key in resign:
                resign_rec = resign[key]
                break
        # Fallback: match on any committee from that resignation record
        if resign_rec is None:
            for ln in lastname_variants(lastname):
                key_any = (congress, ln, "")
                if key_any in resign:
                    cand = resign[key_any]
                    if comm_short in cand.get("_comms", []):
                        resign_rec = cand
                        break

        cr_citation      = resign_rec["cr_citation"]      if resign_rec else ""
        resignation_date = resign_rec["resignation_date"] if resign_rec else ""

        # End date: resignation date → congress end → blank (ongoing)
        if resignation_date:
            end_date = resignation_date
        else:
            cong_end = CONGRESS_DATES.get(congress, (None, None))[1]
            end_date = cong_end.isoformat() if cong_end else ""

        # ── Chamber departure (death, resignation from seat, expulsion) ───────
        # If the member left the House mid-congress, cap end_date at their
        # departure date when it is earlier than what we already have.
        departure_reason = ""
        pred_rec = pred_info.get((bio, congress))
        if pred_rec:
            vacate_str = pred_rec["vacate_date"].isoformat()
            departure_reason = {
                "D": "died", "R": "resigned", "E": "expelled"
            }.get(pred_rec["cause"], pred_rec["cause"])
            if not end_date or vacate_str < end_date:
                end_date = vacate_str

        spells.append({
            "congress":        congress,
            "start_date":      start_date,
            "start_date_imputed": start_date_imputed,
            "end_date":        end_date,
            "departure_reason": departure_reason,
            "bioguide_id":     bio,
            "member_name":     m_info.get("member_name", ""),
            "state":           m_info.get("state", ""),
            "district":        m_info.get("district", ""),
            "party":           m_info.get("party", ""),
            "committee_name":  comm_full,
            "committee_code":  comcode,
            "committee_rank":  last_rank,
            "role":            role,
            "resolution":      resolution,
            "resolution_date": resolution_date,
            "cr_citation":     cr_citation,
            "resignation_date": resignation_date,
        })

    return spells


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("Loading committee codes …")
    comm_codes = load_comm_codes()

    print("Loading elections …")
    elec = load_elections()
    print(f"  {len(elec):,} election index entries")

    print("Loading resignations …")
    resign = load_resignations()
    print(f"  {len(resign):,} resignation index entries")

    print("Loading predecessor info …")
    pred_info = load_predecessor_info()
    print(f"  {len(pred_info):,} chamber departure records")

    print("Parsing snapshots …")
    member_info, obs = parse_snapshots()
    print(f"  {len(member_info):,} unique members")
    print(f"  {len(obs):,} (member, committee, snapshot) observations")

    print("Building spells …")
    spells = build_spells(member_info, obs, elec, resign, comm_codes, pred_info)
    spells.sort(key=lambda r: (r["congress"], r["start_date"], r["bioguide_id"], r["committee_code"]))
    print(f"  {len(spells):,} spells")

    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        writer.writerows(spells)

    # Summary stats
    from collections import Counter
    with_elec    = sum(1 for s in spells if s["resolution"])
    with_resign  = sum(1 for s in spells if s["resignation_date"])
    missing_name = sum(1 for s in spells if not s["member_name"])
    reasons      = Counter(s["departure_reason"] for s in spells if s["departure_reason"])
    print(f"\n  With election record   : {with_elec:,}")
    print(f"  With resignation       : {with_resign:,}")
    for reason, n in sorted(reasons.items()):
        print(f"  Departed ({reason:<10})  : {n:,}")
    print(f"  Missing member name    : {missing_name}")
    print(f"\nOutput → {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
