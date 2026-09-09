# `POST /v1/metrics/citations`

## K čemu to je (byznysově)

Vrátí přehled **citací** — kde (jaké domény/URL) se AI vyhledávače odkazují,
když odpovídají na sledované dotazy, a jak se to rozpadá podle brandu,
enginu, kategorie webu. Podstatně bohatší endpoint, než kolik z něj pipeline
dnes využívá.

## Použití v kódu

`extract_citations()` v `src/rankscale_extract_gcp.py` — volané jednou na
brand, s pevným `timeFrame: "7d"` (bez `isoStartDate`/`isoEndDate`). Čte jen
`data.domainSummary.topDomainsByQuery[]`, zbytek odpovědi se zahazuje.

## Request

```
POST https://rankscale.ai/v1/metrics/citations
Authorization: Bearer <RANKSCALE_API_KEY>
Content-Type: application/json
```

Tělo podle kódu:
```json
{ "brandId": "...", "timeFrame": "7d" }
```

## Response

Ověřeno kompletní reálnou odpovědí (Postman, brand se 508 doménami/1711
citacemi). Obálka — **výrazně bohatší, než co kód čte**:

```json
{
  "success": true,
  "data": {
    "totalCitations": 6434,
    "uniqueCitations": 1711,
    "uniqueDomains": 508,
    "totalBrands": 23,
    "timestampFormat": "daily",
    "paginationInfo": { "...": "viz níže — DŮLEŽITÉ, kód to nekontroluje" },
    "dashboardSummary": { "...": "agregáty pro dashboard, viz níže" },
    "freshness": {
      "dataUpdatedThrough": "2026-09-08T06:36:14.782Z",
      "generatedAt": "2026-09-09T21:25:52.347Z",
      "source": "computed",
      "isLastGood": false,
      "refreshQueued": false
    },
    "domainSummary": { "...": "tohle kód částečně čte, viz níže" },
    "citationsByDomain": [ "...": "NEJPODROBNĚJŠÍ struktura, kód ji nepoužívá vůbec — viz níže, obsahuje historii" ]
  }
}
```

### ⚠️ `data.paginationInfo` — endpoint výsledky ořezává, kód to nekontroluje

```json
{
  "hasMore": false,
  "totalCount": 1711,
  "returnedCount": 1711,
  "citationsCapped": false,
  "brandsCapped": false,
  "capBypassed": false,
  "maxCitations": 5000,
  "maxBrands": 50,
  "responseTrimmed": false,
  "returnedDomainCount": 508,
  "totalDomainCount": 508
}
```

V téhle ukázce nic ořezané nebylo (`*Capped: false`, `returnedCount ==
totalCount`), ale endpoint má tvrdý strop (`maxCitations: 5000`, `maxBrands:
50`) — u brandu s víc citacemi/konkurenty by `citationsCapped`/`brandsCapped`
mohlo být `true` a `extract_citations()` by o tom **vůbec nevěděl**, protože
tahle pole vůbec nečte. Riziko: tichá ztráta dat u brandů s velkým objemem
citací, bez jakéhokoliv varování v logu.

### `data.dashboardSummary` — hotové agregáty pro dashboard (kód nečte)

```json
{
  "topDomains": [ { "url": "csas.cz/cs/osobni-finance/pujcky/pujcka", "occurrences": 66, "trend": 20 } ],
  "topDomainsByHost": [ { "domain": "moneta.cz", "occurrences": 432, "uniqueUrls": 72, "trend": 30 } ],
  "byEngine": [ { "engineName": "Perplexity GUI", "count": 2193 } ],
  "overTime": [ { "date": "2026-08-11", "count": 1180 } ],
  "brandShare": [ { "brand": "Ceska sporitelna (Test)", "occurrences": 795, "isOwnBrand": true } ],
  "byCategory": [ { "category": "comparison_portal", "count": 1752 } ],
  "topQueries": [ { "query": "Které banky umožňují refinancování hypotéky?", "engine": "Multiple", "occurrences": 183 } ],
  "coverage": { "returnedCitations": 1711, "totalCitations": 1711, "uniqueDomains": 508, "totalBrands": 23, "citationsCapped": false, "brandsCapped": false },
  "byRegion": [ { "region": "cz", "count": 6370 } ],
  "isTruncated": false,
  "totalUniqueUrls": 1711
}
```

`overTime[]` je zajímavé — časová řada výskytů podle data, hotová k použití
bez dalšího zpracování, kdyby bylo potřeba jen celkový trend citací v čase.

### `data.domainSummary` — tohle kód částečně čte

