.PHONY: help setup contracts structure lint test dev up down smoke eval models

help:
<TAB>@echo "make setup | contracts | structure | lint | test | dev | up | down | smoke | eval ARM=c1,c2 | models"

setup:
<TAB>bash scripts/setup_dev.sh

contracts:
<TAB>python contracts/validate.py

structure:
<TAB>bash scripts/check_structure.sh

lint:
<TAB>ruff check .

test:
<TAB>pytest

dev:
<TAB>docker compose -f infra/compose/docker-compose.dev.yml up --build

up:
<TAB>docker compose -f infra/compose/docker-compose.yml up -d --build

down:
<TAB>docker compose -f infra/compose/docker-compose.yml down

smoke:
<TAB>bash infra/scripts/smoke_test.sh

eval:
<TAB>python evaluation/system/runner.py --arm $(ARM)

models:
<TAB>python scripts/download_models.py
