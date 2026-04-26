# Tu repo de GitHub para los targets opcionales (upload-secret/clear-cache).
# Tres formas de definirlo, en orden de preferencia:
#   1) Crea un Makefile.local (gitignored) con: REPO := tu_usuario/tu_repo
#   2) Variable de entorno: export REPO=tu_usuario/tu_repo
#   3) En la línea: REPO=tu_usuario/tu_repo make upload-secret
-include Makefile.local
REPO ?=
VENV := .venv
PYTHON := $(VENV)/bin/python
PYTR := $(VENV)/bin/pytr

.PHONY: help setup login init-sheet doctor sync portfolio verify inspect test renta insights features config all refresh-cookie upload-secret clear-cache

help:
	@echo "Setup:"
	@echo "  make setup            crea .venv e instala dependencias"
	@echo "  make login            pytr login (interactivo, SMS si cookie caducada)"
	@echo "  make config-init      asistente interactivo para crear config.yaml"
	@echo "  make init-sheet       crea las pestañas que falten en tu Google Sheet"
	@echo "  make doctor           verifica que el setup está listo (config, OAuth, pestañas, sesión)"
	@echo ""
	@echo "Edición de config (sin tocar el yaml a mano):"
	@echo "  make config-show      imprime el config actual"
	@echo "  make config-validate  valida el config"
	@echo "  make config-features  toggles interactivos de features"
	@echo "  python tr_sync.py config set KEY VALUE        cambia un valor"
	@echo "  python tr_sync.py config add-asset ISIN LABEL añade ISIN al portfolio"
	@echo "  python tr_sync.py config add-ignore SECTION TXT  añade patrón ignore"
	@echo ""
	@echo "Uso diario:"
	@echo "  make sync             sincroniza gastos/ingresos/inversiones del mes"
	@echo "  make portfolio        snapshot del portfolio (valor actual por activo)"
	@echo "  make verify           portfolio dry-run local (no escribe en la Sheet)"
	@echo "  make renta            informe IRPF del año pasado"
	@echo "  make renta YEAR=2025  informe IRPF de un año concreto"
	@echo "  make insights         patrimonio + rentabilidad (simple + MWR) en consola"
	@echo "  make features         tabla de features con su estado (config + soporte broker)"
	@echo "  make inspect          inspecciona eventos brutos de TR (debug)"
	@echo "  make test             ejecuta tests unitarios"
	@echo ""
	@echo "GitHub Actions (opcional, requiere REPO=usuario/repo):"
	@echo "  make all              login + sync + portfolio + sube cookie a GitHub"
	@echo "  make refresh-cookie   login + verify + sube cookie a GitHub"
	@echo "  make upload-secret    sube ~/.pytr a PYTR_KEYS_B64 del repo"
	@echo "  make clear-cache      borra caches pytr-session-* de GitHub Actions"

setup:
	python3 -m venv $(VENV)
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -r requirements.txt
	@echo ""
	@echo "✅ Setup hecho. Ahora:"
	@echo "   1) Copia config.example.yaml a config.yaml y rellénalo"
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

# ── Targets opcionales para GitHub Actions ────────────────────────────────
# Requieren `gh` CLI autenticado y la variable REPO=tu_usuario/tu_repo.

_check-repo:
	@if [ -z "$(REPO)" ]; then \
	  echo "ERROR: define REPO=tu_usuario/tu_repo (ej. REPO=alice/tr-sync make upload-secret)"; \
	  exit 1; \
	fi

all: login sync portfolio upload-secret clear-cache
	@echo ""
	@echo "✅ Login + sync + portfolio completados. Cookie subida a GitHub."

refresh-cookie: login verify upload-secret clear-cache
	@echo ""
	@echo "✅ Cookie refrescada."

upload-secret: _check-repo
	tar -czf /tmp/pytr.tgz -C $$HOME .pytr/
	gh secret set PYTR_KEYS_B64 --repo $(REPO) --body "$$(base64 -i /tmp/pytr.tgz)"
	@rm -f /tmp/pytr.tgz

clear-cache: _check-repo
	@ids=$$(gh cache list --repo $(REPO) --key pytr-session --json id --jq '.[].id'); \
	if [ -z "$$ids" ]; then \
	  echo "no hay caches pytr-session-*"; \
	else \
	  for id in $$ids; do gh cache delete $$id --repo $(REPO); done; \
	fi