```json
{
  "topDomainsOverall": [ { "domain": "moneta.cz", "occurrences": 432, "urls": [ { "url": "https://www.moneta.cz/...", "occurrences": 64 } ] } ],
  "topDomainsByEngine": [ { "engineId": "google_ai_overview", "domains": [ { "domain": "moneta.cz", "occurrences": 155, "urls": [...] } ] } ],
  "topDomainsByQuery": [
    {
      "query": "Jaké banky umožňují předčasné splacení půjčky zdarma?",
      "searchTermIds": ["...": "4 hodnoty"],
      "engines": [ { "engineId": "google_ai_overview", "domains": [ { "domain": "...", "occurrences": 12, "urls": [...] } ] } ]
    }
  ],
  "topDomainsByOwnBrandCitations": [ { "domain": "csas.cz", "occurrences": 176, "urls": [...] } ],
  "topDomainsByCompetitor": [ { "brandName": "Air Bank", "occurrences": 693, "domains": [...] } ]
}
```

**Kód čte jen `topDomainsByQuery[]`** (viz `extract_citations()` — `query`,
`searchTermIds[0]`, `engines[].engineId`, `domains[].domain`/`occurrences`/
`urls[].url`/`occurrences`) → `raw_citations`. `topDomainsOverall`,
`topDomainsByEngine`, `topDomainsByOwnBrandCitations`, `topDomainsByCompetitor`
se dnes nikde nevyužívají.

### ✅ `data.citationsByDomain[]` — nejpodrobnější struktura, obsahuje HISTORII

Tohle kód vůbec nepoužívá, a je to potenciálně řešení pro historii citací
(dřívější poznámka v kódu "API nepodporuje historický backfill" u citací se
týkala `isoStartDate`/`isoEndDate` na tomhle endpointu — ale historie je tu
jinou cestou, v jediném volání bez potřeby cokoliv iterovat):

```json
{
  "domain": "moneta.cz",
  "occurrences": 432,
  "citations": [
    {
      "url": "https://www.moneta.cz/pujcky-a-uvery/pujcka-na-cokoliv",
      "normalizedUrl": "moneta.cz/pujcky-a-uvery/pujcka-na-cokoliv",
      "occurrences": 64,
      "engineAppearances": { "google_ai_overview": 22, "google_ai_mode_gui": 16, "perplexity_gui": 14, "google_gemini_gui": 12 },
      "firstSeenAt": "2026-08-11T06:11:17.913Z",
      "lastSeenAt": "2026-09-08T06:25:06.004Z",
      "category": "product",
      "searchTerms": [ { "id": "8kEO9NhX7C3nAkptz3dp", "query": "Která banka nabízí půjčky?", "engine": "perplexity_gui", "region": "cz", "urlOccurrences": 5 } ],
      "brands": [ { "brandName": "Moneta Money Bank", "occurrences": 39, "isOwnBrand": false, "firstSeenAt": "...", "lastSeenAt": "..." } ],
      "counts": {
        "2026-08-18": 15,
        "2026-08-11": 15,
        "2026-09-01": 11,
        "2026-09-08": 10,
        "2026-08-25": 13
      },
      "countsGranularity": "daily"
    }
  ]
}
```

`counts` je per-URL časová řada výskytů — **v téhle ukázce 5 bodů, přesně
týden od sebe** (2026-08-11 → 2026-09-08), i když se požadavek posílá s
`timeFrame: "7d"` bez jakéhokoliv `isoStartDate`/`isoEndDate`. Tzn. **historie
tu je automaticky, bez ohledu na `timeFrame`**, jen je potřeba ji přečíst
místo ignorovat.

## Pole, která pipeline nečte

Prakticky celá odpověď kromě `domainSummary.topDomainsByQuery[]` — viz výše.
Zejména `paginationInfo` (riziko tiché ztráty dat) a `citationsByDomain[]`
(potenciální zdroj historie) stojí za doplnění.

## Známé chování / gotchas

- **`countsGranularity: "daily"` neznamená denní krok dat** — v ukázce jsou
  body přesně týden od sebe, stejný vzorec jako u `/v1/metrics/report`
  (`aggregation: "daily"` → týdenní rozestupy). Zdá se, že "daily" label
  obecně u týhle API neznamená "jeden bod za den", spíš vázáno na skutečnou
  četnost běhů search termů (`interval: weekly`, viz
  [search-terms.md](search-terms.md)).
- **Endpoint ořezává výsledky** (`maxCitations: 5000`, `maxBrands: 50`) —
  kód tohle nekontroluje ani neloguje. Než se na `raw_citations` něco staví
  dál, stálo by za to přidat kontrolu `citationsCapped`/`brandsCapped` a
  varovat, pokud je `true`.
- `topDomainsByQuery[].searchTermIds` je pole (víc termů může vést na
  stejnou doménu/URL) — kód bere jen `[0]` (`(term_entry.get("searchTermIds")
  or [None])[0]`), takže u víceznačných záznamů ztrácí vazbu na ostatní
  termy. Stejný vzorec jako `aiSearchEngines[0]` u jiných endpointů.

## Další kroky

1. Zvážit čtení `citationsByDomain[].citations[].counts` jako zdroj historie
   citací místo současného přístupu (žádná historie, jen aktuální
   `topDomainsByQuery` snapshot).
2. Přidat kontrolu `paginationInfo.citationsCapped`/`brandsCapped` a
   zalogovat/alertovat, pokud je `true` — tichá ztráta dat jinak není nijak
   vidět.
