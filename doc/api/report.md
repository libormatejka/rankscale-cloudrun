# `POST /v1/metrics/report`

## K čemu to je (byznysově)

Dashboard-level report pro brand: agregované metriky vlastního brandu i
konkurentů, **plus skutečná historická časová řada pro oba** (na rozdíl od
`search-terms-report`). Tohle je endpoint, na který oficiální dokumentace
`search-terms-report` odkazuje slovy *"Use /report for historical metrics
over an exact range."*

**Nepoužívá se v pipeline zatím vůbec** — dokumentuje se jako kandidát na
historický backfill vlastního brandu i konkurentů, viz zjištění níže.

## ✅ Potvrzeno: skutečná historie existuje pro vlastní brand i konkurenty

Ověřeno kompletní (needitovanou) odpovědí — `POST /v1/metrics/report` s
`{"aggregation": "weekly", "isoStartDate": "2026-06-01", "isoEndDate": "2026-08-31"}`
vrátil 14 týdenních bodů (2026-06-01 → 2026-08-31), s odlišnými hodnotami
metrik pro každý týden. Historie je na dvou různých místech v odpovědi:

- **`data.ownBrandMetrics.historicalData`** — historie **jen vlastního
  brandu**, navíc rozpadnutá i podle topicu (`topicMetricsData`) a enginu
  (`engineMetricsData`).
- **`data.competitorTimeSeriesData`** — historie **jen konkurentů**
  (vlastní brand tady není, viz gotcha níže), jeden objekt per konkurent
  s vlastním polem metrik zarovnaným na společné `timestamps[]`.

Obě jsou reálná časová řada, ne opakovaně stejná data — potvrzeno vizuální
kontrolou (`Air Bank.metrics.visibilityScore` = `[59.7, 54.7, 56.1, 50.8,
37.3, ...]`, 14 odlišných hodnot pro 14 týdnů).

**Důsledek pro backfill:** `/report` dá historii pro **oba** — vlastní brand
i konkurenty — na rozdíl od dřívějšího (chybného) závěru v tomhle dokumentu.
Je to ale jiná granularita, než jakou má dnes `raw_brand_snapshots`: `/report`
agreguje **na úrovni celého brandu za týden** (přes všechny search termy
dohromady), ne po jednotlivých search termech. Nešlo by o 1:1 náhradu
backfillu `brand_snapshots` beze změny schématu — potřebovala by se nová
tabulka s jinou granularitou (brand × týden × konkurent, ne brand × search
term × konkurent).

## Request

```
POST https://rankscale.ai/v1/metrics/report
Authorization: Bearer <RANKSCALE_API_KEY>
Content-Type: application/json
```

Tělo použité při testu:
```json
{
  "brandId": "...",
  "timeFrame": "30d",
  "aggregation": "weekly",
  "periodOffset": 0,
  "selectedTopic": "all",
  "selectedTags": "all",
  "selectedEngine": "all",
  "selectedQuery": "all",
  "isoStartDate": "2026-06-01",
  "isoEndDate": "2026-08-31"
}
```

### Parametry podle oficiální dokumentace

| Parametr | Typ | Poznámka |
|---|---|---|
| `brandId` | string, povinné | — |
| `aggregation` | enum `hourly`/`daily`/`weekly`/`monthly` | velikost bucketu v `historicalData`/`topicMetricsData`/`engineMetricsData`/`competitorTimeSeriesData` — **není** to trailing-average okno (to je `timeFrame`) |
| `timeFrame` | enum `24h`/`7d`/`30d`/`3m`/`1y` | vybírá snapshot pro `ownBrandMetrics`/`competitorMetrics` (aktuální/"latest" hodnoty), stejné one-window-down mapování jako jinde; default `7d` |
| `isoStartDate`/`isoEndDate` | string (ISO datum) | vybírá execution window pro historii — **potvrzeno funkční**: s `aggregation=weekly` a tříměsíčním rozsahem vrátilo přesně 14 týdenních bodů pokrývajících celé okno |
| `showLastRunMetrics` | boolean | `false` (default) = trailing average dle `timeFrame`; `true` = "Raw latest" (`lastRunMetrics`), ignoruje to mapování |
| `periodOffset` | integer | — |
| `includeNotFoundExecutions` | boolean | — |
| `selectedTopic`, `selectedEngine`, `selectedQuery`, `selectedTags` | string/`all`/pole hodnot | stejný vzor jako `search-terms-report` |
| `searchTermId` | string | filtr na konkrétní term |
| `userTimezone` | string | — |
| `X-Request-Id` | header, volitelný | — |

Pozn.: dokumentace explicitně upozorňuje, že `topicId`/`aiSearchEngines`
**nejsou** platná pole requestu — je to `selectedTopic`/`selectedEngine`.
Neznámá pole v těle se ignorují a nahlásí přes `warnings[]`.

## Response

Ověřeno kompletní (needitovanou) reálnou odpovědí. Obálka — **potvrzeno, tři
top-level klíče, žádné další**:

