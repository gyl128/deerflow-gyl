install:
	uv sync

dev:
	./scripts/run-langgraph.sh dev --host 0.0.0.0 --port 2024

prod:
	./scripts/run-langgraph.sh prod --host 0.0.0.0 --port 2024

gateway:
	./scripts/run-module.sh uvicorn app.gateway.app:app --host 0.0.0.0 --port 8001

test:
	PYTHONPATH=. uv run pytest tests/ -v

lint:
	uvx ruff check .

format:
	uvx ruff check . --fix && uvx ruff format .
