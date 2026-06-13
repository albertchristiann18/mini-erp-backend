.PHONY: setup test lint fmt type-check create-user change-role run

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

# example -> make create-user username=albert
create-user:
	@test -n "$(username)" || (echo "Usage: make create-user username=<name>"; exit 1)
	uv run python manage.py create_user --username $(username) $(if $(role),--role $(role),)

# example -> make change-role username=albert role=finance
change-role:
	@test -n "$(username)" || (echo "Usage: make change-role username=<name> role=<role>"; exit 1)
	@test -n "$(role)" || (echo "Usage: make change-role username=<name> role=<role>"; exit 1)
	uv run python manage.py change_user_role --username $(username) --role $(role)
