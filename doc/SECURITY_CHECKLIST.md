# Bezpečnostní checklist — GCP Cloud Run Job nasazení

Nálezy z bezpečnostního review nasazení `rankscale-extract` (Cloud Run Job,
projekt `rankscale`). Řazeno podle závažnosti. Odškrtávej, jak se řeší.

**`rankscale` (tento repozitář) je produkční pipeline.** Dřívější verze
tohoto checklistu zmiňovala paralelní GitHub Actions pipeline v projektu
`libor-matejkacz` jako "produkci" — to už neplatí (rozhodnutí 2026-10-19).
`libor-matejkacz` je mimo scope tohoto repozitáře a dál se v souvislosti
s ním nic nesynchronizuje ani neřeší.

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

- [x] **`roles/bigquery.dataEditor` je na úrovni projektu, ne datasetu**,
      **`roles/run.invoker` je na úrovni projektu, ne konkrétního Cloud Run
      Jobu**, a **Cloud Run Job + Cloud Scheduler sdílí jeden service
      account** — tři propojené nálezy, vyřešené jednou migrací
      (2026-10-19). Runtime SA `rankscale-extract-job` dřív mohla zapisovat
      do libovolného datasetu v `rankscale`, spustit jakýkoliv Cloud Run
      job v projektu, a sdílela identitu se schedulerem.

      **Hotovo:** nová `rankscale-scheduler` SA s `run.invoker` scoped jen
      na job `rankscale-extract` (ne projekt); Cloud Scheduler na ni
      přepnutý a ověřený ostrým triggerem. Runtime SA `rankscale-extract-job`
      má teď jen `bigquery.jobUser` na úrovni projektu; project-level
      `run.invoker` i `bigquery.dataEditor` byly odebrané.

      **Nečekaná komplikace při migraci:** `bq add-iam-policy-binding`
      (IAM-style binding na dataset) spadla na `This feature requires
      allowlisting` — na `rankscale` není dostupná. Project-level
      `dataEditor` jsme mezitím odebrali dřív, než dataset-level binding
      vůbec existoval → **runtime SA krátce neměla žádný zápis do BigQuery**
      (`403 Permission bigquery.tables.create denied`, jeden `gcloud run
      jobs execute` selhal). Opraveno přes legacy ACL cestu místo IAM
      bindingu:
      ```bash
      bq show --format=prettyjson ${GCP_PROJECT}:RankScaleDashboard \
        | jq '{access: (.access + [{role: "WRITER", userByEmail: "'"${SA_NAME}"'@'"${GCP_PROJECT}"'.iam.gserviceaccount.com"}])}' \
        > /tmp/dataset_access.json

      bq update --source /tmp/dataset_access.json ${GCP_PROJECT}:RankScaleDashboard
      ```
      (`"role": "WRITER"` v ACL = `bigquery.dataEditor`.) Ověřeno ruční
      exekucí po opravě — proběhla úspěšně. README/`DEPLOY_NEW_PROJECT.md`
      teď rovnou používají tenhle ACL postup, ne `bq
      add-iam-policy-binding`, aby na stejnou past nenarazil nový projekt.

      Výsledný IAM stav ověřen:
      ```bash
      gcloud projects get-iam-policy $GCP_PROJECT \
        --flatten="bindings[].members" \
        --filter="bindings.members:rankscale-extract-job OR bindings.members:rankscale-scheduler" \
        --format="table(bindings.role,bindings.members)"
      ```
      Runtime SA: jen `bigquery.jobUser` (project-level) +
      `bigquery.dataEditor` přes dataset ACL (mimo tenhle výpis) +
      `secretAccessor` na secretu. Scheduler SA: nic na úrovni projektu
      (`run.invoker` scoped na job, taky mimo tenhle výpis).

