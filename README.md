# Rankscale → BigQuery — Cloud Run Job

Denní pipeline, která tahá data z Rankscale Metrics API a ukládá je 1:1 do
BigQuery (`raw_*` tabulky). Běží jako **Cloud Run Job** spouštěný z **Cloud
Scheduleru**.

Vznikla jako GCP varianta paralelní pipeline, která běží přes GitHub Actions
v jiném repozitáři (`rankscaleDashboard`) — stejná extract logika
(`rankscale_extract.py`), jen jiná autentizace a spouštěč. Tenhle repozitář
je samostatný a nezávislý, žádné soubory odsud nejsou sdílené s tím druhým.

---

## Proč jiná autentizace než GitHub Actions verze

| | GitHub Actions varianta | Cloud Run Job (tento repozitář) |
|---|---|---|
| **BigQuery auth** | JSON klíč service accountu ze secretu `GCP_SA_JSON` | Application Default Credentials — service account je přiřazený přímo k jobu, žádný klíč se nikam nekopíruje |
| **Rankscale API klíč** | GitHub Secret `RANKSCALE_API_KEY` | GCP Secret Manager, namountovaný jako env proměnná |
| **Chybové chování** | log chyby, pokračuje dál | log chyby, na konci `sys.exit(1)` pokud selhal alespoň jeden brand → Cloud Run execution se označí jako **Failed** |
| **Spouštěč** | `schedule:` v `.yml` workflow | Cloud Scheduler → HTTP trigger na Cloud Run Jobs API |

Business logika (endpoints, transformace řádků, BQ append, skip-if-no-new-data)
je stejná jako v GitHub Actions variantě.

---

## Obsah složky

| Soubor | Účel |
|---|---|
| `rankscale_extract_gcp.py` | samotný extract skript |
| `Dockerfile` | image pro Cloud Run Job |
| `.dockerignore` | vynechá README z buildu |
| `requirements.txt` | Python závislosti (subset — bez `google-auth`, ten už táhne `google-cloud-bigquery`) |
| `env.yaml` | cílový `GCP_PROJECT` + `BQ_DATASET` pro Cloud Run Job (viz krok 5) |
| `schema_raw.sql` | DDL pro `raw_*` tabulky, bez natvrdo zapsaného project ID (viz krok 3b) |

---

## Zlaté pravidlo: VŠECHNO se stejným `--project`

Celý první pokus o nasazení se zkomplikoval tím, že část příkazů spoléhala na
"aktivní projekt" nastavený přes `gcloud config set project`, a ten se v Cloud
Shellu mezi kartami/sessions nenápadně měnil. Výsledek: service account vznikl
v jiném projektu než Cloud Run Job, secret v jiném projektu než job, který ho
potřeboval číst, atd. — samá 401/403 chyba bez zjevné příčiny.

**Proto má od teď každý příkaz v tomto návodu explicitní `--project=$GCP_PROJECT`
(nebo `--project_id=$GCP_PROJECT` u `bq`), i když by teoreticky fungoval i bez
něj.** Nespoléhej na ambientní `gcloud config` — je to nejčastější zdroj potíží
při přesunu na jiný projekt.

Shell proměnné (`$GCP_PROJECT`, `$REGION`, `$REPO`, `$SA_NAME`) z kroku 1 platí
jen v aktuální kartě/session Cloud Shellu. Než spustíš cokoliv z návodu, ověř:

```bash
echo $GCP_PROJECT $REGION $REPO $SA_NAME
```

Pokud je výstup prázdný, spusť `export` řádky z kroku 1 znovu.

### Kam nastavit cílový GCP projekt a BigQuery dataset pro samotný skript

To určuje soubor [`env.yaml`](env.yaml) v této složce:

```yaml
GCP_PROJECT: rankscale
BQ_DATASET: RankScaleDashboard
```

Skript je čte jako `os.environ["GCP_PROJECT"]` / `os.environ["BQ_DATASET"]`
(viz `rankscale_extract_gcp.py`, funkce `tbl()` — tabulky jsou
`{GCP_PROJECT}.{BQ_DATASET}.raw_*`). Při vytváření jobu (krok 5) se soubor
předá přes `--env-vars-file=env.yaml`.

---

## 1. Příprava GCP projektu

Projekt musí mít zapnuté **billing** — bez něj nejdou zapnout Artifact Registry
ani Cloud Build (BigQuery v malém rozsahu běží i bez billingu v sandbox režimu,
což první pokus na chvíli zamaskoval). Ověř/přiřaď:

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

Samostatný SA jen pro tento job, **založený přímo v `$GCP_PROJECT`** — ne v
projektu, kde zrovna běžíš `gcloud` z předchozí session. SA z jiného projektu
sice jde IAM oprávnit napříč projekty, ale Cloud Run Job i Cloud Scheduler pak
při vytváření/aktualizaci vyžadují `iam.serviceAccounts.actAs` přes hranici
projektů, což typicky selže s `PERMISSION_DENIED` i pro vlastníka projektu
(cross-project `actAs` bývá navíc blokované organizační politikou). Proto SA
i všechny navazující resources drž ve stejném `$GCP_PROJECT`.

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

