# `POST /v1/metrics/search-terms-report`

## K čemu to je (byznysově)

Pro daný brand vrátí — pro každý sledovaný search term — **poslední uložený
snapshot** metrik: jak si v odpovědích AI vyhledávačů vede vlastní brand
(`ownBrand`) a jeho konkurenti (`competitors[]`) na daný dotaz (pozice,
sentiment, detekce, citace...). Volá se dvakrát na brand×týden —
jednou s `includeAnswerTexts: false` (metriky), jednou s `true` (plné texty
odpovědí AI) — viz `extract_snapshots_and_texts()`.

## ⚠️ Klíčové zjištění: `isoStartDate`/`isoEndDate` NEFILTRUJE historii metrik

Z oficiální dokumentace (doslovná citace):

> Metrics always come from each term's latest stored snapshot — the endpoint
> does not re-aggregate the requested dates. When isoStartDate/isoEndDate are
> set, range length infers timeFrame (≤1d → 24h, >1 and ≤7d → 7d, >7 and ≤30d
> → 30d, >30 and ≤90d → 3m, >90d → 1y) then that frame selects the array;
> request timeFrame is ignored for metrics. **Custom dates and periodOffset
> only constrain answerTexts.** Use /report for historical metrics over an
> exact range.

Jinými slovy:
- `isoStartDate`/`isoEndDate` **neposouvá v čase**, jaká data dostaneš — vždy
  je to poslední dostupný snapshot pro daný term.
- Délka intervalu mezi `isoStartDate` a `isoEndDate` jen určuje, **který
  klouzavý průměr** se vrátí (`snapshotH24` / `snapshotD7` / `snapshotD30`) —
  je to výběr agregačního okna, ne cestování v historii.
- Jediné, co `isoStartDate`/`isoEndDate` v tomhle endpointu skutečně omezuje,
  je `answerTexts` (texty odpovědí) — ne `ownBrand`/`competitors` metriky.
- Pro **skutečnou historii metrik přes přesný rozsah dat existuje jiný
  endpoint: `/v1/metrics/report`** — ten tady zatím nemáme zdokumentovaný a
  pipeline ho nepoužívá.

**Tohle přesně potvrzuje empirický test, který jsme dělali dřív** (dvě volání
se stejným brandem, jedno pro aktuální týden a jedno pro týden 20 týdnů
zpátky, obě vrátila identická `lastSnapshotAt` — viz konverzace, ne zapsáno
jako samostatný soubor). Backfill přes `search-terms-report` (smyčka přes
`week_ranges(N)` v `main()`/`extract_snapshots_and_texts()`) je proto
**funkčně no-op na `ownBrand`/`competitors` data** — každá iterace zapíše
znovu ten samý aktuální snapshot. Jediné, co při backfillu skutečně dostaneš
jinak, jsou `answerTexts` (ty `isoStartDate`/`isoEndDate` omezuje).

**Doporučení pro kód:** buď (a) přestat volat `search-terms-report` v cyklu
přes týdny pro `brand_snapshots` a `force=True` chování zrušit/přehodnotit
(ponechat jen pro `answerTexts`), nebo (b) prozkoumat `/v1/metrics/report`
jako náhradu pro historický backfill `brand_snapshots`. Zatím nerozhodnuto —
viz TODO v `doc/api/README.md`.

## Použití v kódu

`_fetch_snapshots()` + `_fetch_answer_texts()` v
`src/rankscale_extract_gcp.py`, volané z `extract_snapshots_and_texts()` —
jednou na `(brand_id, iso_start, iso_end)` dvojici, pro každý týden v
`weeks` (denní run: jen aktuální týden; backfill: `week_ranges(N)`).

## Request

```
POST https://rankscale.ai/v1/metrics/search-terms-report
Authorization: Bearer <RANKSCALE_API_KEY>
Content-Type: application/json
```

Tělo (podle kódu, `_fetch_snapshots`/`_fetch_answer_texts`):
```json
{
  "brandId": "...",
  "isoStartDate": "2026-09-07",
  "isoEndDate": "2026-09-13",
  "selectedTopic": "all",
  "selectedTags": "all",
  "selectedEngine": "all",
  "selectedQuery": "all",
  "includeAnswerTexts": false
}
```

