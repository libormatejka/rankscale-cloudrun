# Rotace Rankscale API klíče

Postup pro výměnu `RANKSCALE_API_KEY` v Secret Manageru — ať jde o pravidelnou
rotaci, podezření na únik, nebo prostě nový klíč z Rankscale dashboardu.

## Kdy rotovat

- Klíč byl vypsán do terminálu v čistém textu (scrollback/shell historie) —
  považuj ho za kompromitovaný bez ohledu na to, jestli "to někdo mohl vidět".
- Podezření na únik jinou cestou.
- Pravidelná bezpečnostní rotace.
- Starý klíč přestal fungovat (`401 Unauthorized` v logu jobu).

## 1. Nový klíč v Rankscale

Rankscale dashboard → Settings → API. Klíč začíná `rk_`.

## 2. Přidat novou verzi do Secret Manageru

Nemaže se stará verze, jen se přidá nová — `rankscale-api-key` secret zůstává
stejný, jen dostane další verzi:

```bash
gcloud secrets versions add rankscale-api-key --project=$GCP_PROJECT \
  --data-file=<(printf "%s" "NOVY_API_KLIC")
```

## 3. Ověřit — nikdy nevypisovat hodnotu klíče na obrazovku

Klíč vypsaný do terminálu = kontaminovaný klíč (jde do shell historie i
scrollbacku). Ověřuj jednou z těchto cest, podle toho, co potřebuješ zjistit:

**Funguje proti API?** (na terminál se propíše jen HTTP status, ne hodnota)
```bash
curl -s -o /dev/null -w "%{http_code}\n" \
  -H "Authorization: Bearer $(gcloud secrets versions access latest --secret=rankscale-api-key --project=$GCP_PROJECT)" \
  "https://rankscale.ai/v1/metrics/brands?limit=1"
```
Očekávaný výsledek: `200`.

**Délka/formát** (např. nechtěný `\n` na konci z `echo` místo `printf`):
```bash
gcloud secrets versions access latest --secret=rankscale-api-key --project=$GCP_PROJECT | wc -c
```

**Je to pořád placeholder / stará hodnota?** (porovnání hashů, ne hodnot)
```bash
gcloud secrets versions access latest --secret=rankscale-api-key --project=$GCP_PROJECT | sha256sum
# porovnej s: printf "OCEKAVANY_KLIC" | sha256sum
```

**Nutná vizuální kontrola** — jen prefix, nikdy celá hodnota:
```bash
gcloud secrets versions access latest --secret=rankscale-api-key --project=$GCP_PROJECT | cut -c1-5
```

**Pokud přesto musíš jednou vypsat celou hodnotu** — vypni kolem toho shell
historii:
```bash
set +o history
# citlivý příkaz
set -o history
```

## 4. Redeploy? Není potřeba

Cloud Run Job má `--set-secrets="RANKSCALE_API_KEY=rankscale-api-key:latest"`
(viz `src/env.yaml` + README krok 5) — `:latest` znamená, že příští spuštění
automaticky sáhne po nově přidané verzi. Nic se nebuildí ani neaktualizuje.

## 5. Otestovat

```bash
make execute
```
a zkontroluj v BigQuery `etl_runs` (`status = success`, žádný `error_message`):
```sql
SELECT * FROM `rankscale.RankScaleDashboard.etl_runs` ORDER BY started_at DESC LIMIT 1
```

## 6. Zneplatnit starý klíč

Až je nový klíč ověřeně funkční, zneplatni starý přímo v Rankscale dashboardu
— výměna v Secret Manageru sama o sobě starý klíč nedeaktivuje, jen ho
přestane používat tenhle job.

## 7. Pokud byl klíč vypsán do terminálu

```bash
history -c
```
v Cloud Shellu, ať nezůstane v `.bash_history`.
