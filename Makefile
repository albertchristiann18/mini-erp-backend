.PHONY: setup test lint fmt type-check

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
