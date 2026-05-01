# VoteView Committee Spells

> **Development code — no warranty.** This repository is research software under active development. Outputs are provided as-is, without any guarantee of completeness, accuracy, or fitness for a particular purpose. Verify results against primary sources before relying on them.

Builds **committee spell data** for the U.S. House and Senate — one row per member × committee × congress — covering the 114th–119th Congresses (House) and 108th–119th Congresses (Senate).

## Outputs

| File | Description |
|---|---|
| `House/committee_spells.csv` | House committee spells |
| `Senate/senate_committee_spells.csv` | Senate committee spells |

### Spell fields

| Field | Description |
|---|---|
| `congress` | Congress number |
| `start_date` | First day of committee service |
| `start_date_imputed` | True if start date came from a snapshot rather than an election resolution |
| `end_date` | Last day of committee service |
| `departure_reason` | `died`, `resigned`, or `expelled` if the member left the chamber mid-congress |
| `bioguide_id` | [Biographical Directory](https://bioguide.congress.gov) identifier |
| `member_name` | Member name |
| `state` | State abbreviation |
| `party` | Party abbreviation |
| `committee_name` | Full committee name |
| `committee_code` | Clerk/Secretary committee code |
| `resolution` | H.Res / S.Res that established this assignment |
| `resolution_date` | Date the resolution was agreed to |

Senate spells also include `senator_class` and `position`. House spells also include `district`, `committee_rank`, `role`, `cr_citation`, and `resignation_date`.

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

Then populate the House snapshot archive (not included in the repo due to size — 80 MB):

```sh
make download
```

## Usage

```sh
make          # fetch latest data, rebuild CSVs only if inputs changed
make download # fetch only
make spells   # rebuild stale CSVs without fetching
make clean    # remove generated CSVs
```

The `congress.gov` download scripts require an API key:

```sh
export CONGRESS_GOV_API_KEY=<your_key>
cd Senate && poetry run python3 download_senate_committee_elections.py
cd House  && poetry run python3 download_committee_elections.py
```