Musí vypsat tvůj skutečný klíč. Pokud jsi secret omylem vytvořil s `echo` místo
`printf` (přidá na konec znak nového řádku, který Rankscale API odmítne jako
neplatný token — projeví se jako `401 Unauthorized` v logu jobu), oprav to
novou verzí:

```bash
gcloud secrets versions add rankscale-api-key --project=$GCP_PROJECT \
  --data-file=<(printf "%s" "rk_tvuj_skutecny_klic")
```

## 3b. BigQuery dataset a tabulky

**Bez tohoto kroku job nemá kam zapisovat — spustí se, ale spadne na zápisu do
BigQuery** (chyba typu `404 Not found: Dataset` nebo `Table not found`).

Skript (`bq_append`) očekává, že dataset a tabulky `raw_*` už existují — sám je
nezakládá. Tenhle GCP projekt je oddělený od paralelní GitHub Actions pipeline
v jiném repozitáři, takže potřebuje **vlastní** dataset a tabulky ve stejném
`$GCP_PROJECT`, kam píše i `env.yaml`:

```bash
bq --project_id=$GCP_PROJECT mk --dataset --location=EU ${GCP_PROJECT}:RankScaleDashboard

bq query --project_id=$GCP_PROJECT --use_legacy_sql=false < schema_raw.sql
```

`schema_raw.sql` v této složce nemá project ID natvrdo zapsané — `--project_id`
určí, do kterého projektu se tabulky založí, takže při přesunu na jiný projekt
stačí mít správně nastavené `$GCP_PROJECT` a soubor spustit beze změny.

Pokud dataset už existuje (např. z předchozího pokusu), `bq mk` ohlásí
`Dataset already exists` — to je neškodné, pokračuj rovnou na `bq query`.

## 4. Build image a push do Artifact Registry

Build se spouští **z kořene tohoto repozitáře**, aby `Dockerfile` a `COPY` cesty
seděly:

```bash
cd rankscale-cloudrun

gcloud artifacts repositories create $REPO \
  --repository-format=docker --location=$REGION --project=$GCP_PROJECT

gcloud builds submit --project=$GCP_PROJECT \
  --tag "${REGION}-docker.pkg.dev/${GCP_PROJECT}/${REPO}/rankscale-extract:latest"
```

## 5. Vytvoření Cloud Run Job

Cílový projekt a dataset (`GCP_PROJECT`, `BQ_DATASET`), které uvidí samotný
skript, se **nepíšou do příkazu ručně** — jsou v [`env.yaml`](env.yaml)
ve stejné složce, `--env-vars-file=env.yaml` je rovnou načte.

```bash
gcloud run jobs create rankscale-extract --project=$GCP_PROJECT \
  --image="${REGION}-docker.pkg.dev/${GCP_PROJECT}/${REPO}/rankscale-extract:latest" \
  --region=$REGION \
  --service-account="${SA_NAME}@${GCP_PROJECT}.iam.gserviceaccount.com" \
  --env-vars-file=env.yaml \
  --set-secrets="RANKSCALE_API_KEY=rankscale-api-key:latest" \
  --max-retries=1 \
  --task-timeout=1200
```

Proměnné:

| Proměnná | Kde se nastavuje |
|---|---|
| `GCP_PROJECT`, `BQ_DATASET` | v souboru `env.yaml` (uprav a ulož) |
| `RANKSCALE_API_KEY` | Secret Manager, mountnutý přes `--set-secrets` (krok 3) |
| `BACKFILL_WEEKS` | volitelné, jen pro backfill, viz níže — nastavuje se zvlášť při konkrétním spuštění |

Když později změníš `env.yaml` (jiný dataset), aplikuješ to na existující job:

```bash
gcloud run jobs update rankscale-extract --project=$GCP_PROJECT \
  --region=$REGION --env-vars-file=env.yaml
```

### Ruční spuštění / test

```bash
gcloud run jobs execute rankscale-extract --project=$GCP_PROJECT --region=$REGION
```

### Backfill

Jednorázově přepíše env proměnnou jen pro tento konkrétní run:

```bash
gcloud run jobs execute rankscale-extract --project=$GCP_PROJECT --region=$REGION \
  --update-env-vars="BACKFILL_WEEKS=52"
```

## 6. Denní spouštění přes Cloud Scheduler

Scheduler job zakládej **ve stejném `$GCP_PROJECT`** jako Cloud Run Job a SA —
scheduler v jiném projektu, který cílí na job/SA v tomhle, narazí na stejný
cross-project `actAs` problém jako v kroku 2.

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

Stejný čas jako GitHub Actions workflow (6:30 UTC) — pokud běží oba, spusť
jen jeden z nich, jinak se data budou appendovat 2×.

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
cd rankscale-cloudrun