```json
{
  "success": true,
  "data": {
    "ownBrandMetrics": { "...": "aktuální agregáty + historie vlastního brandu" },
    "competitorMetrics": [ "...": "plochý seznam AKTUÁLNÍCH hodnot, own brand + konkurenti, bez historie" ],
    "competitorTimeSeriesData": { "...": "historie KONKURENTŮ (bez vlastního brandu)" }
  }
}
```

### `data.ownBrandMetrics` — aktuální agregáty + historie vlastního brandu

```json
{
  "name": "Česká spořitelna (Test)",
  "aliases": ["Spořka", "Česká spořitelna"],
  "visibilityScore": 54.6,
  "sentiment": 63.5,
  "mentions": 2224,
  "sources": 109,
  "citations": 2097,
  "avgPosition": 3.3,
  "detectionRate": 65.8,
  "top3": 38.7,
  "validMetricsCount": 14,
  "executionsAnalyzed": 3644,
  "trends": { "visibilityScore": 1.5, "sentiment": -2.3, "...": "delta vs. předchozí perioda" },
  "historicalData": {
    "hourly": { "...": "prázdné pole u každé metriky (nepoužito při aggregation=weekly)" },
    "daily": { "...": "prázdné (nepoužito při aggregation=weekly)" },
    "weekly": {
      "visibilityScore": [59, 55.7, 52.9, 53.8, 44.8, 48.8, 51.6, 48.1, 51.5, 55.8, 56.2, 55.4, 53.1, 54.6],
      "sentiment": [70.4, 69.9, 69.1, "...": "14 hodnot"],
      "mentions": ["...": "14 hodnot"],
      "sources": ["...": "14 hodnot"],
      "citations": ["...": "14 hodnot"],
      "citationCounts": ["...": "duplicitní s citations"],
      "avgPosition": ["...": "14 hodnot"],
      "detectionRate": ["...": "14 hodnot"],
      "top3": ["...": "14 hodnot"],
      "executionsAnalyzed": ["...": "14 hodnot"],
      "timestamps": ["2026-06-01T00:00:00.000Z", "2026-06-08T00:00:00.000Z", "...": "14 týdenních dat, po sobě jdoucích, bez děr"],
      "brandNotFound": [true, true, "...": "14× true — viz gotcha níže"]
    },
    "monthly": { "...": "prázdné (nepoužito při aggregation=weekly)" }
  },
  "topicMetricsData": {
    "hourly": [], "daily": [],
    "weekly": [
      { "topicId": "ZFyMrgG0cuuEAvCdf1nr", "topicName": "Brand", "visibilityScore": ["...": "14 hodnot"], "timestamps": ["...": "14 hodnot"], "...": "stejná sada metrik jako historicalData" },
      { "topicId": "aVaq5pTG3Io4GR853gAf", "topicName": "Půjčky/Úvěry", "...": "...", "timestamps": ["...": "jen 11 hodnot — topic nemá data pro všechny týdny okna" } ],
    "monthly": []
  },
  "engineMetricsData": {
    "hourly": [], "daily": [],
    "weekly": [ { "engineId": "chatgpt_gui", "engineName": "chatgpt_gui", "visibilityScore": ["...": "N hodnot"], "timestamps": ["...": "N hodnot"] }, "...": "jeden objekt per engine (chatgpt_gui, google_ai_overview, google_ai_mode_gui, perplexity_gui, bing_copilot_gui, google_gemini_gui pozorováno)" ],
    "monthly": []
  },
  "preselectionWhitelist": ["Air Bank", "Komercni banka", "..."],
  "preselectionBlacklist": ["Usetreno.cz", "Banky.cz", "...": "197 položek v ukázce"],
  "manualWhitelist": [],
  "manualBlacklist": []
}
```

Zajímavá pole:
- **`aliases`** — odpovídá `brandInfo.names` z `/v1/metrics/brands` (viz
  [brands.md](brands.md)).
- **`preselectionWhitelist`/`preselectionBlacklist`** — interní seznam jmen,
  která Rankscale automaticky rozpoznává/ignoruje jako konkurenty
  (`preselectionBlacklist` obsahuje zjevné false-positives typu "Visa",
  "Mastercard", generické banky — vysvětluje, proč se v `competitors[]` u
  `search-terms-report` neobjevují irelevantní entity).
- **`topicMetricsData`/`engineMetricsData`** mají **kratší `timestamps[]`**
  než hlavní `historicalData`, pokud daný topic/engine neměl data po celé
  požadované okno (pozorováno u topicu "Půjčky/Úvěry": 11 bodů místo 14) —
  při parsování **nelze spoléhat na to, že všechny časové řady mají stejnou
  délku/zarovnání**, je nutné párovat podle vlastního `timestamps[]`, ne podle
  indexu napříč různými topic/engine objekty.
- **`brandNotFound: [true, true, ...]`** u historie, přestože `visibilityScore`
  má nenulové hodnoty — nevysvětleno, netestováno dál. Flag pro budoucí
  zkoumání, než se na tomhle poli něco postaví.

### `data.competitorMetrics[]` — aktuální hodnoty, own brand + konkurenti, bez historie

