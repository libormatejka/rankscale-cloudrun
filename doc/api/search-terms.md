# `GET /v1/metrics/search-terms`

## K čemu to je (byznysově)

Vrátí seznam sledovaných search termů (dotazů) pro daný brand — jeden term
reprezentuje kombinaci **konkrétní otázky/dotazu × jednoho AI vyhledávacího
enginu** (ne otázku samotnou). Stejná otázka ("Jaká je nejlepší banka v ČR?")
se tak v odpovědi objevuje vícekrát — jednou pro `chatgpt_gui`, jednou pro
`perplexity_gui`, jednou pro `google_ai_overview` atd., pokaždé jako
samostatný záznam s vlastním `id`.

Výstup je vstup pro `search-terms-report` (brand snapshoty, konkurenti) — bez
tohohle volání pipeline neví, jaké `search_term_id` použít jako klíč napříč
zbytkem dat.

## Použití v kódu

`extract_search_terms()` v `src/rankscale_extract_gcp.py`, volané pro každý
brand zvlášť (`GET`, jedno volání na brand, žádné stránkování).

## Request

```
GET https://rankscale.ai/v1/metrics/search-terms?brandId=<BRAND_ID>&limit=5000
Authorization: Bearer <RANKSCALE_API_KEY>
```

| Parametr | Hodnota v kódu | Poznámka |
|---|---|---|
| `brandId` | `brand_id` z `extract_brands()` | povinné |
| `limit` | `5000` | natvrdo v kódu, žádné stránkování implementované |

Žádné tělo requestu (GET).

## Response

Ověřeno reálným voláním (Postman, 2026-09). Obálka:

```json
{
  "success": true,
  "data": {
    "searchTerms": [ /* SearchTerm[] */ ]
  }
}
```

### Struktura jednoho záznamu v `data.searchTerms[]`

```json
{
  "id": "IbVKy9bNC6zLyRlIqXUZ",
  "term": "Jaká je nejlepší banka v ČR?",
  "aiSearchEngines": ["google_ai_mode_gui"],
  "status": "active",
  "executionsAmount": 34,
  "interval": "weekly",
  "region": "cz",
  "websearch": true,
  "createdAt": "2026-01-27T06:51:17.072Z",
  "lastExecutionTime": "2026-09-08T06:12:10.933Z",
  "nextScheduledExecutionTime": "2026-09-15T06:00:00.000Z",
  "searchTermTopicRef": { "id": "ZFyMrgG0cuuEAvCdf1nr", "name": "Brand" },
  "tags": ["product-brand"]
}
```

`aiSearchEngines` je v každém pozorovaném záznamu pole s **přesně jedním**
prvkem — API tedy modeluje "term × engine" jako `N` samostatných záznamů se
společným textem `term`, ne jeden záznam s polem enginů. Kód s tím počítá
(`(t.get("aiSearchEngines") or [""])[0]`), ale bere jen první prvek — pokud by
API v budoucnu vrátilo víc enginů v jednom poli, ostatní by se tiše ztratily.

### Pole — co pipeline používá

| Pole | Používá se jako | Poznámka |
|---|---|---|
| `id` | `raw_search_terms.search_term_id` | povinné (`t["id"]`) |
| `term` | `raw_search_terms.query` | `t.get("term")` |
| `aiSearchEngines[0]` | `raw_search_terms.engine` | jen první prvek pole, viz výše |
| `searchTermTopicRef.id` | `raw_search_terms.topic_id` | `topic.get("id")` |
| `searchTermTopicRef.name` | `raw_search_terms.topic_name` | `topic.get("name")` |
| `region` | `raw_search_terms.region` | — |
| `interval` | `raw_search_terms.interval` | — |
| `tags` | `raw_search_terms.tags` | serializováno jako JSON string (`json.dumps`) |
| `status` | `raw_search_terms.status` | `"active"` \| `"inactive"` |
| `createdAt` | `raw_search_terms.created_at` | — |
| `lastExecutionTime` | `raw_search_terms.last_execution_time` | — |
| `nextScheduledExecutionTime` | `raw_search_terms.next_execution_time` | `null` u `inactive` termů |
| `executionsAmount` | `raw_search_terms.executions_amount` | — |

### Pole, která pipeline nečte

| Pole | Popis |
|---|---|
| `success` | obálka, ne pole termu |
| `websearch` | bool, ve všech pozorovaných záznamech `true` — jestli term používá websearch, nezjištěno co jinak znamená `false` |

Pipeline tedy z tohohle endpointu využívá prakticky všechno kromě `websearch`.

## Zápis do BigQuery

Tabulka `raw_search_terms` (`src/schema_raw.sql`). Před zápisem se řádky
deduplikují podle `search_term_id` (`rows_by_id` dict v
`extract_search_terms()`, commit `b7b931a`) — viz gotcha níže.

## Známé chování / gotchas

- **API vrací stejný term v jedné odpovědi vícekrát, s identickým obsahem.**
  Potvrzeno opakovaně reálnými voláními — např. `Pa7PlDeb2wZgfMTKZZrb`,
  `qcWtoSJhMNCYlM2hRN3X`, `c0Q6GbGAytwdKTo2J8D1`, `yBs9xGph0VvRQuFb8tOv`,
  `8kEO9NhX7C3nAkptz3dp`, `ORvPodEQ6OlKFr3QZz4M`, `XzkhtlFczJZw26uB9dQT`,
  `eNkww11pn39PyDjvMz1n`, `Um0d4t7T1WOZ6Wc9Uq0V`, `zMwnBlMEs9Kr9wfaKtWm`,
  `V65L4J421LJU9aEwMqQZ`, `jcWW2GLyyQFHIVJzvYgI`, `tPfuGwD7uWNSGJhVzkUW`,
  `0Ce9cLUVQPQH5lEb4L2K`, `66A9h8jjf096XhpYQFNK` se v jedné odpovědi objevily
  2× i 3×, pokaždé se stejnými hodnotami všech polí (stejný
  `executionsAmount`, `lastExecutionTime` atd. — není to jiná verze dat,
  je to čistá duplicita záznamu). Sedí to i s nálezem duplicit v
  `operationalSearchTerms[]` na `/v1/metrics/brands` ([brands.md](brands.md))
  — jde o systémovou vlastnost Rankscale datového modelu, ne o glitch
  jednoho volání.
  **Proto je dedup podle `search_term_id` v kódu nutný, ne kosmetický.**
- `status`/`nextScheduledExecutionTime` u `inactive` termů: `nextScheduledExecutionTime`
  je `null`, `executionsAmount` zůstává na hodnotě z doby, kdy byl term ještě
  aktivní (nenuluje se).
- `interval` byl ve všech pozorovaných záznamech `"weekly"` — jiné hodnoty
  (denní? měsíční?) nebyly zatím pozorované, kód s nimi ale počítá obecně
  (ukládá se 1:1, netransformuje se).
