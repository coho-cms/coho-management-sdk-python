.PHONY: sync test lint format type check build clean contracts

sync:
	uv sync --all-extras

test:
	uv run pytest

lint:
	uv run ruff check .
	uv run ruff format --check .

format:
	uv run ruff format .
	uv run ruff check --fix .

type:
	uv run mypy

# Everything CI runs.
check: lint type test

# The wheel and the sdist, into dist/.
build:
	uv build --out-dir dist
	uvx twine check --strict dist/*

clean:
	rm -rf dist build .pytest_cache .ruff_cache .mypy_cache

# Re-vendor the contracts from a coho-data checkout and record its commit.
COHO_DATA ?= ../coho-data
contracts:
	cp $(COHO_DATA)/bff/src/main/resources/openapi/bff.yaml contracts/
	cp $(COHO_DATA)/api-authoring/src/main/resources/openapi/authoring.yaml contracts/
	cp $(COHO_DATA)/api-delivery-contract/src/main/resources/openapi/delivery.yaml contracts/
	git -C $(COHO_DATA) rev-parse HEAD > contracts/PIN
