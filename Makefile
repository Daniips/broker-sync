# Your GitHub repo for the optional targets (upload-secret/clear-cache).
# Three ways to define it, in order of preference:
#   1) Create a Makefile.local (gitignored) with: REPO := your_user/your_repo
#   2) Environment variable: export REPO=your_user/your_repo
#   3) Inline: REPO=your_user/your_repo make upload-secret
-include Makefile.local
REPO ?=
VENV := .venv
PYTHON := $(VENV)/bin/python
PYTR := $(VENV)/bin/pytr

.PHONY: help setup login init-sheet doctor sync portfolio verify inspect test renta insights features config all refresh-cookie upload-secret clear-cache

help:
	@echo "Setup:"
	@echo "  make setup            create .venv and install dependencies"
	@echo "  make login            pytr login (interactive, SMS if cookie expired)"
	@echo "  make config-init      interactive wizard to create config.yaml"
	@echo "  make init-sheet       create the missing tabs in your Google Sheet"
	@echo "  make doctor           verify the setup is ready (config, OAuth, tabs, session)"
	@echo ""
	@echo "Config editing (without touching the yaml by hand):"
	@echo "  make config-show      print the current config"
	@echo "  make config-validate  validate the config"
	@echo "  make config-features  interactive feature toggles"
	@echo "  python tr_sync.py config set KEY VALUE        change a value"
	@echo "  python tr_sync.py config add-asset ISIN LABEL add an ISIN to the portfolio"
	@echo "  python tr_sync.py config add-ignore SECTION TXT  add an ignore pattern"
	@echo ""
	@echo "Daily use:"
	@echo "  make sync             sync the month's expenses/income/investments"
	@echo "  make portfolio        portfolio snapshot (current value per asset)"
	@echo "  make verify           local portfolio dry-run (does not write to the Sheet)"
	@echo "  make renta            IRPF report for last year"
	@echo "  make renta YEAR=2025  IRPF report for a specific year"
	@echo "  make insights         net worth + return (simple + MWR) in console"
	@echo "  make features         table of features with their status (config + broker support)"
	@echo "  make inspect          inspect raw TR events (debug)"
	@echo "  make test             run unit tests"
	@echo ""
	@echo "GitHub Actions (optional, requires REPO=user/repo):"
	@echo "  make all              login + sync + portfolio + uploads cookie to GitHub"
	@echo "  make refresh-cookie   login + verify + uploads cookie to GitHub"
	@echo "  make upload-secret    upload ~/.pytr to PYTR_KEYS_B64 of the repo"
	@echo "  make clear-cache      delete pytr-session-* caches from GitHub Actions"

setup:
	python3 -m venv $(VENV)
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -r requirements.txt
	@echo ""
	@echo "✅ Setup done. Now:"
	@echo "   1) Copy config.example.yaml to config.yaml and fill it in"
	@echo "   2) make login"

login:
	$(PYTR) login --store_credentials

init-sheet:
	$(PYTHON) tr_sync.py --init-sheet

doctor:
	$(PYTHON) tr_sync.py --doctor

config-init:
	$(PYTHON) tr_sync.py config init

config-show:
	$(PYTHON) tr_sync.py config show

config-validate:
	$(PYTHON) tr_sync.py config validate

config-features:
	$(PYTHON) tr_sync.py config features

sync:
	$(PYTHON) tr_sync.py

portfolio:
	$(PYTHON) tr_sync.py --portfolio

verify:
	$(PYTHON) tr_sync.py --portfolio --dry-run

inspect:
	$(PYTHON) inspect_events.py

test:
	$(PYTHON) -m unittest test_tr_sync

renta:
	$(PYTHON) tr_sync.py --renta $(if $(YEAR),--year $(YEAR))

insights:
	$(PYTHON) tr_sync.py --insights

backfill-snapshots:
	$(PYTHON) tr_sync.py --backfill-snapshots $(if $(START),--start $(START)) $(if $(FREQ),--frequency $(FREQ))

mwr-flows:
	$(PYTHON) tr_sync.py --mwr-flows $(if $(BONUS),--bonus-as $(BONUS)) $(if $(LOCALE),--locale $(LOCALE))

features:
	$(PYTHON) tr_sync.py --features

# ── Optional targets for GitHub Actions ──────────────────────────────────
# Require an authenticated `gh` CLI and the variable REPO=your_user/your_repo.

_check-repo:
	@if [ -z "$(REPO)" ]; then \
	  echo "ERROR: define REPO=your_user/your_repo (e.g. REPO=alice/tr-sync make upload-secret)"; \
	  exit 1; \
	fi

all: login sync portfolio upload-secret clear-cache
	@echo ""
	@echo "✅ Login + sync + portfolio completed. Cookie uploaded to GitHub."

refresh-cookie: login verify upload-secret clear-cache
	@echo ""
	@echo "✅ Cookie refreshed."

upload-secret: _check-repo
	tar -czf /tmp/pytr.tgz -C $$HOME .pytr/
	gh secret set PYTR_KEYS_B64 --repo $(REPO) --body "$$(base64 -i /tmp/pytr.tgz)"
	@rm -f /tmp/pytr.tgz

clear-cache: _check-repo
	@ids=$$(gh cache list --repo $(REPO) --key pytr-session --json id --jq '.[].id'); \
	if [ -z "$$ids" ]; then \
	  echo "no pytr-session-* caches"; \
	else \
	  for id in $$ids; do gh cache delete $$id --repo $(REPO); done; \
	fi