- [x] **Duplicitní Rankscale API klíč napříč `libor-matejkacz` a `rankscale`**
      Stejná hodnota klíče ležela ve dvou nezávislých Secret Manager
      instancích — dvě místa k rotaci, dvě místa možného úniku.
      **Rozhodnutí (2026-10-19): vyřešeno rozsahem.** `libor-matejkacz` je
      mimo scope tohoto repozitáře — `rankscale` je produkční pipeline a
      jediné místo, které se odsud spravuje/rotuje. Co se děje v
      `libor-matejkacz`, řeší (pokud vůbec) ten druhý repozitář samostatně.

- [x] **API klíč byl opakovaně vypsán do terminálu v čistém textu během ladění**
      (`gcloud secrets versions access latest`) — prošel Cloud Shell
      scrollbackem a shell historií.
      **Hotovo (2026-10-19):** starý klíč revokován na Rankscale, nový
      vygenerován a uložen do `rankscale-api-key` (verze 3, bez vypsání
      hodnoty — ověřeno přes `wc -c`/HTTP status, ne čtením hodnoty).
      Verze 1 a 2 (staré) `disabled`. Ověřeno ruční exekucí
      (`rankscale-extract-gf9r6`, `Execution completed successfully in
      42.3s`) — produkce běží s novým klíčem. Doporučuju ještě spustit
      `history -c` v Cloud Shellu, pokud jsi starou hodnotu klíče zadával
      přímo (ne přes soubor), ať nezůstane v shell historii.

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
      **Rozhodnutí (2026-10-19): přijatelné riziko.** Jediný správce,
      malý projekt — separace rolí by jen přidala komplexitu bez reálného
      přínosu. Přehodnotit, pokud přibydou další lidé s přístupem.

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
      **Rozhodnutí (2026-10-19): přijatelné riziko.** Defaultní Compute SA
      už nemá `roles/editor` (kritický nález výše vyřešen) — zbývající
      riziko je jen sdílení identity s případnými budoucími workloady,
      ne aktivní zranitelnost. Přehodnotit, pokud v projektu přibydou další
      Cloud Build pipeline/workloady na téhle SA.
      (Nalezeno v externím review 2026-10.)

---

## 🟡 Střední závažnost

- [x] **Image bez pinnutí na digest** — `src/Dockerfile` už používá
      `FROM python:3.12-slim@sha256:...`.
      **Vulnerability scanning — potvrzeno aktivní** (2026-10-19):
      `gcloud artifacts repositories describe` ukazuje
      `vulnerabilityScanningConfig.enablementState: SCANNING_ACTIVE`,
      aktivní dokonce už od založení repa (`lastEnableTime: 2026-09-01`,
      nejspíš default pro nové Artifact Registry repa) —
      `containerscanning.googleapis.com` enable jen potvrdil existující
      stav, žádná akce navíc nebyla potřeba.

- [x] **Cloud Run Job je nasazený na tag `latest`, ne unikátní tag** —
      `Makefile`/README/`DEPLOY_NEW_PROJECT.md` tagují image podle git SHA
      (`TAG := $(shell git rev-parse --short HEAD)`), ne `latest`. Na
      `rankscale` repu zapnuté `--immutable-tags` (2026-10-19, ověřeno
      `dockerConfig.immutableTags: true`) — přepsání libovolného tagu v
      repu (vč. `latest`) teď selže.
      (Nalezeno v externím review 2026-10.)

- [x] **Chybí audit logging přístupu k Secret Manageru**
      Neloguje se, kdo/kdy čte `rankscale-api-key`.
      **Hotovo** (2026-10-19): IAM & Admin → Audit Logs → "Secret Manager
      API" → **Data Read** zaškrtnuto a uloženo.
      (Nalezeno v externím review 2026-10.)

