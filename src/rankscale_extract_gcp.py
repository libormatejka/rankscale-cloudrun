"""
Rankscale → BigQuery  |  Topic Metrics History — GCP verze
Stahuje týdenní historii visibility/sentiment (vlastní brand i konkurenti,
rozdělenou po topicu) z Rankscale API a ukládá do BigQuery.

Určeno pro spuštění jako Cloud Run Job (spouštěný Cloud Schedulerem).
  - Autentizace k BigQuery přes Application Default Credentials (Cloud Run
    Job service account) — žádný GCP_SA_JSON soubor/secret není potřeba.
  - RANKSCALE_API_KEY se čte z env proměnné napojené na Secret Manager.
  - Neúspěch (výjimka na úrovni main) končí nenulovým exit kódem, aby to
    Cloud Run Job / Scheduler vyhodnotil jako failed execution.

Každý běh (denní i backfill) stahuje stejné, kompletní okno historie
(TOPIC_METRICS_START_DATE → dnešek) a tabulku topic_metrics_history celou
přepíše (TRUNCATE) — POST /v1/metrics/report vrací pokaždé celé okno znovu,
ne jen nová data (viz doc/api/report.md).
"""

import io
import json
import logging
import os
import sys
import time
import uuid
from datetime import UTC, date, datetime

import requests
from google.cloud import bigquery

# ── Konfigurace ────────────────────────────────────────────────────────────────
API_BASE       = "https://rankscale.ai"
API_KEY        = os.environ["RANKSCALE_API_KEY"]
GCP_PROJECT    = os.environ["GCP_PROJECT"]
BQ_DATASET     = os.environ["BQ_DATASET"]
BACKFILL_WEEKS = int(os.environ["BACKFILL_WEEKS"]) if os.environ.get("BACKFILL_WEEKS") else None
RATE_SLEEP     = 0.5

# Odkud sahá historie topic_metrics_history — reálná data v Rankscale existují
# až od poloviny května 2026, dřívější datum by jen vracelo prázdné řádky.
TOPIC_METRICS_START_DATE = "2026-05-11"

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


