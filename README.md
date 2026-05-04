# VoteView Committee Spells

> **Development code — no warranty.** This repository is research software under active development. Outputs are provided as-is, without any guarantee of completeness, accuracy, or fitness for a particular purpose. Verify results against primary sources before relying on them.

Builds **committee spell data** for the U.S. House and Senate — one row per member × committee × congress — covering the 114th–119th Congresses (House) and 108th–119th Congresses (Senate).

## Outputs

| File | Description |
|---|---|
| `House/house_elections.csv` | House committee election resolutions (H.Res) |
| `House/house_resignations.csv` | House committee resignation citations |
| `House/house_committee_spells.csv` | House committee spells |
| `Senate/senate_elections.csv` | Senate committee election resolutions (S.Res) |
| `Senate/senate_committee_spells.csv` | Senate committee spells |

### Spell fields

| Field | Description |
|---|---|
| `congress` | Congress number |
| `start_date` | First day of committee service |
| `start_date_imputed` | True if start date came from a roster snapshot rather than an election resolution |
| `end_date` | Last day of committee service |
| `departure_reason` | `died`, `resigned`, or `expelled` if the member left the chamber mid-congress |
| `bioguide_id` | [Biographical Directory](https://bioguide.congress.gov) identifier |
| `member_name` | Member name |
| `state` | State abbreviation |
| `party` | Party abbreviation |
| `party_designation` | `majority` or `minority` based on member party vs. chamber majority (Senate: from election resolution; House: derived from party) |
| `committee_name` | Full committee name |
| `committee_code` | Clerk/Secretary committee code |
| `resolution_rank` | Member's ordinal position within the committee as listed in the election resolution (blank if no matching resolution found) |
| `roster_snapshot_rank` | Member's ordinal position as recorded in the most recent roster snapshot within the congress (House only; blank for Senate) |
| `resolution` | H.Res / S.Res that established this assignment |
| `resolution_date` | Date the resolution was agreed to |

Senate spells also include `senator_class` and `position`. House spells also include `district`, `role`, `cr_citation`, and `resignation_date`.

## Data currency

| Chamber | Last update attempted | Last roster change |
|---|---|---|
| House | <!-- house-last-attempted -->2026-05-04<!-- /house-last-attempted --> | <!-- house-roster-date -->2026-05-01<!-- /house-roster-date --> |
| Senate | <!-- senate-last-attempted -->2026-05-04<!-- /senate-last-attempted --> | <!-- senate-roster-date -->2026-05-01<!-- /senate-roster-date --> |

## Data sources

| Source | Used for |
|---|---|
| [clerk.house.gov](https://clerk.house.gov/xml/lists/MemberData.xml) | House member and committee roster snapshots |
| [senate.gov committee XML](https://www.senate.gov/general/committee_membership/committee_memberships_SSAF.xml) | Senate committee roster snapshots |
| [senate.gov senator directory](https://www.senate.gov/general/contact_information/senators_cfm.xml) | Senator biographical data |
| [congress.gov API](https://api.congress.gov) | Committee election resolutions (H.Res / S.Res) |
| Congressional Record | House committee resignation citations |

## Setup

```sh
poetry install
```


## Usage

```sh
make          # full update: check for roster changes, download elections/resignations
              # if the roster changed, then rebuild any stale CSVs
make download # check for roster changes and conditionally download elections/resignations
make spells   # rebuild stale CSVs from current inputs without fetching
```

When the roster changes, `make` and `make download` automatically download new election
resolutions and resignations. This requires a congress.gov API key:

```sh
export CONGRESS_GOV_API_KEY=<your_key>
make
```

To force-download elections or resignations regardless of roster change detection:

```sh
make download-elections    # re-fetch H.Res and S.Res election XMLs
make download-resignations # re-fetch House CR resignation HTMLs
```
