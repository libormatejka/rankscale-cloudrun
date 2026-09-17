"""
JEDNORÁZOVÝ TESTOVACÍ SKRIPT — není součástí produkční pipeline (src/), nikam
se nedeployuje, nespouští ho Cloud Run Job.

Ověřuje, jestli má smysl rozšířit topic_metrics_history o rozpad podle AI
enginu — volá POST /v1/metrics/report per (brand, topic, engine) a zapíše
výsledek do DOČASNÉ tabulky {GCP_PROJECT}.{BQ_DATASET}.topic_engine_metrics_history_test,
ať se dá porovnat s ostrou topic_metrics_history, než se cokoliv mění na
produkčním schématu/kódu.

Až bude ověřeno, tabulku smaž:
  bq rm -f -t $GCP_PROJECT:RankScaleDashboard.topic_engine_metrics_history_test
a tenhle skript buď smaž, nebo ponech jako referenci pro příští podobný test.

Použití (Cloud Shell nebo lokálně):
  pip install --user requests google-cloud-bigquery
  export RANKSCALE_API_KEY=$(gcloud secrets versions access latest \
    --secret=rankscale-api-key --project=$GCP_PROJECT)
  export GCP_PROJECT=rankscale
  export BQ_DATASET=RankScaleDashboard
  python3 scripts/test_topic_engine_metrics.py
"""

import io
import json
import os
import time
from datetime import date

import requests
from google.cloud import bigquery

API_BASE    = "https://rankscale.ai"
API_KEY     = os.environ["RANKSCALE_API_KEY"]
GCP_PROJECT = os.environ["GCP_PROJECT"]
BQ_DATASET  = os.environ["BQ_DATASET"]
START_DATE  = "2026-05-11"  # stejné jako TOPIC_METRICS_START_DATE v produkčním skriptu
RATE_SLEEP  = 0.5

ENGINES = [
    "chatgpt_gui",
    "google_ai_overview",
    "google_ai_mode_gui",
    "bing_copilot_gui",
    "google_gemini_gui",
]

HEADERS = {"Authorization": f"Bearer {API_KEY}"}


def api_get(path: str, params: dict | None = None) -> dict:
    r = requests.get(f"{API_BASE}{path}", headers=HEADERS, params=params, timeout=120)
    r.raise_for_status()
    return r.json()


def api_post(path: str, body: dict) -> dict:
    r = requests.post(f"{API_BASE}{path}", headers=HEADERS, json=body, timeout=120)
    r.raise_for_status()
    return r.json()


def get_topics_by_brand() -> dict[str, list[tuple[str, str]]]:
    data = api_get("/v1/metrics/brands", {"limit": 1000})
    brands = data["data"]["brands"]
    return {
        b["id"]: [(t["topicId"], t["name"]) for t in b.get("operationalTopics", [])]
        for b in brands
    }


def rows_from_series(
    brand_id: str,
    topic_id: str,
    topic_name: str,
    engine: str,
    entity_name: str,
    is_own_brand: bool,
    series: dict,
) -> list[dict]:
    timestamps = series.get("timestamps", [])

    def at(key: str, i: int):
        values = series.get(key, [])
        return values[i] if i < len(values) else None

    return [
        {
            "brand_id":        brand_id,
            "topic_id":        topic_id,
            "topic_name":      topic_name,
            "engine":          engine,
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


def main() -> None:
    client = bigquery.Client(project=GCP_PROJECT)
    topics_by_brand = get_topics_by_brand()
    iso_end = date.today().isoformat()

    total_calls = sum(len(topics) for topics in topics_by_brand.values()) * len(ENGINES)
    print(f"Chystám se udělat {total_calls} volání /v1/metrics/report...")

    rows: list[dict] = []
    done = 0
    for brand_id, topics in topics_by_brand.items():
        for topic_id, topic_name in topics:
            for engine in ENGINES:
                done += 1
                print(f"[{done}/{total_calls}] {brand_id} / {topic_name} / {engine}")
                try:
                    data = api_post("/v1/metrics/report", {
                        "brandId":       brand_id,
                        "aggregation":   "weekly",
                        "selectedTopic": topic_id,
                        "selectedEngine": engine,
                        "isoStartDate":  START_DATE,
                        "isoEndDate":    iso_end,
                    })
                except Exception as e:
                    print(f"    chyba: {e}")
                    continue

                d = data["data"]
                own = d["ownBrandMetrics"]
                rows += rows_from_series(
                    brand_id, topic_id, topic_name, engine, own["name"], True,
                    own["historicalData"]["weekly"],
                )

                comp_series = d["competitorTimeSeriesData"]["weekly"]
                comp_timestamps = comp_series.get("timestamps", [])
                for comp in comp_series.get("competitors", []):
                    rows += rows_from_series(
                        brand_id, topic_id, topic_name, engine, comp["name"], False,
                        {"timestamps": comp_timestamps, **comp["metrics"]},
                    )

                time.sleep(RATE_SLEEP)

    table = f"{GCP_PROJECT}.{BQ_DATASET}.topic_engine_metrics_history_test"
    print(f"Celkem {len(rows)} řádků, zapisuji do {table} ...")
    if not rows:
        print("Nic k zapsání.")
        return

    for row in rows:
        row["etl_loaded_at"] = date.today().isoformat()
    ndjson = "\n".join(json.dumps(r, ensure_ascii=False, default=str) for r in rows)
    cfg = bigquery.LoadJobConfig(
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
        autodetect=True,  # dočasná tabulka, autodetect stačí — nejde do schema_raw.sql
    )
    client.load_table_from_file(io.BytesIO(ndjson.encode()), table, job_config=cfg).result()
    print("Hotovo.")


if __name__ == "__main__":
    main()
