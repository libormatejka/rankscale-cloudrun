"""
Rankscale → BigQuery  |  Raw Extract  (L0 vrstva) — GCP verze
Stahuje data z Rankscale API a ukládá je 1:1 do BigQuery (raw_ tabulky).
Žádná transformační logika — ta patří do transformačních SQL skriptů.

Určeno pro spuštění jako Cloud Run Job (spouštěný Cloud Schedulerem).
Rozdíly oproti paralelní GitHub Actions pipeline (jiný repozitář):
  - Autentizace k BigQuery přes Application Default Credentials (Cloud Run
    Job service account) — žádný GCP_SA_JSON soubor/secret není potřeba.
  - RANKSCALE_API_KEY se čte z env proměnné napojené na Secret Manager.
  - Neúspěch (výjimka na úrovni main) končí nenulovým exit kódem, aby to
    Cloud Run Job / Scheduler vyhodnotil jako failed execution.

Režimy spuštění:
  - Denní run (výchozí): stáhne aktuální týden, přeskočí pokud nejsou nová data
  - Backfill:            BACKFILL_WEEKS=N projde N týdnů zpět (vždy zapíše)

Časové okno: každý týden se volá s explicitním isoStartDate + isoEndDate (pondělí–neděle).
"""

import io
import json
import logging
import os
import sys
import time
import uuid
from datetime import UTC, date, datetime, timedelta

import requests
from google.cloud import bigquery

# ── Konfigurace ────────────────────────────────────────────────────────────────
API_BASE       = "https://rankscale.ai"
API_KEY        = os.environ["RANKSCALE_API_KEY"]
GCP_PROJECT    = os.environ["GCP_PROJECT"]
BQ_DATASET     = os.environ["BQ_DATASET"]
BACKFILL_WEEKS = int(os.environ["BACKFILL_WEEKS"]) if os.environ.get("BACKFILL_WEEKS") else None
RATE_SLEEP     = 0.5

NOW    = datetime.now(UTC).isoformat()
RUN_ID = str(uuid.uuid4())

# Souhrnná statistika běhu pro etl_runs (viz log_run) — bq_append do ní
# přičítá počet zapsaných řádků napříč všemi tabulkami.
run_stats = {"rows_written": 0}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


# ── BigQuery helpers ───────────────────────────────────────────────────────────
def make_client() -> bigquery.Client:
    # Na Cloud Run Job běžíme pod service accountem jobu — ADC to vyřeší samo,
    # žádný JSON klíč se nikam nepřenáší.
    return bigquery.Client(project=GCP_PROJECT)


def tbl(name: str) -> str:
    return f"{GCP_PROJECT}.{BQ_DATASET}.raw_{name}"


def bq_append(client: bigquery.Client, table: str, rows: list[dict]) -> None:
    if not rows:
        log.info(f"    → {table.split('.')[-1]}: 0 řádků, přeskakuji")
        return
    for row in rows:
        row["etl_loaded_at"] = NOW
    ndjson = "\n".join(json.dumps(r, ensure_ascii=False, default=str) for r in rows)
    cfg = bigquery.LoadJobConfig(
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
        autodetect=False,
    )
    client.load_table_from_file(
        io.BytesIO(ndjson.encode()),
        table,
        job_config=cfg,
    ).result()
    run_stats["rows_written"] += len(rows)
    log.info(f"    → {table.split('.')[-1]}: {len(rows)} řádků zapsáno")


