# salud-empresarial-api

Django project that pulls data from SUNAT and exposes it through a REST API. Two modules:

- **`sunat_mailbox`** — scrapes the electronic mailbox (*buzón SOL*), including the PDF
  attachments and their text. Requires SOL credentials.
- **`suppliers`** — a registry of suppliers whose RUC standing is checked on SUNAT every
  day, so a supplier going *de baja* or *no habido* shows up immediately. Public data,
  no credentials needed.
- **`remype`** — REMYPE accreditation (Ministerio de Trabajo) for the company and its
  suppliers. Cached and refreshed monthly, since accreditation rarely changes.
- **`ruc_profile`** — the *full* SUNAT RUC file: the main table plus all nine detail
  buttons (coactive debt, tax omissions, headcount, legal representatives…), captured
  monthly as a snapshot so changes are visible over time.

## Setup

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium   # the full build is required, not the headless shell
cp .env.example .env                    # then fill in the SOL and PostgreSQL credentials
createdb salud_empresarial              # if it does not exist yet
python manage.py migrate
```

Redis is needed for the scheduled tasks (`brew install redis` on macOS).

## Scraping

Messages are written exclusively by the management command; the API never writes.

```bash
python manage.py scrape_mailbox                 # everything (messages + notifications)
python manage.py scrape_mailbox --max-pages 1   # first page only, for a smoke test
python manage.py scrape_mailbox --details       # also fetch bodies and attachment metadata
python manage.py scrape_mailbox --attachments   # download the PDFs and extract their text
python manage.py scrape_mailbox --type 2        # 1 = messages, 2 = notifications
python manage.py scrape_mailbox --headful       # show the browser, to debug the login
```

Re-running is safe: rows are upserted on `(taxpayer_id, message_code, message_type)`,
attachments are reconciled in place, and files already downloaded are skipped unless
`--redownload` is passed. Fetching details does **not** mark messages as read in SUNAT —
`fecLectura` stays null.

### Attachment text

`--attachments` downloads each PDF, extracts its text with `pypdf` and stores it in
`Attachment.text_content` (the bytes themselves are not kept, only the text and a
SHA-256 of the original). `extraction_status` records the outcome:

| Status | Meaning |
| --- | --- |
| `extracted` | Text recovered |
| `empty` | Valid PDF with no text layer — a scan, needs OCR |
| `unsupported` | Not a PDF, or a file SUNAT serves from another subsystem |
| `failed` | Download or parsing error, details in `extraction_error` |
| `pending` | Not downloaded yet |

Downloads hit `/visor/bajarArchivo/{codArchivo}/{annex}/{system}/{ruc}`, which returns
`200` with an **empty body** unless the owning message's detail was requested earlier in
the same session — the synchronizer always does this first.

### Document archive

Every scraped comprobante is also written to disk, under `MEDIA_ROOT`:

```
comprobantes/<ruc>/<year>/<month>/<factura|nota_credito|nota_debito|recibo_honorarios>/<series-number>-<uuid>.<ext>
```

Facturas and notes store their signed XML byte-for-byte as SUNAT served it (`.xml`).
Fee receipts store the detail page as served (`.html`) — the consulta SUNAT offers the
paying company has no PDF/XML download — or the PDF the worker handed over when the
receipt was registered by upload (`.pdf`), which a later scrape never replaces. The uuid
is the row's primary key, so re-downloading replaces the file instead of duplicating it.
Rows scraped before the archive existed get their XML written from the database with:

```bash
python manage.py archive_xml                    # every company
python manage.py archive_xml --ruc 20604442533  # one company
```

### Downloads

Every place the UI shows a document carries PDF / XML download icons. The endpoints:

| Endpoint | Returns |
| --- | --- |
| `GET /api/cpe/invoices/{id}/xml/` | The signed XML, byte for byte, named the SUNAT way (`{ruc}-{tipo}-{serie}-{numero}.xml`) |
| `GET /api/cpe/invoices/{id}/pdf/` | A printable representation built from the XML on each request (`sunat_cpe/services/pdf.py`): parties, lines, totals by operation type, IGV, payment terms, detracción, the document a note modifies, and the XML's SHA-256 in the footer |
| `GET /api/rhe/receipts/{id}/pdf/` | The PDF uploaded when the receipt was registered from paper, or —for scraped receipts— a representation built from the receipt's data and detail |

Fee receipts have no XML: SUNAT never hands one to the paying company, so the UI shows only the PDF icon for them.

### Bulk download (.zip, with a one-time code)

Each document section (facturación, comprobantes recibidos, notas de crédito, honorarios)
has a "Descargar todo (.zip)" button that bundles every document of the section's filter:
`year/month/type/` folders with the XML and PDF of each comprobante (PDF only for fee
receipts), plus `indice.csv` for Excel and a `LEEME.txt`. Because that is the company's whole
paper trail leaving the system, it is gated twice:

- **Paid plan only** (`PaidPlanActive`): the trial does not include it, and the button is
  disabled without it (and when the section has nothing to export).
- **One-time code by email**: `POST /api/documents/exports/` validates the filter, counts the
  documents (max 2 000 per download) and emails a 6-digit code to the requesting user;
  `POST /api/documents/exports/{id}/download/` with `{"code"}` returns the zip once. The code
  expires in 10 minutes, dies after 5 wrong attempts, and a new request voids the previous one.
  Every export is logged (`documents.DocumentExport`, read-only in the admin): who, which
  filter, how many documents, when it was downloaded.

## API

| Endpoint | Description |
| --- | --- |
| `GET /api/messages/` | Paginated list |
| `GET /api/messages/{uuid}/` | Full message with attachments and raw payloads |
| `GET /api/messages/summary/` | Counts by type and read state, honouring active filters |

Filters: `taxpayer_id`, `message_type`, `is_read`, `is_urgent`, `is_starred`,
`office_code`, `label_code`, `has_attachments`, `has_text`, `sent_from`, `sent_to`,
`published_from`, `published_to`.
`?search=` covers the subject, the sender **and the extracted PDF text**, and
`?ordering=` works over the date fields.

The list view returns metadata only; the detail view adds each attachment's
`text_content`, which is what a downstream AI pipeline would consume.

```bash
curl 'http://127.0.0.1:8000/api/messages/?message_type=2&has_attachments=true'
curl 'http://127.0.0.1:8000/api/messages/?search=Coactiva'   # searches inside the PDFs
curl 'http://127.0.0.1:8000/api/messages/summary/'
```

With `DJANGO_DEBUG=True` the API is open and the browsable renderer is on. When debug is
off it requires an authenticated session and returns JSON only.

## Supplier monitoring

Register the suppliers you buy from and their SUNAT standing is re-checked every day.
A supplier is flagged (`has_issue`) whenever it is not both **ACTIVO** and **HABIDO** —
including states SUNAT may add later, which fail safe as issues rather than being ignored.

```bash
python manage.py check_suppliers                  # every tracked supplier
python manage.py check_suppliers --ruc 20100070970  # just one, repeatable
python manage.py check_suppliers --skip-checked   # skip today's successes, to retry a run
python manage.py check_suppliers --all            # include suppliers marked untracked
```

The daily run is scheduled through Celery Beat — see [Scheduled tasks](#scheduled-tasks).
The management command stays available for ad-hoc runs and debugging.

One `SupplierCheck` row is written per supplier per day, so re-running is idempotent.
A check that differs from the previous standing is marked `changed=True` — that flag is
the point of keeping the history, since it pinpoints the day a supplier's status moved.
Failed lookups are recorded without overwriting the last known good standing, and a
failed day never registers as a change.

| Endpoint | Description |
| --- | --- |
| `GET/POST /api/suppliers/` | List and register suppliers |
| `GET/PATCH /api/suppliers/{uuid}/` | Supplier with its 30 most recent checks |
| `GET /api/suppliers/{uuid}/checks/` | Full history for one supplier |
| `POST /api/suppliers/{uuid}/check/` | Check now, without waiting for the cron |
| `GET /api/suppliers/summary/` | Totals plus the list of flagged suppliers |
| `GET /api/supplier-checks/` | History across all suppliers |

Only `ruc`, `alias`, `is_tracked` and `notes` are writable; everything SUNAT owns is
read-only. RUCs are validated locally with the modulus-11 check digit, so a typo never
costs a SUNAT request.

```bash
curl -X POST http://127.0.0.1:8000/api/suppliers/ \
  -H 'Content-Type: application/json' -d '{"ruc":"20100070970","alias":"Supermercados"}'