def bq_append(
    client: bigquery.Client,
    table: str,
    rows: list[dict],
    write_disposition: str = bigquery.WriteDisposition.WRITE_APPEND,
) -> None:
    if not rows:
        log.info(f"    → {table.split('.')[-1]}: 0 řádků, přeskakuji")
        return
    for row in rows:
        row["etl_loaded_at"] = NOW
    ndjson = "\n".join(json.dumps(r, ensure_ascii=False, default=str) for r in rows)
    cfg = bigquery.LoadJobConfig(
        write_disposition=write_disposition,
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
def extract_brand_topics(client: bigquery.Client) -> dict[str, list[tuple[str, str]]]:
    """Vrátí topics_by_brand: brand_id → [(topic_id, topic_name), ...], z
    operationalTopics. Nezapisuje nic do BQ — slouží jen jako vstup pro
    extract_topic_metrics_history()."""
    log.info("── brands (jen seznam topiců)")
    data   = api_get("/v1/metrics/brands", {"limit": 1000})
    brands = data["data"]["brands"]
    topics_by_brand = {
        b["id"]: [(t["topicId"], t["name"]) for t in b.get("operationalTopics", [])]
        for b in brands
    }
    log.info(f"    {len(topics_by_brand)} brand(ů): {list(topics_by_brand.keys())}")
    return topics_by_brand


def _topic_metric_rows(
    brand_id: str,
    topic_id: str,
    topic_name: str,
    entity_name: str,
    is_own_brand: bool,
    series: dict,
) -> list[dict]:
    """Rozbalí jednu 'weekly' časovou řadu (ownBrandMetrics.historicalData.weekly
    nebo competitorTimeSeriesData.weekly.competitors[].metrics + společné
    timestamps) na řádky, jeden per týden. Pole se defenzivně indexují —
    v odpovědi API nemají všechny metriky nutně stejnou délku jako timestamps
    (viz doc/api/report.md)."""
    timestamps = series.get("timestamps", [])

    def at(key: str, i: int):
        values = series.get(key, [])
        return values[i] if i < len(values) else None

    return [
        {
            "brand_id":        brand_id,
            "topic_id":        topic_id,
            "topic_name":      topic_name,
            "entity_name":     entity_name,
            "is_own_brand":    is_own_brand,
            "week_start":      ts,
            "visibility_score": at("visibilityScore", i),
            "sentiment":       at("sentiment", i),
            "avg_position":    at("avgPosition", i),
            "detection_rate":  at("detectionRate", i),
            "top3":            at("top3", i),
            "mentions":        at("mentions", i),
            "citations":       at("citations", i),
        }
        for i, ts in enumerate(timestamps)
    ]


def extract_topic_metrics_history(
    client: bigquery.Client,
    topics_by_brand: dict[str, list[tuple[str, str]]],
    iso_start: str,
    iso_end: str,
) -> tuple[list[str], list[str]]:
    """Týdenní historie visibility/sentiment (+ pár dalších metrik) pro vlastní
    brand i konkurenty, rozdělená po topicu. Tabulka se přepisuje celá
    (TRUNCATE) při každém běhu — POST /v1/metrics/report s 'selectedTopic'
    vrací pokaždé kompletní okno historie znovu, ne jen nová data, takže
    WRITE_APPEND by jen duplikoval stejné týdny (viz doc/api/report.md).
    Musí se volat zvlášť per topic — 'selectedTopic: all' vrací jinou
    (nesprávnou, souhrnnou) historii pro konkurenty.

    Vrátí (failed_brands, error_messages) pro log_run().
    """
    log.info("── topic_metrics_history")
    rows: list[dict] = []
    failed_brands: list[str] = []
    errors: list[str] = []

    for brand_id, topics in topics_by_brand.items():
        brand_failed = False
        for topic_id, topic_name in topics:
            try:
                data = api_post("/v1/metrics/report", {
                    "brandId":       brand_id,
                    "aggregation":   "weekly",
                    "selectedTopic": topic_id,
                    "isoStartDate":  iso_start,
                    "isoEndDate":    iso_end,
                })
            except Exception as e:
                log.error(f"    {brand_id}/{topic_name}: selhalo — {e}")
                brand_failed = True
                errors.append(f"{brand_id}/{topic_name}: {e}")
                continue

            d = data["data"]
            own = d["ownBrandMetrics"]
            rows += _topic_metric_rows(
                brand_id, topic_id, topic_name, own["name"], True,
                own["historicalData"]["weekly"],
            )

            comp_series = d["competitorTimeSeriesData"]["weekly"]
            comp_timestamps = comp_series.get("timestamps", [])
            for comp in comp_series.get("competitors", []):
                rows += _topic_metric_rows(
                    brand_id, topic_id, topic_name, comp["name"], False,
                    {"timestamps": comp_timestamps, **comp["metrics"]},
                )

            log.info(f"    {brand_id}/{topic_name}: {len(comp_series.get('competitors', []))} konkurentů")
            time.sleep(RATE_SLEEP)

        if brand_failed:
            failed_brands.append(brand_id)

    bq_append(
        client,
        f"{GCP_PROJECT}.{BQ_DATASET}.topic_metrics_history",
        rows,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
    )

    return failed_brands, errors


# ── Main ───────────────────────────────────────────────────────────────────────
def main() -> None:
    mode = f"BACKFILL {BACKFILL_WEEKS} týdnů" if BACKFILL_WEEKS else "denní run"
    log.info("╔══════════════════════════════════════════╗")
    log.info(f"║  Rankscale Topic Metrics History (GCP)  [{mode}]")
    log.info("╚══════════════════════════════════════════╝")

    started_at = NOW
    client     = make_client()
    topics_by_brand: dict[str, list[tuple[str, str]]] = {}

    try:
        topics_by_brand = extract_brand_topics(client)

        failed_brands, errors = extract_topic_metrics_history(
            client, topics_by_brand,
            iso_start=TOPIC_METRICS_START_DATE,
            iso_end=date.today().isoformat(),
        )

        log_run(
            client,
            started_at=started_at,
            status="failed" if failed_brands else "success",
            brands_total=len(topics_by_brand),
            brands_failed=len(failed_brands),
            error_message="; ".join(errors) if errors else None,
        )

        if failed_brands:
            log.error("╔══════════════════════════════════════════╗")
            log.error(f"║  Dokončeno s chybami — selhalo {len(failed_brands)}/{len(topics_by_brand)} brandů")
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
        # Chyba mimo smyčku brandů (např. extract_brand_topics) — zaloguj run
        # jako failed i tady, ať je v etl_runs vidět i tenhle typ selhání.
        log.error(f"Pipeline selhala: {e}")
        log_run(
            client,
            started_at=started_at,
            status="failed",
            brands_total=len(topics_by_brand),
            brands_failed=0,
            error_message=str(e),
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
