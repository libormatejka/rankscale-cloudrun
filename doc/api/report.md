# `POST /v1/metrics/report`

## K čemu to je (byznysově)

Dashboard-level report pro brand: agregované metriky vlastního brandu i
konkurentů, **plus skutečná historická časová řada** (na rozdíl od
`search-terms-report`). Tohle je endpoint, na který official dokumentace
`search-terms-report` odkazuje slovy *"Use /report for historical metrics
over an exact range."*

**Nepoužívá se v pipeline zatím vůbec** — dokumentuje se jako kandidát na
náhradu/doplnění backfillu, viz zjištění níže.

## ✅ Potvrzeno: skutečná historie existuje — ale jen pro vlastní brand

V `data.ownBrandMetrics.historicalData.daily` jsou reálně odlišné hodnoty pro
různá `timestamps`:

| timestamp | visibilityScore | sentiment | mentions |
|---|---|---|---|
| 2026-04-07 | 58.5 | 63 | 180 |
| 2026-04-14 | 56.5 | 63 | 176 |
| 2026-04-21 | 61.3 | 62.7 | 182 |
| 2026-04-28 | 58.9 | 64.3 | 176 |

To je jednoznačně **jiné chování** než `search-terms-report`, kde bylo pro
libovolné požadované okno vráceno identické `lastSnapshotAt`/metriky. `/report`
tedy skutečně agreguje přes čas, ne jen vrací poslední snapshot.

**Ale:** `historicalData`/`topicMetricsData`/`engineMetricsData` (všechny s
časovou řadou) jsou **jen uvnitř `ownBrandMetrics`**. `competitorMetrics[]`
(vlastní brand + konkurenti) je **plochý seznam bez historie** — má jen
`latestValue`, `trend` (delta vs. předchozí perioda) a agregáty
(`avgRank`, `avgSentiment`, `appearances`...), žádné `timestamps[]`.

**Důsledek pro backfill:** i `/report` by dal jen historii **vlastního**
brandu (`ownBrandMetrics.historicalData`), ne historii konkurentů. Struktura
`raw_brand_snapshots` dnes ukládá oba (own i competitors) na úrovni
jednotlivého search termu — tohle je hrubší agregace (celý brand, ne po
termech) a bez konkurenční historie. Nejde o 1:1 náhradu backfillu
`brand_snapshots`, spíš o jiný typ dat (brand-level trend, ne
term-level snapshoty).

## Request

```
POST https://rankscale.ai/v1/metrics/report
Authorization: Bearer <RANKSCALE_API_KEY>
Content-Type: application/json
```

### Parametry podle oficiální dokumentace

| Parametr | Typ | Poznámka |
|---|---|---|
| `brandId` | string, povinné | — |
| `aggregation` | enum `hourly`/`daily`/`weekly`/`monthly` | velikost bucketu v `historicalData`/`topicMetricsData`/`engineMetricsData` — **není** to trailing-average okno (to je `timeFrame`) |
| `timeFrame` | enum `24h`/`7d`/`30d`/`3m`/`1y` | vybírá snapshot pro `ownBrandMetrics`/`competitorMetrics` (aktuální hodnoty), stejné one-window-down mapování jako jinde (24h/7d→snapshotH24, 30d→snapshotD7, 3m/1y→snapshotD30); default `7d` |
| `isoStartDate`/`isoEndDate` | string (ISO datum) | vybírá execution window pro historii — **na rozdíl od `search-terms-report` tady skutečně ovlivňuje vrácená historická data** (nepotvrzeno druhým testem s explicitním rozsahem, ale `historicalData` v ukázce jasně obsahuje víc period) |
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

Ověřeno reálným voláním (Postman). Response je výrazně bohatší než u
ostatních endpointů — obálka:

```json
{
  "success": true,
  "data": {
    "ownBrandMetrics": { /* ... */ },
    "competitorMetrics": [ /* ... */ ]
    /* pravděpodobně další top-level klíče (topicMetrics/engineMetrics
       samostatně od ownBrandMetrics?) — odpověď byla oříznutá, TODO ověřit */
  }
}
```

### `data.ownBrandMetrics`

Aktuální agregáty + historie:

```json
{
  "name": "Česká spořitelna (Test)",
  "aliases": ["Spořka", "Česká spořitelna"],
  "visibilityScore": 58.9,
  "sentiment": 64.3,
  "mentions": 714,
  "sources": 122,
  "citations": 470,
  "avgPosition": 3.4,
  "detectionRate": 70.7,
  "top3": 41.4,
  "validMetricsCount": 4,
  "executionsAnalyzed": 999,
  "trends": { "visibilityScore": -2.4, "sentiment": 1.6, "...": "delta vs. předchozí perioda" },
  "historicalData": {
    "hourly": { "...": "prázdné pole u každé metriky, nepoužito v ukázce" },
    "daily": {
      "visibilityScore": [58.5, 56.5, 61.3, 58.9],
      "timestamps": ["2026-04-07T00:00:00.000Z", "2026-04-14T00:00:00.000Z", "2026-04-21T00:00:00.000Z", "2026-04-28T00:00:00.000Z"],
      "brandNotFound": [true, true, true, true]
    },
    "weekly": { "...": "prázdné v ukázce" },
    "monthly": { "...": "prázdné v ukázce" }
  },
  "topicMetricsData": { "daily": [ { "topicId": "...", "topicName": "...", "visibilityScore": [...], "timestamps": [...] } ] },
  "engineMetricsData": { "daily": [ { "engineId": "...", "engineName": "...", "visibilityScore": [...], "timestamps": [...] } ] },
  "preselectionWhitelist": ["Air Bank", "Raiffeisenbank", "..."],
  "preselectionBlacklist": ["CS", "Cofidis", "..."],
  "manualWhitelist": [],
  "manualBlacklist": []
}
```

