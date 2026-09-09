# `GET /v1/metrics/brands`

## K čemu to je (byznysově)

Vrátí seznam brandů (vlastních značek), které má účet v Rankscale nastavené ke
sledování. Je to první krok celé pipeline — `brand_id`, které tenhle endpoint
vrátí, se pak používají ve všech dalších voláních (search-terms,
search-terms-report, citations). Bez tohoto volání pipeline neví, pro koho má
data stahovat.

## Použití v kódu

`extract_brands()` v `src/rankscale_extract_gcp.py`, volané jednou na začátku
každého runu (denního i backfillu) — bez cyklu, jedno volání na celý běh.

## Request

```
GET https://rankscale.ai/v1/metrics/brands?limit=1000
Authorization: Bearer <RANKSCALE_API_KEY>
```

| Parametr | Hodnota v kódu | Poznámka |
|---|---|---|
| `limit` | `1000` | natvrdo v kódu — nikdy jsme nenarazili na účet s víc brandy, takže stránkování (pokud existuje) není implementované |

Žádné tělo requestu (GET).

## Response

Ověřeno reálným voláním (Postman, 2026-09). Obálka:

```json
{
  "success": true,
  "data": {
    "brands": [ /* Brand[] */ ]
  }
}
```

### Struktura jednoho záznamu v `data.brands[]`

```json
{
  "id": "E5GAVmqco65u7Smx3hso",
  "name": "Česká spořitelna (Test)",
  "brandInfo": {
    "names": ["Česká spořitelna (Test)", "Spořka", "Česká spořitelna"],
    "productNames": [],
    "description": ""
  },
  "description": "",
  "url": "https://www.csas.cz",
  "additionalDomains": [],
  "createdAt": "2026-01-26T18:53:11.123Z",
  "operationalTopics": [
    { "topicId": "aVaq5pTG3Io4GR853gAf", "name": "Půjčky/Úvěry", "addedAt": "2026-01-27T08:45:06.454Z" }
  ],
  "operationalSearchTerms": [
    { "searchTermId": "xS7FDRiCviDIlTtUg80f" }
  ],
  "operationalTags": ["dip", "investice", "product-brand"],
  "defaultCountry": "cz",
  "defaultLanguage": "cs",
  "syncSchedules": {
    "monthly": null,
    "weekly": { "weekday": "tue" },
    "daily": { "hour": 6 }
  }
}
```

### Pole v `data.brands[]` — co pipeline používá

| Pole | Používá se jako | Poznámka |
|---|---|---|
| `id` | `raw_brands.brand_id` | povinné (`b["id"]`, žádný `.get()` fallback — pokud chybí, run spadne) |
| `name` | `raw_brands.name` | povinné (`b["name"]`) |
| `url` | `raw_brands.domain` | volitelné (`b.get("url")` — může být `None`) |

### Pole, která pipeline nečte

| Pole | Popis | Potenciální využití |
|---|---|---|
| `success` | obálka celé odpovědi, ne pole brandu | kód ho vůbec nekontroluje — pokud by bylo `false`, `data.brands` pravděpodobně chybí a `data["data"]["brands"]` spadne s `KeyError` (žádné explicitní ošetření) |
| `brandInfo.names` | alternativní/historické názvy brandu (u tohohle příkladu 3 varianty) | mohlo by se hodit pro fuzzy matching brandu v `answer_text`/citacích |
| `brandInfo.productNames`, `brandInfo.description` | v ukázce prázdné | — |
| `description` | popis brandu (prázdný v ukázce) | — |
| `additionalDomains` | další domény brandu kromě `url` (prázdné v ukázce) | pro `raw_citations`/`raw_answer_texts` matching by mohly být relevantní |
| `createdAt` | kdy byl brand v Rankscale založen | — |
| `operationalTopics[]` | témata (`topicId`+`name`+`addedAt`), která má brand nastavená — odpovídá `topic_id`/`topic_name` v `raw_search_terms`/`raw_brand_snapshots` | zdroj pravdy pro mapování topic_id → topic_name, dnes se topic_name bere z jiných endpointů |
| `operationalTags[]` | tagy nastavené na úrovni brandu (v ukázce `["dip", "investice", "product-brand"]`) — odpovídají `tags` u jednotlivých search termů | — |
| `defaultCountry`/`defaultLanguage` | výchozí region/jazyk brandu (`"cz"`/`"cs"`) | odpovídá `region` u search termů — možná zdroj pravdy místo natvrdo čtení z každého termu zvlášť |
| `syncSchedules` | kdy Rankscale spouští sync pro tenhle brand (`daily.hour`, `weekly.weekday`, `monthly`) | vysvětluje časování `lastExecutionTime`/`nextScheduledExecutionTime` u search termů — v ukázce `daily.hour: 6` sedí na denní spouštění pipeline v 6:30 UTC (README krok 6) |
| `operationalSearchTerms[]` | seznam `searchTermId`, které patří tomuto brandu | **viz gotcha níže — obsahuje duplicity** |

## Zápis do BigQuery

Tabulka `raw_brands` (`src/schema_raw.sql`) — `is_own_brand` se u tohohle
endpointu vždy natvrdo zapisuje jako `true` (kódem, ne z API), protože endpoint
vrací jen vlastní brandy, ne konkurenty (ti se objevují až v
`search-terms-report`). Vše z `brandInfo`, `operationalTopics`,
`operationalSearchTerms` se dnes zahazuje — do `raw_brands` jde jen `id`,
`name`, `url`.

## Známé chování / gotchas

- **`operationalSearchTerms[]` obsahuje duplicitní `searchTermId`.** V reálné
  odpovědi se stejné ID objevuje opakovaně (v surové odpovědi vidět jako bloky
  po ~6 položkách, které se opakují). To je **nezávislé potvrzení** nálezu ze
  `search-terms` endpointu (dedup přidán v `extract_search_terms()`, commit
  `b7b931a`) — Rankscale má duplicitní asociace search termů k brandu na
  úrovni datového modelu, napříč endpointy, ne že by šlo o náhodný artefakt
  jednoho konkrétního volání. Potvrzuje to, že dedup v kódu (podle
  `search_term_id`) je správné a trvalé řešení, ne obezlička kolem
  jednorázového API glitche.
- Tenhle endpoint se dnes v pipeline nepoužívá k ničemu jinému než k získání
  `brand_id` seznamu — `operationalSearchTerms`/`operationalTopics` by
  teoreticky mohly nahradit/ověřit data z `/v1/metrics/search-terms`, ale
  zatím se to nedělá.
