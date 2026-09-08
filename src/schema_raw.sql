-- ============================================================
-- Rankscale → BigQuery  |  Raw tabulky (L0 landing zone)
-- Dataset:  RankScaleDashboard
-- ============================================================
-- Varianta ../sql/extract1/schema_raw.sql bez natvrdo zapsaného
-- project ID — použije se ten, který zadáš přes `bq query --project_id=...`
-- (viz krok 3b v README.md). Díky tomu jde tenhle soubor beze změny
-- spustit na libovolném GCP projektu, kam Cloud Run Job zapisuje.
--
-- Odděleno od produkční pipeline (GitHub Actions), která píše do
-- libor-matejkacz.RankScaleDashboard — viz ../sql/extract1/schema_raw.sql.
--
-- Tabulky jsou plněny scriptem rankscale_extract_gcp.py.
-- Žádná transformační logika — data jsou 1:1 z Rankscale API.
-- Každý run APPENDuje nové řádky — historická data zůstávají.
-- ============================================================


-- ------------------------------------------------------------
-- raw_brands
-- Zdroj: GET /v1/metrics/brands
-- Jeden řádek per brand per ETL run.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `RankScaleDashboard.raw_brands`
(
  brand_id      STRING,
  name          STRING,
  domain        STRING,
  is_own_brand  BOOL,
  etl_loaded_at TIMESTAMP
);


-- ------------------------------------------------------------
-- raw_search_terms
-- Zdroj: GET /v1/metrics/search-terms
-- Jeden řádek per search term (prompt × engine) per brand per ETL run.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `RankScaleDashboard.raw_search_terms`
(
  brand_id            STRING,
  search_term_id      STRING,
  query               STRING,
  engine              STRING,
  topic_id            STRING,
  topic_name          STRING,
  region              STRING,
  `interval`          STRING,
  tags                STRING,    -- JSON string, např. '["dip","investice"]'
  status              STRING,    -- "active" | "inactive"
  created_at          TIMESTAMP,
  last_execution_time TIMESTAMP,
  next_execution_time TIMESTAMP,
  executions_amount   INT64,
  etl_loaded_at       TIMESTAMP
);


-- ------------------------------------------------------------
-- raw_brand_snapshots
-- Zdroj: POST /v1/metrics/search-terms-report
-- Jeden řádek per brand (vlastní i competitor) per search term per ETL run.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `RankScaleDashboard.raw_brand_snapshots`
(
  brand_id          STRING,
  search_term_id    STRING,
  engine            STRING,
  topic_id          STRING,
  topic_name        STRING,
  last_snapshot_at  TIMESTAMP,
  brand_name        STRING,
  is_own_brand      BOOL,
  visibility_score  FLOAT64,
  avg_sentiment     FLOAT64,
  avg_rank          FLOAT64,
  latest_rank       INT64,
  detection_rate    FLOAT64,
  top3_rate         FLOAT64,
  citation_count    INT64,
  appearances       INT64,
  etl_loaded_at     TIMESTAMP
);


-- ------------------------------------------------------------
-- raw_answer_texts
-- Zdroj: POST /v1/metrics/search-terms-report (includeAnswerTexts: true)
-- Jeden řádek per AI exekuce per brand per ETL run.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `RankScaleDashboard.raw_answer_texts`
(
  brand_id       STRING,
  search_term_id STRING,
  execution_id   STRING,
  executed_at    TIMESTAMP,
  engine         STRING,
  answer_text    STRING,
  etl_loaded_at  TIMESTAMP
);

-- ------------------------------------------------------------
-- raw_citations
-- Zdroj: POST /v1/metrics/citations → domainSummary.topDomainsByQuery
-- Jeden řádek per URL × engine × query per brand per ETL run.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `RankScaleDashboard.raw_citations`
(
  brand_id       STRING,
  search_term_id STRING,
  query          STRING,
  engine         STRING,
  domain         STRING,
  url            STRING,
  occurrences    INT64,
  etl_loaded_at  TIMESTAMP
);
