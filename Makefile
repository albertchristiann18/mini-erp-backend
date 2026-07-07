.PHONY: setup test lint fmt type-check create-user run

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

# example -> make create-user username=albert role=finance password=secret123 company=Acme
create-user:
	@test -n "$(username)" || (echo "Usage: make create-user username=<name>"; exit 1)
	uv run python scripts/create_user.py --username $(username) $(if $(role),--role $(role),) $(if $(password),--password $(password),) $(if $(company),--company $(company),)
