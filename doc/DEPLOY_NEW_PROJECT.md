# Nasazení do nového GCP projektu — checklist

Stručný bodový postup pro nasazení tohoto repozitáře do **nového, prázdného**
GCP projektu. Detaily/vysvětlení k jednotlivým krokům jsou v hlavním
[README.md](../README.md) — tenhle soubor je jen sekvence příkazů k odškrtání.

Předpoklad: máš `git clone` tohoto repa a jsi v jeho kořeni (`cd rankscale-cloudrun`).

---

## 0. Proměnné (nastav na začátku, platí jen pro tuhle terminálovou session)

```bash
export GCP_PROJECT=tvuj-projekt-id      # skutečné project ID, ne "hezký" název
export REGION=europe-west3              # nebo jiný region blízko tebe
export REPO=rankscale
export SA_NAME=rankscale-extract-job        # runtime SA (Cloud Run Job)
export SCHEDULER_SA_NAME=rankscale-scheduler # samostatná SA jen pro Cloud Scheduler
```

## 1. Příprava projektu

- [ ] Projekt má připojený billing účet:
  ```bash
  gcloud billing accounts list
  gcloud billing projects link $GCP_PROJECT --billing-account=BILLING_ACCOUNT_ID
  ```
- [ ] Aktivní `gcloud config` odpovídá `$GCP_PROJECT`:
  ```bash
  gcloud config set project $GCP_PROJECT
  gcloud config get-value project
  ```
- [ ] Zapnuté potřebné API:
  ```bash
  gcloud services enable \
    run.googleapis.com \
    cloudscheduler.googleapis.com \
    artifactregistry.googleapis.com \
    secretmanager.googleapis.com \
    bigquery.googleapis.com \
    cloudbuild.googleapis.com \
    containerscanning.googleapis.com \
    --project=$GCP_PROJECT
  ```
  (`containerscanning.googleapis.com` = automatický vulnerability scanning
  image v Artifact Registry, žádný další krok nevyžaduje.)
- [ ] Cloud Build oprávnění pro výchozí Compute SA (nutné u projektů založených 2024+):
  ```bash
  PROJECT_NUMBER=$(gcloud projects describe $GCP_PROJECT --format="value(projectNumber)")
  gcloud projects add-iam-policy-binding $GCP_PROJECT \
    --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
    --role="roles/cloudbuild.builds.builder"
  ```

## 2. Service accounts (runtime + scheduler)

Dvě oddělené identity — scheduler nemá důvod sahat na BigQuery, runtime
nemá důvod spouštět Cloud Run joby.

- [ ] Založit runtime SA přímo v `$GCP_PROJECT`:
  ```bash
  gcloud iam service-accounts create $SA_NAME \
    --project=$GCP_PROJECT \
    --display-name="Rankscale Extract Cloud Run Job"
  ```
- [ ] Runtime SA — jen `bigquery.jobUser` (projekt-level, musí být —
  BigQuery joby se spouští per-projekt). `bigquery.dataEditor` přijde
  až v kroku 4, na úrovni datasetu, až dataset existuje:
  ```bash
  gcloud projects add-iam-policy-binding $GCP_PROJECT \
    --member="serviceAccount:${SA_NAME}@${GCP_PROJECT}.iam.gserviceaccount.com" \
    --role="roles/bigquery.jobUser"
  ```
- [ ] Založit scheduler SA (role přijde v kroku 7, až na konkrétní job):
  ```bash
  gcloud iam service-accounts create $SCHEDULER_SA_NAME \
    --project=$GCP_PROJECT \
    --display-name="Rankscale Extract Cloud Scheduler"
  ```

## 3. Rankscale API klíč do Secret Manageru

- [ ] Vytvořit secret (klíč z Rankscale dashboard → Settings/API, začíná `rk_`):
  ```bash
  printf "%s" "rk_tvuj_skutecny_klic" | gcloud secrets create rankscale-api-key \
    --project=$GCP_PROJECT --data-file=-
  ```
- [ ] Oprávnění pro SA:
  ```bash
  gcloud secrets add-iam-policy-binding rankscale-api-key --project=$GCP_PROJECT \
    --member="serviceAccount:${SA_NAME}@${GCP_PROJECT}.iam.gserviceaccount.com" \
    --role="roles/secretmanager.secretAccessor"
  ```
- [ ] Ověřit bez vypsání hodnoty na obrazovku — viz [API_KEY_ROTATION.md](API_KEY_ROTATION.md), sekce 3.

## 4. BigQuery dataset a tabulky

