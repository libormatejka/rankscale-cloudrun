# Bezpečnostní checklist — GCP Cloud Run Job nasazení

Nálezy z bezpečnostního review nasazení `rankscale-extract` (Cloud Run Job,
projekt `rankscale`). Řazeno podle závažnosti. Odškrtávej, jak se řeší.

Kontext: dvě nezávislé pipeline běží paralelně — GitHub Actions
(`libor-matejkacz.RankScaleDashboard`, produkce) a tento Cloud Run Job
(`rankscale.RankScaleDashboard`, GCP varianta).

---

## 🔴 Kritické

- [x] **SQL injection v `bq_max_snapshot()`** — [rankscale_extract_gcp.py:83-89](rankscale_extract_gcp.py#L83-L89)
      `brand_id` jde f-stringem přímo do SQL dotazu bez parametrizace.
      Dnes pochází z Rankscale API (důvěryhodné), ale žádný security review
      tuhle domněnku neakceptuje.
      **Fix:** `bigquery.ScalarQueryParameter` + `QueryJobConfig(query_parameters=...)`.
      Opraveno v `GCP/rankscale_extract_gcp.py` i v `src/rankscale_extract.py`
      (stejný bug byl i v produkční GitHub Actions verzi). Nový image
      nasazen a otestován (`rankscale-extract-q67hv`, `Completed: True`).

- [x] **Výchozí Compute service account má `roles/editor` na projektu `rankscale`**
      (`968813943604-compute@developer.gserviceaccount.com`). Tenhle SA reálně
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
      `jsem@libor-matejka.cz` má Owner na obou projektech a dělá vývoj, deploy
      i IAM správu — single point of failure, no separation of duties.
      **Fix:** v korporátním provozu rozdělit na least-privilege role
      (deployer / data steward / security admin) podle potřeby.

---

## 🟡 Střední závažnost

- [ ] **Image bez pinnutí na digest, bez vulnerability scanningu**
      `FROM python:3.12-slim` — obsah image se může časem změnit. Artifact
      Registry vulnerability scanning není zapnutý.
      **Fix:** pin na `sha256:...` digest, zapnout Artifact Analysis scanning.

- [ ] **Kontejner běží jako root**
      Dockerfile nenastavuje `USER`. Cloud Run má vlastní izolaci, ale je to
      zbytečně široký attack surface.
      **Fix:** přidat non-root `USER` do Dockerfile.

- [ ] **Bez VPC Service Controls / síťového perimetru**
      BigQuery, Secret Manager i Cloud Run API jsou dostupné z veřejného
      internetu (jen IAM autentizace, žádná síťová izolace).
      **Fix:** zvážit VPC-SC perimetr kolem BigQuery/Secret Manageru, pokud
      jde o citlivá data.

- [ ] **Šifrování jen Google-managed klíči (ne CMEK)**
      Artifact Registry, Secret Manager i BigQuery běží na defaultním
      Google-managed key.
      **Fix:** zvážit CMEK, pokud to vyžaduje compliance/regulace.

- [ ] **`raw_answer_texts` obsahuje syrové AI odpovědi bez klasifikace dat**
      Pokud by se v promptech/odpovědích objevily citlivé/osobní údaje, nikde
      to není klasifikováno ani maskováno.
      **Fix:** data classification review, zvážit DLP scanning.

- [ ] **Dvě nezávislé kopie stejného datasetu bez jasného vlastnictví**
      GitHub Actions → `libor-matejkacz`, Cloud Run Job → `rankscale`. Bez
      definovaného "zdroje pravdy" roste riziko rozjetí dat a nekonzistentní
      access policy.
      **Fix:** rozhodnout, která pipeline je produkční, druhou buď vypnout,
      nebo jasně označit jako staging/test.

---

## 🟢 Nízká / hygiena

- [ ] **`max-retries=1` + `WRITE_APPEND` bez idempotency klíče**
      Retry po částečném selhání může vytvořit duplicitní řádky (datová
      integrita / audit trail).

- [ ] **Cloud Audit Logs "Data Access" pro BigQuery pravděpodobně nejsou zapnuté**
      Defaultně vypnuté kvůli objemu logů — bez nich není vidět, kdo přesně
      četl jaká data.

- [ ] **`BACKFILL_WEEKS` lze přepsat kýmkoliv s `roles/run.developer`**
      Nízké riziko, ale umožňuje neplánovaně zatáhnout až rok historie
      (cost / abuse potential).

---

## Priorita (top 3)

1. SQL injection (#1) — čistá, bezriziková oprava v kódu
2. Editor role na default Compute SA (#2) — odebrat, nic dalšího nezávisí
3. Duplicitní + vystavený API klíč (#4, #5) — rotovat
