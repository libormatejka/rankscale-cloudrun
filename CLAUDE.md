# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A daily pipeline that pulls weekly visibility/sentiment history (own brand
and named competitors, broken down per topic) from the Rankscale Metrics API
into a single BigQuery table, `topic_metrics_history`. It runs as a single
Python script inside a **Cloud Run Job**, triggered daily by **Cloud
Scheduler**. There is no server, no framework — one script, one container,
one scheduled invocation.

This repo previously ingested a much wider set of `raw_*` tables (brands,
search terms, per-search-term brand snapshots, answer texts, citations) —
that approach was retired because the source endpoint
(`search-terms-report`) turned out not to return real historical data (see
`doc/api/search-terms-report.md`). `doc/api/` still documents those
endpoints and the investigation that led here; the pipeline itself no longer
calls most of them (see `doc/api/README.md` for which endpoints are still
live).

## Commands

```bash
make help           # list all targets
make lint           # ruff check . — the only quality gate in this repo (no tests)
make build           # docker build -t rankscale-extract-local src (build context is src/, not repo root)
make run             # build + docker run locally (needs RANKSCALE_API_KEY/GCP_PROJECT/BQ_DATASET env + ADC mounted)
make deploy-build     # gcloud builds submit src --tag ... (push new image to Artifact Registry)
make deploy-update    # gcloud run jobs update ... --image ... (point the Cloud Run Job at it)
make deploy          # deploy-build + deploy-update
make execute         # gcloud run jobs execute (manual/test run)
make backfill WEEKS=N # one-off run with BACKFILL_WEEKS set (see note below — no longer changes behavior)
```

`deploy-*`/`execute`/`backfill` require `GCP_PROJECT`, `REGION`, `REPO` exported in
the shell first (see README step 1) — `check-env` in the Makefile fails fast if
they're missing. There is no test suite; `ruff check .` is the only automated check.
`ruff format` is intentionally not used — it would collapse the hand-aligned `=`
and dict-key columns used throughout `src/rankscale_extract_gcp.py`, which is the
deliberate style in this file.

## Repository layout

- `src/` — everything that gets deployed to GCP (the Docker build context). Contains
  the script, `Dockerfile`, `requirements.txt`, `env.yaml` (non-secret job config),
  and `schema_raw.sql` (DDL). Nothing outside `src/` is copied into the image —
  `src/.dockerignore` explicitly keeps `env.yaml`/`schema_raw.sql` out of the build
  context too, since the Dockerfile only `COPY`s the script and `requirements.txt`.
- `doc/` — supporting docs that are never deployed: `SECURITY_CHECKLIST.md`,
  `API_KEY_ROTATION.md`, and `api/` (per-endpoint Rankscale API reference,
  built from real Postman captures — see `doc/api/README.md`).
- Root — only `README.md`, `Makefile`, `pyproject.toml` (ruff config), `requirements-dev.txt`.

## Architecture: one script, four config surfaces

`src/rankscale_extract_gcp.py` is intentionally a single flat file (no package,
no internal modules). Read it top-to-bottom rather than expecting a multi-file
architecture — extract functions, a couple of BigQuery helpers, and `main()`.

Configuration is split across four places that are easy to conflate — know which
one to change:

1. **Shell vars** (`$GCP_PROJECT`, `$REGION`, `$REPO`, `$SA_NAME`) — local-only,
   `export`ed per terminal session, just convenience for `gcloud`/`make` commands.
   Never persisted anywhere.
2. **`src/env.yaml`** — non-secret Cloud Run Job env vars (`GCP_PROJECT`, `BQ_DATASET`).
   Loaded via `--env-vars-file` into the *Job's* config in GCP, not baked into the
   image — the same image can be repointed at a different project/dataset without
   a rebuild.
3. **Secret Manager** — the one actual secret, `RANKSCALE_API_KEY`, mounted into the
   job via `--set-secrets`. Never put other config here.
4. **`BACKFILL_WEEKS`** — set per-invocation only via `--update-env-vars` on a manual
   `gcloud run jobs execute` / `make backfill`; never persisted in the Job config.
   Kept for the `mode` label in `etl_runs` and in case a future data source needs
   real incremental backfill — today it does not change what
   `extract_topic_metrics_history()` fetches (see below).

## Data flow

