SHELL := /bin/sh
PYTHON := .venv/bin/python
COMPOSE := docker compose
SLICE ?= catalog

.PHONY: up render seed parity promote rollback down test lint

render:
	$(PYTHON) strangler/render.py

up: render
	$(COMPOSE) up -d --build

seed:
	$(COMPOSE) exec -T db mysql -ulegacy -plegacy legacy_shop < db/seed.sql

parity:
	HOST_UID=$(shell id -u) HOST_GID=$(shell id -g) \
		$(COMPOSE) --profile tools run --rm parity python tools/parity.py --slice $(SLICE)

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
