-- ============================================================
-- Rankscale → BigQuery  |  Tabulky
-- Dataset:  RankScaleDashboard
-- ============================================================
-- Bez natvrdo zapsaného project ID — použije se ten, který zadáš přes
-- `bq query --project_id=...` (viz krok 3b v README.md). Díky tomu jde
-- tenhle soubor beze změny spustit na libovolném GCP projektu, kam
-- Cloud Run Job zapisuje.
--
-- Tabulky jsou plněny scriptem rankscale_extract_gcp.py.
-- ============================================================


-- ------------------------------------------------------------
-- etl_runs
-- Metadata tabulka — jeden řádek per spuštění rankscale_extract_gcp.py,
-- ať už úspěšné nebo neúspěšné.
-- Zapisuje se přes log_run() v skriptu, vždy na konci běhu.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `RankScaleDashboard.etl_runs`
(
  run_id         STRING,     -- UUID, unikátní per spuštění
  started_at     TIMESTAMP,
  finished_at    TIMESTAMP,
  mode           STRING,     -- "daily" | "backfill"
  status         STRING,     -- "success" | "failed"
  brands_total   INT64,
  brands_failed  INT64,
  rows_written   INT64,      -- součet přes všechny tabulky v tomto běhu
  error_message  STRING,     -- NULL pokud status = "success"
  etl_loaded_at  TIMESTAMP
);


-- ------------------------------------------------------------
-- topic_metrics_history
-- Týdenní historie visibility/sentiment (+ pár dalších metrik) pro vlastní
-- brand i konkurenty, rozdělená po topicu.
-- Zdroj: POST /v1/metrics/report, volané zvlášť pro každou (brand, topic)
-- dvojici se selectedTopic=<topic_id> a aggregation=weekly — viz
-- doc/api/report.md.
-- Na rozdíl od "append-only" konvence se PŘEPISUJE CELÁ (TRUNCATE) při
-- každém běhu extract_topic_metrics_history() — API vrací pokaždé
-- kompletní okno historie znovu, ne jen nová data, takže append by jen
-- duplikoval týdny.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `RankScaleDashboard.topic_metrics_history`
(
  brand_id         STRING,
  topic_id         STRING,
  topic_name       STRING,
  entity_name      STRING,   -- jméno vlastního brandu nebo konkurenta (vč. "Others")
  is_own_brand     BOOL,
  week_start       TIMESTAMP,
  visibility_score FLOAT64,
  sentiment        FLOAT64,
  avg_position     FLOAT64,
  detection_rate   FLOAT64,
  top3             FLOAT64,
  mentions         INT64,
  citations        INT64,
  etl_loaded_at    TIMESTAMP
);
