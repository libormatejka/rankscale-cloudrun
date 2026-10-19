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

- [ ] **`roles/bigquery.dataEditor` je na úrovni projektu, ne datasetu**
      SA `rankscale-extract-job` může zapisovat do libovolného datasetu v
      `rankscale`, ne jen `RankScaleDashboard`.
      **Fix:** binding na úrovni datasetu (`bq add-iam-policy-binding`), ne
      `gcloud projects add-iam-policy-binding`.

- [ ] **`roles/run.invoker` je na úrovni projektu, ne konkrétního Cloud Run Jobu**
      SA smí spustit jakýkoliv Cloud Run service/job v projektu, ne jen
      `rankscale-extract`.
      **Fix:** `gcloud run jobs add-iam-policy-binding` na konkrétní job.

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
      **Fix:** dedikovaný build SA jen s oprávněním číst zdrojový kód, sestavit
      image, zapisovat do konkrétního Artifact Registry repa a build logů
      (případně i deploy, pokud půjde přes build pipeline).
      (Nalezeno v externím review 2026-10.)

- [ ] **Cloud Run Job a Cloud Scheduler sdílí jeden service account**
      Stejný SA má zároveň BigQuery oprávnění (scheduler je nepotřebuje) i
      `run.invoker` na úrovni projektu (runtime SA ho nepotřebuje vůbec).
      Rozšiřuje to blast radius při kompromitaci kterékoli ze dvou rolí.
      **Fix:** rozdělit na dvě identity:
      - **Runtime SA**: `bigquery.jobUser` (projekt) + `bigquery.dataEditor`
        (jen cílový dataset, viz nález výše) + `secretmanager.secretAccessor`
        (jen konkrétní secret)
      - **Scheduler SA**: `run.invoker` jen na konkrétním Cloud Run Jobu
        (ne projekt, viz nález výše)
      (Nalezeno v externím review 2026-10.)

---

## 🟡 Střední závažnost

- [x] **Image bez pinnutí na digest** — `src/Dockerfile` už používá
      `FROM python:3.12-slim@sha256:...`. Zbývá jen vulnerability scanning
      (GCP strana):
      **Fix:** zapnout Artifact Analysis / Artifact Registry vulnerability
      scanning pro repo s touto image (`gcloud artifacts` / Cloud Console).

- [ ] **Cloud Run Job je nasazený na tag `latest`, ne unikátní tag**
      Cloud Run při deploymentu `latest` přeloží na konkrétní digest, takže
      pozdější přepsání `latest` jiným image automaticky nezmění už nasazený
      job — ale ztrácí se dohledatelnost verze a možnost snadného rollbacku.
      **Fix:** tagovat image např. `image:2026-09-25` a zapnout immutable tags
      v Artifact Registry, aby tagy nešly přepsat.
      (Nalezeno v externím review 2026-10.)

- [ ] **Chybí audit logging přístupu k Secret Manageru**
      Neloguje se, kdo/kdy čte `rankscale-api-key`.
      **Fix:** IAM & Admin → Audit Logs → zapnout Data Read pro Secret
      Manager API.
      (Nalezeno v externím review 2026-10.)

- [ ] **Dvě verze secretu `api-key`, nejasné jestli je v1 ještě platná**
      `:latest` ukazuje na verzi 2. Není ověřeno, jestli je verze 1 zneplatněná
      i na straně Rankscale (riziko zombie credential).
      **Fix:** ověřit u Rankscale, zda je starý klíč zrušený; pokud ne,
      zrušit ho. Viz také `doc/API_KEY_ROTATION.md`.
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

- [ ] **`BACKFILL_WEEKS` lze přepsat kýmkoliv s `roles/run.developer`**
      Nízké riziko, ale umožňuje neplánovaně zatáhnout až rok historie
      (cost / abuse potential).

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