curl 'http://127.0.0.1:8000/api/suppliers/?has_issue=true'
curl 'http://127.0.0.1:8000/api/supplier-checks/?changed=true'
```

In the admin, suppliers show a colour-coded standing, their recent checks inline, and a
bulk action to re-check the selected rows immediately.

## Full RUC profile

The daily supplier check reads only *estado* and *condición*. This module captures the
**whole** consultation page: the main table plus every button behind it, as one snapshot
per RUC. Snapshots are reused for 25 days, so the monthly run is cheap.

```bash
python manage.py capture_ruc_profile                    # company + tracked suppliers
python manage.py capture_ruc_profile --ruc 20604442533  # one RUC, repeatable
python manage.py capture_ruc_profile --force            # ignore recent snapshots
```

The nine sections, each posted as `accion` to the same endpoint:

| Section key | Button | Risk signal |
| --- | --- | --- |
| `historical` | Información histórica | |
| `coactive_debt` | Deuda coactiva | ✅ |
| `tax_omissions` | Omisiones tributarias | ✅ |
| `workers` | Cantidad de trabajadores | |
| `probatory_acts` | Actas probatorias | ✅ |
| `physical_invoices` | Facturas físicas | |
| `reactiva_peru` | Reactiva Perú: deuda coactiva | ✅ |
| `covid_guarantee` | Garantías COVID-19: deuda coactiva | ✅ |
| `legal_representatives` | Representante(s) legal(es) | |

Each section is stored **generically** — `tables: [{headers, rows}]` — because SUNAT
reshuffles these pages and a JSON payload survives that where fixed columns would not.
On top of that, the parts worth querying are lifted into real columns and models:
the risk flags (`has_coactive_debt`, `reactiva_peru_debt`, …, plus `has_risk_signals`),
`WorkerHeadcount` (12 months of PLAME figures) and `LegalRepresentative`.

A snapshot that differs from the previous one is marked `changed`, with a readable
`change_summary` such as `status: 'ACTIVO' -> 'BAJA DE OFICIO'; has_coactive_debt: False -> True`.

| Endpoint | Description |
| --- | --- |
| `GET /api/ruc-profiles/me/` | Full profile for the configured RUC |
| `GET /api/ruc-profiles/current/` | Latest snapshot per RUC |
| `GET /api/ruc-profiles/current/?ruc=X` | One company's latest, in full |
| `GET /api/ruc-profiles/` | Every snapshot, filterable |
| `POST /api/ruc-profiles/capture/` | Capture now — `{"ruc": "...", "force": false}` |

```bash
curl 'http://127.0.0.1:8000/api/ruc-profiles/?has_risk_signals=true'
curl 'http://127.0.0.1:8000/api/ruc-profiles/?has_coactive_debt=true'
curl 'http://127.0.0.1:8000/api/ruc-profiles/?changed=true'
```

A section that fails is recorded on the snapshot with its error and the rest still run:
one broken page must not cost the whole profile.

## REMYPE accreditation

Checks whether a company is accredited in REMYPE (the Ministry of Labour's micro and
small business registry). Accreditation is granted once and rarely revoked, so lookups
are **cached for 30 days** and a stored check is reused instead of re-queried.

```bash
python manage.py check_remype                    # company RUC + every tracked supplier
python manage.py check_remype --ruc 20604442533  # one RUC, repeatable
python manage.py check_remype --force            # ignore the cache
```

| Endpoint | Description |
| --- | --- |
| `GET /api/remype/me/` | Standing for the RUC in `SUNAT_RUC` — "do I have REMYPE?" |
| `GET /api/remype/current/` | Latest check per RUC |
| `GET /api/remype/current/?ruc=X` | One company's current standing |
| `GET /api/remype/` | Full history, filterable |
| `POST /api/remype/lookup/` | Query now — `{"ruc": "...", "force": false}` |

`is_registered` means the RUC appears in REMYPE; `is_active` additionally requires that
it has not been struck off (`deregistered_on`). A check that differs from the previous
one is marked `changed`, which is how a revoked accreditation surfaces.

### Why this one needs a browser

The endpoint (`consultas-remype/consulta/remype.tra`) verifies a **reCAPTCHA Enterprise**
token server-side — without one it answers `401 {"message": "Captcha invalido"}`. The
token can only be minted by the page's own JavaScript, so the client drives Chromium,
executes `grecaptcha.enterprise.execute` and posts from within the page. It also sends
the hardcoded `Authorization: Basic YWRtaW46YWRtaW4=` header the Angular app attaches to
every request; the endpoint rejects calls without it.

One browser serves a whole batch, and all lookups finish before anything is written to the
database: Playwright's sync API runs an event loop in the calling thread, and Django
refuses ORM access from an async context, so the two phases cannot be interleaved.

## Scheduled tasks

Scheduling runs on Celery with Redis as the broker. Two processes are needed besides the
web server:

```bash
redis-server                                          # broker
celery -A config worker -l info -Q default,scraping   # runs the tasks
celery -A config beat -l info                         # dispatches them on schedule
```

`-Q` is not optional. `sync.run_job` — the whole "Sincronizar ahora" flow — is routed to
the `scraping` queue (see `CELERY_TASK_ROUTES`), and a worker started without it consumes
only `default`: the jobs pile up in Redis, the UI shows them stuck "en cola" forever, and
nothing in the logs says why. In production the scraping queue gets its own worker with a
low `-c`, since each job drives a browser through several SUNAT portals.

Seeded schedule (America/Lima):

| Task | When | What it does |
| --- | --- | --- |
| `suppliers.check_all` | 07:00 daily | Checks every tracked supplier on SUNAT |
| `sunat_mailbox.scrape` | 07:30 daily | Pulls new messages and their attachment text |
| `remype.refresh` | 08:00, 1st of month | REMYPE standing; skips RUCs still within cache |
| `ruc_profile.capture` | 08:30, 1st of month | Full RUC file: main table plus all nine buttons |
| `suppliers.check_one` | on demand | Single supplier, retries if SUNAT is unreachable |

The schedule lives in the database (`django-celery-beat`), seeded by a data migration, so
**times can be changed or a task paused from the admin** under *Periodic tasks* — no
redeploy. Task outcomes are stored by `django-celery-results` and are browsable in the
admin under *Task results*.

The mailbox task starts half an hour after the supplier check on purpose: it drives a real
browser and is far heavier, so overlapping them is wasteful. Both tasks are idempotent —
one check row per supplier per day, messages upserted — which is why `task_acks_late` is
on: if a worker dies mid-task the job is redelivered rather than lost.

`suppliers.check_all` skips suppliers already checked today by default, so retrying a
partially failed run only covers what is still missing instead of re-querying SUNAT.

Set `CELERY_TASK_ALWAYS_EAGER=True` to run tasks inline without a broker.

## Layout

```text
config/
  celery.py      Celery app; schedules live in the database