gcloud builds submit --project=$GCP_PROJECT \
  --tag "${REGION}-docker.pkg.dev/${GCP_PROJECT}/${REPO}/rankscale-extract:latest"

gcloud run jobs update rankscale-extract --project=$GCP_PROJECT \
  --image="${REGION}-docker.pkg.dev/${GCP_PROJECT}/${REPO}/rankscale-extract:latest" \
  --region=$REGION
```

---

## Monitoring a logy

- **Cloud Console → Cloud Run → Jobs → rankscale-extract → Executions** — historie běhů, exit kódy
- **Cloud Logging** (`resource.type="cloud_run_job"`) — stdout/stderr ze skriptu
- **Cloud Logging** (`resource.type="cloud_scheduler_job"`) — historie pokusů o spuštění, `status: {}` = úspěch, jinak obsahuje chybu
- Neúspěšný brand (chyba API/BQ) se loguje, ale extract pokračuje na dalších brandech;
  pokud selhal **alespoň jeden**, celý job skončí s `exit(1)` → execution je označená **Failed**
  a lze na to navázat alert v Cloud Monitoringu (`Cloud Run Job Execution Failed`).

## Troubleshooting — reálné chyby, na které lze narazit

| Chyba | Příčina | Oprava |
|---|---|---|
| `requests.exceptions.HTTPError: 401 ... rankscale.ai/v1/metrics/brands` | V Secret Manageru je placeholder `rk_tvuj_klic`, nebo klíč s nadbytečným `\n` z `echo` | `gcloud secrets versions access latest --secret=rankscale-api-key --project=$GCP_PROJECT` — over hodnotu; oprav přes `gcloud secrets versions add ...` (krok 3) |
| `FAILED_PRECONDITION: Billing account for project '...' is not found` | Projekt nemá připojený billing účet | `gcloud billing projects link $GCP_PROJECT --billing-account=...` (krok 1) |
| `gcloud builds submit`: `AccessDeniedException: ... does not have storage.objects.get access` | U nových projektů (2024+) chybí výchozímu Compute SA role potřebná pro Cloud Build bucket | Grantni `roles/cloudbuild.builds.builder` compute SA (krok 1, sekce "Cloud Build oprávnění") |
| `gcloud run jobs create`: `Permission 'iam.serviceaccounts.actAs' denied` | SA a Cloud Run Job/Scheduler jsou v **různých** projektech — cross-project `actAs` selže i pro vlastníka projektu | Založ SA přímo v `$GCP_PROJECT`, kde vytváříš job/scheduler (krok 2) — nepoužívej SA z jiného projektu |
| `google.api_core.exceptions.Forbidden: 403 ... User does not have bigquery.jobs.create permission` | Service account byl založený/oprávněný v jiném projektu, než do kterého `env.yaml` píše | `gcloud iam service-accounts describe "${SA_NAME}@${GCP_PROJECT}.iam.gserviceaccount.com" --project=$GCP_PROJECT` ověří, kde SA vznikl |
| Scheduler log `PERMISSION_DENIED` / `403` bez detailu, žádná nová execution | Cloud Run Job, na který Scheduler cílí (`namespaces/$GCP_PROJECT`), ve skutečnosti neexistuje v tom projektu (vznikl jinde) | `gcloud run jobs describe rankscale-extract --project=$GCP_PROJECT --region=$REGION` ověří, jestli job v cílovém projektu vůbec je |
| `404 Not found: Dataset ...` nebo `Table ... not found` | Dataset/tabulky v cílovém projektu ještě nevznikly | krok 3b — `bq mk` + `bq query < schema_raw.sql` |
| `-bash: --env-vars-file=env.yaml: command not found` | Víceřádkový příkaz se zalomením `\` se při kopírování rozdělil na samostatné řádky | Vlož celý příkaz najednou jako blok, nebo použij jednořádkovou verzi bez `\` |
| `bq: command not found` / `xxd: command not found` | Cloud Shell nemá `xxd` předinstalované | Použij `od -c` místo `xxd` |
| Prázdný výstup `echo $GCP_PROJECT ...` | `export` proměnné platí jen v aktuální session/kartě Cloud Shellu | Spusť `export` řádky z kroku 1 znovu v aktuálním terminálu |

## Lokální test image

```bash
cd rankscale-cloudrun
docker build -t rankscale-extract-local .

docker run --rm \
  -e RANKSCALE_API_KEY=rk_tvuj_klic \
  -e GCP_PROJECT=rankscale \
  -e BQ_DATASET=RankScaleDashboard \
  -v ~/.config/gcloud:/root/.config/gcloud:ro \
  rankscale-extract-local
```

(Mount `~/.config/gcloud` funguje jen pokud máš lokálně `gcloud auth application-default login`
a image běží jako root — pro rychlý lokální test stačí, pro produkci se auth řeší
přes service account Cloud Run Jobu, viz výše.)