### Parametry podle oficiální dokumentace (širší, než co kód používá)

| Parametr | Typ | Poznámka |
|---|---|---|
| `brandId` | string, povinné | — |
| `isoStartDate`/`isoEndDate` | string (ISO datum) | musí být obě, nebo žádná; neplatná kombinace → `400 bad_request`; **na metriky nemá vliv, jen na `answerTexts`** (viz výše) |
| `periodOffset` | integer | omezuje jen `answerTexts`, kód ho nepoužívá (default) |
| `timeFrame` | enum `24h`/`7d`/`30d`/`3m`/`1y` | výchozí `7d`; s `isoStartDate`/`isoEndDate` nastaveným se **ignoruje** pro metriky (délka intervalu ho odvodí sama); kód ho vůbec neposílá |
| `includeAnswerTexts` | boolean | kód ho posílá explicitně (`false`/`true`, dvě volání) |
| `selectedTopic` | string/`all`/`_orphaned` | kód posílá `"all"`; jinak topic ID nebo case-insensitive název; nerozpoznaná hodnota → `warnings[]` v odpovědi, ne chyba |
| `selectedTags`, `selectedEngine`, `selectedQuery` | string/`all` nebo pole hodnot | kód posílá `"all"` u všech — žádné filtrování se v pipeline neděje, stahuje se všechno |
| `searchTermId` | string | filtr na konkrétní term, kód ho nepoužívá (chce všechny termy najednou) |
| `X-Request-Id` | header, volitelný | korelační ID, kód ho neposílá |

## Response

Ověřeno reálným voláním (Postman, `timeFrame` odvozený z `isoStartDate`/`isoEndDate` na `7d`).

```json
{
  "success": true,
  "data": {
    "timeFrame": "7d",
    "requestedDateRange": {
      "source": "timeFrame",
      "startDate": "2026-09-02T20:55:06.577Z",
      "endDate": "2026-09-09T20:55:06.577Z"
    },
    "searchTerms": [ /* SearchTermSnapshot[] */ ]
  }
}
```

`requestedDateRange`/`timeFrame` v odpovědi — kód je nikde nečte, ale jsou
užitečné pro debugging (potvrzují, jaké okno API skutečně použilo).

### Struktura jednoho záznamu v `data.searchTerms[]`

```json
{
  "searchTermId": "z2J9LPa2VNuI24t05pAC",
  "query": "Která banka nabízí nejnižší úrok u spotřebitelské půjčky?",
  "aiSearchEngines": ["google_ai_mode_gui"],
  "topic": { "id": "ZFyMrgG0cuuEAvCdf1nr", "name": "Brand" },
  "tags": [],
  "status": "active",
  "interval": "weekly",
  "region": "cz",
  "websearch": true,
  "lastSnapshotAt": "2026-09-08T06:12:18.864Z",
  "ownBrand": {
    "name": "Česká spořitelna",
    "appearances": 1,
    "avgRank": 3,
    "isOwnBrand": true,
    "latestRank": 3,
    "detectionRate": 100,
    "top3": 100,
    "citationCount": 0,
    "sourceAppearanceCount": 0,
    "responseCitationCount": 0,
    "avgSentiment": 75,
    "firstSeen": "2026-09-08T06:12:08.769Z",
    "lastSeen": "2026-09-08T06:12:08.769Z",
    "visibilityScore": 83.3,
    "variations": []
  },
  "competitors": [
    {
      "name": "Trinity Bank",
      "appearances": 1,
      "avgRank": 1,
      "isOwnBrand": false,
      "latestRank": 1,
      "detectionRate": 100,
      "top3": 100,
      "citationCount": 0,
      "avgSentiment": 85,
      "firstSeen": "2026-09-08T06:12:08.769Z",
      "lastSeen": "2026-09-08T06:12:08.769Z",
      "visibilityScore": 100,
      "variations": []
    }
  ]
}
```

Poznámky ke struktuře:
- `ownBrand` **chybí úplně**, pokud brand pro daný term v posledním
  snapshotu vůbec nebyl detekovaný — pole je jen na úrovni `if "ownBrand" in t`
  v kódu (`_fetch_snapshots`), takže se to řeší, žádný bug.
