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
import unicodedata
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

# Which party held the House majority in each congress.
MAJORITY_PARTY = {
    114: "R", 115: "R",
    116: "D", 117: "D",
    118: "R", 119: "R",
}

# State postal abbreviation → lowercase full name (for "of State" key building).
_STATE_NAMES = {
    "AL": "alabama", "AK": "alaska", "AZ": "arizona", "AR": "arkansas",
    "CA": "california", "CO": "colorado", "CT": "connecticut", "DE": "delaware",
    "FL": "florida", "GA": "georgia", "HI": "hawaii", "ID": "idaho",
    "IL": "illinois", "IN": "indiana", "IA": "iowa", "KS": "kansas",
    "KY": "kentucky", "LA": "louisiana", "ME": "maine", "MD": "maryland",
    "MA": "massachusetts", "MI": "michigan", "MN": "minnesota", "MS": "mississippi",
    "MO": "missouri", "MT": "montana", "NE": "nebraska", "NV": "nevada",
    "NH": "new hampshire", "NJ": "new jersey", "NM": "new mexico", "NY": "new york",
    "NC": "north carolina", "ND": "north dakota", "OH": "ohio", "OK": "oklahoma",
    "OR": "oregon", "PA": "pennsylvania", "RI": "rhode island", "SC": "south carolina",
    "SD": "south dakota", "TN": "tennessee", "TX": "texas", "UT": "utah",
    "VT": "vermont", "VA": "virginia", "WA": "washington", "WV": "west virginia",
    "WI": "wisconsin", "WY": "wyoming", "DC": "district of columbia",
    "PR": "puerto rico", "GU": "guam", "VI": "virgin islands",
    "AS": "american samoa", "MP": "northern mariana islands",
}

