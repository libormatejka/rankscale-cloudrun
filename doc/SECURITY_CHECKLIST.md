# Bezpečnostní checklist — GCP Cloud Run Job nasazení

Nálezy z bezpečnostního review nasazení `rankscale-extract` (Cloud Run Job,
projekt `rankscale`). Řazeno podle závažnosti. Odškrtávej, jak se řeší.

Kontext: dvě nezávislé pipeline běží paralelně — GitHub Actions
(`libor-matejkacz.RankScaleDashboard`, produkce) a tento Cloud Run Job
(`rankscale.RankScaleDashboard`, GCP varianta).

---

## 🔴 Kritické

- [x] **SQL injection v `bq_max_snapshot()`** (funkce od té doby z kódu
      **odstraněná** — patřila k `raw_brand_snapshots`, která se v rámci
      přechodu na `topic_metrics_history` přestala plnit; odkaz na řádek níže
      je tak jen historický, ne aktuální).
      `brand_id` šel f-stringem přímo do SQL dotazu bez parametrizace.
      Dnes pocházel z Rankscale API (důvěryhodné), ale žádný security review
      tuhle domněnku neakceptuje.
      **Fix:** `bigquery.ScalarQueryParameter` + `QueryJobConfig(query_parameters=...)`.
      Opraveno v tomto repozitáři i v paralelní GitHub Actions pipeline
      (`rankscale_extract.py` v jiném repozitáři, stejný bug byl i tam). Nový
      image nasazen a otestován (`rankscale-extract-q67hv`, `Completed: True`).

- [x] **Výchozí Compute service account má `roles/editor` na projektu `rankscale`**
      (`<PROJECT_NUMBER>-compute@developer.gserviceaccount.com`). Tenhle SA reálně
      používá Cloud Build při každém `gcloud builds submit` — kompromitovaný
      build/závislost = Editor práva na celém projektu.
      **Fix:** odebrat `roles/editor`, ponechat jen `roles/cloudbuild.builds.builder`
      + explicitně přidat `roles/artifactregistry.writer` (potřeba pro push image).
      Ověřeno funkčním buildem i deployem bez `roles/editor`.

---

## 🟠 Vysoká závažnost

- [ ] **`roles/bigquery.dataEditor` je na úrovni projektu, ne datasetu**,
      **`roles/run.invoker` je na úrovni projektu, ne konkrétního Cloud Run
      Jobu**, a **Cloud Run Job + Cloud Scheduler sdílí jeden service
      account** — tři propojené nálezy, jedna migrace. SA
      `rankscale-extract-job` dnes může zapisovat do libovolného datasetu
      v `rankscale` (ne jen `RankScaleDashboard`), smí spustit jakýkoliv
      Cloud Run service/job v projektu (ne jen `rankscale-extract`), a
      používá ji zároveň Cloud Scheduler (který žádné BigQuery právo
      nepotřebuje) i runtime (který `run.invoker` nepotřebuje vůbec).
      Rozšiřuje to blast radius při kompromitaci kterékoli ze dvou rolí.

      **Fix — migrace `rankscale` na dvě SA** (nové projekty mají tohle
      už od začátku, viz README krok 2/3b/6 a `doc/DEPLOY_NEW_PROJECT.md`):

      ```bash
      export GCP_PROJECT=rankscale
      export REGION=europe-west3   # over, jaký region job reálně používá
      export SA_NAME=rankscale-extract-job
      export SCHEDULER_SA_NAME=rankscale-scheduler

      # 1. Nová scheduler SA
      gcloud iam service-accounts create $SCHEDULER_SA_NAME \
        --project=$GCP_PROJECT \
        --display-name="Rankscale Extract Cloud Scheduler"

      # 2. run.invoker pro novou SA, jen na konkrétní job (ne projekt)
      gcloud run jobs add-iam-policy-binding rankscale-extract \
        --project=$GCP_PROJECT --region=$REGION \
        --member="serviceAccount:${SCHEDULER_SA_NAME}@${GCP_PROJECT}.iam.gserviceaccount.com" \
        --role="roles/run.invoker"

      # 3. Přepni Cloud Scheduler na novou SA
      gcloud scheduler jobs update http rankscale-extract-daily \
        --project=$GCP_PROJECT --location=$REGION \
        --oauth-service-account-email="${SCHEDULER_SA_NAME}@${GCP_PROJECT}.iam.gserviceaccount.com"

      # 4. Over, že scheduler novou SA reálně použije (ručně spusť, zkontroluj execution)
      gcloud scheduler jobs run rankscale-extract-daily --project=$GCP_PROJECT --location=$REGION
      sleep 30
      gcloud run jobs executions list --job=rankscale-extract --project=$GCP_PROJECT --region=$REGION --limit=1 \
        --format="table(metadata.name,status.conditions[0].status)"

      # 5. Dataset-level bigquery.dataEditor pro runtime SA
      bq add-iam-policy-binding \
        --member="serviceAccount:${SA_NAME}@${GCP_PROJECT}.iam.gserviceaccount.com" \
        --role="roles/bigquery.dataEditor" \
        ${GCP_PROJECT}:RankScaleDashboard

      # 6. Over ruční exekucí, že runtime SA dál umí zapisovat (dataset-level binding funguje)
      gcloud run jobs execute rankscale-extract --project=$GCP_PROJECT --region=$REGION

      # --- Až 4. a 6. prokazatelně projdou (execution STATUS: True), odeber staré project-level bindingy: ---

      # 7. Zruš project-level run.invoker na runtime SA (teď ho má jen scheduler SA, scoped na job)
      gcloud projects remove-iam-policy-binding $GCP_PROJECT \
        --member="serviceAccount:${SA_NAME}@${GCP_PROJECT}.iam.gserviceaccount.com" \
        --role="roles/run.invoker"

      # 8. Zruš project-level bigquery.dataEditor na runtime SA (teď má jen dataset-level z kroku 5)
      gcloud projects remove-iam-policy-binding $GCP_PROJECT \
        --member="serviceAccount:${SA_NAME}@${GCP_PROJECT}.iam.gserviceaccount.com" \
        --role="roles/bigquery.dataEditor"
      ```

      Po dokončení ověř výsledný stav:
      ```bash
      gcloud projects get-iam-policy $GCP_PROJECT \
        --flatten="bindings[].members" \
        --filter="bindings.members:rankscale-extract-job OR bindings.members:rankscale-scheduler" \
        --format="table(bindings.role,bindings.members)"
      ```
      Runtime SA by tam měla mít jen `bigquery.jobUser` (+ `secretAccessor`
      na secretu, to je mimo tenhle project-level výpis), scheduler SA
      vůbec nic na úrovni projektu (její `run.invoker` je scoped na job,
      taky mimo tenhle výpis).

