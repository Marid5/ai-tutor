.PHONY: setup test lint format validate user

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
	.venv/bin/python -m app.cli create-user $(NAME) $(ARGS)
