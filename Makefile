.PHONY: help install setup test lint format clean run dev

help:
	@echo "Video Agent - Makefile Commands"
	@echo "=================================="
	@echo "make install    - Install dependencies"
	@echo "make setup      - Full project setup"
	@echo "make test       - Run tests"
	@echo "make lint       - Run code linting"
	@echo "make format     - Format code"
	@echo "make clean      - Clean temporary files"
	@echo "make run        - Run video generation"
	@echo "make dev        - Setup development environment"
	@echo "make docker     - Build Docker image"
	@echo "make trending   - Show trending videos"

install:
	pip install -r requirements.txt

setup:
	chmod +x setup.sh
	./setup.sh
	@echo "Setup complete! Update .env with API keys"

dev:
	pip install -r requirements.txt
	pip install -e .
	pre-commit install
	@echo "Development environment ready"

test:
	pytest tests/ -v --cov=src

lint:
	black --check src/ tests/
	ruff check src/ tests/
	mypy src/

format:
	black src/ tests/
	ruff check --fix src/ tests/

clean:
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
	rm -rf .pytest_cache .coverage .mypy_cache
	rm -rf temp/* logs/*

run:
	python -m src.cli generate --topic "AI Trends" --duration 60

dev-run:
	python -m src.main

trending:
	python -m src.cli trending

test-api:
	python -m src.cli test-api

docker-build:
	docker build -t video-agent:latest .

docker-run:
	docker-compose up

examples:
	python examples.py

.DEFAULT_GOAL := help