- [x] **Dvě verze secretu `api-key`, nejasné jestli je v1 ještě platná**
      `:latest` ukazuje na verzi 2. Verze 1 (2026-10-19) **zneplatněna v
      GCP** (`gcloud secrets versions disable 1` — reverzibilní, ne
      `destroy`, pro případ že by to bylo potřeba vrátit). Rankscale-strana
      (jestli má odpovídající starý klíč taky zrušený) zůstává neověřená s
      jistotou — `disable` v GCP ale sám o sobě znemožňuje, aby tahle verze
      šla ještě někdy znovu namountovat do jobu, takže riziko zombie
      credential z GCP strany je uzavřené bez ohledu na stav na Rankscale.
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
      **Rozhodnutí (2026-10-19): přijatelné riziko.** Data (marketing/SEO
      visibility metriky) nejsou citlivá/regulovaná v míře, co by
      ospravedlnila síťový perimetr navíc k IAM. Přehodnotit, pokud se
      charakter dat změní nebo přibude compliance požadavek.

- [ ] **Šifrování jen Google-managed klíči (ne CMEK)**
      Artifact Registry, Secret Manager i BigQuery běží na defaultním
      Google-managed key.
      **Fix:** zvážit CMEK, pokud to vyžaduje compliance/regulace.
      **Rozhodnutí (2026-10-19): přijatelné riziko.** Stejný důvod jako
      VPC-SC výše — relevantní jen při konkrétním compliance požadavku,
      který dnes není.

- [x] **`raw_answer_texts` obsahuje syrové AI odpovědi bez klasifikace dat**
      *(neaplikovatelné — tabulka `raw_answer_texts` byla v rámci přechodu na
      `topic_metrics_history` smazaná i s daty, viz README.)*

- [x] **Dvě nezávislé kopie stejného datasetu bez jasného vlastnictví**
      GitHub Actions → `libor-matejkacz`, Cloud Run Job → `rankscale`. Bez
      definovaného "zdroje pravdy" rostlo riziko rozjetí dat a
      nekonzistentní access policy.
      **Rozhodnutí (2026-10-19): vyřešeno.** `rankscale` (tento repozitář)
      je produkční pipeline. `libor-matejkacz` je mimo scope tohoto repa —
      co se s ním děje (vypnutí, staging označení, cokoliv), je mimo tenhle
      checklist.

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

- [x] **Cloud Audit Logs "Data Access" pro BigQuery pravděpodobně nejsou zapnuté**
      *(neaplikovatelné — ověřeno v IAM & Admin → Audit Logs, "BigQuery API"
      se v konfigurovatelném seznamu vůbec nenabízí.)* Google píše BigQuery
      Data Access audit logy (Data Read) natvrdo ve výchozím stavu u
      všech projektů a nejdou přes tenhle toggle vypnout ani zapnout — na
      rozdíl od většiny ostatních API (vč. Secret Manageru), kde je to
      opt-in. Není tedy co dělat, je to už defaultně pokryté.

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
      **Rozhodnutí (2026-10-19): přijatelné riziko.** Audit příkaz výše
      nebyl spuštěný/vyhodnocený naživo — přijato jako nízkoprioritní bez
      ověření (jediný správce s přístupem k projektu dělá riziko malým i
      bez kontroly). Pokud bys to chtěl ověřit, stačí spustit příkaz výše.