```json
{
  "name": "CSOB",
  "isOwnBrand": false,
  "latestValue": 39.2,
  "trend": -0.7,
  "variations": ["ČSOB", "ČSOB / Era", "...": "20 variant v ukázce"],
  "visibilityScore": 39.2,
  "latestRank": 7,
  "benchmarkAvgPosition": 3.9,
  "avgRank": 4.1,
  "avgSentiment": 62.3,
  "appearances": 1740,
  "citationCount": 1385,
  "detectionRate": 49.6,
  "top3": 22.4,
  "validMetricsCount": 225,
  "benchmarkObservationCount": 14
}
```

Vlastní brand je v tomhle poli taky (`isOwnBrand: true`), se stejnou sadou
polí jako konkurenti — je to jediné místo v odpovědi, kde jsou vlastní brand
i konkurenti pohromadě ve stejném formátu (ale bez historie).

### `data.competitorTimeSeriesData` — historie KONKURENTŮ (bez vlastního brandu)

```json
{
  "hourly": { "timestamps": [], "competitors": [] },
  "daily": { "timestamps": [], "competitors": [] },
  "weekly": {
    "timestamps": ["2026-06-01T00:00:00.000Z", "2026-06-08T00:00:00.000Z", "...": "14 hodnot, stejné jako v ownBrandMetrics.historicalData.weekly.timestamps"],
    "competitors": [
      {
        "name": "Air Bank",
        "isOwnBrand": false,
        "variations": ["Air Bank", "Air Bank (Účet pro mladé)", "..."],
        "metrics": {
          "visibilityScore": [59.7, 54.7, 56.1, 50.8, 37.3, 45.6, 48.2, 52.5, 52.9, 49.2, 50.6, 56.1, 52.6, 53],
          "sentiment": [74.7, 75.5, "...": "14 hodnot"],
          "avgPosition": ["...": "14 hodnot"],
          "detectionRate": ["...": "14 hodnot"],
          "top3": ["...": "14 hodnot"],
          "mentions": ["...": "14 hodnot"],
          "citations": ["...": "14 hodnot"]
        }
      }
    ]
  },
  "monthly": { "timestamps": [], "competitors": [] }
}
```

V testu (`aggregation: weekly`) mělo `weekly.competitors[]` **21 položek** —
20 pojmenovaných konkurentů + jedna speciální položka **`"name": "Others"`**
(souhrn menších/nesledovaných konkurentů do jedné bucket entity). Všechny
mají `isOwnBrand: false` — **vlastní brand v tomhle poli není vůbec**, jeho
historie je jen v `ownBrandMetrics.historicalData`.

`metrics` u konkurenta má **užší sadu polí** než `ownBrandMetrics.historicalData`
— chybí `sources`, `citationCounts`, `executionsAnalyzed`, `brandNotFound`
(jsou jen `visibilityScore`, `sentiment`, `avgPosition`, `detectionRate`,
`top3`, `mentions`, `citations`).

## Pole, která pipeline nečte

Celý endpoint se v pipeline zatím nepoužívá — nic z něj se nezapisuje nikam.

## Známé chování / gotchas

- **Historie existuje pro vlastní brand (`ownBrandMetrics.historicalData`) i
  konkurenty (`competitorTimeSeriesData`), ale ve dvou různých strukturách a
  vlastní brand není v `competitorTimeSeriesData` — je nutné je sloučit ručně,
  párováno podle `name`/`isOwnBrand`, ne podle společného pole.**
- Granularita je **brand-level týdenní agregát přes všechny search termy**,
  ne per-search-term jako dnešní `raw_brand_snapshots` — přímá náhrada
  backfillu bez změny schématu není možná.
- `topicMetricsData`/`engineMetricsData` mohou mít kratší `timestamps[]` než
  hlavní `historicalData` — nespoléhat na shodnou délku/indexové zarovnání
  napříč různými poli, vždy párovat podle vlastního `timestamps[]`.
- `"Others"` entita v `competitorTimeSeriesData.weekly.competitors[]` —
  souhrn nesledovaných/menších konkurentů, ne konkrétní brand.
- `brandNotFound` pole neodpovídá intuitivně nenulovým metrikám ve stejném
  bodě — nevysvětleno, netestováno dál.
- `aggregation=weekly` s tříměsíčním (`isoStartDate`–`isoEndDate`) oknem dalo
  čistě 14 po sobě jdoucích týdenních bodů bez děr a bez duplicit — na rozdíl
  od dřívějšího nejasného pozorování s `aggregation=daily`, které vracelo jen
  4 body týden od sebe (možná byl použitý kratší `isoStartDate`–`isoEndDate`
  rozsah, nebylo přímo srovnáno).

## Další kroky, než se implementuje backfill

1. Rozhodnout o nové tabulce pro brand-level týdenní historii (jiná
   granularita než `raw_brand_snapshots`) — návrh schématu + skript zatím
   neřešeno, čeká na rozhodnutí.
2. Ošetřit v parsování nerovnoměrnou délku `timestamps[]` napříč
   `historicalData`/`topicMetricsData`/`engineMetricsData`/`competitorTimeSeriesData`.
3. Vyjasnit `brandNotFound` sémantiku, než se na ní něco postaví.
