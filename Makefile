.DEFAULT_GOAL := help

# One package, one source tree. No workspace, no members, no kits — the
# Arvel Makefile this was adapted from drove a monorepo, and almost all of it
# had nothing to answer here.
SRC := src tests

.PHONY: help
help:  ## Show this help
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / { printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

.PHONY: sync
sync:  ## Install every extra a contributor needs
	uv sync --all-extras

.PHONY: dev
dev: sync  ## Alias for sync

.PHONY: lock
lock:  ## Refresh uv.lock
	uv lock

.PHONY: format
format:  ## Ruff format
	uv run ruff format $(SRC)

.PHONY: format-check
format-check:  ## Ruff format --check
	uv run ruff format --check $(SRC)

.PHONY: lint
lint:  ## Ruff check --fix
	uv run ruff check --fix $(SRC)

.PHONY: lint-check
lint-check:  ## Ruff check, no fixes — what CI runs
	uv run ruff check $(SRC)

.PHONY: typecheck
typecheck:  ## mypy + pyright (src only — see pyproject for why not tests)
	uv run mypy
	uv run pyright

# Two install shapes, two runs. The lexical-only install is a SUPPORTED
# configuration, not a degraded one, and it has code paths of its own: the
# semantic modules are never imported, the arm reports itself absent, search
# falls back. A suite run only with the extra installed never executes them.
.PHONY: test
test: test-lexical test-semantic  ## Run the suite in both install shapes

.PHONY: test-lexical
test-lexical:  ## Suite in a genuinely minimal env (no semantic extra)
	@# A SEPARATE environment, not a flag. `uv run --no-extra semantic` does
	@# not uninstall anything: the shared .venv already carries the extra from
	@# `uv sync --all-extras`, so the import still succeeds and the run is
	@# indistinguishable from the semantic one. Both legs reported 210 passed
	@# before this was caught — a test matrix testing the same thing twice.
	UV_PROJECT_ENVIRONMENT=.venv-lexical uv sync --quiet
	UV_PROJECT_ENVIRONMENT=.venv-lexical uv run pytest tests -q

.PHONY: test-semantic
test-semantic:  ## Suite with the semantic extra
	uv run --extra semantic pytest tests -q

# Coverage has to span both shapes and be combined, not measured in one of them.
# Either leg alone understates the other: only the lexical-only run reaches the
# absent-semantic-arm fallbacks, and only the semantic run reaches the embedder
# and reranker. The floor lives in pyproject under [tool.coverage.report].
.PHONY: coverage
coverage:  ## Combined coverage across both install shapes, floor enforced
	rm -f .coverage .coverage.lexical .coverage.semantic
	COVERAGE_FILE=.coverage.lexical UV_PROJECT_ENVIRONMENT=.venv-lexical uv sync --quiet
	@# --cov-fail-under=0 on the legs: the floor applies to the combined total,
	@# and pytest-cov would otherwise fail each leg for missing the other's paths.
	COVERAGE_FILE=.coverage.lexical UV_PROJECT_ENVIRONMENT=.venv-lexical \
		uv run pytest tests -q --cov --cov-report= --cov-fail-under=0
	COVERAGE_FILE=.coverage.semantic uv run --extra semantic \
		pytest tests -q --cov --cov-report= --cov-fail-under=0
	uv run coverage combine .coverage.lexical .coverage.semantic
	uv run coverage report

.PHONY: bench
bench:  ## Answer-recall benchmark; ROOT=<abs path to a corpus>
	@test -n "$(ROOT)" || { echo "usage: make bench ROOT=/abs/path/to/corpus"; exit 2; }
	uv run --extra semantic forge-search bench \
		--root "$(ROOT)" --fixture bench/apexyard.txt --semantic --top-k 10

.PHONY: security
security:  ## bandit + pip-audit, via uvx so neither is carried as a dev dep
	uvx bandit -r src -ll
	uvx pip-audit --skip-editable

.PHONY: build
build:  ## Build sdist + wheel
	@# Clear dist first: it is not versioned away, so old wheels accumulate and
	@# anything selecting one by glob would install whichever sorts first —
	@# 0.4.2 rather than 0.5.0. `smoke` used to do exactly that.
	rm -rf dist
	uv build --out-dir dist

.PHONY: smoke
smoke: build  ## Install the built wheel into a clean venv and actually run it
	bash scripts/smoke.sh

pre-commit:  ## Run all pre-commit hooks across the repo
	uv run pre-commit run --all-files

.PHONY: clean
clean:  ## Remove build / cache artifacts
	rm -rf dist build .ruff_cache .pytest_cache .mypy_cache .smoke .venv-lexical .coverage* coverage.xml
	find . \( -name "__pycache__" -o -name ".forge_search" \) -type d -prune -exec rm -rf {} +

.PHONY: ci
ci: lint-check format-check typecheck coverage  ## What CI runs, locally
	@# `coverage` rather than `test`: it runs the same two legs and adds the
	@# floor, so this is the same work with one more assertion. `make test` is
	@# still there for a quicker run without it.

.PHONY: all
all: sync ci security smoke  ## Everything