- [ ] **Alerting pokrývá jen "failed execution", ne neúplná/chybějící data**
      Současný alert (Cloud Monitoring, `completed_execution_count{result="failed"}`)
      nezachytí: job se vůbec nespustí (scheduler fail), úspěšný běh s
      neúplnými daty, neočekávaně nízký počet řádků, nebo abnormálně dlouhý běh.
      **Fix:**
      - [x] row-count validace přímo ve skriptu + navázaný log-based alert —
        **hotovo** (2026-10-19). `_warn_if_row_count_dropped()` v
        `src/rankscale_extract_gcp.py` porovná počet řádků, které se chystá
        zapsat `extract_topic_metrics_history()`, s počtem řádků, co jsou v
        `topic_metrics_history` teď (z předchozího běhu), a zaloguje
        `WARNING` s ASCII markerem `ROW_COUNT_DROP_WARNING` při poklesu >
        `ROW_COUNT_DROP_WARN_THRESHOLD` (30 %). Cloud Monitoring alert
        policy (`conditionMatchedLog` na `textPayload:"ROW_COUNT_DROP_WARNING"`,
        `alertPolicies/11970488685546518399`) posílá e-mail na stejný
        kanál jako existující failed-execution alert. Ověřeno nasazením +
        ručním během.
      - `ALIGN_COUNT` → `ALIGN_SUM`, severity na `error` (GCP)
      - alert na chybějící execution (scheduler se nespustil) (GCP)
      - alert na timeout/retry (GCP)
      - ověřit, že alert kanál reálně funguje na failed-execution alertu —
        v logu jsou fail runy, přišlo na ně oznámení? (proces/GCP)

      **Rozhodnutí (2026-10-19) pro zbylé 3 body výše: přijatelné riziko.**
      Nejdůležitější mezera (úspěšný běh s tichým poklesem dat) je
      pokrytá. `ALIGN_SUM`/missing-execution/timeout alerty jsou jemnější
      doladění s nižší pravděpodobností výskytu u jednou denně běžícího
      jobu — přehodnotit, pokud se objeví konkrétní incident, který by to
      bylo chytlo.
      (Nalezeno v externím review 2026-10.)

---

## ⚪ Otevřené otázky (ne technický nález, nutno rozhodnout)

- [x] **Kdo bude zodpovědný za správu skriptu/deploymentu? Kdo může
      upravovat skript a dělat redeploy?** (2026-10-19) Jen Libor —
      jediný správce, jediný s přístupem k oběma projektům (viz přijaté
      riziko u `roles/owner` výše).
- [x] **Zůstane deployment manuální, nebo se zautomatizuje (CI/CD)?**
      (2026-10-20) Nedorozumění ujasněno — **denní *spouštění* skriptu už
      je automatické** přes Cloud Scheduler (`rankscale-extract-daily`,
      `30 6 * * *` UTC, `state: ENABLED`), ověřeno živě (execution
      `rankscale-extract-268zr` naběhla přesně v 6:30 UTC a doběhla
      úspěšně). *Deployment* (build+push nové image po změně kódu) zůstává
      manuální přes `make deploy` — to se neautomatizuje, žádný CI/CD
      trigger na push/merge se zatím nezakládá.
- [x] Jak bude skript/pipeline reagovat na změnu struktury Rankscale API
      (neočekávaně jiný tvar odpovědi)? **Rozhodnuto (2026-10-19):**
      zůstává dnešní chování — spadne na výjimce, brand se označí jako
      failed, `log_run()` zapíše chybu do `etl_runs`, e-mailový alert
      upozorní. Žádná defenzivnější parsovací logika se nepřidává.
- [x] **Kdo bude rotovat API klíč a podle jakého plánu?** (2026-10-20)
      Libor, ad-hoc — žádný pevný interval, rotuje se jen při konkrétním
      důvodu (podezření na únik, jako 2026-10-19, nebo požadavek ze strany
      Rankscale).
- [x] **Byl ověřen alertingový kanál reálným testem?** (2026-10-20)
      Rozhodnutí: ne naostro — konfigurace (notification channel +
      policy navázaná na správnou metriku/log filter) je důvěryhodná bez
      umělého shození runu. Row-count alert byl nepřímo ověřen nasazením
      a úspěšným během; failed-execution alert zůstává neotestovaný naživo.

---

## Priorita (top 3) — všechny hotové

1. ✅ SQL injection (#1) — opraveno
2. ✅ Editor role na default Compute SA (#2) — odebráno
3. ✅ Vystavený API klíč — rotován 2026-10-19 (duplicita napříč projekty
   vyřešena rozsahem, `libor-matejkacz` je mimo scope, viz výše)

Všechny 🔴/🟠/🟡/🟢 nálezy jsou teď buď opravené, nebo vědomě přijaté jako
riziko s datem a důvodem (viz jednotlivé položky výše). Otevřené zůstávají
jen ⚪ organizační otázky, které nejdou vyřešit technickým fixem.
