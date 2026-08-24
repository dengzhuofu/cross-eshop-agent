.PHONY: install dev studio test lint seed reset

install:
	python -m pip install -e ".[dev]"

dev:
	python -m uvicorn app.api.main:app --reload --port 8000

studio:
	python -m langgraph dev

test:
	pytest -q

lint:
	ruff check src tests scripts

seed:
	python scripts/seed_mock_data.py

reset:
	python scripts/reset_demo.py
