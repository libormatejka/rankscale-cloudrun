# Rankscale API — dokumentace endpointů

Neoficiální dokumentace endpointů Rankscale Metrics API, jak je používá tenhle
repozitář (`src/rankscale_extract_gcp.py`). Mapováno ručně přes Postman proti
skutečným odpovědím — ne přepis oficiální Rankscale dokumentace (tu nemáme).

Cíl: u každého endpointu mít strukturu requestu/response a byznysový popis, aby
šlo změny v API (nová pole, změněné chování) rozpoznat a promítnout do kódu
vědomě, ne až omylem při ladění produkčního problému (viz zjištění u
`search-terms-report`, že `isoStartDate`/`isoEndDate` ve skutečnosti historii
nefiltruje — [search-terms-report.md](search-terms-report.md)).

## Endpointy

| Endpoint | Metoda | Použití v pipeline | Dokumentace |
|---|---|---|---|
| `/v1/metrics/brands` | GET | `extract_brands()` | [brands.md](brands.md) |
| `/v1/metrics/search-terms` | GET | `extract_search_terms()` | [search-terms.md](search-terms.md) |
| `/v1/metrics/search-terms-report` | POST | `extract_snapshots_and_texts()` | [search-terms-report.md](search-terms-report.md) — ⚠️ nevrací historii metrik, viz dokument |
| `/v1/metrics/citations` | POST | `extract_citations()` | [citations.md](citations.md) — ⚠️ kód čte jen zlomek odpovědi, chybí kontrola limitů (`paginationInfo`) a historie (`citationsByDomain[].citations[].counts`) se nevyužívá |
| `/v1/metrics/report` | POST | **nepoužívá se** — kandidát na historický backfill vlastního brandu i konkurentů | [report.md](report.md) — ✅ historie existuje pro oba (`ownBrandMetrics.historicalData` + `competitorTimeSeriesData`), jiná granularita než `raw_brand_snapshots` |
| `/v1/metrics/topics` (nepotvrzeno) | GET (odhad) | **nepoužívá se** | [topics.md](topics.md) — metoda/cesta nepotvrzená, jen odvozená z odpovědi |
| `/v1/metrics/sentiment` (nepotvrzeno) | POST (odhad) | **nepoužívá se** | [sentiment.md](sentiment.md) — keyword-level sentiment detail, objemná odpověď, metoda nepotvrzená |

## Syrové odpovědi (fixtures)

`full-responses/` obsahuje kompletní reálné JSON odpovědi, ze kterých byla
dokumentace psaná (`brands.json`, `search-terms.json`, `search-terms-report.json`,
`metrics-report.json`, `citations.json`, `sentiment.json`, `topics.json`) —
pro dohledání detailu, který se do `.md` souborů nevešel, nebo pro budoucí
ověření, že se chování API nezměnilo.

## Jak dokumentaci doplňovat

1. Request si spusť v Postmanu proti reálnému API.
2. Vlož sem (mně do chatu) skutečnou JSON odpověď — z ní vytáhnu strukturu,
   doplním byznysový popis a označím, která pole pipeline skutečně používá
   (cross-reference na `src/rankscale_extract_gcp.py`) a která ne.
3. Necitlivá data (příklad odpovědi v dokumentu) jsou v pořádku commitnout —
   pokud by šlo o něco citlivého (reálné API klíče, interní ID zákazníků),
   nahraď to placeholderem před vložením sem.
