# Rankscale → BigQuery — Cloud Run Job

Denní pipeline, která tahá data z Rankscale Metrics API a ukládá je 1:1 do
BigQuery (`raw_*` tabulky). Běží jako **Cloud Run Job** spouštěný z **Cloud
Scheduleru**.

- **Autentizace k BigQuery**: Application Default Credentials — service
  account je přiřazený přímo k jobu, žádný klíč se nikam nekopíruje.
- **Rankscale API klíč**: GCP Secret Manager, namountovaný jako env proměnná.
- **Chybové chování**: neúspěšný brand se loguje, extract pokračuje na dalších;
  pokud selhal alespoň jeden, celý job skončí s `sys.exit(1)` → Cloud Run
  execution se označí jako **Failed**.

---

## Obsah

- [Obsah repozitáře](#obsah-repozitáře)
- [Než začneš: vždy stejný `--project`](#než-začneš-vždy-stejný---project)
- [1. Příprava GCP projektu](#1-příprava-gcp-projektu)
- [2. Service account pro job](#2-service-account-pro-job)
- [3. Rankscale API klíč do Secret Manageru](#3-rankscale-api-klíč-do-secret-manageru)
- [3b. BigQuery dataset a tabulky](#3b-bigquery-dataset-a-tabulky)
- [4. Build image a push do Artifact Registry](#4-build-image-a-push-do-artifact-registry)
- [5. Vytvoření Cloud Run Job](#5-vytvoření-cloud-run-job)
- [6. Denní spouštění přes Cloud Scheduler](#6-denní-spouštění-přes-cloud-scheduler)
- [7. Aktualizace image po změně kódu](#7-aktualizace-image-po-změně-kódu)
- [8. E-mailová notifikace při selhání](#8-e-mailová-notifikace-při-selhání)
- [Monitoring a logy](#monitoring-a-logy)
- [Smazání dat (truncate)](#smazání-dat-truncate)
- [Troubleshooting](#troubleshooting)
- [Lint](#lint)
- [Lokální test image](#lokální-test-image)

---

## Obsah repozitáře

| Cesta | Účel |
|---|---|
| `README.md` | tento návod |
| `Makefile` | zkratky pro lint/build/deploy příkazy z tohoto návodu (`make help` vypíše cíle) |
| `pyproject.toml` | konfigurace `ruff` (linter) |
| `requirements-dev.txt` | vývojářské závislosti (jen `ruff`, do image se nekopírují) |
| `doc/SECURITY_CHECKLIST.md` | bezpečnostní review nasazení, otevřené položky k řešení |
| `doc/API_KEY_ROTATION.md` | postup výměny `RANKSCALE_API_KEY` v Secret Manageru |
| `src/rankscale_extract_gcp.py` | samotný extract skript |
| `src/Dockerfile` | image pro Cloud Run Job |
| `src/.dockerignore` | vynechá `env.yaml`/`schema_raw.sql` z Docker build kontextu (do image se stejně kopírují jen `requirements.txt` + skript) |
| `src/requirements.txt` | Python závislosti (subset — bez `google-auth`, ten už táhne `google-cloud-bigquery`) |
| `src/env.yaml` | cílový `GCP_PROJECT` + `BQ_DATASET` pro Cloud Run Job (viz krok 5) |
| `src/schema_raw.sql` | DDL pro `raw_*` tabulky + `etl_runs` (run log), bez natvrdo zapsaného project ID (viz krok 3b) |

`src/` je vše, co se nasazuje do GCP (image + jeho build inputy). `doc/` jsou
podpůrné dokumenty, které se nikam nenasazují.

Většina příkazů z tohoto návodu má zkratku v [`Makefile`](Makefile) —
`make help` vypíše dostupné cíle. `deploy-*`/`execute`/`backfill` cíle
potřebují nastavené `$GCP_PROJECT`/`$REGION`/`$REPO` stejně jako `gcloud`
příkazy níže (krok 1).

---

## Než začneš: vždy stejný `--project`

Každý příkaz v tomto návodu má explicitní `--project=$GCP_PROJECT` (nebo
`--project_id=$GCP_PROJECT` u `bq`), i když by teoreticky fungoval i bez něj —
nespoléhej na ambientní `gcloud config set project`, ať si jsi jistý, že
service account, secret, Cloud Run Job i Scheduler vznikají ve stejném
projektu.

Shell proměnné (`$GCP_PROJECT`, `$REGION`, `$REPO`, `$SA_NAME`) z kroku 1 platí
jen v aktuální session terminálu. Než spustíš cokoliv z návodu, ověř:

```bash
echo $GCP_PROJECT $REGION $REPO $SA_NAME
```

Pokud je výstup prázdný, spusť `export` řádky z kroku 1 znovu.

### Kam nastavit cílový GCP projekt a BigQuery dataset pro samotný skript

To určuje soubor [`src/env.yaml`](src/env.yaml):

```yaml
GCP_PROJECT: rankscale
BQ_DATASET: RankScaleDashboard
```

Skript je čte jako `os.environ["GCP_PROJECT"]` / `os.environ["BQ_DATASET"]`
(viz `src/rankscale_extract_gcp.py`, funkce `tbl()` — tabulky jsou
`{GCP_PROJECT}.{BQ_DATASET}.raw_*`). Při vytváření jobu (krok 5) se soubor
předá přes `--env-vars-file=src/env.yaml`.

---

## 1. Příprava GCP projektu

Projekt musí mít zapnuté **billing** — bez něj nejdou zapnout Artifact Registry
ani Cloud Build. Ověř/přiřaď:

```bash
gcloud billing accounts list

gcloud billing projects link rankscale --billing-account=BILLING_ACCOUNT_ID
```

(`BILLING_ACCOUNT_ID` ze sloupce `ACCOUNT_ID` prvního příkazu, formát
`XXXXXX-XXXXXX-XXXXXX`. Jde udělat i přes Cloud Console → Billing → Link a
billing account.)

```bash
export GCP_PROJECT=rankscale         # skutečné project ID (ne "hezký" název)
export REGION=europe-west3          # nebo jiný region blízko tebe
export REPO=rankscale
export SA_NAME=rankscale-extract-job

gcloud config set project $GCP_PROJECT
gcloud config get-value project      # musí vypsat přesně $GCP_PROJECT

gcloud services enable \
  run.googleapis.com \
  cloudscheduler.googleapis.com \
  artifactregistry.googleapis.com \
  secretmanager.googleapis.com \
  bigquery.googleapis.com \
  cloudbuild.googleapis.com \
  --project=$GCP_PROJECT
```

### Cloud Build oprávnění (nutné u nově založených projektů)

Projekty založené zhruba od poloviny 2024 už automaticky nedávají výchozímu
Compute service accountu roli Editor, takže `gcloud builds submit` bez tohoto
kroku spadne na `AccessDeniedException: ... does not have storage.objects.get
access`:

```bash
PROJECT_NUMBER=$(gcloud projects describe $GCP_PROJECT --format="value(projectNumber)")

gcloud projects add-iam-policy-binding $GCP_PROJECT \
  --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
  --role="roles/cloudbuild.builds.builder"
```

## 2. Service account pro job

Samostatný SA jen pro tento job, založený přímo v `$GCP_PROJECT`.

```bash
gcloud iam service-accounts create $SA_NAME \
  --project=$GCP_PROJECT \
  --display-name="Rankscale Extract Cloud Run Job"

gcloud projects add-iam-policy-binding $GCP_PROJECT \
  --member="serviceAccount:${SA_NAME}@${GCP_PROJECT}.iam.gserviceaccount.com" \
  --role="roles/bigquery.dataEditor"

gcloud projects add-iam-policy-binding $GCP_PROJECT \
  --member="serviceAccount:${SA_NAME}@${GCP_PROJECT}.iam.gserviceaccount.com" \
  --role="roles/bigquery.jobUser"

gcloud projects add-iam-policy-binding $GCP_PROJECT \
  --member="serviceAccount:${SA_NAME}@${GCP_PROJECT}.iam.gserviceaccount.com" \
  --role="roles/run.invoker"
```

(`roles/run.invoker` se hodí až u kroku 6 pro Cloud Scheduler, ale je jednodušší
přidat ho rovnou tady se zbytkem.)

Ověř, že SA opravdu vznikl v `$GCP_PROJECT`:

```bash
gcloud iam service-accounts describe "${SA_NAME}@${GCP_PROJECT}.iam.gserviceaccount.com" \
  --project=$GCP_PROJECT
```

## 3. Rankscale API klíč do Secret Manageru

```bash
printf "%s" "rk_tvuj_skutecny_klic" | gcloud secrets create rankscale-api-key \
  --project=$GCP_PROJECT --data-file=-

gcloud secrets add-iam-policy-binding rankscale-api-key --project=$GCP_PROJECT \
  --member="serviceAccount:${SA_NAME}@${GCP_PROJECT}.iam.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"
```

**Nahraď `rk_tvuj_skutecny_klic` svým reálným Rankscale API klíčem** (Rankscale
dashboard → Settings/API, klíč začíná `rk_`) — ne placeholderem z tohoto návodu.
Ověř, že se uložil správně:

```bash
gcloud secrets versions access latest --secret=rankscale-api-key --project=$GCP_PROJECT
```

Musí vypsat tvůj skutečný klíč. Použij vždy `printf`, ne `echo` — `echo` přidá
na konec znak nového řádku, který Rankscale API odmítne jako neplatný token
(`401 Unauthorized`). Oprava přes novou verzi:

```bash
gcloud secrets versions add rankscale-api-key --project=$GCP_PROJECT \
  --data-file=<(printf "%s" "rk_tvuj_skutecny_klic")
```

## 3b. BigQuery dataset a tabulky

Skript (`bq_append`) očekává, že dataset a tabulky `raw_*` už existují — sám
je nezakládá:

```bash
bq --project_id=$GCP_PROJECT mk --dataset --location=EU ${GCP_PROJECT}:RankScaleDashboard

bq query --project_id=$GCP_PROJECT --use_legacy_sql=false < src/schema_raw.sql
```

`src/schema_raw.sql` nemá project ID natvrdo zapsané — `--project_id`
určí, do kterého projektu se tabulky založí, takže při přesunu na jiný projekt
stačí mít správně nastavené `$GCP_PROJECT` a soubor spustit beze změny.

Pokud dataset už existuje, `bq mk` ohlásí `Dataset already exists` — to je
neškodné, pokračuj rovnou na `bq query`.

Všechny `CREATE TABLE` jsou `IF NOT EXISTS`, takže `bq query < src/schema_raw.sql`
je bezpečné spustit znovu i na už běžícím prostředí (např. po přidání nové
tabulky do souboru) — existující tabulky se nedotkne, jen založí ty chybějící.

## 4. Build image a push do Artifact Registry

Build context je složka `src/` (tam je `Dockerfile` a vše, co `COPY` potřebuje),
příkaz spouštěj z kořene repozitáře:

```bash
gcloud artifacts repositories create $REPO \
  --repository-format=docker --location=$REGION --project=$GCP_PROJECT

gcloud builds submit src --project=$GCP_PROJECT \
  --tag "${REGION}-docker.pkg.dev/${GCP_PROJECT}/${REPO}/rankscale-extract:latest"
```

Zkratka: `make deploy-build`.

## 5. Vytvoření Cloud Run Job

Cílový projekt a dataset (`GCP_PROJECT`, `BQ_DATASET`), které uvidí samotný
skript, se **nepíšou do příkazu ručně** — jsou v [`src/env.yaml`](src/env.yaml),
`--env-vars-file=src/env.yaml` je rovnou načte.

```bash
gcloud run jobs create rankscale-extract --project=$GCP_PROJECT \
  --image="${REGION}-docker.pkg.dev/${GCP_PROJECT}/${REPO}/rankscale-extract:latest" \
  --region=$REGION \
  --service-account="${SA_NAME}@${GCP_PROJECT}.iam.gserviceaccount.com" \
  --env-vars-file=src/env.yaml \
  --set-secrets="RANKSCALE_API_KEY=rankscale-api-key:latest" \
  --max-retries=1 \
  --task-timeout=1200
```

Proměnné:

| Proměnná | Kde se nastavuje |
|---|---|
| `GCP_PROJECT`, `BQ_DATASET` | v souboru `src/env.yaml` (uprav a ulož) |
| `RANKSCALE_API_KEY` | Secret Manager, mountnutý přes `--set-secrets` (krok 3) |
| `BACKFILL_WEEKS` | volitelné, jen pro backfill, viz níže — nastavuje se zvlášť při konkrétním spuštění |

Když později změníš `src/env.yaml` (jiný dataset), aplikuješ to na existující job:

```bash
gcloud run jobs update rankscale-extract --project=$GCP_PROJECT \
  --region=$REGION --env-vars-file=src/env.yaml
```

### Ruční spuštění / test

```bash
gcloud run jobs execute rankscale-extract --project=$GCP_PROJECT --region=$REGION
```

Zkratka: `make execute`.

### Backfill

Jednorázově přepíše env proměnnou jen pro tento konkrétní run:

```bash
gcloud run jobs execute rankscale-extract --project=$GCP_PROJECT --region=$REGION \
  --update-env-vars="BACKFILL_WEEKS=52"
```

Zkratka: `make backfill` (výchozí `WEEKS=52`, jinak `make backfill WEEKS=10`).

## 6. Denní spouštění přes Cloud Scheduler

Scheduler job zakládej ve stejném `$GCP_PROJECT` jako Cloud Run Job a SA.

```bash
gcloud scheduler jobs create http rankscale-extract-daily \
  --project=$GCP_PROJECT \
  --location=$REGION \
  --schedule="30 6 * * *" \
  --uri="https://${REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${GCP_PROJECT}/jobs/rankscale-extract:run" \
  --http-method=POST \
  --oauth-service-account-email="${SA_NAME}@${GCP_PROJECT}.iam.gserviceaccount.com" \
  --time-zone="UTC"
```

(`roles/run.invoker` pro tenhle SA je už přidaný z kroku 2.)

Otestuj rovnou ostrým triggerem (ne jen `gcloud run jobs execute`, ať víš, že
zítřejší automatický běh přes Scheduler projde):

```bash
gcloud scheduler jobs run rankscale-extract-daily --project=$GCP_PROJECT --location=$REGION

sleep 30

gcloud run jobs executions list --job=rankscale-extract --project=$GCP_PROJECT --region=$REGION --limit=3 \
  --format="table(metadata.name,status.startTime,status.completionTime,status.conditions[0].status)"
```

Hledej execution s časem odpovídajícím spuštění scheduleru a `STATUS: True`.

## 7. Aktualizace image po změně kódu

```bash
gcloud builds submit src --project=$GCP_PROJECT \
  --tag "${REGION}-docker.pkg.dev/${GCP_PROJECT}/${REPO}/rankscale-extract:latest"

gcloud run jobs update rankscale-extract --project=$GCP_PROJECT \
  --image="${REGION}-docker.pkg.dev/${GCP_PROJECT}/${REPO}/rankscale-extract:latest" \
  --region=$REGION
```

Zkratka pro oba příkazy najednou: `make deploy`.

## 8. E-mailová notifikace při selhání

Skript sám žádné e-maily neposílá (žádné SMTP klíče v kódu/secretech) —
místo toho se napojí Cloud Monitoring přímo na výsledek Cloud Run Job
executions, což je pro tenhle případ jednodušší a spolehlivější.

Nejdřív notifikační kanál (e-mail, na který mají chodit alerty):

```bash
gcloud beta monitoring channels create \
  --project=$GCP_PROJECT \
  --display-name="Rankscale Extract – email" \
  --type=email \
  --channel-labels=email_address=TVUJ_EMAIL@example.com
```

Výstup obsahuje `name` ve tvaru `projects/$GCP_PROJECT/notificationChannels/NNNNNNN`
— tohle číslo (`NNNNNNN`) potřebuješ v dalším kroku.

Pak alerting policy, která hlídá built-in metriku Cloud Run Jobs
(`run.googleapis.com/job/completed_execution_count` s labelem `result=failed`)
a při jakémkoliv failed execution pošle e-mail:

```bash
cat > /tmp/rankscale-alert-policy.json <<'EOF'
{
  "displayName": "rankscale-extract: execution failed",
  "combiner": "OR",
  "conditions": [{
    "displayName": "Failed execution",
    "conditionThreshold": {
      "filter": "resource.type=\"cloud_run_job\" AND resource.labels.job_name=\"rankscale-extract\" AND metric.type=\"run.googleapis.com/job/completed_execution_count\" AND metric.labels.result=\"failed\"",
      "comparison": "COMPARISON_GT",
      "thresholdValue": 0,
      "duration": "0s",
      "aggregations": [{
        "alignmentPeriod": "300s",
        "perSeriesAligner": "ALIGN_COUNT",
        "crossSeriesReducer": "REDUCE_SUM"
      }]
    }
  }],
  "notificationChannels": ["projects/$GCP_PROJECT/notificationChannels/NNNNNNN"]
}
EOF

# nahraď NNNNNNN skutečným ID kanálu z předchozího kroku
gcloud alpha monitoring policies create \
  --project=$GCP_PROJECT \
  --policy-from-file=/tmp/rankscale-alert-policy.json
```

Od teď: jakmile execution skončí jako `failed` (tj. `sys.exit(1)` ve skriptu),
přijde e-mail na zadanou adresu. Detail chyby (který brand, jaká výjimka) je
buď v Cloud Loggingu (viz níže), nebo v tabulce `etl_runs` (viz krok o
run logu výše — sloupec `error_message`).

---

## Monitoring a logy

- **Cloud Console → Cloud Run → Jobs → rankscale-extract → Executions** — historie běhů, exit kódy
- **Cloud Logging** (`resource.type="cloud_run_job"`) — stdout/stderr ze skriptu
- **Cloud Logging** (`resource.type="cloud_scheduler_job"`) — historie pokusů o spuštění, `status: {}` = úspěch, jinak obsahuje chybu
- **BigQuery `{GCP_PROJECT}.{BQ_DATASET}.etl_runs`** — jeden řádek per spuštění skriptu
  (`started_at`, `finished_at`, `mode`, `status`, `brands_total`, `brands_failed`,
  `rows_written`, `error_message`). Zapisuje se na konci každého běhu, ať dopadl
  jakkoliv (viz `log_run()` v `src/rankscale_extract_gcp.py`). Rychlý přehled:
  ```sql
  SELECT * FROM `RankScaleDashboard.etl_runs` ORDER BY started_at DESC LIMIT 20
  ```
- Neúspěšný brand (chyba API/BQ) se loguje, ale extract pokračuje na dalších brandech;
  pokud selhal **alespoň jeden**, celý job skončí s `exit(1)` → execution je označená
  **Failed** a spustí e-mailový alert z kroku 8.

## Smazání dat (truncate)

**Nevratně** smaže všechna data ve všech tabulkách (`raw_*` i `etl_runs`), schéma
zůstává — pro znovunahrání dat od nuly (např. po chybném backfillu):

```bash
make truncate-tables CONFIRM=yes
```

Bez `CONFIRM=yes` cíl skončí chybou a nic nesmaže — je to schválně, ať se nedá
spustit omylem.

## Troubleshooting

| Chyba | Příčina | Oprava |
|---|---|---|
| `requests.exceptions.HTTPError: 401 ... rankscale.ai/v1/metrics/brands` | V Secret Manageru je placeholder klíč, nebo klíč s nadbytečným `\n` z `echo` | `gcloud secrets versions access latest --secret=rankscale-api-key --project=$GCP_PROJECT` — over hodnotu; oprav přes `gcloud secrets versions add ...` (krok 3) |
| `FAILED_PRECONDITION: Billing account for project '...' is not found` | Projekt nemá připojený billing účet | `gcloud billing projects link $GCP_PROJECT --billing-account=...` (krok 1) |
| `gcloud builds submit`: `AccessDeniedException: ... does not have storage.objects.get access` | U nových projektů (2024+) chybí výchozímu Compute SA role potřebná pro Cloud Build bucket | Grantni `roles/cloudbuild.builds.builder` compute SA (krok 1, sekce "Cloud Build oprávnění") |
| `gcloud run jobs create`: `Permission 'iam.serviceaccounts.actAs' denied` | SA a Cloud Run Job/Scheduler jsou v různých projektech | Založ SA přímo v `$GCP_PROJECT`, kde vytváříš job/scheduler (krok 2) |
| `google.api_core.exceptions.Forbidden: 403 ... User does not have bigquery.jobs.create permission` | Service account byl založený/oprávněný v jiném projektu, než do kterého `src/env.yaml` píše | `gcloud iam service-accounts describe "${SA_NAME}@${GCP_PROJECT}.iam.gserviceaccount.com" --project=$GCP_PROJECT` ověří, kde SA vznikl |
| Scheduler log `PERMISSION_DENIED` / `403` bez detailu, žádná nová execution | Cloud Run Job, na který Scheduler cílí, ve skutečnosti neexistuje v tom projektu | `gcloud run jobs describe rankscale-extract --project=$GCP_PROJECT --region=$REGION` ověří, jestli job v cílovém projektu vůbec je |
| `404 Not found: Dataset ...` nebo `Table ... not found` | Dataset/tabulky v cílovém projektu ještě nevznikly | krok 3b — `bq mk` + `bq query < src/schema_raw.sql` |
| `bq: command not found` / `xxd: command not found` | Cloud Shell nemá `xxd` předinstalované | Použij `od -c` místo `xxd` |
| Prázdný výstup `echo $GCP_PROJECT ...` | `export` proměnné platí jen v aktuální session/kartě terminálu | Spusť `export` řádky z kroku 1 znovu |

## Lint

Kód se kontroluje přes [`ruff`](https://docs.astral.sh/ruff/) (konfigurace v
[`pyproject.toml`](pyproject.toml)):

```bash
pip install -r requirements-dev.txt
ruff check .
# nebo: make lint
```

Jen `check` (chyby, nepoužité importy, apod.) — `ruff format` se záměrně
nepoužívá, přepsal by ručně zarovnané klíče/`=` v `src/rankscale_extract_gcp.py`
do jiného stylu.

## Lokální test image

```bash
docker build -t rankscale-extract-local src

docker run --rm \
  -e RANKSCALE_API_KEY=rk_tvuj_klic \
  -e GCP_PROJECT=rankscale \
  -e BQ_DATASET=RankScaleDashboard \
  -v ~/.config/gcloud:/root/.config/gcloud:ro \
  rankscale-extract-local
```

Zkratka (stejné proměnné, ale čtené ze shellu): `RANKSCALE_API_KEY=rk_tvuj_klic GCP_PROJECT=rankscale BQ_DATASET=RankScaleDashboard make run`.

(Mount `~/.config/gcloud` funguje jen pokud máš lokálně `gcloud auth application-default login`
a image běží jako root — pro rychlý lokální test stačí, pro produkci se auth řeší
přes service account Cloud Run Jobu, viz výše.)