- [ ] **Duplicitní Rankscale API klíč napříč `libor-matejkacz` a `rankscale`**
      Stejná hodnota klíče leží ve dvou nezávislých Secret Manager instancích —
      dvě místa k rotaci, dvě místa možného úniku, riziko zombie credential po
      rotaci jen v jednom projektu.
      **Fix:** rozhodnout o jednom zdroji pravdy, nebo mít proces, který rotuje
      oba současně.

- [ ] **API klíč byl opakovaně vypsán do terminálu v čistém textu během ladění**
      (`gcloud secrets versions access latest`) — prošel Cloud Shell
      scrollbackem a shell historií.
      **Fix:** rotovat Rankscale API klíč (starý zneplatnit, nový uložit do
      Secret Manageru), spustit `history -c` v Cloud Shellu.

      **Jak příště ladit secrets bez toho, aby se vypsaly na obrazovku:**
      - Funguje/nefunguje → test proti API, ne výpis hodnoty:
        ```bash
        curl -s -o /dev/null -w "%{http_code}\n" \
          -H "Authorization: Bearer $(gcloud secrets versions access latest --secret=rankscale-api-key --project=$GCP_PROJECT)" \
          "https://rankscale.ai/v1/metrics/brands?limit=1"
        ```
        Hodnota jde přímo do `curl`, na terminál se propíše jen HTTP status.
      - Délka/formát (např. nadbytečný `\n` z `echo` místo `printf`) → jen počet bajtů:
        ```bash
        gcloud secrets versions access latest --secret=rankscale-api-key --project=$GCP_PROJECT | wc -c
        ```
      - Je to pořád placeholder? → porovnání hashů, ne hodnot:
        ```bash
        gcloud secrets versions access latest --secret=rankscale-api-key --project=$GCP_PROJECT | sha256sum
        # porovnej s: printf "rk_tvuj_klic" | sha256sum
        ```
      - Nutná vizuální kontrola → jen prefix, nikdy celá hodnota:
        ```bash
        gcloud secrets versions access latest --secret=rankscale-api-key --project=$GCP_PROJECT | cut -c1-5
        ```
      - Kolem případného výjimečného výpisu vypnout shell historii:
        ```bash
        set +o history
        # citlivý příkaz
        set -o history
        ```
      Klíč, který už jednou na obrazovku vypsaný byl, se považuje za
      kontaminovaný bez ohledu na následnou opatrnost — rotuje se, ne "možná
      to nikdo neviděl".

