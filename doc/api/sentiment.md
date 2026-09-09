# `POST /v1/metrics/sentiment` (metoda/cesta nepotvrzená — odvozeno)

## K čemu to je (byznysově)

Nejpodrobnější sentiment analýza ze všech endpointů — nejde jen o číslo
(`avgSentiment`), ale o **konkrétní klíčová slova/fráze**, kvůli kterým AI
odpověď hodnotila brand pozitivně/neutrálně/negativně, s odkazem na
konkrétní `executionId` a časové razítko každého výskytu. Umožnilo by
odpovědět na "proč má tenhle brand sentiment 60, ne 80" na úrovni
jednotlivých frází v odpovědích AI.

**Nepoužívá se v pipeline vůbec.** Zmínka o `/sentiment` existuje v oficiální
dokumentaci `isoStartDate`/`isoEndDate` pole u jiných endpointů ("on
/sentiment the live competitor-allowlist snapshot family... Sentiment tab"),
což potvrzuje, že endpoint reálně existuje pod tímhle jménem — přesná
metoda/request tělo ale nebyly zachyceny, jen výsledná odpověď.

⚠️ **Response je objemný** — originální zachycená odpověď měla 34 MB, po
zúžení (pravděpodobně kratší časové okno nebo méně brandů) 4 MB. Kvůli
keyword-level detailu (každá unikátní fráze = vlastní záznam s
`executionIds`/`timestamps`) roste velikost rychle s počtem exekucí a
sledovaných brandů — na rozdíl od ostatních endpointů tenhle není vhodné
ukládat 1:1 do jedné ploché BQ tabulky bez rozmyslu.

## Request

**TODO — nepotvrzeno.** Podle vzoru ostatních `POST /v1/metrics/*` endpointů
pravděpodobně:
```
POST https://rankscale.ai/v1/metrics/sentiment
Authorization: Bearer <RANKSCALE_API_KEY>
Content-Type: application/json
{ "brandId": "...", "timeFrame": "7d", "...": "případně isoStartDate/isoEndDate" }
```

## Response

Ověřeno reálnou odpovědí (Postman, upravenou/zúženou uživatelem). Obálka:

```json
{
  "success": true,
  "data": {
    "brandSentiments": [ /* 1 položka v ukázce — vlastní brand */ ],
    "companySentiments": [ /* 50 položek v ukázce — širší seznam firem/konkurentů */ ],
    "brandSentimentsBySearchTerm": [ /* 31 položek — rozpad podle termu, jen vlastní brand */ ],
    "companySentimentsBySearchTerm": [ /* 35 položek — rozpad podle termu, širší seznam */ ]
  }
}
```

Rozdíl `brandSentiments` vs. `companySentiments` není z dat jednoznačně
potvrzený — v ukázce má `brandSentiments` jen 1 záznam (vlastní brand),
`companySentiments` 50 (pravděpodobně vlastní brand + konkurenti, širší
seznam podobný `preselectionWhitelist` z [report.md](report.md)). **TODO:
potvrdit přesný rozdíl** (možná "sledované/nakonfigurované brandy" vs.
"všechny firmy zmíněné v odpovědích AI").

### Struktura jednoho záznamu v `data.brandSentiments[]` / `data.companySentiments[]`

```json
{
  "name": "Ceska sporitelna (Test)",
  "isOwnBrand": true,
  "totalSentimentScore": 6215,
  "sentimentCount": 102,
  "avgSentiment": 60.93,
  "executionCount": 102,
  "positiveCount": 75,
  "neutralCount": 74,
  "negativeCount": 9,
  "hasWebGrounding": true,
  "hasTrainingData": false,
  "nameVariations": ["Česká spořitelna", "ČS", "Česká spořitelna (Buřinka)", "ČS banka", "Českou spořitelnu"],
  "positiveKeywords": {
    "nulový poplatek za vyřízení hypotéky": {
      "count": 1,
      "executionIds": ["YLIFqoBjAx76flQRwbjy"],
      "timestamps": ["2026-09-08T06:22:58.829Z"]
    }
  },
  "neutralKeywords": { "...": "stejná struktura jako positiveKeywords" },
  "negativeKeywords": { "...": "stejná struktura jako positiveKeywords" },
  "webGroundingKeywords": { "positive": { "...": "podmnožina positiveKeywords, jen z web-grounded odpovědí" }, "neutral": {}, "negative": {} },
  "trainingDataKeywords": { "positive": {}, "neutral": {}, "negative": {} },
  "webGroundingSentimentByEngine": {
    "perplexity_gui": { "sum": 6215, "count": 102, "avg": 60.93 }
  },
  "trainingDataSentimentByEngine": {}
}
```

Zajímavá pole:
- **`positiveKeywords`/`neutralKeywords`/`negativeKeywords`** — klíč je
  přímo text fráze (ne ID), hodnota `{count, executionIds[], timestamps[]}`.
  `positiveCount`/`neutralCount`/`negativeCount` = počet **unikátních frází**
  v dané kategorii (ne součet `count` napříč frázemi — v ukázce
  `positiveCount: 75` = přesně 75 klíčů v `positiveKeywords`).
- **`hasWebGrounding`/`hasTrainingData`** — rozlišuje, jestli sentiment
  pochází z odpovědi s webovým vyhledáváním (`websearch: true` u search
  termu, viz [search-terms.md](search-terms.md)) nebo z trénovacích dat
  modelu bez groundingu. `webGroundingKeywords`/`trainingDataKeywords` jsou
  odpovídající podmnožiny hlavních keyword polí.
- **`webGroundingSentimentByEngine`/`trainingDataSentimentByEngine`** —
  rozpad průměrného sentimentu podle AI enginu, jen v rámci dané kategorie
  (grounded/training). V ukázce jen `perplexity_gui` — brand měl web-grounded
  data jen z tohohle enginu.
- **`timestamps[]`** u každé fráze — potenciální zdroj historie (podobně
  jako `counts` u `/v1/metrics/citations`, viz [citations.md](citations.md)),
  ale tady je to seznam přesných časů jednotlivých výskytů, ne agregovaný
  denní/týdenní součet.

### `data.brandSentimentsBySearchTerm[]` / `data.companySentimentsBySearchTerm[]`

Stejná struktura sentimentu jako výše, jen obalená per search term:

```json
{
  "searchTermId": "01u10gSMJLP3lWqLFKvA",
  "query": "Jaká banka má nejnižší poplatky spojené s hypotékou?",
  "brandSentiments": [ /* pole se stejnou strukturou jako data.brandSentiments[] výše */ ]
}
```

Počet položek (31 u `brandSentimentsBySearchTerm`, 35 u
`companySentimentsBySearchTerm`) — méně než celkový počet search termů
brandu, protože se zjevně zahrnou jen termy, které měly za dané období
alespoň jednu exekuci se sentimentem.

## Pole, která pipeline nečte

Celý endpoint — nepoužívá se vůbec.

## Známé chování / gotchas

- **Objem dat roste rychle** — keyword-level detail s `executionIds`/
  `timestamps` u každé unikátní fráze. Než se tenhle endpoint začne
  pravidelně stahovat, promyslet rozsah (`timeFrame`/`isoStartDate`—
  `isoEndDate`) a jestli ukládat opravdu všechno, nebo jen agregáty
  (`avgSentiment`, `positiveCount`/`neutralCount`/`negativeCount`) bez
  keyword-level rozpadu.
- **Metoda/cesta requestu nepotvrzená** — TODO ověřit skutečný request,
  stejně jako u `topics.md`.
- Rozdíl `brandSentiments` vs. `companySentiments` (a jejich `BySearchTerm`
  varianty) není jednoznačně potvrzený z dat — TODO ujasnit.
- `executionIds[]` u jednotlivých frází by šly propojit s `raw_answer_texts.execution_id`
  (viz [search-terms-report.md](search-terms-report.md)) — potenciální most
  mezi "co AI řeklo" (`answer_text`) a "proč to takhle ohodnotili sentiment"
  (konkrétní fráze), kdyby se tenhle endpoint začal používat.
