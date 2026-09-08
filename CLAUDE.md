# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A daily ETL pipeline that pulls data from the Rankscale Metrics API and appends it
1:1 (no transformation) into BigQuery `raw_*` tables. It runs as a single Python
script inside a **Cloud Run Job**, triggered daily by **Cloud Scheduler**. There is
no server, no framework — one script, one container, one scheduled invocation.

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
make backfill WEEKS=N # one-off backfill run (default WEEKS=52)
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
- `doc/` — supporting docs that are never deployed (currently `SECURITY_CHECKLIST.md`).
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

## Data flow and run modes

- `extract_brands` → `extract_search_terms` → per-brand loop of
  `extract_snapshots_and_texts` (+ `extract_citations`, current week only — the API
  has no historical backfill for citations).
- **Daily run** (default): only the current week (Mon–Sun). Before writing
  `raw_brand_snapshots`, `bq_max_snapshot()` checks BigQuery's existing max
  `last_snapshot_at` for that brand and skips the write if the API has nothing newer
  — this is the "skip-if-no-new-data" behavior referenced in the table docs.
  `bq_max_snapshot` uses a parameterized query (`@brand_id`), not an f-string —
  keep it that way; a prior SQL-injection finding (see `doc/SECURITY_CHECKLIST.md`)
  was fixed here and in the parallel pipeline.
- **Backfill** (`BACKFILL_WEEKS=N`): iterates `week_ranges(N)` and always writes
  (`force=True`), bypassing the skip check.
- One brand failing does not stop the run — failures are collected in `main()` and
  only raise `sys.exit(1)` (marking the Cloud Run execution "Failed") after all
  brands have been attempted. A separate top-level `try/except` in `main()` catches
  failures *outside* the per-brand loop (e.g. `extract_brands` itself failing) and
  still logs a run row before exiting — see below.

## Per-run logging (`etl_runs`) and alerting

Every run — success, partial brand failure, or a failure before the brand loop
even starts — writes exactly one summary row to `{GCP_PROJECT}.{BQ_DATASET}.etl_runs`
via `log_run()`, called from both the normal path and the outer `except` in `main()`.
`run_stats["rows_written"]` is a module-level counter incremented inside `bq_append()`
itself, so it tallies rows across every table touched in the run without each
extract function having to report back explicitly. If `log_run()`'s own write fails,
it only logs the error — it must never mask or override the run's actual success/failure
status or exit code.

Failure notification is deliberately *not* done in Python (no SMTP secrets in the
job) — it's wired at the infra level via a Cloud Monitoring alerting policy on the
Cloud Run Jobs `completed_execution_count{result="failed"}` metric (README step 8).
When changing failure-handling logic, keep both mechanisms in mind: the `etl_runs`
row carries the diagnostic detail (`error_message`), the Monitoring alert is just
the "something failed" trigger.

## BigQuery write pattern

All writes go through `bq_append()` (NDJSON → `load_table_from_file`, `WRITE_APPEND`).
There is no dedup/merge logic anywhere — every table is append-only, including
`etl_runs`. Table names for the `raw_*` tables come from `tbl(name)` which prefixes
`raw_`; `etl_runs` is referenced by its full name directly since it isn't a `raw_*`
mirror table. Schema changes go in `src/schema_raw.sql` (all `CREATE TABLE IF NOT
EXISTS`, safe to re-run against a live dataset) and must stay project-ID-agnostic —
the project is supplied externally via `bq query --project_id=...`, not hardcoded
in the SQL.

## Behavioral rules

- Before committing, run `make lint` and make sure it passes.
- Never modify `src/schema_raw.sql` without `CREATE TABLE IF NOT EXISTS` — it must
  stay safe to re-run against a live dataset (see README step 3b).
