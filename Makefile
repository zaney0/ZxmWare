# ZxmWare — convenience targets. Override vars on the command line, e.g.
#   make rebrand SRC=/path/to/XFW-0053_02022024 VERSION=0053
# Most real work lives in the per-component scripts; this is just glue.

PY      ?= python3
NAME    ?= ZxmWare
VERSION ?= 0053
SRC     ?=
SPLASH  ?=
DATA    ?= server/data
SERVER  ?= http://localhost:8000

.PHONY: help rebrand index seed-apps serve serve-docker sync list install-deps clean

help:
	@echo "ZxmWare targets:"
	@echo "  rebrand       SRC=<bundle dir> VERSION=<ver> [SPLASH=<splash.bin>]  rebrand+pack an OTA bundle"
	@echo "  seed-apps     SRC=<bundle dir|resources.tar>                        extract .fap apps into the repo"
	@echo "  index                                                              (re)build directory.json manifests"
	@echo "  serve                                                              run the FastAPI server locally (uvicorn)"
	@echo "  serve-docker                                                       run the server via docker compose"
	@echo "  sync                                                               companion CLI: pull catalogs from SERVER"
	@echo "  list                                                               companion CLI: list available apps"
	@echo "  install-deps                                                       pip install server + installer requirements"

rebrand:
	@test -n "$(SRC)" || { echo "set SRC=<bundle dir containing update.fuf>"; exit 1; }
	$(PY) -I build/pack_update.py rebrand --src "$(SRC)" --out $(DATA)/firmware \
		--name $(NAME) --version $(VERSION) $(if $(SPLASH),--splash "$(SPLASH)",)

seed-apps:
	@test -n "$(SRC)" || { echo "set SRC=<bundle dir or resources.tar>"; exit 1; }
	$(PY) -I server/indexer/import_from_bundle.py "$(SRC)" --apps-dir $(DATA)/apps

index:
	$(PY) -I server/indexer/index_repo.py --data-dir $(DATA) --pretty

serve:
	cd server && uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

serve-docker:
	cd server && docker compose up -d --build

sync:
	$(PY) installer/zxmware.py sync --server $(SERVER)

list:
	$(PY) installer/zxmware.py list-apps

install-deps:
	$(PY) -m pip install -r server/app/requirements.txt -r installer/requirements.txt

clean:
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