- `extract_brand_topics()` → `GET /v1/metrics/brands`, but only to build
  `topics_by_brand: dict[brand_id, list[(topic_id, topic_name)]]` from each
  brand's `operationalTopics`. Nothing from this call is written to BigQuery.
- `extract_topic_metrics_history()` → for every `(brand_id, topic_id)` pair,
  calls `POST /v1/metrics/report` with `selectedTopic=<topic_id>` and
  `aggregation=weekly`, covering `TOPIC_METRICS_START_DATE` (hardcoded,
  `2026-05-11` — when real data starts in this Rankscale account) through
  today. **Must be called once per topic** — `selectedTopic: "all"` returns
  different, non-comparable aggregate data; confirmed via a real A/B test,
  see `doc/api/report.md`.
- From each response: `ownBrandMetrics.historicalData.weekly` gives the own
  brand's weekly time series; `competitorTimeSeriesData.weekly.competitors[]`
  gives the same per named competitor (plus a catch-all `"Others"` bucket).
  Both get flattened into rows by `_topic_metric_rows()` and written to
  `topic_metrics_history`. Own brand and competitors use different key sets
  in the API (`ownBrandMetrics` has more fields) — `_topic_metric_rows()`
  only pulls the subset both have in common.
- Runs unconditionally on every invocation (daily or backfill) — there is no
  skip-if-no-new-data check and no per-brand loop with partial early exit;
  a single call sequence covers all brands and topics.
- One topic call failing marks its brand as failed (collected in
  `extract_topic_metrics_history()`) but does not stop the rest — other
  topics/brands are still attempted. `main()` raises `sys.exit(1)` only after
  everything has been attempted, if anything failed. A separate top-level
  `try/except` in `main()` catches failures *outside* that loop (e.g.
  `extract_brand_topics()` itself failing) and still logs a run row before
  exiting.

## Per-run logging (`etl_runs`) and alerting

Every run — success, partial failure, or a failure before extraction even
starts — writes exactly one summary row to `{GCP_PROJECT}.{BQ_DATASET}.etl_runs`
via `log_run()`, called from both the normal path and the outer `except` in `main()`.
`run_stats["rows_written"]` is a module-level counter incremented inside `bq_append()`
itself. If `log_run()`'s own write fails, it only logs the error — it must
never mask or override the run's actual success/failure status or exit code.

Failure notification is deliberately *not* done in Python (no SMTP secrets in the
job) — it's wired at the infra level via a Cloud Monitoring alerting policy on the
Cloud Run Jobs `completed_execution_count{result="failed"}` metric (README step 8).
When changing failure-handling logic, keep both mechanisms in mind: the `etl_runs`
row carries the diagnostic detail (`error_message`), the Monitoring alert is just
the "something failed" trigger.

## BigQuery write pattern

All writes go through `bq_append()` (NDJSON → `load_table_from_file`), default
`WRITE_APPEND`. `etl_runs` is append-only (one row per run) as you'd expect.
**`topic_metrics_history` is the exception** — `extract_topic_metrics_history()`
writes it with `write_disposition=WRITE_TRUNCATE` (passed explicitly to
`bq_append()`) and replaces the whole table on every run — deliberate:
`/v1/metrics/report` returns the complete history window fresh on every
call, so appending would duplicate every week on every run. Keep that
override in mind if you touch `bq_append()`'s signature. Both tables are
referenced by their full `{GCP_PROJECT}.{BQ_DATASET}.<name>` name directly
(no `raw_` prefix convention — that belonged to the retired tables). Schema
changes go in `src/schema_raw.sql` (all `CREATE TABLE IF NOT EXISTS`, safe
to re-run against a live dataset) and must stay project-ID-agnostic — the
project is supplied externally via `bq query --project_id=...`, not
hardcoded in the SQL.

## Behavioral rules

- **Always ask for explicit confirmation before writing or changing any code
  — describe the intended change first and wait for a yes.** This applies
  even when the change follows directly from a decision the user just made
  (e.g. picking an option in a question) — a decision about *what* to do is
  not the same as approval to *make the edit right now*. Docs-only changes
  the user explicitly asked for in the same message are fine to do directly.
- Before committing, run `make lint` and make sure it passes.
- Never modify `src/schema_raw.sql` without `CREATE TABLE IF NOT EXISTS` — it must
  stay safe to re-run against a live dataset (see README step 3b).
