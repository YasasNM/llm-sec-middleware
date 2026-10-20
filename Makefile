.PHONY: help setup contracts structure lint test dev up down smoke eval models

help:
	@echo "make setup | contracts | structure | lint | test | dev | up | down | smoke | eval ARM=c1,c2 | models"

setup:
	bash scripts/setup_dev.sh

contracts:
	python contracts/validate.py

structure:
	bash scripts/check_structure.sh

lint:
	ruff check .

test:
	pytest

dev:
	docker compose -f infra/compose/docker-compose.dev.yml up --build

up:
	docker compose -f infra/compose/docker-compose.yml up -d --build

down:
	docker compose -f infra/compose/docker-compose.yml down

smoke:
	bash infra/scripts/smoke_test.sh

eval:
	python evaluation/system/runner.py --arm $(ARM)

models:
	python scripts/download_models.py