def bq_max_snapshot(client: bigquery.Client, brand_id: str) -> datetime | None:
    """Vrátí MAX(last_snapshot_at) z raw_brand_snapshots pro daný brand. None pokud tabulka prázdná."""
    query = f"""
        SELECT MAX(last_snapshot_at) AS max_snap
        FROM `{GCP_PROJECT}.{BQ_DATASET}.raw_brand_snapshots`
        WHERE brand_id = @brand_id
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("brand_id", "STRING", brand_id)]
    )
    try:
        rows = list(client.query(query, job_config=job_config).result())
        return rows[0].max_snap if rows and rows[0].max_snap else None
    except Exception:
        return None


def log_run(
    client: bigquery.Client,
    started_at: str,
    status: str,
    brands_total: int,
    brands_failed: int,
    error_message: str | None,
) -> None:
    """Zapíše jeden souhrnný řádek běhu do {GCP_PROJECT}.{BQ_DATASET}.etl_runs.

    Chyba při zápisu logu se jen zaloguje, nesmí shodit jinak úspěšný run —
    proto vlastní try/except (na rozdíl od ostatních bq_append volání).
    """
    row = {
        "run_id":         RUN_ID,
        "started_at":     started_at,
        "finished_at":    datetime.now(UTC).isoformat(),
        "mode":           "backfill" if BACKFILL_WEEKS else "daily",
        "status":         status,
        "brands_total":   brands_total,
        "brands_failed":  brands_failed,
        "rows_written":   run_stats["rows_written"],
        "error_message":  error_message,
    }
    try:
        bq_append(client, f"{GCP_PROJECT}.{BQ_DATASET}.etl_runs", [row])
    except Exception as e:
        log.error(f"Nepodařilo se zapsat run log do etl_runs: {e}")


# ── Týdenní date ranges ────────────────────────────────────────────────────────
def week_ranges(num_weeks: int) -> list[tuple[str, str]]:
    """
    Vrátí list (iso_start, iso_end) pro posledních N týdnů, od nejstaršího po nejnovější.
    Každý týden = pondělí–neděle.
    """
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    ranges = []
    for i in range(num_weeks - 1, -1, -1):
        start = monday - timedelta(weeks=i)
        end   = start + timedelta(days=6)
        ranges.append((start.isoformat(), end.isoformat()))
    return ranges


def current_week() -> tuple[str, str]:
    """Vrátí (iso_start, iso_end) pro aktuální týden (pondělí–neděle)."""
    return week_ranges(1)[0]


# ── Rankscale API ──────────────────────────────────────────────────────────────
HEADERS = {"Authorization": f"Bearer {API_KEY}"}


def api_get(path: str, params: dict | None = None) -> dict:
    r = requests.get(f"{API_BASE}{path}", headers=HEADERS, params=params, timeout=120)
    r.raise_for_status()
    return r.json()


def api_post(path: str, body: dict) -> dict:
    r = requests.post(f"{API_BASE}{path}", headers=HEADERS, json=body, timeout=120)
    r.raise_for_status()
    return r.json()


# ── Extract kroky ──────────────────────────────────────────────────────────────
def extract_brands(client: bigquery.Client) -> list[str]:
    log.info("── brands")
    data   = api_get("/v1/metrics/brands", {"limit": 1000})
    brands = data["data"]["brands"]
    rows   = [
        {
            "brand_id":     b["id"],
            "name":         b["name"],
            "domain":       b.get("url"),
            "is_own_brand": True,
        }
        for b in brands
    ]
    bq_append(client, tbl("brands"), rows)
    ids = [b["id"] for b in brands]
    log.info(f"    {len(ids)} brand(ů): {ids}")
    return ids


def extract_search_terms(client: bigquery.Client, brand_ids: list[str]) -> None:
    log.info("── search_terms")
    for brand_id in brand_ids:
        data  = api_get("/v1/metrics/search-terms", {"brandId": brand_id, "limit": 5000})
        terms = data["data"]["searchTerms"]
        rows  = []
        for t in terms:
            topic = t.get("searchTermTopicRef") or {}
            rows.append({
                "brand_id":             brand_id,
                "search_term_id":       t["id"],
                "query":                t.get("term"),
                "engine":               (t.get("aiSearchEngines") or [""])[0],
                "topic_id":             topic.get("id"),
                "topic_name":           topic.get("name"),
                "region":               t.get("region"),
                "interval":             t.get("interval"),
                "tags":                 json.dumps(t.get("tags", []), ensure_ascii=False),
                "status":               t.get("status"),
                "created_at":           t.get("createdAt"),
                "last_execution_time":  t.get("lastExecutionTime"),
                "next_execution_time":  t.get("nextScheduledExecutionTime"),
                "executions_amount":    t.get("executionsAmount"),
            })
        bq_append(client, tbl("search_terms"), rows)
        log.info(f"    {brand_id}: {len(rows)} termů")
        time.sleep(RATE_SLEEP)


def _fetch_snapshots(brand_id: str, iso_start: str, iso_end: str) -> tuple[list[dict], datetime | None]:
    """
    Zavolá search-terms-report API a vrátí (rows, api_max_snapshot_at).
    rows = připravené řádky pro raw_brand_snapshots.
    api_max_snapshot_at = nejnovější lastSnapshotAt ze všech search termů v response.
    """
    data  = api_post("/v1/metrics/search-terms-report", {
        "brandId":            brand_id,
        "isoStartDate":       iso_start,
        "isoEndDate":         iso_end,
        "selectedTopic":      "all",
        "selectedTags":       "all",
        "selectedEngine":     "all",
        "selectedQuery":      "all",
        "includeAnswerTexts": False,
    })
    terms = data["data"].get("searchTerms", [])
    rows  = []
    max_snap = None

    for t in terms:
        topic  = t.get("topic") or {}
        engine = (t.get("aiSearchEngines") or [""])[0]
        snap   = t.get("lastSnapshotAt")

        if snap:
            snap_dt = datetime.fromisoformat(snap.replace("Z", "+00:00"))
            if max_snap is None or snap_dt > max_snap:
                max_snap = snap_dt

        base = {
            "brand_id":         brand_id,
            "search_term_id":   t["searchTermId"],
            "engine":           engine,
            "topic_id":         topic.get("id"),
            "topic_name":       topic.get("name"),
            "last_snapshot_at": snap,
        }

        def make_row(b: dict, is_own: bool, base: dict = base) -> dict:
            return {
                **base,
                "brand_name":       b.get("name"),
                "is_own_brand":     is_own,
                "visibility_score": b.get("visibilityScore"),
                "avg_sentiment":    b.get("avgSentiment"),
                "avg_rank":         b.get("avgRank"),
                "latest_rank":      b.get("latestRank"),
                "detection_rate":   b.get("detectionRate"),
                "top3_rate":        b.get("top3"),
                "citation_count":   b.get("citationCount"),
                "appearances":      b.get("appearances"),
            }

        if "ownBrand" in t:
            rows.append(make_row(t["ownBrand"], is_own=True))
        for comp in t.get("competitors", []):
            rows.append(make_row(comp, is_own=False))

    return rows, max_snap


def _fetch_answer_texts(brand_id: str, iso_start: str, iso_end: str) -> list[dict]:
    data  = api_post("/v1/metrics/search-terms-report", {
        "brandId":            brand_id,
        "isoStartDate":       iso_start,
        "isoEndDate":         iso_end,
        "selectedTopic":      "all",
        "selectedTags":       "all",
        "selectedEngine":     "all",
        "selectedQuery":      "all",
        "includeAnswerTexts": True,
    })
    terms = data["data"].get("searchTerms", [])
    rows  = []
    for t in terms:
        for at in t.get("answerTexts") or []:
            rows.append({
                "brand_id":       brand_id,
                "search_term_id": t["searchTermId"],
                "execution_id":   at["executionId"],
                "executed_at":    at.get("executedAt"),
                "engine":         at.get("engine"),
                "answer_text":    at.get("answerText"),
            })
    return rows


def extract_snapshots_and_texts(
    client: bigquery.Client,
    brand_id: str,
    iso_start: str,
    iso_end: str,
    force: bool = False,
) -> bool:
    """
    Stáhne brand_snapshots a answer_texts pro daný brand a týdenní okno.
    force=True: vždy zapíše (backfill).
    force=False: přeskočí pokud BQ už má data pro tento snapshot (denní run).
    Vrátí True pokud data byla zapsána, False pokud přeskočeno.
    """
    log.info(f"── brand_snapshots + answer_texts  (brand={brand_id}, {iso_start} → {iso_end})")

    snap_rows, api_max = _fetch_snapshots(brand_id, iso_start, iso_end)

    if not force:
        bq_max = bq_max_snapshot(client, brand_id)
        if api_max and bq_max and api_max <= bq_max:
            log.info(f"    → přeskočeno: BQ již má snapshot {api_max.date()} (žádná nová data)")
            return False

    bq_append(client, tbl("brand_snapshots"), snap_rows)
    time.sleep(RATE_SLEEP)

    text_rows = _fetch_answer_texts(brand_id, iso_start, iso_end)
    bq_append(client, tbl("answer_texts"), text_rows)

    return True


def extract_citations(client: bigquery.Client, brand_id: str) -> None:
    log.info(f"── citations  (brand={brand_id})")
    data = api_post("/v1/metrics/citations", {
        "brandId":   brand_id,
        "timeFrame": "7d",
    })
    rows = []
    for term_entry in data["data"].get("domainSummary", {}).get("topDomainsByQuery", []):
        query          = term_entry.get("query", "")
        search_term_id = (term_entry.get("searchTermIds") or [None])[0]
        for engine_entry in term_entry.get("engines", []):
            engine = engine_entry.get("engineId", "")
            for domain_entry in engine_entry.get("domains", []):
                domain      = domain_entry.get("domain", "")
                occurrences = domain_entry.get("occurrences", 0)
                urls        = domain_entry.get("urls") or []
                if urls:
                    for url_entry in urls:
                        rows.append({
                            "brand_id":       brand_id,
                            "search_term_id": search_term_id,
                            "query":          query,
                            "engine":         engine,
                            "domain":         domain,
                            "url":            url_entry.get("url"),
                            "occurrences":    url_entry.get("occurrences", 0),
                        })
                else:
                    rows.append({
                        "brand_id":       brand_id,
                        "search_term_id": search_term_id,
                        "query":          query,
                        "engine":         engine,
                        "domain":         domain,
                        "url":            None,
                        "occurrences":    occurrences,
                    })
    bq_append(client, tbl("citations"), rows)


# ── Main ───────────────────────────────────────────────────────────────────────
def main() -> None:
    mode = f"BACKFILL {BACKFILL_WEEKS} týdnů" if BACKFILL_WEEKS else "denní run"
    log.info("╔══════════════════════════════════════════╗")
    log.info(f"║  Rankscale Raw Extract (GCP)  [{mode}]")
    log.info("╚══════════════════════════════════════════╝")

    started_at = NOW
    client     = make_client()
    brand_ids: list[str] = []

    try:
        brand_ids = extract_brands(client)
        extract_search_terms(client, brand_ids)

        # Backfill: N týdnů zpět s explicitními date ranges (pondělí–neděle)
        # Denní run: jen aktuální týden
        is_backfill = bool(BACKFILL_WEEKS)
        weeks = week_ranges(BACKFILL_WEEKS) if is_backfill else [current_week()]

        log.info(f"    Týdny ke stažení: {[w[0] for w in weeks]}")

        failed_brands = []
        errors = []
        for brand_id in brand_ids:
            log.info(f"━━ Brand: {brand_id}")
            try:
                for iso_start, iso_end in weeks:
                    extract_snapshots_and_texts(
                        client, brand_id, iso_start, iso_end, force=is_backfill
                    )
                    time.sleep(RATE_SLEEP)

                # Citations: vždy jen aktuální týden (API nepodporuje historický backfill)
                extract_citations(client, brand_id)
                time.sleep(RATE_SLEEP)

            except Exception as e:
                log.error(f"Brand {brand_id} selhal: {e}")
                failed_brands.append(brand_id)
                errors.append(f"{brand_id}: {e}")

        log_run(
            client,
            started_at=started_at,
            status="failed" if failed_brands else "success",
            brands_total=len(brand_ids),
            brands_failed=len(failed_brands),
            error_message="; ".join(errors) if errors else None,
        )

        if failed_brands:
            log.error("╔══════════════════════════════════════════╗")
            log.error(f"║  Dokončeno s chybami — selhalo {len(failed_brands)}/{len(brand_ids)} brandů")
            log.error("╚══════════════════════════════════════════╝")
            # Nenulový exit kód → Cloud Run Job execution se označí jako Failed
            # a je vidět v Cloud Monitoring / notifikacích.
            sys.exit(1)

        log.info("╔══════════════════════════════════════════╗")
        log.info("║  Hotovo ✓                                 ║")
        log.info("╚══════════════════════════════════════════╝")

    except SystemExit:
        raise
    except Exception as e:
        # Chyba mimo smyčku brandů (např. extract_brands) — zaloguj run jako
        # failed i tady, ať je v etl_runs vidět i tenhle typ selhání.
        log.error(f"Pipeline selhala: {e}")
        log_run(
            client,
            started_at=started_at,
            status="failed",
            brands_total=len(brand_ids),
            brands_failed=0,
            error_message=str(e),
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
