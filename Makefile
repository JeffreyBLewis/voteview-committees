# Makefile — VoteView Congressional Committee Data Pipeline
#
# Targets
# -------
#   all              (default) Fetch latest data, then rebuild any CSV that is
#                    out-of-date with respect to its inputs.
#   download         Fetch new snapshots from live sources only; do not rebuild.
#   spells           Rebuild stale CSVs from current inputs; do not fetch.
#   house-spells     Rebuild House/committee_spells.csv if inputs changed.
#   senate-spells    Rebuild Senate/senate_committee_spells.csv if inputs changed.
#   senate-elections Reparse Senate/senate_elections.csv from election XMLs.
#   clean            Remove all generated CSV outputs.
#
# How "only if necessary" works
# ------------------------------
# The download scripts save a new snapshot only when its content has changed.
# 'all' runs download, then re-invokes make for spells so that any newly-saved
# snapshot files are included in wildcard expansion before timestamps are checked.
# If no new snapshots were saved, no CSVs are rebuilt.

PYTHON = poetry run python3
HOUSE  = House
SENATE = Senate

# ── Generated outputs ─────────────────────────────────────────────────────────
HOUSE_SPELLS  = $(HOUSE)/committee_spells.csv
SENATE_ELEC   = $(SENATE)/senate_elections.csv
SENATE_SPELLS = $(SENATE)/senate_committee_spells.csv

# ── Inputs for timestamp-based dependency tracking ────────────────────────────
HOUSE_SNAPS       = $(wildcard $(HOUSE)/MemberData_snapshots/*.xml)
SENATE_COMM_SNAPS = $(wildcard $(SENATE)/SenateCommittees_snapshots/*.xml)
SENATE_SEN_SNAPS  = $(wildcard $(SENATE)/SenatorData_snapshots/*.xml)
SENATE_ELEC_SRC   = $(wildcard $(SENATE)/senate_committee_elections_xml/*.xml) \
                    $(wildcard $(SENATE)/senate_committee_elections_xml/*.html)

# ─────────────────────────────────────────────────────────────────────────────
.DEFAULT_GOAL := all
.PHONY: all install download spells house-spells senate-spells senate-elections clean

# Install Python dependencies via Poetry (run once, or after pyproject.toml changes).
install:
	poetry install

# Two-pass: download may add new snapshot files; spells is a fresh invocation
# so wildcard expansion sees those files when evaluating dependencies.
all:
	$(MAKE) download
	$(MAKE) spells

# ── Fetch live data ───────────────────────────────────────────────────────────
download:
	@echo "=== House: checking for MemberData updates ==="
	cd $(HOUSE) && $(PYTHON) download_memberdata_current.py
	@echo ""
	@echo "=== Senate: checking for snapshot updates ==="
	cd $(SENATE) && $(PYTHON) update_senate_snapshots.py

# ── Rebuild stale outputs ─────────────────────────────────────────────────────
spells: $(HOUSE_SPELLS) $(SENATE_SPELLS)

# House: rebuild if any MemberData snapshot, elections, or resignations changed.
house-spells: $(HOUSE_SPELLS)

$(HOUSE_SPELLS): $(HOUSE_SNAPS) $(HOUSE)/elections.csv $(HOUSE)/resignations.csv
	@echo "=== House: building committee_spells.csv ==="
	cd $(HOUSE) && $(PYTHON) build_committee_spells.py

# Senate elections: reparse only when election XML/HTML source files change.
# (These are updated separately via the congress.gov download scripts.)
senate-elections: $(SENATE_ELEC)

$(SENATE_ELEC): $(SENATE_ELEC_SRC)
	@echo "=== Senate: parsing senate_elections.csv ==="
	cd $(SENATE) && $(PYTHON) parse_senate_committee_elections.py

# Senate spells: rebuild if any roster/senator snapshot or elections CSV changed.
senate-spells: $(SENATE_SPELLS)

$(SENATE_SPELLS): $(SENATE_COMM_SNAPS) $(SENATE_SEN_SNAPS) $(SENATE_ELEC)
	@echo "=== Senate: building senate_committee_spells.csv ==="
	cd $(SENATE) && $(PYTHON) build_senate_committee_spells.py

# ── Clean ─────────────────────────────────────────────────────────────────────
clean:
	rm -f $(HOUSE_SPELLS) $(SENATE_ELEC) $(SENATE_SPELLS)
	@echo "Removed generated CSV outputs."