OUTPUT_FIELDS = [
    "congress", "start_date", "start_date_imputed", "end_date", "departure_reason",
    "bioguide_id", "member_name", "state", "district", "party", "party_designation",
    "committee_name", "committee_code", "resolution_rank", "roster_snapshot_rank", "role",
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


_TITLE_PREFIX = re.compile(r"^(Mr\.|Ms\.|Mrs\.|Miss\.?|Dr\.)\s+", re.IGNORECASE)
_PARENTHETICAL = re.compile(r"\(.*?\)")
_OF_STATE = re.compile(r"\s+of\s+\S.*$", re.IGNORECASE)
_NAME_SUFFIX = re.compile(r",?\s+(Jr\.?|Sr\.?|I{2,}|IV|V|VI{0,3})\s*$", re.IGNORECASE)


def fold_diacritics(s: str) -> str:
    """Normalize accented characters to their ASCII equivalents (e.g., á → a)."""
    return unicodedata.normalize("NFD", s).encode("ascii", "ignore").decode("ascii")


def lastname_variants(name: str) -> set[str]:
    """Return a set of lower-case ASCII name tokens useful for fuzzy matching.

    When an "of STATENAME" qualifier is present it is preserved as an additional
    variant (e.g. "davis of california") so that state-qualified names take
    precedence in lookups over plain last names.
    """
    name = fold_diacritics(name)
    name = _TITLE_PREFIX.sub("", name.strip())
    name = _PARENTHETICAL.sub("", name)
    name = _NAME_SUFFIX.sub("", name)
    name_with_state = name.strip().rstrip(".,")
    name_plain = _OF_STATE.sub("", name_with_state).strip().rstrip(".,")
    parts = name_plain.split()
    if not parts:
        return set()
    variants = {parts[-1].lower(), name_plain.lower()}
    if len(parts) >= 2:
        variants.add(" ".join(parts[-2:]).lower())
    if name_with_state.lower() != name_plain.lower():
        variants.add(name_with_state.lower())
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
        (snapshot_date, congress, bioguide, comcode, rank_int, leadership_str, party_str)
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
                "district":      re.sub(r"(?<=\d)(st|nd|rd|th)$", "", (mi.findtext("district") or "").strip(), flags=re.IGNORECASE),
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
                obs.append((snap_date, congress, bio, comcode, rank, leadership, info["party"]))

    # Build per-congress party lookup: (congress, name_variant) → party
    # Keyed by all lastname_variants of the member's name so that multi-word names
    # like "austin scott" disambiguate from plain "scott" collisions.
    # Keep only the last snapshot per (bio, congress) to capture mid-congress switches.
    last_snap: dict[tuple, tuple] = {}  # (bio, congress) → (snap_date, party)
    for snap_date, congress, bio, comcode, rank, leadership, party in obs:
        bc = (bio, congress)
        if bc not in last_snap or snap_date > last_snap[bc][0]:
            last_snap[bc] = (snap_date, party)

    party_by_cong: dict[tuple, str] = {}
    for (bio, congress), (_, party) in last_snap.items():
        bio_info = member_info.get(bio, {})
        member_name = bio_info.get("member_name", "")
        state = bio_info.get("state", "")
        if member_name and party:
            for variant in lastname_variants(member_name):
                party_by_cong[(congress, variant)] = party
            # Also store "lastname of statename" for disambiguation when elections
            # use "Mr. Davis of California" rather than a first name.
            state_name = _STATE_NAMES.get(state, "")
            lastname = fold_diacritics(bio_info.get("lastname", "")).lower()
            if state_name and lastname:
                party_by_cong[(congress, f"{lastname} of {state_name}")] = party

    return member_info, obs, party_by_cong


# ---------------------------------------------------------------------------
# Load elections
# ---------------------------------------------------------------------------

def _build_within_party_roster(rows_with_idx: list[tuple[int, dict]]) -> dict[int, int]:
    """
    Build a within-party seniority roster from election rows for a single party side.

    Base elections (no rank_after) append members in chronological resolution order.
    Mid-congress additions (rank_after present) are inserted immediately after their
    named anchor; ties sharing the same anchor are broken by their ordinal rank.

    Returns {global_row_index: within_party_rank}.
    """
    sorted_rows = sorted(rows_with_idx,
                         key=lambda x: (x[1]["date"] or "0", int(x[1]["rank"] or 0)))

    res_groups: dict[tuple, list[tuple[int, dict]]] = {}
    for idx, row in sorted_rows:
        key = (row["date"], row["resolution"])
        res_groups.setdefault(key, []).append((idx, row))

    roster: list[tuple[frozenset, int]] = []

    for key in sorted(res_groups):
        group = res_groups[key]
        has_rank_after = any(row["rank_after"] for _, row in group)

        if not has_rank_after:
            for idx, row in group:
                roster.append((frozenset(lastname_variants(row["member"])), idx))
        else:
            anchor_map: dict[tuple, list[tuple[int, dict]]] = {}
            for idx, row in group:
                if row["rank_after"]:
                    member_vars = frozenset(lastname_variants(row["member"]))
                    roster[:] = [(nvs, i) for nvs, i in roster if not (nvs & member_vars)]
                    ak = tuple(sorted(lastname_variants(row["rank_after"])))
                    anchor_map.setdefault(ak, []).append((idx, row))
                else:
                    member_vars = frozenset(lastname_variants(row["member"]))
                    if not any(nvs & member_vars for nvs, _ in roster):
                        roster.append((frozenset(lastname_variants(row["member"])), idx))

            anchored: list[tuple[int, list]] = []
            for ak, additions in anchor_map.items():
                anchor_vars = set(ak)
                pos = next(
                    (i for i, (nvs, _) in enumerate(roster) if nvs & anchor_vars),
                    len(roster) - 1,
                )
                anchored.append((pos, additions))

            for pos, additions in sorted(anchored, key=lambda x: -x[0]):
                for j, (idx, row) in enumerate(additions):
                    roster.insert(pos + 1 + j,
                                  (frozenset(lastname_variants(row["member"])), idx))

    return {idx: rank for rank, (_, idx) in enumerate(roster, 1)}


def _classify_resolution(members: list[str], party_fn):
    """
    Classify a resolution as 'majority' or 'minority' by majority vote.
    Returns None if the resolution is genuinely mixed or has only one member.
    Uses an 80% threshold so a single misclassified member doesn't flip a result.
    """
    if len(members) <= 1:
        return None
    results = [party_fn(m) for m in members]
    maj = results.count("majority")
    total = len(results)
    if maj / total >= 0.8:
        return "majority"
    if (total - maj) / total >= 0.8:
        return "minority"
    return None


def _resolve_committee_ranks(rows_with_idx: list[tuple[int, dict]],
                              party_fn) -> dict[int, int]:
    """
    Split rows into majority/minority and build a within-party roster for each side.

    Per-member classification uses party_fn, but each resolution's classification
    is validated by majority vote: if ≥80% of a resolution's members classify as
    one side, all members of that resolution are assigned to that side.  This
    prevents a single ambiguous last name (e.g., "Mrs. Murphy" when both a
    Democrat and a Republican share the surname) from being placed in the wrong
    party's roster.

    Returns {global_row_index: within_party_rank}.
    """
    # Group rows by resolution to enable resolution-level classification
    by_res: dict[tuple, list[tuple[int, dict]]] = defaultdict(list)
    for idx, row in rows_with_idx:
        key = (row["date"], row["resolution"])
        by_res[key].append((idx, row))

    maj_rows: list[tuple[int, dict]] = []
    min_rows: list[tuple[int, dict]] = []
    for res_rows in by_res.values():
        members = [row["member"] for _, row in res_rows]
        res_party = _classify_resolution(members, party_fn)
        for idx, row in res_rows:
            member_party = party_fn(row["member"])
            effective_party = res_party if res_party else member_party
            if effective_party == "majority":
                maj_rows.append((idx, row))
            else:
                min_rows.append((idx, row))

    overrides: dict[int, int] = {}
    if maj_rows:
        overrides.update(_build_within_party_roster(maj_rows))
    if min_rows:
        overrides.update(_build_within_party_roster(min_rows))
    return overrides


def load_elections(party_by_cong: dict):
    """
    Returns a lookup:
        elec[(congress, lastname_variant, comm_norm)] = {resolution, resolution_date, role, rank}

    Within-party seniority ranks are computed for every committee by combining all
    election resolutions for the same party side in chronological order.  Mid-congress
    additions that specify rank_after are inserted after their named anchor within the
    same party's roster.

    When multiple resolutions match the same member, the rank_after-based record is
    preferred; otherwise the earlier-dated record wins.
    """
    all_rows: list[tuple[int, dict]] = []
    with open(ELECTIONS_CSV, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                int(row["congress"])
            except ValueError:
                continue
            all_rows.append((len(all_rows), row))

    by_comm: dict[tuple, list[tuple[int, dict]]] = defaultdict(list)
    for idx, row in all_rows:
        cong = int(row["congress"])
        comm = norm_comm(row["committee"])
        by_comm[(cong, comm)].append((idx, row))

    rank_overrides: dict[int, int] = {}
    for (cong, comm), rows_with_idx in by_comm.items():
        maj_party = MAJORITY_PARTY.get(cong, "")

        def party_fn(member_name, cong=cong, maj_party=maj_party):
            # Build variants: standard lastname_variants PLUS title-stripped name
            # with "of State" preserved ("davis of california") for disambiguation.
            variants = set(lastname_variants(member_name))
            title_stripped = _TITLE_PREFIX.sub("", fold_diacritics(member_name).strip()).strip().rstrip(".,").lower()
            variants.add(title_stripped)
            # Try longest variant first so "davis of california" beats plain "davis"
            for ln in sorted(variants, key=len, reverse=True):
                party = party_by_cong.get((cong, ln), "")
                if party:
                    return "majority" if party == maj_party else "minority"
            return "minority"

        rank_overrides.update(_resolve_committee_ranks(rows_with_idx, party_fn))

    elec: dict[tuple, dict] = {}
    for idx, row in all_rows:
        cong    = int(row["congress"])
        comm    = norm_comm(row["committee"])
        from_ra = bool(row["rank_after"])
        rec = {
            "resolution":      row["resolution"],
            "resolution_date": row["date"],
            "role":            row.get("role", ""),
            "rank":            str(rank_overrides.get(idx, row["rank"])),
            "from_rank_after": from_ra,
        }
        # Build lookup keys: standard lastname variants PLUS the title-stripped
        # name with "of State" preserved (e.g., "johnson of georgia") so that
        # same-last-name members on the same committee are disambiguated.
        title_stripped = _TITLE_PREFIX.sub("", row["member"].strip()).strip().rstrip(".,").lower()
        keys_to_store = set(lastname_variants(row["member"]))
        keys_to_store.add(title_stripped)
        for ln in sorted(keys_to_store, key=len, reverse=True):
            key = (cong, ln, comm)
            existing = elec.get(key)
            prefer_new = (
                existing is None
                or (from_ra and not existing["from_rank_after"])
                or (from_ra == existing["from_rank_after"] and row["date"] < existing["resolution_date"])
            )
            if prefer_new:
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
    # Group observations: (bioguide, comcode, congress) → list of (snap_date, rank, leadership, party)
    groups: dict[tuple, list] = defaultdict(list)
    for snap_date, congress, bio, comcode, rank, leadership, party in obs:
        groups[(bio, comcode, congress)].append((snap_date, rank, leadership, party))

    spells = []

    for (bio, comcode, congress), entries in groups.items():
        entries.sort()  # by snap_date

        first_snap = entries[0][0]
        last_snap  = entries[-1][0]
        # Use last snapshot's rank, leadership, and party
        _, last_rank, last_leadership, last_party = entries[-1]

        m_info = member_info.get(bio, {})
        lastname    = m_info.get("lastname", "")
        member_name = m_info.get("member_name", "")
        comm_full = comm_codes.get(comcode, comcode)
        comm_short = norm_comm(comm_full)

        # ── Election lookup ──────────────────────────────────────────────
        # Use all name variants, longest first, so "david scott" beats plain "scott"
        # and "johnson of georgia" beats plain "johnson" when members share a last name.
        elec_rec = None
        state = m_info.get("state", "")
        state_name = _STATE_NAMES.get(state, "")
        elec_variants = sorted(
            lastname_variants(member_name) | lastname_variants(lastname),
            key=len, reverse=True,
        )
        # Prepend "lastname of statename" so it's tried before plain lastname
        if state_name and lastname:
            elec_variants = [f"{fold_diacritics(lastname)} of {state_name}"] + elec_variants
        for ln in elec_variants:
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

        party = last_party or m_info.get("party", "")
        maj   = MAJORITY_PARTY.get(congress, "")
        party_designation = "majority" if (party and party == maj) else "minority"

        spells.append({
            "congress":          congress,
            "start_date":        start_date,
            "start_date_imputed": start_date_imputed,
            "end_date":          end_date,
            "departure_reason":  departure_reason,
            "bioguide_id":       bio,
            "member_name":       m_info.get("member_name", ""),
            "state":             m_info.get("state", ""),
            "district":          m_info.get("district", ""),
            "party":             party,
            "party_designation": party_designation,
            "committee_name":    comm_full,
            "committee_code":       comcode,
            "resolution_rank":      elec_rec["rank"] if elec_rec and elec_rec["rank"] else "",
            "roster_snapshot_rank": str(last_rank) if last_rank else "",
            "role":                 role,
            "resolution":        resolution,
            "resolution_date":   resolution_date,
            "cr_citation":       cr_citation,
            "resignation_date":  resignation_date,
        })

    return spells


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("Loading committee codes …")
    comm_codes = load_comm_codes()

    print("Parsing snapshots …")
    member_info, obs, party_by_cong = parse_snapshots()
    print(f"  {len(member_info):,} unique members")
    print(f"  {len(obs):,} (member, committee, snapshot) observations")

    print("Loading elections …")
    elec = load_elections(party_by_cong)
    print(f"  {len(elec):,} election index entries")

    print("Loading resignations …")
    resign = load_resignations()
    print(f"  {len(resign):,} resignation index entries")

    print("Loading predecessor info …")
    pred_info = load_predecessor_info()
    print(f"  {len(pred_info):,} chamber departure records")

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
    both_ranks   = [(s["resolution_rank"], s["roster_snapshot_rank"])
                    for s in spells
                    if s["resolution_rank"] and s["roster_snapshot_rank"]]
    rank_match   = sum(1 for r, s in both_ranks if str(r) == str(s))
    rank_differ  = len(both_ranks) - rank_match
    print(f"\n  With election record   : {with_elec:,}")
    print(f"  With resignation       : {with_resign:,}")
    for reason, n in sorted(reasons.items()):
        print(f"  Departed ({reason:<10})  : {n:,}")
    print(f"  Missing member name    : {missing_name}")
    print(f"\n  Rank comparison (spells with both sources):")
    print(f"    Total                : {len(both_ranks):,}")
    print(f"    Match                : {rank_match:,}")
    print(f"    Differ               : {rank_differ:,}")
    print(f"\nOutput → {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