Zajímavá pole:
- **`aliases`** — přímo odpovídá `brandInfo.names` z `/v1/metrics/brands`
  (viz [brands.md](brands.md)), jen bez samotného hlavního jména.
- **`preselectionWhitelist`/`preselectionBlacklist`** — interní seznam
  jmen, která Rankscale automaticky rozpoznává/ignoruje jako konkurenty
  (`preselectionBlacklist` obsahuje zjevné false-positives typu "Visa",
  "Mastercard", "banka" — obecná slova, co by jinak matchovala jako brand).
  Vysvětluje, proč se v `competitors[]` u `search-terms-report` neobjevují
  irelevantní entity.
- **`brandNotFound: [true, true, true, true]`** u historie, přestože
  `visibilityScore` má nenulové hodnoty (58.5 atd.) — **rozporuplné na první
  pohled**, nejspíš `brandNotFound` značí něco jiného než "nulová
  viditelnost" (možná "nebyl nalezen v *raw* executions tohoto konkrétního
  bucketu" vs. metriky počítané jinak). Nevysvětleno, netestováno dál —
  flag pro budoucí zkoumání, než se cokoliv postaví na tomhle poli.
- `aggregation: "daily"` v requestu, ale vrácené `timestamps` jsou týden od
  sebe (2026-04-07, -14, -21, -28) — "daily" bucket zjevně neznamená denní
  krok dat, spíš že se surová data agregují do bucketů podle nějaké vlastní
  logiky (možná vázané na `interval: weekly` u samotných search termů, viz
  [search-terms.md](search-terms.md)). Nejasné, nepotvrzeno.

### `data.competitorMetrics[]`

Plochý seznam (own brand + konkurenti pohromadě, rozlišeno `isOwnBrand`),
**bez historie**:

```json
{
  "name": "CSOB",
  "isOwnBrand": false,
  "latestValue": 47.8,
  "trend": 3.1,
  "variations": ["ČSOB", "ČSOB / Stavební spořitelna ČSOB", "..."],
  "visibilityScore": 47.8,
  "latestRank": 2,
  "benchmarkAvgPosition": 4.2,
  "avgRank": 4.4,
  "avgSentiment": 59.3,
  "appearances": 599,
  "citationCount": 349,
  "detectionRate": 61,
  "top3": 28.1,
  "validMetricsCount": 249,
  "benchmarkObservationCount": 4
}
```

`variations[]` tady (na rozdíl od `search-terms-report`, kde bylo vždy
prázdné) **skutečně obsahuje alternativní názvy** konkurenta — užitečné pro
matching v `answer_text`/citacích, kdyby se to chtělo použít.

## Pole, která pipeline nečte

Celý endpoint se v pipeline zatím nepoužívá — nic z něj se nezapisuje nikam.

## Známé chování / gotchas

- **Jediný zdroj skutečné historie vlastního brandu** — na rozdíl od
  `search-terms-report`. Pro historii konkurentů zatím nemáme potvrzený
  žádný endpoint.
- Response byla v Postmanu oříznutá (50k znaků limit) — nevíme jistě, jestli
  existují další top-level klíče vedle `ownBrandMetrics`/`competitorMetrics`
  (např. samostatné `topicMetrics`/`engineMetrics` na úrovni `data`, ne jen
  uvnitř `ownBrandMetrics`). **TODO: ověřit kompletní odpověď.**
- `brandNotFound` pole neodpovídá intuitivně nenulovým metrikám ve stejném
  bodě — nevysvětleno, netestováno dál.
- Vztah `aggregation` parametru k reálné granularitě vrácených `timestamps`
  není jasný z jednoho vzorku (`daily` → týdenní kroky) — potřeba otestovat
  s jinou hodnotou (`weekly`, `monthly`) a delším/kratším `isoStartDate`—`isoEndDate`
  rozsahem, než se na tenhle endpoint něco staví.

## Další kroky, než se rozhodne o backfillu

1. Ověřit kompletní strukturu odpovědi (bez oříznutí).
2. Otestovat `aggregation=weekly` s dlouhým `isoStartDate`/`isoEndDate`
   rozsahem (např. 36 týdnů) a ověřit, že `timestamps[]` skutečně pokryje
   celé okno bez děr a bez duplicit.
3. Rozhodnout, jestli je brand-level historie (bez konkurentů) dostatečná pro
   účel, kvůli kterému se backfill řešil — pokud je cílem sledovat i
   konkurenty do minulosti, `/report` sám o sobě nestačí.