- [ ] **`roles/owner` na jednom osobním účtu bez separace rolí**
      Osobní účet má Owner na obou projektech a dělá vývoj, deploy
      i IAM správu — single point of failure, no separation of duties.
      **Fix:** v korporátním provozu rozdělit na least-privilege role
      (deployer / data steward / security admin) podle potřeby.

- [x] **Souběh dvou exekucí Cloud Run Jobu s `WRITE_TRUNCATE`**
      `maxRetries=1` + `taskCount=1` je OK pro jednu exekuci, ale nic nebrání
      dvěma *souběžným* exekucím (např. manuální `make execute`/`gcloud run
      jobs execute` zrovna když běží scheduler). Při `WRITE_TRUNCATE` na
      `topic_metrics_history` tak může dojít k přepsání kompletních dat
      nekompletním výsledkem, nebo k výsledku závislém na pořadí dokončení.
      **Fix (hotovo):** advisory lock přes BigQuery — `mark_run_started()`
      zapíše na začátku běhu `status = "running"` řádek do `etl_runs`,
      `check_no_concurrent_run()` před extraktem zkontroluje, že neběží
      jiný `run_id` s `status = "running"` novější než
      `LOCK_STALE_AFTER_SECONDS` (30 min). Best-effort (ne transakční
      garance pro běhy odstartované během pár sekund od sebe), ale
      spolehlivě kryje reálný scénář (scheduler + ruční spuštění s
      odstupem desítek sekund/minut). Žádná nová GCP infrastruktura.
      (Nalezeno v externím review 2026-10.)

- [ ] **Cloud Build používá defaultní Compute Engine SA sdílený s jinými workloady**
      I po odebrání `roles/editor` (viz kritický nález výše) zůstává Cloud
      Build na stejné identitě, kterou mohou v budoucnu používat i jiné
      workloady v projektu (VM instance apod.) — spojuje odpovědnosti různých
      funkcí do jedné identity.

      **Fix:** dedikovaná build SA:
      ```bash
      gcloud iam service-accounts create rankscale-build \
        --project=$GCP_PROJECT --display-name="Rankscale Cloud Build"

      gcloud projects add-iam-policy-binding $GCP_PROJECT \
        --member="serviceAccount:rankscale-build@${GCP_PROJECT}.iam.gserviceaccount.com" \
        --role="roles/cloudbuild.builds.builder"

      gcloud projects add-iam-policy-binding $GCP_PROJECT \
        --member="serviceAccount:rankscale-build@${GCP_PROJECT}.iam.gserviceaccount.com" \
        --role="roles/artifactregistry.writer"
      ```
      Build pak spustíš s `--service-account`:
      ```bash
      gcloud builds submit src --project=$GCP_PROJECT \
        --service-account="projects/${GCP_PROJECT}/serviceAccounts/rankscale-build@${GCP_PROJECT}.iam.gserviceaccount.com" \
        --tag "${REGION}-docker.pkg.dev/${GCP_PROJECT}/${REPO}/rankscale-extract:${TAG}"
      ```
      **Nevyzkoušeno naživo, ověř prvním buildem:** Cloud Build s vlastní
      (ne defaultní) SA typicky vyžaduje buď nastavit projektové Cloud
      Build logování na "Cloud Logging only" (Cloud Console → Cloud Build
      → Settings → Default logging), nebo explicitní `--gcs-log-dir` na
      bucket, kam má `rankscale-build` přístup — bez toho build může
      spadnout na chybě o přístupu k výchozímu GCS log bucketu (ten vlastní
      defaultní Compute SA, ne tahle nová identita). Kvůli tomu tenhle krok
      zatím **není** zapojený do `Makefile`/README jako výchozí — je to
      opt-in hardening, ne součást základního postupu, dokud není ověřený.
      (Nalezeno v externím review 2026-10.)

---

## 🟡 Střední závažnost

- [x] **Image bez pinnutí na digest** — `src/Dockerfile` už používá
      `FROM python:3.12-slim@sha256:...`.
      **Vulnerability scanning — zapnout na existujícím projektu:**
      ```bash
      gcloud services enable containerscanning.googleapis.com --project=$GCP_PROJECT
      ```
      Jednorázové, běží pak automaticky na pozadí pro všechny budoucí push
      do Artifact Registry (žádný další krok). Nové projekty to mají v
      `gcloud services enable` hned v `DEPLOY_NEW_PROJECT.md` kroku 1.

