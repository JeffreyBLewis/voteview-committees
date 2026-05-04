#!/usr/bin/env python3
"""
Build senate_committee_spells.csv — one row per senator × committee × congress.

Sources
-------
  SenateCommittees_snapshots/<CODE>_<timestamp>.xml   committee assignments (primary)
  SenatorData_snapshots/SenatorData_<timestamp>.xml   bioguide_id, senator_class
  senate_elections.csv                                 S.Res resolution numbers/dates

Logic
-----
  A spell is created for every (senator, committee_code, congress) tuple observed
  in at least one committee snapshot.

  start_date: S.Res resolution date if a matching election record is found;
              otherwise the date of the first snapshot in that congress.

  end_date:   last day of the congress for completed congresses; blank for the
              current (ongoing) congress. If the senator's last observed snapshot
              is more than 90 days before the congress end, that snapshot date
              is used instead (signals a mid-congress departure).

  Unlike the House, senators do not formally resign from committees, so there
  are no resignation fields.

Output: senate_committee_spells.csv
"""

import csv
import re
import unicodedata
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

SENATE_COMM_DIR  = Path("SenateCommittees_snapshots")
SENATOR_DATA_DIR = Path("SenatorData_snapshots")
ELECTIONS_CSV    = Path("senate_elections.csv")
OUTPUT_CSV       = Path("senate_committee_spells.csv")

# Which party held the Senate majority in each congress.
MAJORITY_PARTY = {
    108: "R", 109: "R", 110: "D", 111: "D",
    112: "D", 113: "D", 114: "R", 115: "R",
    116: "R", 117: "D", 118: "D", 119: "R",
}

# Congress start (inclusive) and end (exclusive) dates.
# End is None for the current ongoing congress.
CONGRESS_DATES = {
    108: (date(2003, 1,  7), date(2005, 1,  4)),
    109: (date(2005, 1,  4), date(2007, 1,  4)),
    110: (date(2007, 1,  4), date(2009, 1,  6)),
    111: (date(2009, 1,  6), date(2011, 1,  5)),
    112: (date(2011, 1,  5), date(2013, 1,  3)),
    113: (date(2013, 1,  3), date(2015, 1,  6)),
    114: (date(2015, 1,  6), date(2017, 1,  3)),
    115: (date(2017, 1,  3), date(2019, 1,  3)),
    116: (date(2019, 1,  3), date(2021, 1,  3)),
    117: (date(2021, 1,  3), date(2023, 1,  3)),
    118: (date(2023, 1,  3), date(2025, 1,  3)),
    119: (date(2025, 1,  3), None),
}

OUTPUT_FIELDS = [
    "congress", "start_date", "start_date_imputed", "end_date", "departure_reason",
    "bioguide_id", "member_name", "state", "senator_class", "party", "party_designation",
    "committee_name", "committee_code", "resolution_rank", "roster_snapshot_rank", "position",
    "resolution", "resolution_date",
]

# Reason a senator's service ended before the close of the congress.
# Keyed by bioguide_id; value is one of: "died", "resigned", "temporary appointment".
DEPARTURE_REASONS: dict[str, str] = {
    "B000243": "resigned",               # Max Baucus → Ambassador to China
    "C001099": "temporary appointment",  # William Cowan, filled Kerry seat
    "C001100": "temporary appointment",  # Jeff Chiesa, filled Lautenberg seat
    "S001141": "resigned",               # Jeff Sessions → Attorney General
    "F000457": "resigned",               # Al Franken
    "S001202": "temporary appointment",  # Luther Strange, filled Sessions seat
    "C000567": "resigned",               # Thad Cochran (health)
    "M000303": "died",                   # John McCain
    "I000055": "resigned",               # Johnny Isakson (Parkinson's)
    "F000062": "died",                   # Dianne Feinstein
    "M000639": "resigned",               # Robert Menendez (post-conviction)
}

