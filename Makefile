.PHONY: help install up down reset logs ps health test test-all lint format typecheck check

help:
	@echo "Meridian development commands:"
	@echo "  make install     sync dependencies via uv"
	@echo "  make up          boot postgres+timescale and redis"
	@echo "  make down        stop containers (preserves volumes)"
	@echo "  make reset       stop containers and delete volumes"
	@echo "  make logs        tail container logs"
	@echo "  make ps          show container status"
	@echo "  make health      run the healthcheck CLI"
	@echo "  make test        run unit tests"
	@echo "  make test-all    run unit + integration tests"
	@echo "  make lint        ruff check"
	@echo "  make format      ruff format"
	@echo "  make typecheck   mypy"
	@echo "  make check       lint + typecheck + tests"

install:
	uv sync

up:
	docker compose up -d --wait

down:
	docker compose down

reset:
	docker compose down -v

logs:
	docker compose logs -f --tail=200

ps:
	docker compose ps

health:
	uv run python -m meridian.cli health

test:
	uv run pytest -m "not integration"

test-all:
	uv run pytest

lint:
	uv run ruff check .

format:
	uv run ruff format .

typecheck:
	uv run mypy

check: lint typecheck test
