.PHONY: setup test lint format validate user build dev serve

setup:
	/opt/homebrew/bin/python3.12 -m venv .venv || python3 -m venv .venv
	.venv/bin/pip install -r requirements-dev.txt
	if [ -f frontend/package.json ]; then npm --prefix frontend ci; fi
	cp -n .env.example .env || true
	mkdir -p data

test:
	.venv/bin/python -m pytest -q

lint:
	.venv/bin/ruff check . && .venv/bin/ruff format --check .

format:
	.venv/bin/ruff format .

validate:
	.venv/bin/python scripts/validate_content.py

user:
	$(if $(NAME),,$(error NAME is required, e.g. make user NAME=ada))
	.venv/bin/python -m app.cli create-user $(NAME) $(ARGS)

# Build the web client and publish it where the backend serves static files.
build:
	@test -d frontend/node_modules || { echo "No frontend dependencies (frontend/node_modules). Run 'make setup' first."; exit 1; }
	npm --prefix frontend run build
	rm -rf static
	cp -r frontend/dist static

# Backend with auto-reload (including content YAML) plus the Vite dev server
# on http://localhost:5173, which forwards /api to the backend. Ctrl-C stops both.
dev:
	@test -x .venv/bin/uvicorn || { echo "No Python environment (.venv). Run 'make setup' first."; exit 1; }
	@test -d frontend/node_modules || { echo "No frontend dependencies (frontend/node_modules). Run 'make setup' first."; exit 1; }
	trap 'kill 0' EXIT; \
	COOKIE_SECURE=false .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload --reload-dir app --reload-dir content --reload-include '*.yaml' & \
	npm --prefix frontend run dev & \
	wait

# Production-like local run over plain http on http://127.0.0.1:8000: the built
# client served by the backend, settings from .env. The session cookie is not
# marked Secure here, otherwise the browser would drop it over http.
serve: build
	@test -x .venv/bin/uvicorn || { echo "No Python environment (.venv). Run 'make setup' first."; exit 1; }
	set -a; [ -f .env ] && . ./.env; set +a; COOKIE_SECURE=false .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