# Actual known departure dates for senators who left office mid-congress.
# Used directly as the spell end_date (last day of Senate service).
DEPARTURE_DATES: dict[str, str] = {
    "B000243": "2014-02-07",  # Max Baucus resigned → Ambassador to China
    "C001099": "2013-07-16",  # William Cowan temp appt ended
    "C001100": "2013-10-16",  # Jeff Chiesa temp appt ended
    "S001141": "2017-02-08",  # Jeff Sessions resigned → Attorney General
    "F000457": "2018-01-02",  # Al Franken resigned
    "S001202": "2018-01-03",  # Luther Strange temp appt ended
    "C000567": "2018-04-01",  # Thad Cochran resigned (health)
    "M000303": "2018-08-25",  # John McCain died
    "I000055": "2019-12-31",  # Johnny Isakson resigned (Parkinson's)
    "F000062": "2023-09-29",  # Dianne Feinstein died
    "M000639": "2024-08-20",  # Robert Menendez resigned (post-conviction)
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SMART_APOS = re.compile(r"[''`]")
_WS         = re.compile(r"\s+")
_COMM_PREFIX = re.compile(r"^Committee (?:on|of)(?: the| The)?\s+", re.IGNORECASE)

_COMM_ALIASES = {
    "governmental affairs":                        "homeland security and governmental affairs",
    "labor and human resources":                   "health, education, labor, and pensions",
    "indian affairs":                              "indian affairs",
    "small business":                              "small business and entrepreneurship",
    "agriculture":                                 "agriculture, nutrition, and forestry",
    "aging":                                       "aging",
    "special committee on aging":                  "aging",
    "select committee on intelligence":            "intelligence",
    "ethics":                                      "ethics",
    "select committee on ethics":                  "ethics",
}


def ascii_fold(s: str) -> str:
    """Decompose accented characters to ASCII equivalents (é→e, ñ→n, etc.)."""
    return "".join(
        c for c in unicodedata.normalize("NFD", s)
        if unicodedata.category(c) != "Mn"
    )


def strip_comm_prefix(s: str) -> str:
    s = _SMART_APOS.sub("'", s.strip().rstrip(":"))
    s = _WS.sub(" ", s)
    return _COMM_PREFIX.sub("", s).strip()


def norm_comm(s: str) -> str:
    s = strip_comm_prefix(s).lower()
    s = _SMART_APOS.sub("'", _WS.sub(" ", s)).strip()
    return _COMM_ALIASES.get(s, s)


_TITLE_PREFIX   = re.compile(r"^(Mr\.|Ms\.|Mrs\.|Miss\.|Dr\.)\s+", re.IGNORECASE)
_PARENTHETICAL  = re.compile(r"\(.*?\)")
_OF_STATE       = re.compile(r"\s+of\s+\S.*$", re.IGNORECASE)

# Typos found in source resolutions: election-record last name → canonical last name
_MEMBER_TYPOS = {
    "fisher": "fischer",   # S.Res.16 119th: "Mrs. Fisher" should be "Mrs. Fischer"
}


def lastname_variants(name: str) -> set[str]:
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
    # Expand with known typo corrections
    corrected = {_MEMBER_TYPOS[v] for v in variants if v in _MEMBER_TYPOS}
    variants |= corrected
    return variants


def snap_date_from_path(path: Path) -> date:
    m = re.search(r"(\d{14}|\d{8})", path.name)
    ts = m.group(1)
    return datetime.strptime(ts[:8], "%Y%m%d").date()


def date_to_congress(d: date):
    for cong in sorted(CONGRESS_DATES, reverse=True):
        if d >= CONGRESS_DATES[cong][0]:
            return cong
    return None


# ---------------------------------------------------------------------------
# Load SenatorData snapshots → bioguide + class lookup
# ---------------------------------------------------------------------------

def load_senator_data():
    """
    Returns senator_lookup[(last_lower, state_lower)] = {
        bioguide_id, member_name, senator_class, party
    }

    When a senator served across multiple periods, the most recent record wins.
    A secondary index on bioguide_id handles re-election across congresses.
    """
    lookup: dict[tuple, dict] = {}   # (last, state) → info
    by_date: dict[tuple, date] = {}  # (last, state) → latest snap date seen

    files = sorted(SENATOR_DATA_DIR.glob("SenatorData_*.xml"))
    print(f"  Parsing {len(files)} SenatorData snapshots …", flush=True)

    for path in files:
        snap_d = snap_date_from_path(path)
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError:
            continue

        for member in root.findall(".//member"):
            last  = (member.findtext("last_name")  or "").strip()
            first = (member.findtext("first_name") or "").strip()
            state = (member.findtext("state")      or "").strip()
            bio   = (member.findtext("bioguide_id")or "").strip()
            party = (member.findtext("party")      or "").strip()
            cls_raw = (member.findtext("class")    or "").strip()
            # "Class I" → "I", "Class II" → "II", etc.
            cls = re.sub(r"(?i)^class\s+", "", cls_raw).strip()

            if not (last and state and bio):
                continue

            key = (ascii_fold(last.lower()), state.lower())
            if snap_d >= by_date.get(key, date.min):
                by_date[key] = snap_d
                lookup[key] = {
                    "bioguide_id":   bio,
                    "member_name":   f"{first} {last}".strip(),
                    "senator_class": cls,
                    "party":         party,
                }

    return lookup


# ---------------------------------------------------------------------------
# Load SenatorData snapshots → per-congress tenure tracking
# ---------------------------------------------------------------------------

def load_senator_tenure():
    """
    Returns two dicts:
      snaps_by_cong[bioguide][congress] = sorted list of snapshot dates
      last_seen[bioguide] = most recent date across ALL snapshots

    Used to detect senators who left office before the end of a congress:
      • No appearances in this congress → retired/lost seat at the transition
      • Appearances in congress but last_seen_in_congress is >90 days before
        congress end with no appearances in any subsequent congress →
        mid-congress departure (death, resignation, cabinet appointment)
    """
    snaps: dict[str, dict[int, list]] = defaultdict(lambda: defaultdict(list))
    last_seen: dict[str, date] = {}

    files = sorted(SENATOR_DATA_DIR.glob("SenatorData_*.xml"))
    for path in files:
        snap_d = snap_date_from_path(path)
        cong   = date_to_congress(snap_d)
        if cong is None:
            continue
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError:
            continue
        for member in root.findall(".//member"):
            bio = (member.findtext("bioguide_id") or "").strip()
            if not bio:
                continue
            snaps[bio][cong].append(snap_d)
            if snap_d > last_seen.get(bio, date.min):
                last_seen[bio] = snap_d

    # Sort each congress list
    for bio in snaps:
        for cong in snaps[bio]:
            snaps[bio][cong].sort()

    return snaps, last_seen


def senator_end_date(bio: str, congress: int, cong_end,
                     snaps_by_cong: dict, last_seen: dict) -> tuple[str, str]:
    """
    Return (end_date_str, departure_reason) for a spell.

    departure_reason is "" for normal service through the congress end, or one of
    "died" / "resigned" / "temporary appointment" when the senator left early.
    End dates for early departures are approximated from the last SenatorData snapshot.

    Logic
    -----
    1. No SenatorData appearances in this congress:
       The senator retired or lost their seat at the preceding transition.
       The Senate is a continuing body so they may still appear in early
       committee snapshots; their service ends at the congress start date.

    2. Appearances in this congress, but last appearance is >150 days before
       congress end AND the senator never appears in a later congress:
       Mid-congress departure (death, resignation, cabinet appointment).
       Use last_seen_in_congress as the approximate end date.

    3. Otherwise: use the normal congress end date (or blank if ongoing).
    """
    if not bio or cong_end is None:
        return ("" if cong_end is None else (cong_end - timedelta(days=1)).isoformat(), "")

    cong_start = CONGRESS_DATES[congress][0]
    bio_snaps  = snaps_by_cong.get(bio, {})
    in_cong    = bio_snaps.get(congress, [])

    # Case 1: no appearances in this congress at all — senator left at the transition.
    # Last day served = day before this congress began (= last day of previous congress).
    if not in_cong:
        overall = last_seen.get(bio)
        if overall and overall < cong_start:
            reason = DEPARTURE_REASONS.get(bio, "departed")
            return ((cong_start - timedelta(days=1)).isoformat(), reason)
        return ((cong_end - timedelta(days=1)).isoformat(), "")

    # Case 2: mid-congress departure detected from SenatorData.
    last_in_cong = in_cong[-1]
    days_gap = (cong_end - last_in_cong).days
    if days_gap > 150:
        served_later = any(
            bio_snaps.get(c)
            for c in range(congress + 1, max(CONGRESS_DATES) + 1)
        )
        if not served_later:
            reason = DEPARTURE_REASONS.get(bio, "departed")
            known_date = DEPARTURE_DATES.get(bio)
            if known_date:
                return (known_date, reason)
            return (last_in_cong.isoformat(), reason)

    return ((cong_end - timedelta(days=1)).isoformat(), "")


# ---------------------------------------------------------------------------
# Parse SenateCommittees snapshots → observations
# ---------------------------------------------------------------------------

def parse_committee_snapshots():
    """
    Returns obs: list of
        (snap_date, congress, last_lower, state_lower, comm_code, comm_name,
         party, position)
    and comm_meta: dict[comm_code] = committee_name  (most recent name seen)
    """
    obs = []
    comm_meta: dict[str, str] = {}

    files = sorted(SENATE_COMM_DIR.glob("*.xml"))
    print(f"  Parsing {len(files)} committee snapshots …", flush=True)

    for path in files:
        snap_d  = snap_date_from_path(path)
        congress = date_to_congress(snap_d)
        if congress is None:
            continue

        try:
            root = ET.parse(path).getroot()
        except ET.ParseError as exc:
            print(f"    XML error {path.name}: {exc}")
            continue

        found = root.find("committees")
        committees_el = found if found is not None else root
        comm_name = (committees_el.findtext("committee_name") or "").strip()
        comm_code = (committees_el.findtext("committee_code") or "").strip()

        if comm_code:
            comm_meta[comm_code] = comm_name

        members_el = committees_el.find("members")
        if members_el is None:
            continue

        for member in members_el.findall("member"):
            name_el = member.find("name")
            if name_el is None:
                continue
            last  = (name_el.findtext("last")  or "").strip()
            state = (member.findtext("state")   or "").strip()
            party = (member.findtext("party")   or "").strip()
            pos   = (member.findtext("position")or "").strip()

            if not (last and state and comm_code):
                continue

            obs.append((snap_d, congress, ascii_fold(last.lower()), state.lower(),
                        comm_code, comm_name, party, pos))

    return obs, comm_meta


# ---------------------------------------------------------------------------
# Load senate elections
# ---------------------------------------------------------------------------

def load_elections():
    """
    Returns elec[(congress, lastname_variant, comm_norm)] = {
        resolution, resolution_date, party_designation
    }
    """
    elec: dict[tuple, dict] = {}
    with open(ELECTIONS_CSV, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                cong = int(row["congress"])
            except ValueError:
                continue
            comm = norm_comm(row["committee"])
            rec  = {
                "resolution":        row["resolution"],
                "resolution_date":   row["date"],
                "party_designation": row.get("party_designation", ""),
                "rank":              row.get("rank", ""),
            }
            for ln in lastname_variants(row["member"]):
                for key_ln in {ln, ascii_fold(ln)}:
                    key = (cong, key_ln, comm)
                    existing = elec.get(key)
                    if existing is None or row["date"] < existing["resolution_date"]:
                        elec[key] = rec
    return elec


# ---------------------------------------------------------------------------
# Build replacement-date index from election resolutions
# ---------------------------------------------------------------------------

def load_replacement_index():
    """
    Returns res_members[(congress, party_desig, comm_norm, resolution)]
        = {"date": str, "members": set of ascii-folded last-name variants}

    Used to find the first post-departure S.Res that lists a full replacement
    roster for a committee without the departed senator.  Only resolutions
    whose titles say "constitute … membership" are full rosters; mid-congress
    additions typically name only a single appointment.  We include all here
    and rely on correct party filtering to avoid false matches.
    """
    idx: dict[tuple, dict] = {}
    with open(ELECTIONS_CSV, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                cong = int(row["congress"])
            except ValueError:
                continue
            key = (cong, row["party_designation"],
                   norm_comm(row["committee"]), row["resolution"])
            if key not in idx:
                idx[key] = {"date": row["date"], "members": set()}
            idx[key]["members"] |= {ascii_fold(v)
                                    for v in lastname_variants(row["member"])}
    return idx


def replacement_end_date(last_lower: str, congress: int, comm_norm: str,
                         party: str, after_date: str,
                         res_index: dict) -> str:
    """
    Find the date of the first S.Res (for the senator's party side) that covers
    comm_norm in `congress`, is dated after `after_date`, and does NOT include
    the senator.  Returns a date string or "" if none found.

    Only looks at the senator's own majority/minority side to avoid false
    matches against the opposite party's roster reshuffles.
    """
    maj_party = MAJORITY_PARTY.get(congress, "")
    party_desig = "majority" if party.upper() in (maj_party, "I") else "minority"
    # Independents (Sanders, King) caucus with Dems → treat as majority when Dems lead
    if party.upper() == "I":
        party_desig = "majority" if maj_party == "D" else "minority"

    last_fn = ascii_fold(last_lower)
    best = ""
    for (c, pd, cm, res), info in res_index.items():
        if c != congress or pd != party_desig or cm != comm_norm:
            continue
        if info["date"] <= after_date:
            continue
        if last_fn not in info["members"]:
            if not best or info["date"] < best:
                best = info["date"]
    # Return the day before the replacement resolution: that is the senator's last day.
    if best:
        return (date.fromisoformat(best) - timedelta(days=1)).isoformat()
    return ""


# ---------------------------------------------------------------------------
# Build spells
# ---------------------------------------------------------------------------

def build_spells(obs, comm_meta, senator_lookup, elec, snaps_by_cong, senator_last_seen, res_index):
    # Group: (last, state, comm_code, congress) → [(snap_date, party, position)]
    groups: dict[tuple, list] = defaultdict(list)
    comm_names: dict[tuple, str] = {}  # group key → committee name
    for snap_d, congress, last, state, comm_code, comm_name, party, pos in obs:
        key = (last, state, comm_code, congress)
        groups[key].append((snap_d, party, pos))
        if key not in comm_names and comm_name:
            comm_names[key] = comm_name

    cong_end = {c: v[1] for c, v in CONGRESS_DATES.items()}

    spells = []
    for (last, state, comm_code, congress), entries in groups.items():
        entries.sort()
        first_snap, _, _  = entries[0]
        last_snap, last_party, last_pos = entries[-1]

        # Bioguide lookup (last is already ascii_folded from parse step)
        s_info = senator_lookup.get((last, state), {})
        bio    = s_info.get("bioguide_id", "")

        # Committee name and normalized form
        comm_name  = comm_names.get((last, state, comm_code, congress),
                     comm_meta.get(comm_code, comm_code))
        comm_short = norm_comm(strip_comm_prefix(comm_name))

        # Election lookup
        elec_rec = None
        for ln in lastname_variants(last):
            key = (congress, ln, comm_short)
            if key in elec:
                elec_rec = elec[key]
                break

        resolution        = elec_rec["resolution"]        if elec_rec else ""
        resolution_date   = elec_rec["resolution_date"]   if elec_rec else ""
        committee_rank    = elec_rec["rank"]               if elec_rec else ""
        party_designation = elec_rec["party_designation"] if elec_rec else ""
        if not party_designation:
            senator_party = last_party or s_info.get("party", "")
            maj = MAJORITY_PARTY.get(congress, "")
            # Independents (Sanders, King) caucus with Democrats
            is_maj = (senator_party == maj) or (senator_party == "I" and maj == "D")
            party_designation = "majority" if is_maj else "minority"

        if resolution_date:
            start_date = resolution_date
            start_date_imputed = False
        else:
            start_date = first_snap.isoformat()
            start_date_imputed = True

        # End date: adjusted for senators who left office before the congress ended.
        end_d = cong_end.get(congress)
        end_date, departure_reason = senator_end_date(
            bio, congress, end_d, snaps_by_cong, senator_last_seen
        )

        # For senators with an unknown exact departure date, try to narrow it using
        # the first replacement S.Res that covers their committee without them.
        if departure_reason and bio not in DEPARTURE_DATES:
            last_party = last_party or s_info.get("party", "")
            rep_date = replacement_end_date(
                last, congress, comm_short, last_party, end_date, res_index
            )
            if rep_date:
                end_date = rep_date

        spells.append({
            "congress":            congress,
            "start_date":          start_date,
            "start_date_imputed":  start_date_imputed,
            "end_date":            end_date,
            "departure_reason":    departure_reason,
            "bioguide_id":         bio,
            "member_name":         s_info.get("member_name", f"{last.title()}"),
            "state":               state.upper(),
            "senator_class":       s_info.get("senator_class", ""),
            "party":               last_party or s_info.get("party", ""),
            "party_designation":   party_designation,
            "committee_name":      comm_name,
            "committee_code":      comm_code,
            "resolution_rank":     committee_rank,
            "roster_snapshot_rank": "",
            "position":            last_pos,
            "resolution":          resolution,
            "resolution_date":     resolution_date,
        })

    return spells


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("Loading senator data …")
    senator_lookup = load_senator_data()
    print(f"  {len(senator_lookup):,} unique (lastname, state) entries")

    print("Loading senator tenure …")
    snaps_by_cong, senator_last_seen = load_senator_tenure()
    print(f"  {len(senator_last_seen):,} bioguides tracked across SenatorData snapshots")

    print("Loading senate elections …")
    elec = load_elections()
    print(f"  {len(elec):,} election index entries")

    print("Parsing committee snapshots …")
    obs, comm_meta = parse_committee_snapshots()
    print(f"  {len(obs):,} (senator, committee, snapshot) observations")
    print(f"  {len(comm_meta):,} unique committee codes")

    print("Loading replacement index …")
    res_index = load_replacement_index()
    print(f"  {len(res_index):,} (congress, party, committee, resolution) entries")

    print("Building spells …")
    spells = build_spells(obs, comm_meta, senator_lookup, elec, snaps_by_cong, senator_last_seen, res_index)
    spells.sort(key=lambda r: (r["congress"], r["start_date"],
                               r["bioguide_id"], r["committee_code"]))
    print(f"  {len(spells):,} spells")

    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        writer.writerows(spells)

    with_elec   = sum(1 for s in spells if s["resolution"])
    missing_bio = sum(1 for s in spells if not s["bioguide_id"])
    from collections import Counter
    reasons = Counter(s["departure_reason"] for s in spells if s["departure_reason"])
    print(f"\n  With election record   : {with_elec:,} ({with_elec/len(spells)*100:.1f}%)")
    for reason, n in sorted(reasons.items()):
        print(f"  Departed ({reason:<22}): {n:,}")
    print(f"  Missing bioguide_id    : {missing_bio}")
    print(f"\nOutput → {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
