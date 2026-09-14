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
export SA_NAME=rankscale-extract-job
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
    --project=$GCP_PROJECT
  ```
- [ ] Cloud Build oprávnění pro výchozí Compute SA (nutné u projektů založených 2024+):
  ```bash
  PROJECT_NUMBER=$(gcloud projects describe $GCP_PROJECT --format="value(projectNumber)")
  gcloud projects add-iam-policy-binding $GCP_PROJECT \
    --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
    --role="roles/cloudbuild.builds.builder"
  ```

## 2. Service account pro job

- [ ] Založit SA přímo v `$GCP_PROJECT`:
  ```bash
  gcloud iam service-accounts create $SA_NAME \
    --project=$GCP_PROJECT \
    --display-name="Rankscale Extract Cloud Run Job"
  ```
- [ ] Oprávnění (BigQuery + Cloud Run invoke):
  ```bash
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

## 5. `src/env.yaml`

- [ ] Uprav a ulož, ať odpovídá tomuhle projektu:
  ```yaml
  GCP_PROJECT: <stejné jako $GCP_PROJECT>
  BQ_DATASET: RankScaleDashboard
  ```

## 6. Build image a Cloud Run Job

- [ ] Artifact Registry repo + build:
  ```bash
  gcloud artifacts repositories create $REPO \
    --repository-format=docker --location=$REGION --project=$GCP_PROJECT

  make deploy-build
  ```
- [ ] Vytvořit job:
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
- [ ] Ověřit ručním spuštěním:
  ```bash
  make execute
  ```
  a zkontrolovat v BQ:
  ```sql
  SELECT * FROM `RankScaleDashboard.etl_runs` ORDER BY started_at DESC LIMIT 1
  ```
  (`status = success`)

## 7. Cloud Scheduler (denní spouštění)

- [ ] Založit scheduler job (stejný `$GCP_PROJECT` jako job i SA):
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