- I když `ownBrand` je přítomný, může mít nulové/`null` hodnoty:
  pozorováno `appearances: 0, avgRank: 0, latestRank: null, avgSentiment: null,
  firstSeen/lastSeen: "1970-01-01T00:00:00.000Z"` (epoch = brand nikdy
  neviděn) zároveň s `sourceAppearanceCount: 1` — tzn. brand se objevil jako
  *zdroj* (citace), ale ne jako *entita v odpovědi*. Kód to zapisuje 1:1 bez
  problému (`b.get(...)` fallbacky, `NULL` v BQ je v pořádku).
- `competitors[]` **nemá** `sourceAppearanceCount`/`responseCitationCount` —
  tahle dvě pole jsou jen u `ownBrand`. Kód je stejně nečte ani u jednoho.
- `variations[]` pozorováno vždy prázdné — pravděpodobně alternativní
  názvy/zmínky brandu, nevyužito.
- `warnings[]` zmíněné v dokumentaci (u nerozpoznaného `selectedTopic`) —
  nepozorováno v žádné z našich odpovědí (posíláme `"all"`), kód ho nečte.

### Pole — co pipeline používá

| Pole | Používá se jako | Poznámka |
|---|---|---|
| `searchTermId` | `raw_brand_snapshots.search_term_id`, `raw_answer_texts.search_term_id` | `t["searchTermId"]`, povinné |
| `topic.id`/`topic.name` | `raw_brand_snapshots.topic_id`/`topic_name` | `t.get("topic") or {}` |
| `aiSearchEngines[0]` | `raw_brand_snapshots.engine` | jen první prvek, stejně jako u `search-terms` |
| `lastSnapshotAt` | `raw_brand_snapshots.last_snapshot_at` + vstup do skip-check (`bq_max_snapshot`) | klíčové pole pro "skip-if-no-new-data" logiku |
| `ownBrand.*` | `raw_brand_snapshots` řádek s `is_own_brand=true` | `name→brand_name`, `appearances`, `avgRank→avg_rank`, `latestRank→latest_rank`, `detectionRate→detection_rate`, `top3→top3_rate`, `citationCount→citation_count`, `avgSentiment→avg_sentiment`, `visibilityScore→visibility_score` |
| `competitors[].*` | `raw_brand_snapshots` řádek per konkurent, `is_own_brand=false` | stejná pole jako `ownBrand` |
| `answerTexts[]` (jen při `includeAnswerTexts=true`) | `raw_answer_texts` | viz `_fetch_answer_texts()` — `executionId`, `executedAt`, `engine`, `answerText` |

### Pole, která pipeline nečte

`success`, `data.timeFrame`, `data.requestedDateRange`, `tags`, `status`,
`interval`, `region`, `websearch`, `sourceAppearanceCount`,
`responseCitationCount`, `variations[]`, `warnings[]`.

## Zápis do BigQuery

`raw_brand_snapshots` a `raw_answer_texts` (`src/schema_raw.sql`) — viz
`extract_snapshots_and_texts()`. Denní run: skip-if-no-new-data přes
`bq_max_snapshot()`. Backfill: `force=True`, vždy zapíše — **ale vzhledem k
výše popsanému chování API se tím jen duplikuje ten samý aktuální snapshot**,
ne získává historie.

## Známé chování / gotchas

- **Backfill přes tenhle endpoint pro `ownBrand`/`competitors` metriky
  nefunguje** — viz sekce výše. Potvrzeno jak oficiální dokumentací, tak
  empirickým testem.
- `isoStartDate`/`isoEndDate` ovlivňuje jen `answerTexts` — takže backfill
  `answerTexts` (na rozdíl od `brand_snapshots`) může dávat smysl, pokud API
  skutečně vrací historické `executedAt` časy pro starší okna (nebylo zatím
  ověřeno samostatně, jen odvozeno z dokumentace).
- `selectedTopic` přijímá i neplatné hodnoty bez chyby — jen je nahlásí přes
  `warnings[]`. Kód posílá `"all"`, takže se ho tohle netýká, ale při budoucí
  úpravě (např. filtrování podle topicu) na to pamatovat.
- Pro skutečný historický backfill metrik existuje `/v1/metrics/report` —
  **zatím nezdokumentováno**, další krok až budeme chtít backfill řešit
  pořádně.
