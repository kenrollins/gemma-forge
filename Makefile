# GemmaForge — convenience targets.
#
# Phase 0: install / lint / test only. Phase-specific targets
# (`make vm-up`, `make demo`, etc.) are added by their owning phases.

.PHONY: help install lint format test compose-config clean

help:  ## Show this help
	@awk 'BEGIN {FS = ":.*## "} /^[a-zA-Z_-]+:.*## / {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

install:  ## Install the Python package in editable mode with dev deps
	pip install -e ".[dev]"

lint:  ## Run ruff and mypy
	ruff check .
	ruff format --check .
	mypy gemma_forge

format:  ## Auto-format with ruff
	ruff format .
	ruff check --fix .

test:  ## Run pytest
	pytest --cov=gemma_forge --cov-report=term-missing

compose-config:  ## Validate the docker-compose.yml file
	docker compose -f docker-compose.yml config --quiet
	@echo "docker-compose.yml is valid Compose v2"

clean:  ## Remove build artifacts and caches
	rm -rf build dist *.egg-info .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
