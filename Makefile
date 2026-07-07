SHELL := /bin/bash

.PHONY: setup test lint fmt type-check docker-up docker-down docker-restart docker-reset create-user run

run:
	uv run manage.py runserver

setup:
	uv sync
	uv run pre-commit install

test:
	uv run pytest

lint:
	uv run ruff check .

fmt:
	uv run ruff format .

type-check:
	uv run mypy apps/

# Bring up db + migrate (idempotent — safe to run repeatedly, does not touch existing data)
docker-up:
	docker compose up -d

# Stop containers WITHOUT deleting the database volume — your data survives this
docker-down:
	docker compose down

# Restart containers WITHOUT deleting the database volume — your data survives this
docker-restart:
	docker compose down
	docker compose up -d

# DESTRUCTIVE: wipes the database volume entirely and starts fresh. Requires typing "yes" to confirm.
docker-reset:
	@echo "This will PERMANENTLY DELETE all local database data."
	@read -p "Type 'yes' to continue: " confirm && [ "$$confirm" = "yes" ] || (echo "Aborted."; exit 1)
	docker compose down -v
	docker compose up -d

# example -> make create-user username=albert role=finance password=secret123 company=Acme
create-user:
	@test -n "$(username)" || (echo "Usage: make create-user username=<name>"; exit 1)
	uv run python scripts/create_user.py --username $(username) $(if $(role),--role $(role),) $(if $(password),--password $(password),) $(if $(company),--company $(company),)
