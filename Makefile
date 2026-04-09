# GemmaForge — convenience targets.
#
# Phase 0: install / lint / test only. Phase-specific targets
# (`make vm-up`, `make demo`, etc.) are added by their owning phases.

.PHONY: help install lint format test compose-config clean \
       vllm-build vllm-install demo-up demo-down demo-status demo-logs demo-test

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

# ---------------------------------------------------------------------------
# Inference plane — vLLM serving layer
# ---------------------------------------------------------------------------

DEMO_SERVICES := gemma-forge-architect gemma-forge-auditor gemma-forge-sentry

vllm-build:  ## Build the gemma-forge/vllm:latest container image
	docker build -t gemma-forge/vllm:latest -f infra/vllm/Dockerfile .

vllm-install:  ## Install the vLLM systemd units (requires sudo)
	./infra/vllm/scripts/install.sh

demo-up:  ## Start all 3 inference services (Architect, Auditor, Sentry)
	@echo "Starting GemmaForge inference plane..."
	@for svc in $(DEMO_SERVICES); do \
		echo "  starting $$svc..."; \
		sudo systemctl start $$svc.service; \
	done
	@echo ""
	@echo "Waiting for models to load (this takes 2-3 minutes)..."
	@echo "Run 'make demo-status' or 'make demo-logs' to monitor progress."
	@echo "Run 'make demo-test' to verify all endpoints are responding."

demo-down:  ## Stop all 3 inference services and free GPUs
	@echo "Stopping GemmaForge inference plane..."
	@for svc in $(DEMO_SERVICES); do \
		sudo systemctl stop $$svc.service 2>/dev/null || true; \
	done
	@echo "Done. GPU memory freed:"
	@nvidia-smi --query-gpu=index,memory.used,memory.total --format=csv,noheader

demo-status:  ## Show service status + GPU memory usage
	@echo "=== Service status ==="
	@for svc in $(DEMO_SERVICES); do \
		printf "  %-30s " "$$svc:"; \
		systemctl is-active $$svc.service 2>/dev/null || echo "inactive"; \
	done
	@echo ""
	@echo "=== GPU memory ==="
	@nvidia-smi --query-gpu=index,name,memory.used,memory.total --format=csv,noheader
	@echo ""
	@echo "=== Endpoints ==="
	@printf "  Architect (31B NVFP4 tp=2): "; curl -s -o /dev/null -w "http://localhost:8050 [%{http_code}]" http://localhost:8050/v1/models 2>/dev/null || echo "not responding"; echo
	@printf "  Auditor   (E4B):            "; curl -s -o /dev/null -w "http://localhost:8060 [%{http_code}]" http://localhost:8060/v1/models 2>/dev/null || echo "not responding"; echo
	@printf "  Sentry    (E2B):            "; curl -s -o /dev/null -w "http://localhost:8070 [%{http_code}]" http://localhost:8070/v1/models 2>/dev/null || echo "not responding"; echo

demo-logs:  ## Tail logs from all 3 inference services
	@echo "Tailing logs (Ctrl+C to stop)..."
	sudo journalctl -f -u gemma-forge-architect -u gemma-forge-auditor -u gemma-forge-sentry

demo-test:  ## Verify all 3 inference endpoints are responding
	@PASS=0; FAIL=0; \
	for endpoint in "Architect:8050" "Auditor:8060" "Sentry:8070"; do \
		NAME=$${endpoint%%:*}; PORT=$${endpoint##*:}; \
		printf "  %-12s " "$$NAME:"; \
		if curl -s "http://localhost:$$PORT/v1/models" 2>/dev/null | grep -q '"object"'; then \
			echo "OK"; PASS=$$((PASS+1)); \
		else \
			echo "FAIL (http://localhost:$$PORT)"; FAIL=$$((FAIL+1)); \
		fi; \
	done; \
	echo ""; \
	echo "$$PASS/3 endpoints responding."; \
	if [ $$FAIL -gt 0 ]; then echo "Run 'make demo-logs' to diagnose failures."; exit 1; fi
