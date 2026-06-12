.PHONY: install collect scan run test lint fmt type check

install:
	uv sync --extra dev
	uv run playwright install chromium

collect:
	uv run beto collect

scan:
	uv run beto scan

run:
	uv run beto run

test:
	uv run pytest

lint:
	uv run ruff check src tests

fmt:
	uv run ruff format src tests && uv run ruff check --fix src tests

type:
	uv run mypy

check: lint type test