core/            abstract bases (UUID primary key + timestamps)
                 plus the migration seeding the Beat schedule
sunat_mailbox/
  models.py      Message, Attachment
  tasks.py       scheduled scrape
  serializers.py list and detail representations
  filters.py     query filters
  views.py       read-only viewset
  services/
    client.py     SUNAT authentication, JSON reads and downloads
    sync.py       payload -> model mapping
    extraction.py PDF text extraction
    parsing.py    date/text coercion helpers
    constants.py  endpoints and browser settings
  tests/          no network access required
suppliers/
  models.py       Supplier, SupplierCheck
  validators.py   RUC modulus-11 check digit
  tasks.py        scheduled and on-demand checks
  services/
    ruc_client.py public RUC lookup and HTML parsing
    monitor.py    daily run and change detection
  tests/          no network access required
remype/
  models.py       RemypeCheck
  tasks.py        monthly refresh
  services/
    client.py     browser + reCAPTCHA Enterprise + JSON lookup
    sync.py       recording and the 30-day cache policy
  tests/          no network access required
ruc_profile/
  models.py       RucSnapshot, RucSection, LegalRepresentative, WorkerHeadcount
  tasks.py        monthly capture
  services/
    constants.py  the nine buttons and their actions
    client.py     extends the suppliers RUC client with the detail pages
    parsers.py    generic table extraction
    sync.py       snapshots, risk flags and change detection
  tests/          no network access required
