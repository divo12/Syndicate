.PHONY: up down client health

up:
	test -f .env || cp .env.example .env
	docker compose up --build -d
	@echo "API: http://127.0.0.1:8000/docs"
	@echo "Trigger worker (optional): npx trigger.dev@latest dev"

down:
	docker compose down

health:
	curl -sf http://127.0.0.1:8000/health

client:
	.venv/bin/python scripts/job_client.py regex-log extract-elf log-summary-date-ranges
