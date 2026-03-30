# ContextGO — developer task runner
# Usage: make <target>
# Run `make help` for a full target listing.

PYTHON     := python3
PIP        := $(PYTHON) -m pip
PYTEST     := $(PYTHON) -m pytest
RUFF       := ruff
SCRIPTS    := scripts
TESTS      := tests
BENCHMARKS := benchmarks

.PHONY: help install install-remote dev-check \
        lint format type-check \
        test test-fast test-cov \
        smoke smoke-installed health bench \
        build dist check-dist \
        release-dry-run \
        clean clean-dist clean-all

# ---------------------------------------------------------------------------
# Default target
# ---------------------------------------------------------------------------

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}' | sort
	@echo ""
	@echo "  Core workflow:  install → lint → test → smoke → build"

# ---------------------------------------------------------------------------
# Installation
# ---------------------------------------------------------------------------

install: ## Install package and dev dependencies (editable)
	$(PIP) install -e ".[dev]"

install-remote: ## Also install optional remote/httpx extra
	$(PIP) install -e ".[dev,remote]"

# ---------------------------------------------------------------------------
# Code quality
# ---------------------------------------------------------------------------

lint: ## Run ruff check and format-check (non-destructive)
	$(RUFF) check src/contextgo/ $(SCRIPTS)/ $(TESTS)/ $(BENCHMARKS)/
	$(RUFF) format --check src/contextgo/ $(SCRIPTS)/ $(TESTS)/ $(BENCHMARKS)/

format: ## Auto-format and auto-fix with ruff
	$(RUFF) format src/contextgo/ $(SCRIPTS)/ $(TESTS)/ $(BENCHMARKS)/
	$(RUFF) check --fix src/contextgo/ $(SCRIPTS)/ $(TESTS)/ $(BENCHMARKS)/

type-check: ## Run mypy type checking (informational)
	$(PYTHON) -m mypy $(SCRIPTS)/ --ignore-missing-imports || true

dev-check: lint ## Full pre-commit check: syntax + lint
	bash -n $(SCRIPTS)/*.sh
	$(PYTHON) -m py_compile $(SCRIPTS)/*.py

# ---------------------------------------------------------------------------
# Testing
# ---------------------------------------------------------------------------

TEST_FILES := \
	$(TESTS)/test_context_cli.py \
	$(TESTS)/test_context_core.py \
	$(TESTS)/test_context_native.py \
	$(TESTS)/test_context_smoke.py \
	$(TESTS)/test_source_adapters.py \
	$(TESTS)/test_session_index.py \
	$(TESTS)/test_autoresearch_contextgo.py \
	$(TESTS)/test_utility_scripts.py

test: ## Run full pytest suite with coverage
	$(PYTEST) $(TEST_FILES) \
		--cov=src/contextgo --cov-report=term-missing --cov-report=xml -v

test-fast: ## Run tests without coverage (faster iteration)
	$(PYTEST) $(TEST_FILES) -v --no-cov

test-cov: test ## Run tests and open HTML coverage report
	$(PYTHON) -m coverage html
	@echo "Coverage report: htmlcov/index.html"

# ---------------------------------------------------------------------------
# Runtime verification
# ---------------------------------------------------------------------------

smoke: ## Run smoke suite in sandboxed mode
	$(PYTHON) $(SCRIPTS)/context_cli.py smoke --sandbox

smoke-installed: ## Run installed runtime and installed CLI wrapper smoke
	$(PYTHON) $(SCRIPTS)/smoke_installed_runtime.py
	$(PYTHON) $(SCRIPTS)/smoke_installed_cli.py

health: ## Run health check via CLI
	$(PYTHON) $(SCRIPTS)/context_cli.py health

e2e: ## Run end-to-end quality gate
	$(PYTHON) $(SCRIPTS)/e2e_quality_gate.py

bench: ## Run benchmark harness (Python vs native-wrapper)
	$(PYTHON) -m $(BENCHMARKS) --mode both --iterations 1 --warmup 0 --query benchmark --format text

# ---------------------------------------------------------------------------
# Build and distribution
# ---------------------------------------------------------------------------

build: ## Build sdist and wheel into dist/
	$(PIP) install --quiet build
	$(PYTHON) -m build

check-dist: build ## Validate distribution with twine
	$(PIP) install --quiet twine
	twine check dist/*

release-dry-run: check-dist ## Validate release artifacts without uploading
	@echo "dist/ contents:"
	@ls -lh dist/
	@echo ""
	@echo "VERSION: $$(cat VERSION)"
	@echo "Release dry-run passed — push a v* tag to trigger the Release workflow."

# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

clean: ## Remove Python bytecode and cache directories
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	find . -type f -name ".coverage" -delete 2>/dev/null || true
	find . -type d -name "htmlcov" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".mypy_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".ruff_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true

clean-dist: ## Remove build artifacts
	rm -rf dist/ build/ *.egg-info

clean-all: clean clean-dist ## Full clean (bytecode + build artifacts)
