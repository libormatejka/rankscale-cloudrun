# Vyžaduje pro deploy-* / execute / backfill: GCP_PROJECT, REGION, REPO
# (viz README, krok 1) — exportované v shellu, ne natvrdo tady.
IMAGE ?= $(REGION)-docker.pkg.dev/$(GCP_PROJECT)/$(REPO)/rankscale-extract:latest
JOB   := rankscale-extract
WEEKS ?= 52

.PHONY: help lint build run deploy deploy-build deploy-update execute backfill check-env

.DEFAULT_GOAL := help

help: ## Vypíše dostupné cíle
	@awk 'BEGIN {FS = ":.*##"} /^[a-zA-Z_-]+:.*##/ { printf "  %-15s %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

lint: ## Lokální lint (ruff) — viz README "Lint"
	ruff check .

build: ## Docker build image lokálně (bez pushe)
	docker build -t rankscale-extract-local src

run: build ## Spustí lokálně sestavený image (potřebuje ADC + RANKSCALE_API_KEY/GCP_PROJECT/BQ_DATASET v shellu)
	docker run --rm \
		-e RANKSCALE_API_KEY \
		-e GCP_PROJECT \
		-e BQ_DATASET \
		-v ~/.config/gcloud:/root/.config/gcloud:ro \
		rankscale-extract-local

check-env:
	@test -n "$(GCP_PROJECT)" || (echo "GCP_PROJECT není nastaven (viz README krok 1)"; exit 1)
	@test -n "$(REGION)" || (echo "REGION není nastaven (viz README krok 1)"; exit 1)
	@test -n "$(REPO)" || (echo "REPO není nastaven (viz README krok 1)"; exit 1)

deploy-build: check-env ## Build image v Cloud Buildu a push do Artifact Registry (README krok 4/7)
	gcloud builds submit src --project=$(GCP_PROJECT) --tag "$(IMAGE)"

deploy-update: check-env ## Aktualizuje Cloud Run Job na nově pushnutý image (README krok 7)
	gcloud run jobs update $(JOB) --project=$(GCP_PROJECT) --region=$(REGION) --image="$(IMAGE)"

deploy: deploy-build deploy-update ## Build + push + update jedním příkazem

execute: check-env ## Ruční spuštění jobu (README krok 5, "Ruční spuštění / test")
	gcloud run jobs execute $(JOB) --project=$(GCP_PROJECT) --region=$(REGION)

backfill: check-env ## Backfill — WEEKS=N make backfill (default 52, README krok 5, "Backfill")
	gcloud run jobs execute $(JOB) --project=$(GCP_PROJECT) --region=$(REGION) \
		--update-env-vars="BACKFILL_WEEKS=$(WEEKS)"