- [ ] Založit dataset:
  ```bash
  bq --project_id=$GCP_PROJECT mk --dataset --location=EU ${GCP_PROJECT}:RankScaleDashboard
  ```
- [ ] Založit tabulky (`topic_metrics_history`, `etl_runs`):
  ```bash
  bq query --project_id=$GCP_PROJECT --use_legacy_sql=false < src/schema_raw.sql
  ```
- [ ] Dát runtime SA `bigquery.dataEditor` jen na tenhle dataset (ne na
  celý projekt):
  ```bash
  bq add-iam-policy-binding \
    --member="serviceAccount:${SA_NAME}@${GCP_PROJECT}.iam.gserviceaccount.com" \
    --role="roles/bigquery.dataEditor" \
    ${GCP_PROJECT}:RankScaleDashboard
  ```

## 5. `src/env.yaml`

- [ ] Uprav a ulož, ať odpovídá tomuhle projektu:
  ```yaml
  GCP_PROJECT: <stejné jako $GCP_PROJECT>
  BQ_DATASET: RankScaleDashboard
  ```

## 6. Build image a Cloud Run Job

- [ ] Artifact Registry repo (immutable tags — žádný tag v repu, vč.
  `latest`, se nedá přepsat) + build (tag podle git SHA, `make deploy-build`
  to řeší automaticky přes `TAG` v `Makefile`):
  ```bash
  gcloud artifacts repositories create $REPO \
    --repository-format=docker --location=$REGION --project=$GCP_PROJECT \
    --immutable-tags

  export TAG=$(git rev-parse --short HEAD)
  make deploy-build
  ```
- [ ] Vytvořit job:
  ```bash
  gcloud run jobs create rankscale-extract --project=$GCP_PROJECT \
    --image="${REGION}-docker.pkg.dev/${GCP_PROJECT}/${REPO}/rankscale-extract:${TAG}" \
    --region=$REGION \
    --service-account="${SA_NAME}@${GCP_PROJECT}.iam.gserviceaccount.com" \
    --env-vars-file=src/env.yaml \
    --set-secrets="RANKSCALE_API_KEY=rankscale-api-key:latest" \
    --max-retries=1 \
    --task-timeout=1200
  ```
- [ ] Ověřit ručním spuštěním:
  ```bash
  make execute
  ```
  a zkontrolovat v BQ:
  ```sql
  SELECT * FROM `RankScaleDashboard.etl_runs`
  WHERE status != 'running' ORDER BY started_at DESC LIMIT 1
  ```
  (`status = success`; každý běh zapisuje i dočasný `status = "running"`
  řádek se stejným `run_id`, viz README sekce "Monitoring a logy")

## 7. Cloud Scheduler (denní spouštění)

- [ ] Dát scheduler SA `run.invoker` jen na tenhle job (ne na projekt):
  ```bash
  gcloud run jobs add-iam-policy-binding rankscale-extract \
    --project=$GCP_PROJECT --region=$REGION \
    --member="serviceAccount:${SCHEDULER_SA_NAME}@${GCP_PROJECT}.iam.gserviceaccount.com" \
    --role="roles/run.invoker"
  ```
- [ ] Založit scheduler job (stejný `$GCP_PROJECT` jako job, scheduler SA
  z kroku 2):
  ```bash
  gcloud scheduler jobs create http rankscale-extract-daily \
    --project=$GCP_PROJECT \
    --location=$REGION \
    --schedule="30 6 * * *" \
    --uri="https://${REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${GCP_PROJECT}/jobs/rankscale-extract:run" \
    --http-method=POST \
    --oauth-service-account-email="${SCHEDULER_SA_NAME}@${GCP_PROJECT}.iam.gserviceaccount.com" \
    --time-zone="UTC"
  ```
- [ ] Otestovat ostrým triggerem:
  ```bash
  gcloud scheduler jobs run rankscale-extract-daily --project=$GCP_PROJECT --location=$REGION
  ```

## 8. E-mailový alert při selhání

- [ ] Notification channel + alerting policy — viz [README.md, krok 8](../README.md#8-e-mailová-notifikace-při-selhání).

## 9. Finální kontrola

- [ ] `SELECT COUNT(*) FROM topic_metrics_history` > 0
- [ ] `topic_name`/`entity_name` v tabulce odpovídají tomu, co vidíš v Rankscale UI
- [ ] Scheduler má `state: ENABLED` a `httpTarget.uri` cílí na správný projekt/job
- [ ] E-mailový alert funguje (vyzkoušet uměle shozeným runem, nebo věřit konfiguraci)
