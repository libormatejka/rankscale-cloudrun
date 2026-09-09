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
| `/v1/metrics/citations` | POST | `extract_citations()` | *TODO* |

## Jak dokumentaci doplňovat

1. Request si spusť v Postmanu proti reálnému API.
2. Vlož sem (mně do chatu) skutečnou JSON odpověď — z ní vytáhnu strukturu,
   doplním byznysový popis a označím, která pole pipeline skutečně používá
   (cross-reference na `src/rankscale_extract_gcp.py`) a která ne.
3. Necitlivá data (příklad odpovědi v dokumentu) jsou v pořádku commitnout —
   pokud by šlo o něco citlivého (reálné API klíče, interní ID zákazníků),
   nahraď to placeholderem před vložením sem.