- [x] **Cloud Run Job je nasazený na tag `latest`, ne unikátní tag** —
      `Makefile`/README/`DEPLOY_NEW_PROJECT.md` teď tagují image podle
      git SHA (`TAG := $(shell git rev-parse --short HEAD)`), ne `latest`.
      **Zbývá migrovat existující `rankscale` repo:**
      ```bash
      gcloud artifacts repositories update $REPO \
        --project=$GCP_PROJECT --location=$REGION --immutable-tags
      ```
      Jednorázové. Po tomhle přepsání `latest` (nebo jakéhokoliv jiného
      tagu) v repu selže — `make deploy`/`make deploy-build` to neudělají,
      používají už `$(TAG)` z gitu, ale pokud bys ručně spustil starý
      `--tag ...:latest` příkaz z paměti/historie, spadne to na chybě
      immutable tag.
      (Nalezeno v externím review 2026-10.)

- [ ] **Chybí audit logging přístupu k Secret Manageru**
      Neloguje se, kdo/kdy čte `rankscale-api-key`.
      **Fix:** Console-only, žádný spolehlivý `gcloud` příkaz pro tenhle
      konkrétní toggle — IAM & Admin → Audit Logs → najít "Secret Manager
      API" → zaškrtnout **Data Read** (a klidně i Data Write) → Save.
      (Nalezeno v externím review 2026-10.)

- [ ] **Dvě verze secretu `api-key`, nejasné jestli je v1 ještě platná**
      `:latest` ukazuje na verzi 2. Není ověřeno, jestli je verze 1 zneplatněná
      i na straně Rankscale (riziko zombie credential).
      **Fix:**
      ```bash
      gcloud secrets versions list rankscale-api-key --project=$GCP_PROJECT
      ```
      U Rankscale (dashboard → Settings/API) ověř, že starý klíč (verze 1)
      je zrušený. Pokud ano, zneplatni i v GCP (nevratné — `destroy`, ne
      `disable`, pokud je jisté, že se k ní nikdy nebude potřeba vrátit):
      ```bash
      gcloud secrets versions disable 1 --secret=rankscale-api-key --project=$GCP_PROJECT
      ```
      Viz také `doc/API_KEY_ROTATION.md`.
      (Nalezeno v externím review 2026-10.)

- [x] **Kontejner běží jako root**
      `src/Dockerfile` nyní vytváří `appuser` (`useradd --create-home`) a
      spouští entrypoint přes `USER appuser`. Lokální ADC test (`make run`)
      upraven na mount do `/home/appuser/.config/gcloud` (dřív `/root/...`).

- [ ] **Bez VPC Service Controls / síťového perimetru**
      BigQuery, Secret Manager i Cloud Run API jsou dostupné z veřejného
      internetu (jen IAM autentizace, žádná síťová izolace).
      **Fix:** zvážit VPC-SC perimetr kolem BigQuery/Secret Manageru, pokud
      jde o citlivá data.

- [ ] **Šifrování jen Google-managed klíči (ne CMEK)**
      Artifact Registry, Secret Manager i BigQuery běží na defaultním
      Google-managed key.
      **Fix:** zvážit CMEK, pokud to vyžaduje compliance/regulace.

- [x] **`raw_answer_texts` obsahuje syrové AI odpovědi bez klasifikace dat**
      *(neaplikovatelné — tabulka `raw_answer_texts` byla v rámci přechodu na
      `topic_metrics_history` smazaná i s daty, viz README.)*

- [ ] **Dvě nezávislé kopie stejného datasetu bez jasného vlastnictví**
      GitHub Actions → `libor-matejkacz`, Cloud Run Job → `rankscale`. Bez
      definovaného "zdroje pravdy" roste riziko rozjetí dat a nekonzistentní
      access policy.
      **Fix:** rozhodnout, která pipeline je produkční, druhou buď vypnout,
      nebo jasně označit jako staging/test.

---

## 🟢 Nízká / hygiena

