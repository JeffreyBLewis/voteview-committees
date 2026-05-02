# Makefile — VoteView Congressional Committee Data Pipeline
#
# Targets
# -------
#   all                  (default) Full update: check for roster changes, download
#                        any new elections/resignations, rebuild stale CSVs.
#                        Requires CONGRESS_GOV_API_KEY when roster changes.
#   download             Check for snapshot changes; if any, download new election
#                        resolutions and resignations automatically.
#   download-elections   Force-download H.Res and S.Res election XMLs from
#                        congress.gov regardless of roster change detection.
#   download-resignations Force-download CR resignation HTMLs from GovInfo
#                        regardless of roster change detection.
#   spells               Rebuild stale CSVs from current inputs; do not fetch.
#   house-spells         Rebuild House/house_committee_spells.csv if inputs changed.
#   senate-spells        Rebuild Senate/senate_committee_spells.csv if inputs changed.
#   senate-elections     Reparse Senate/senate_elections.csv from election XMLs.
#
# How "only if necessary" works
# ------------------------------
# Snapshot download scripts save a new file only when content has changed, and
# touch .roster_changed when they do. 'download' checks that sentinel: if present,
# it downloads new election resolutions and resignations, then removes it.
# 'spells' rebuilds only CSVs whose inputs are newer than the output.
# 'all' runs download then spells as separate make invocations so that any newly
# saved files are visible to wildcard expansion before timestamps are checked.

PYTHON = poetry run python3
HOUSE  = House
SENATE = Senate

# ── Sentinel files written by download scripts when new snapshots are saved ───
HOUSE_CHANGED  = $(HOUSE)/.roster_changed
SENATE_CHANGED = $(SENATE)/.roster_changed

# ── Generated outputs ─────────────────────────────────────────────────────────
HOUSE_ELEC    = $(HOUSE)/house_elections.csv
HOUSE_RESIGN  = $(HOUSE)/house_resignations.csv
HOUSE_SPELLS  = $(HOUSE)/house_committee_spells.csv
SENATE_ELEC   = $(SENATE)/senate_elections.csv
SENATE_SPELLS = $(SENATE)/senate_committee_spells.csv

# ── Inputs for timestamp-based dependency tracking ────────────────────────────
HOUSE_SNAPS       = $(wildcard $(HOUSE)/MemberData_snapshots/*.xml.gz)
HOUSE_ELEC_SRC    = $(wildcard $(HOUSE)/committee_elections_xml/*.xml) \
                    $(wildcard $(HOUSE)/committee_elections_xml/*.html)
HOUSE_RESIGN_SRC  = $(wildcard $(HOUSE)/cr_resignations/*.html)
SENATE_COMM_SNAPS = $(wildcard $(SENATE)/SenateCommittees_snapshots/*.xml)
SENATE_SEN_SNAPS  = $(wildcard $(SENATE)/SenatorData_snapshots/*.xml)
SENATE_ELEC_SRC   = $(wildcard $(SENATE)/senate_committee_elections_xml/*.xml) \
                    $(wildcard $(SENATE)/senate_committee_elections_xml/*.html)

# ─────────────────────────────────────────────────────────────────────────────
.DEFAULT_GOAL := all
.PHONY: all install download download-elections download-resignations spells house-spells senate-spells senate-elections

# Install Python dependencies via Poetry (run once, or after pyproject.toml changes).
install:
	poetry install

# Two-pass: download may add new snapshot and source files; spells is a fresh
# invocation so wildcard expansion sees those files when evaluating dependencies.
all:
	$(MAKE) download
	$(MAKE) spells

# ── Fetch snapshots; if roster changed, fetch elections and resignations ───────
download:
	@echo "=== House: checking for MemberData updates ==="
	cd $(HOUSE) && $(PYTHON) download_memberdata_current.py
	@echo ""
	@echo "=== Senate: checking for snapshot updates ==="
	cd $(SENATE) && $(PYTHON) update_senate_snapshots.py
	@if [ -f $(HOUSE_CHANGED) ]; then \
		echo "" ; \
		echo "=== House: roster changed — downloading election resolutions ===" ; \
		(cd $(HOUSE) && $(PYTHON) download_committee_elections.py) ; \
		echo "=== House: downloading CR resignation references ===" ; \
		$(PYTHON) $(HOUSE)/download_cr_resignations.py ; \
		rm -f $(HOUSE_CHANGED) ; \
	fi
	@if [ -f $(SENATE_CHANGED) ]; then \
		echo "" ; \
		echo "=== Senate: roster changed — downloading election resolutions ===" ; \
		(cd $(SENATE) && $(PYTHON) download_senate_committee_elections.py) ; \
		rm -f $(SENATE_CHANGED) ; \
	fi

# ── Force-download elections/resignations regardless of roster change ──────────
# Requires CONGRESS_GOV_API_KEY to be set in the environment.
download-elections:
	@echo "=== House: downloading committee election resolutions ==="
	cd $(HOUSE) && $(PYTHON) download_committee_elections.py
	@echo ""
	@echo "=== Senate: downloading committee election resolutions ==="
	cd $(SENATE) && $(PYTHON) download_senate_committee_elections.py

download-resignations:
	@echo "=== House: downloading CR resignation references ==="
	$(PYTHON) $(HOUSE)/download_cr_resignations.py

# ── Rebuild stale outputs ─────────────────────────────────────────────────────
spells: $(HOUSE_SPELLS) $(SENATE_SPELLS)

# House elections: reparse only when election XML/HTML source files change.
house-elections: $(HOUSE_ELEC)

$(HOUSE_ELEC): $(HOUSE_ELEC_SRC)
	@echo "=== House: parsing house_elections.csv ==="
	cd $(HOUSE) && $(PYTHON) parse_committee_elections.py

# House resignations: reparse only when CR HTML source files change.
house-resignations: $(HOUSE_RESIGN)

$(HOUSE_RESIGN): $(HOUSE_RESIGN_SRC)
	@echo "=== House: parsing house_resignations.csv ==="
	cd $(HOUSE) && $(PYTHON) parse_cr_resignations.py

# House spells: rebuild if any MemberData snapshot, elections, or resignations changed.
house-spells: $(HOUSE_SPELLS)

$(HOUSE_SPELLS): $(HOUSE_SNAPS) $(HOUSE_ELEC) $(HOUSE_RESIGN)
	@echo "=== House: building house_committee_spells.csv ==="
	cd $(HOUSE) && $(PYTHON) build_committee_spells.py

# Senate elections: reparse only when election XML/HTML source files change.
senate-elections: $(SENATE_ELEC)

$(SENATE_ELEC): $(SENATE_ELEC_SRC)
	@echo "=== Senate: parsing senate_elections.csv ==="
	cd $(SENATE) && $(PYTHON) parse_senate_committee_elections.py

# Senate spells: rebuild if any roster/senator snapshot or elections CSV changed.
senate-spells: $(SENATE_SPELLS)

$(SENATE_SPELLS): $(SENATE_COMM_SNAPS) $(SENATE_SEN_SNAPS) $(SENATE_ELEC)
	@echo "=== Senate: building senate_committee_spells.csv ==="
	cd $(SENATE) && $(PYTHON) build_senate_committee_spells.py