```

171 tests in total, all offline — the government responses are fixtures and tasks are
called directly, so no broker or browser is needed to run them.

## How the login works

SUNAT cannot be reached with `requests` alone. The real flow is:

1. `MenuInternet.htm?exe=buzon` redirects via JavaScript to the `api-seguridad` OAuth2 flow.
2. `POST oauth2/j_security_check` authenticates — this part does work with `requests`.
3. The menu then mints a viewer URL shaped
   `/ol-ti-itvisornoti/visor/master?hc=<hash>&token=<java-serialized UsuarioBean>`.
   That `hc` is signed server-side and **cannot be reproduced externally**, so this step
   needs a real browser.
4. Opening that URL sets the `ITVISORNOTISESSION` cookie, which unlocks the JSON endpoints.

So [client.py](sunat_mailbox/services/client.py) drives Playwright only for authentication and
then hands the cookies to a `requests` session, which is much faster for pagination.

Two WAF quirks, both already handled:

- `e-menu.sunat.gob.pe` resets the connection against Playwright's *headless shell*.
  Chromium must be launched with `channel="chromium"` and `--disable-blink-features=AutomationControlled`.
- The captcha only appears after three consecutive failed logins. If it shows up the command
  fails with `SunatLoginError` and you need to log in manually once from a browser.

## Notes on SUNAT's data

- **`indEstado` is not a read flag.** It was observed changing across runs without any
  message being opened, so it is stored verbatim in `status_code` and never interpreted.
  Read state is derived from `fecLectura` (`read_at`), which is only known once the detail
  has been fetched.
- **`codArchivo` is not unique.** SUNAT reuses `0` for several attachments of the same
  message, so attachments carry no unique constraint and are matched on
  `(file_code, file_name)` instead. A `codArchivo` of `0` also means the file lives in
  another subsystem (RVIE/RCE tickets) and cannot be downloaded from the mailbox viewer;
  those rows end up as `unsupported`.
- Subjects arrive with HTML entities double-encoded (`confirmaci&oacute;n`) and are unescaped
  on the way in.
- The raw `list_payload` and `detail_payload` columns are kept so new fields can be
  backfilled without re-scraping.