- [x] **`max-retries=1` + `WRITE_APPEND` bez idempotency klíče**
      Retry po částečném selhání může vytvořit duplicitní řádky (datová
      integrita / audit trail).
      **Fix (hotovo):** `bq_append()` teď předává `load_table_from_file()`
      deterministické `job_id` (`{RUN_ID}-{tabulka}-{pořadí}`) — pokud by se
      tahle konkrétní load operace zopakovala se stejným ID, BigQuery vrátí
      původní job místo založení druhého (doporučený BigQuery idempotency
      pattern). Pozn.: retry celé Cloud Run Job exekuce (nový proces) má
      vždy nový `RUN_ID`, takže se tím nededuplikují dvě *oddělené* exekuce —
      to je očekávané, každá exekuce má v `etl_runs` svůj vlastní auditní
      řádek.

- [ ] **Cloud Audit Logs "Data Access" pro BigQuery pravděpodobně nejsou zapnuté**
      Defaultně vypnuté kvůli objemu logů — bez nich není vidět, kdo přesně
      četl jaká data.
      **Fix:** Console-only, stejně jako Secret Manager audit logy výše —
      IAM & Admin → Audit Logs → najít "BigQuery API" → zaškrtnout
      **Data Read** → Save. Zváž objem/cenu logů před zapnutím (tahle
      tabulka se čte jen přes dashboard/ad-hoc dotazy, ne vysokofrekvenčně,
      takže by nemělo jít o velký objem).

- [ ] **`BACKFILL_WEEKS` lze přepsat kýmkoliv s `roles/run.developer`**
      Nízké riziko, ale umožňuje neplánovaně zatáhnout až rok historie
      (cost / abuse potential — ve skutečnosti dnes bez efektu, viz
      `README.md` sekce Backfill, ale `run.developer` držitel to neví a
      riziko zůstává, kdyby se backfill v budoucnu stal funkčním).
      **Fix:** zkontroluj, kdo všechno má `roles/run.developer` (nebo širší)
      na `$GCP_PROJECT`:
      ```bash
      gcloud projects get-iam-policy $GCP_PROJECT \
        --flatten="bindings[].members" \
        --filter="bindings.role:roles/run.developer OR bindings.role:roles/run.admin OR bindings.role:roles/owner OR bindings.role:roles/editor" \
        --format="table(bindings.role,bindings.members)"
      ```
      Odeber roli komukoliv, kdo `rankscale-extract` nepotřebuje ručně
      spouštět/upravovat.

- [ ] **Alerting pokrývá jen "failed execution", ne neúplná/chybějící data**
      Současný alert (Cloud Monitoring, `completed_execution_count{result="failed"}`)
      nezachytí: job se vůbec nespustí (scheduler fail), úspěšný běh s
      neúplnými daty, neočekávaně nízký počet řádků, nebo abnormálně dlouhý běh.
      **Fix:**
      - `ALIGN_COUNT` → `ALIGN_SUM`, severity na `error` (GCP)
      - alert na chybějící execution (scheduler se nespustil) (GCP)
      - alert na timeout/retry (GCP)
      - [x] row-count validace přímo ve skriptu (porovnání extrahovaného počtu
        řádků vs. předchozí běh, log/alert při neočekávané odchylce) —
        **hotovo**, `_warn_if_row_count_dropped()` v
        `src/rankscale_extract_gcp.py` porovná počet řádků, které se chystá
        zapsat `extract_topic_metrics_history()`, s počtem řádků, co jsou v
        `topic_metrics_history` teď (z předchozího běhu), a zaloguje
        `WARNING` při poklesu > `ROW_COUNT_DROP_WARN_THRESHOLD` (30 %).
        Zbývá navázat log-based alert na tenhle `WARNING` (GCP, viz níže).
      - ověřit, že alert kanál reálně funguje — v logu jsou fail runy, přišlo
        na ně oznámení? (proces/GCP)
      (Nalezeno v externím review 2026-10.)

---

## ⚪ Otevřené otázky (ne technický nález, nutno rozhodnout)

- [ ] Kdo bude zodpovědný za správu skriptu/deploymentu?
- [ ] Kdo může upravovat skript a dělat redeploy?
- [ ] Zůstane deployment manuální, nebo se zautomatizuje (CI/CD)?
- [ ] Jak bude skript/pipeline reagovat na změnu struktury Rankscale API
      (neočekávaně jiný tvar odpovědi)?
- [ ] Kdo bude rotovat API klíč a podle jakého plánu?
- [ ] Byl ověřen alertingový kanál reálným testem (ne jen konfigurací)?

---

## Priorita (top 3)

1. SQL injection (#1) — čistá, bezriziková oprava v kódu
2. Editor role na default Compute SA (#2) — odebrat, nic dalšího nezávisí
3. Duplicitní + vystavený API klíč (#4, #5) — rotovat
