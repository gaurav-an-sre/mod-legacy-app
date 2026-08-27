SHELL := /bin/sh
PYTHON := .venv/bin/python
COMPOSE := docker compose
SLICE ?= catalog

ifeq ($(SLICE),orders)
CANDIDATE_URL ?= http://orders:8002
else
CANDIDATE_URL ?= http://fake-candidate:8000
endif

.PHONY: up render seed parity promote rollback down test lint

render:
	$(PYTHON) strangler/render.py

up: render
	$(COMPOSE) up -d --build

seed:
	$(COMPOSE) exec -T db mysql -ulegacy -plegacy legacy_shop < db/seed.sql

parity:
	$(COMPOSE) --profile tools run --rm -e CANDIDATE_URL=$(CANDIDATE_URL) parity python tools/parity.py --slice $(SLICE)

promote:
	$(PYTHON) tools/cutover.py promote --slice $(SLICE)

rollback:
	$(PYTHON) tools/cutover.py rollback --slice $(SLICE)

down:
	$(COMPOSE) down

test:
	$(PYTHON) -m pytest

lint:
	.venv/bin/ruff check .
	.venv/bin/ruff format --check .
