# `GET /v1/metrics/topics` (endpoint neznámý — nepoužívá se v pipeline)

## K čemu to je (byznysově)

Vrátí seznam topiců (témat) nastavených pro brand — stejná entita, která se
objevuje jako `topic_id`/`topic_name` u `raw_search_terms`/`raw_brand_snapshots`,
ale tohle je zdroj pravdy pro samotný topic (kdo ho založil, kdy, jaké
`searchTermIds` pod něj patří).

**Nepoužívá se v pipeline vůbec.** Metoda/přesná cesta endpointu není
potvrzená z requestu (jen z Postman odpovědi) — název `topics.json` a tvar
odpovědi (`data.topics[]`) naznačují `GET /v1/metrics/topics`, ale nebylo to
ověřeno proti requestu, jen odvozeno.

## Request

**TODO — nepotvrzeno.** Pravděpodobně:
```
GET https://rankscale.ai/v1/metrics/topics?brandId=<BRAND_ID>
Authorization: Bearer <RANKSCALE_API_KEY>
```

## Response

Ověřeno reálnou odpovědí (Postman) — 7 topiců pro jeden brand. Obálka:

```json
{
  "success": true,
  "data": {
    "topics": [ /* Topic[] */ ]
  }
}
```

### Struktura jednoho záznamu v `data.topics[]`

```json
{
  "id": "ZFyMrgG0cuuEAvCdf1nr",
  "name": "Brand",
  "description": "",
  "brandRef": "E5GAVmqco65u7Smx3hso",
  "myBrand": "Česká spořitelna",
  "createdAt": { "_seconds": 1769496543, "_nanoseconds": 580000000 },
  "updatedAt": { "_seconds": 1779450831, "_nanoseconds": 232000000 },
  "createdBy": "hwSqTrrKF1ZaC98LamviUz9Bvqw1",
  "keywords": "",
  "searchTermIds": ["xS7FDRiCviDIlTtUg80f", "o72kIZbXRhMoy6lKPF52", "..."]
}
```

Zajímavá pole:
- **`createdAt`/`updatedAt`** jsou tady **jiný formát** než všude jinde v API
  (Firestore timestamp `{_seconds, _nanoseconds}`, ne ISO string jako
  `createdAt` u brandů/search termů). Při případném parsování na tohle dát
  pozor — vyžaduje jinou konverzi (`datetime.fromtimestamp(_seconds, tz=UTC)`,
  ne `datetime.fromisoformat`).
- **`searchTermIds[]`** — přímý seznam termů patřících pod topic. U topicu
  "Brand" v ukázce 250+ ID, u nově založených topiců (`"Spoření"`, `"Účty"`,
  `"FinančněZdravější"`) prázdné pole (`[]`) — topic existuje, ale zatím nemá
  přiřazené žádné termy.
- `description`/`keywords` — v ukázce vždy prázdné, nepoužito.
- `myBrand` — jméno brandu (ne ID) duplicitně vedle `brandRef` — zjevně
  denormalizované pro zobrazení bez dalšího joinu.

## Pole, která pipeline nečte

Celý endpoint — nepoužívá se vůbec. `topic_id`/`topic_name` dnes pipeline
získává výhradně z `searchTermTopicRef`/`topic` polí u `/v1/metrics/search-terms`
a `/v1/metrics/search-terms-report`, ne odsud.

## Známé chování / gotchas

- **Metoda/cesta endpointu nepotvrzená** — TODO ověřit skutečný request
  (Postman historie/Collection), než se cokoliv staví na předpokladu
  `GET /v1/metrics/topics`.
- `createdAt`/`updatedAt` ve Firestore timestamp formátu, ne ISO string —
  nekonzistentní s celým zbytkem API.
- Mohlo by sloužit jako alternativní/doplňkový zdroj pro `topic_name` mapping
  místo spoléhání na `searchTermTopicRef`/`topic` v jiných odpovědích — ale
  zatím nedává jasnou výhodu, protože ty další endpointy topic_name už nesou.
