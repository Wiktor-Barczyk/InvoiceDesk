# Invoice Desk

A portal for preparing, reviewing and posting clients' accounting documents. It combines the original document preview, invoice form, AI data preparation, splitting of scans containing multiple documents, a client ledger and depreciation schedules.

This documentation covers the code after the following updates: the GnuCash account catalog, posting and corrections, the client ledger, depreciation with loss events, the mobile interface and automatic scan splitting. It also includes the account balances view in the client ledger.

## Contents

1. [Choosing how to run the application](#choosing-how-to-run-the-application)
2. [Features and scope](#features-and-scope)
3. [Workflow](#workflow)
4. [Application architecture](#application-architecture)
5. [Database](#database)
6. [Requirements](#requirements)
7. [Running without Docker](#running-without-docker)
8. [Database and staff account setup](#database-and-staff-account-setup)
9. [Configuration](#configuration)
10. [AI document processing](#ai-document-processing)
11. [Splitting documents](#splitting-documents)
12. [Posting, corrections and the client ledger](#posting-corrections-and-the-client-ledger)
13. [Depreciation](#depreciation)
14. [Roles and security](#roles-and-security)
15. [System service and HTTPS](#system-service-and-https)
16. [Running with Docker](#running-with-docker)
17. [Updates and backups](#updates-and-backups)
18. [Troubleshooting and tests](#troubleshooting-and-tests)
19. [Main endpoints](#main-endpoints)

## Choosing how to run the application

Choose **one application deployment option**. The features, PostgreSQL database and invoice review process are the same in both options.

| Option | What runs | When to choose it | Instructions |
|---|---|---|---|
| Without Docker | Python in `.venv`, the web application served by Uvicorn, optionally managed by systemd. | When using an existing server installation or managing processes without containers. | [Running without Docker](#running-without-docker) |
| Docker | The application image and containers managed by Compose. | When a working Docker installation is available and you want to run the application in containers. | [Running with Docker](#running-with-docker) |

Independently, choose **one AI data preparation method**:

| AI method | Without Docker | Docker |
|---|---|---|
| MCP | Web application and a connected AI client; no `app.worker`. | `compose.yml` and an AI client; no `compose.background.yml`. |
| OpenAI API | Web application and the API worker. | `compose.yml` and `compose.background.yml`. |

Set `BACKGROUND_PROCESSING=1` in both cases. In MCP mode, `AI_ENABLED=0` is correct because the AI runs through the MCP client. For the API, set `AI_ENABLED=1` and configure the API key. See [AI document processing](#ai-document-processing) for details.

First prepare the database and storage access, then configure AI, and finally start the selected application deployment. For an existing installation, retain its data and configuration. Do not follow both deployment procedures at the same time.

## Features and scope

- Work with a selected client's purchase and sales documents, invoice lines, counterparties and amounts.
- Upload PDF, JPG and PNG files; preview pages and download originals after signing in.
- Display the form beside the original on large screens; switch between the form and preview on smaller screens.
- AI prepares invoice data and proposes ledger accounts and VAT codes through the OpenAI API or an MCP client. Staff review the results and correct them when necessary.
- Automatically split readable scans containing multiple documents, with proposed regions and manual splitting for uncertain results.
- Use one category/account selector, search accounts by number or name, and keep the VAT code separate.
- Post supported invoices in one step, creating both ledger and VAT register records.
- Allow administrators and chief accountants to correct posted invoices.
- View the client ledger for a selected period, including current entries, details, change history, receivables and payables. Browse the account hierarchy, balances and transactions by account.
- Prepare depreciation schedules for Inventaris items, add notes and record theft or loss.
- Check duplicates, concurrent changes and closed periods, with an audit trail of operations.

**Current posting engine scope:** automatic journal entries cover ordinary domestic purchase and sales invoices in EUR using supported VAT codes. Accepting a file, recognizing a receipt or listing an account in the catalog does not mean that the posting engine supports that case. Foreign invoices, reverse-charge/ICP transactions, commercial credit notes and other currencies require separate handling. See the posting section for details.

The project does not include a complete banking module, tax return submission, payroll processing or automatic synchronization of journal entries with GnuCash. Some tables in the shared database schema support those areas, but their presence does not mean the corresponding application feature has been implemented.

## Workflow

1. A staff member signs in and selects a client.
2. They upload documents (optional). The application stores the originals and checks whether a scan can be split into separate documents.
3. AI prepares proposed invoice data, including ledger accounts and VAT codes. The document waits in the queue; the staff member can work on another document.
4. The staff member compares the form with the original, corrects the fields and confirms the review.
5. Clicking **Post invoice** creates the invoice, invoice lines, balanced journal entry and VAT register records in one transaction. The current interface labels this button **Zaksięguj fakturę**.
6. The invoice appears among saved documents and in the client ledger. A purchase posted to Inventaris makes a depreciation schedule available.

AI prepares the data and classification. Staff compare the form with the original, correct errors or missing values, and confirm the review instead of completing every invoice from scratch. AI does not replace that review or post documents on its own. Uncertain account or VAT matches may be left blank and must be corrected before posting.

### Three separate status layers

The document's workflow status, its processing job status and its invoice status are separate.

| Layer | Main values | Meaning |
|---|---|---|
| Document | `to_book` | The document is awaiting processing. |
| Document | `booked` | The document has been posted. |
| Document | `archived` | For example, an original replaced by split documents; the original is retained. |
| Processing | `queued`, `processing` | Waiting for or undergoing data preparation. |
| Processing | `ready`, `review` | The proposal is ready or requires particular attention. |
| Processing | `failed`, `cancelled` | Processing failed or was interrupted. |
| Invoice | `approved` | Saved and reviewed; a legacy stage before posting. |
| Invoice | `posted` | Posted, with a link to the general ledger. |

`ready` does not mean `posted`. The current interface's standard action saves and posts together. The older approval-only endpoint remains available in the API.

## Application architecture

The backend uses **Python and FastAPI**, served by Uvicorn. The interface uses HTML, CSS and JavaScript without a separate frontend build process. Structured data is stored in **PostgreSQL**, while document files are stored on the filesystem.

| File / directory | Responsibility |
|---|---|
| `run.py` | Starts the web application and administrative commands while loading `.env` safely. |
| `app/main.py` | HTTP endpoints, login, permissions and document uploads. |
| `app/db.py` | PostgreSQL connections, documents, reference data, invoice persistence and auditing. |
| `app/domain.py` | Validation of form data, dates, amounts and invoice lines. |
| `app/storage.py` | File access, integrity checks and preview caching. |
| `app/extraction.py` | File inspection, PDF rendering and technical OCR support. |
| `app/auto_split.py`, `app/splitting.py` | Scan layout detection, document cropping, and saving or undoing splits. |
| `app/processing.py`, `app/worker.py` | Queue management, reservations, retries and API processing. |
| `app/ai.py` | OpenAI Responses API requests and response validation. |
| `app/mcp_server.py`, `app/mcp_auth.py`, `app/mcp_jobs.py` | MCP tools, OAuth and proposal submission by an external client. |
| `app/booking.py`, `app/posting.py` | Account selection, posting and corrections. |
| `app/ledger.py`, `app/ledger_accounts.py` | The client ledger, account balances and entry history. |
| `app/inventaris.py` | Depreciation schedules and loss events. |
| `app/security.py` | Password hashing and session signing. |
| `app/static/` | The form, preview, menu, ledger, depreciation and mobile interface. |
| `app/migrations/` | Migrations executed by `run.py migrate`. |
| `database/` | The base V5 schema and additional feature-specific SQL scripts. |
| `tests/` | Automated tests with synthetic documents and a separate test database. |

The queue is stored in PostgreSQL. Redis and Celery are not required. The API worker processes jobs sequentially; a PostgreSQL lock allows only one active worker per database.

## Database

### Engine and data organization

The project uses PostgreSQL with the Psycopg 3 driver. **It does not use SQLite** for authentication or accounting. The connection is defined by `DATABASE_URL` in `.env`.

| PostgreSQL schema | Purpose |
|---|---|
| `core` | Staff, clients, counterparties, registrations and fiscal years. |
| `documents` | Documents, file metadata, processing results, splits, the queue and MCP authorization. |
| `accounting` | Accounts, VAT codes, invoices, invoice lines, the ledger and VAT register. |
| `assets` | Fixed asset data; the portal stores its own depreciation schedules. |
| `tax` | Includes period locks and tax return structures referenced when checking entries. |
| `banking` | Banking structures and payment links; no complete banking interface in this project. |
| `payroll` | Payroll structures in the shared model; outside the portal's scope. |
| `audit` | Operation and change history. |

### Main tables and relationships

| Table | Stored data / relationships |
|---|---|
| `core.staff_users` | Staff account, password hash, role and active status. |
| `core.clients` | Client records; `odoo_partner_id` supports links to an external system. |
| `core.client_counterparties` | Counterparties belonging to a client. |
| `documents.accounting_documents` | A client's document and its workflow stage. |
| `documents.stored_files` | SHA-256, location, size and file type. |
| `documents.accounting_document_files` | Links a document to a file, including its name and role. |
| `documents.document_ocr_runs` | Successive OCR/AI results: raw output, extracted fields and warnings. |
| `documents.document_imports`, `document_import_items` | Split batches, provenance, crop boundaries and rotation. |
| `documents.processing_jobs`, `processing_worker` | Queue state and worker heartbeat. |
| `documents.mcp_grants`, `mcp_codes`, `mcp_claims` | OAuth access grants and MCP document reservations. |
| `accounting.accounts` | Chart of accounts, including account numbers and types. |
| `accounting.invoice_desk_account_catalog` | Accounts permitted for purchase or sales invoice lines. |
| `accounting.staff_booking_categories`, `booking_category_account_mappings` | Compatibility with the older category and account mapping model. |
| `accounting.vat_codes` | Internal VAT code, label, rate, direction and validity period. |
| `accounting.invoices`, `invoice_lines` | Invoice headers and lines with accounts and VAT codes. |
| `accounting.invoice_source_documents` | Links an invoice to its correct source document. |
| `accounting.invoice_validation_checks` | Validation results saved during approval. |
| `accounting.general_ledger_entries`, `general_ledger_entry_lines` | Journal entry headers and debit/credit lines. |
| `accounting.vat_transactions` | Taxable amounts, input VAT and output VAT, linked to invoice lines and the ledger. |
| `accounting.invoice_desk_corrections` | Previous invoice, replacement invoice, reversal and correction reason. |
| `assets.invoice_desk_depreciation_plans` | A depreciation schedule linked to an invoice line, its note and any loss event. |
| `tax.accounting_period_locks`, `core.client_fiscal_years` | Closed-period and fiscal-year checks. |
| `audit.change_log` | Who performed an operation, when, and the before/after data; protected against modification and deletion of history. |

The working relationship is: **client → document → invoice → invoice lines → ledger and VAT records**. Files are linked to documents through a separate table. After splitting, each crop has its own document and can be posted independently.

Client records are read from the database. This project does not implement a standalone Odoo synchronizer or client editor; records must be supplied by another process or through database administration.

### Files, dates and data compatibility

- Original file bytes are not stored in PostgreSQL. The database holds metadata and links; the file itself is stored in `DOCUMENT_STORAGE`.
- New files use a content-based path: `<first-2-SHA-characters>/<SHA>.<extension>`. Identical content can be shared while maintaining separate client links.
- `EXISTING_FILE_ROOTS` grants access to existing local or mounted resources. It is not a Nextcloud client or a file synchronizer.
- The backend stores monetary amounts as `Decimal` / `numeric`, without calculating persisted monetary values using `float`.
- The form has no separate **VAT date**. New records use the invoice issue date. The `vat_date` column remains in V5 for compatibility; the portal does not remove it or rewrite historical settlements.
- A single category/account selector does not physically merge all legacy tables. The current catalog selects an account; older fields can remain in historical data.

### Base schema versus updates

Use `database/schema_v5.sql` only to create a **new, empty database**. Do not rerun it against an existing operational database.

`run.py migrate` executes the files in `app/migrations/`. Depreciation, posting and the account catalog also have separate SQL files in `database/`; `migrate` does not execute them. Install both sets on new deployments.

## Requirements

- Python **3.11 or 3.12** and a virtual environment; the Docker image uses 3.12.
- PostgreSQL with the V5 schema and the `pgcrypto` extension; the included demonstration environment uses PostgreSQL 16.
- Tesseract OCR with the `pol`, `nld` and `eng` language data specified in `OCR_LANGUAGES`, for supporting scan analysis and splitting.
- Access to document storage, with write permissions for the application user.
- HTTPS and a reachable portal address when an external MCP client is used.
- One configured AI data preparation method: the OpenAI API with an API key, or an MCP client with portal access.

Python dependencies are pinned in `requirements.txt`. Native installation uses `requirements-native.txt`, which additionally installs `python-dotenv`. The frontend does not require Node.js or npm.

On older systems, check the interpreter version. System Python 3.7 is insufficient. If the existing `.venv` uses Python 3.11, keep using it rather than recreating it with an older `python3`.

## Running without Docker

### Already configured installation

From the application directory:

```bash
# Run from the project directory.
.venv/bin/python run.py web
```

Default address: **http://127.0.0.1:8001**. Keep the terminal open. `run.py` loads `.env` without expanding `$` characters in passwords. If a system service is already running, do not start another process on the same port.

### First native installation

On a modern Ubuntu system with Python 3.11 or newer:

```bash
sudo apt update
sudo apt install -y python3-venv tesseract-ocr tesseract-ocr-pol tesseract-ocr-nld tesseract-ocr-eng postgresql-client

# Run from the project directory.
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-native.txt
tesseract --list-langs
```

If the appropriate interpreter is available as `python3.11`, create the environment with `python3.11 -m venv .venv`. Do not recreate an existing working `.venv` unnecessarily.

Run the connection configuration tool:

```bash
.venv/bin/python prepare.py connect
nano .env
chmod 600 .env
```

The tool does not overwrite an existing `.env`. For a native installation, enter the actual PostgreSQL host; the suggested `host.docker.internal` is intended for containers. In `.env`, set `APP_ORIGIN` to the exact address used in the browser, for example `http://127.0.0.1:8001` for a local test.

Next, prepare the database and staff account as described below, configure one of the [AI methods](#ai-document-processing), and start `run.py web`. For the API option, also start the worker. For MCP, connect the MCP client. Starting the web application alone does not prepare invoices through AI.

## Database and staff account setup

### Existing database

Keep the current `DATABASE_URL`. Back up the database before changing its structure. Run application migrations explicitly:

```bash
# Run from the project directory.
.venv/bin/python run.py migrate
```

This does not install any missing standalone SQL extensions.

### New, empty database

A PostgreSQL administrator creates the database and its owner. Example **on the PostgreSQL host**, for a new local installation:

```bash
sudo -u postgres createuser --pwprompt invoice_desk
sudo -u postgres createdb --owner=invoice_desk invoice_desk
```

Then run `prepare.py connect` to configure `.env` with these credentials. Import the base schema only into an empty database:

```bash
.venv/bin/python - <<'PY'
from pathlib import Path
from dotenv import load_dotenv
from app.db import Database
import os

load_dotenv('.env', override=True, interpolate=False)
with Database(os.environ['DATABASE_URL']).connect() as c:
    existing = c.execute("SELECT to_regclass('core.clients') AS name").fetchone()
    if existing['name']:
        raise SystemExit('The database already has a schema. Import cancelled.')
    c.execute(Path('database/schema_v5.sql').read_text(encoding='utf-8'))
print('V5 schema created.')
PY

.venv/bin/python run.py migrate
```

This command does not populate client records or connect an external synchronization process.

### SQL extensions for the current version

Use the following command to set up the account catalog, posting and depreciation schedules on a new installation, or to verify them on an existing installation. All listed files must exist in the current project. A missing file or conflicting data aborts the transaction.

```bash
.venv/bin/python - <<'PY'
from pathlib import Path
from dotenv import load_dotenv
from app.db import Database
import os

load_dotenv('.env', override=True, interpolate=False)
names = [
    'gnucash_invoice_accounts.sql',
    'invoice_desk_posting.sql',
    'invoice_desk_inventaris.sql',
    'invoice_desk_inventaris_events.sql',
]
scripts = [Path('database', name).read_text(encoding='utf-8') for name in names]
with Database(os.environ['DATABASE_URL']).connect() as c:
    for script in scripts:
        c.execute(script)
print('Database extensions installed/verified.')
PY
```

The account catalog does not overwrite existing accounts with conflicting meanings. If a conflict is reported, compare the account number, name, type and active status rather than deleting data. The script contains a sample GnuCash account catalog; adapt it deliberately when using a different chart of accounts.

### Staff account

`run.py` has no `create-user` subcommand. Load `.env` and invoke the administrative module:

```bash
.venv/bin/python -c '
from dotenv import load_dotenv
import runpy
import sys

load_dotenv(".env", override=True, interpolate=False)
sys.argv = ["app.manage", "create-user"]
runpy.run_module("app.manage", run_name="__main__")
'
```

Enter an email address, name, role and a password of at least 12 characters. The command does not overwrite an existing account.

To grant an existing accountant the chief accountant role, replace the example email address below:

```bash
.venv/bin/python -m app.manage grant-chief-accountant ksiegowy@example.org
```

This subcommand loads `.env` itself and accepts an active account with the `accountant` or `chief_accountant` role.

## Configuration

Configuration is stored in `.env`. Do not commit this file, passwords or client documents to the repository. Addresses below are examples, not existing server credentials or settings.

| Variable | Value / meaning |
|---|---|
| `DATABASE_URL` | Required PostgreSQL DSN; the configuration tool URL-encodes special characters in the password. |
| `APP_SESSION_SECRET` | Required session secret of at least 32 characters, generated by the configuration tool. Changing it invalidates existing sessions. |
| `DOCUMENT_STORAGE` | New file storage; defaults to `./data/files`, or `/data/files` in Compose. |
| `EXISTING_FILE_ROOTS` | Additional permitted file directories; colon-separated on Linux. |
| `APP_ORIGIN` | Exact browser origin, such as `https://invoices.example.org`, without a path. |
| `COOKIE_SECURE` | `1` for HTTPS; defaults to `0`. When set to `1`, login over plain HTTP will not work correctly. |
| `OCR_LANGUAGES` | Defaults to `pol+nld+eng`; requires the corresponding Tesseract language data. |
| `BACKGROUND_PROCESSING` | Set to `1` for both documented AI methods. `0` selects legacy local processing rather than the documented AI workflow. |
| `AI_PROVIDER` | `mcp` selects MCP; other values follow `AI_ENABLED`. The API configuration tool writes `openai`. |
| `AI_ENABLED` | Set to `1` for the API. With `AI_PROVIDER=mcp`, the configuration tool sets `0` because AI runs through the MCP client. Outside MCP, `0` selects legacy local processing. |
| `OPENAI_API_KEY` | Key used only in API mode. |
| `OPENAI_MODEL` | API model name; the default in the code is `gpt-4.1-mini`. |
| `PROCESSING_POLL_SECONDS` | Worker queue scan interval; defaults to 15 seconds, limited to 5–300 seconds. |
| `PROCESSING_GAP_SECONDS` | Worker pause after each job; defaults to 2 seconds, limited to 1–60 seconds. Errors can cause longer delays. |
| `MCP_PUBLIC_URL` | Public HTTPS address of this application, without a path, such as `https://invoices.example.org`. |
| `MCP_CLIENT_ID`, `MCP_CLIENT_SECRET` | Private OAuth client credentials created by `configure_mcp.py`. |
| `MCP_ALLOWED_STAFF_IDS` | Comma-separated list of permitted staff IDs. |
| `MCP_REDIRECT_URIS` | Additional callback addresses allowed by the OAuth implementation. |
| `APP_DEMO`, `DEMO_SEED`, `DEMO_PASSWORD` | Variables for a separate demonstration environment; do not enable demo seeding in an operational database. |

`run.py` loads `.env`, but `python -m app.worker` and most `app.manage` commands do not. The worker and administrative examples below therefore load the configuration explicitly.

## AI document processing

In the documented workflow, **AI prepares the proposed form data**, including accounts and VAT codes from the portal's reference data. Staff compare the result with the original, correct errors or missing values, and confirm the review. AI should not guess uncertain values; these may remain for correction.

| AI method | Who prepares the proposal | Required processes |
|---|---|---|
| OpenAI API | The worker sends images and context to the model through the API, then validates the response. | Web application, `app.worker` and an API key. |
| MCP | An external AI client retrieves documents and submits proposals using MCP tools. | Continuously available web application and a connected MCP client; no `app.worker`. |

The AI method is independent of whether the application runs with or without Docker. Both deployments use the same review and posting rules.

**The role of OCR:** Tesseract remains a technical dependency, including scan layout recognition for splitting. It does not replace AI form preparation in the documented workflow. Legacy OCR-only modes remain in the code for compatibility, but the instructions below configure AI processing and do not require enabling them. Updating the documentation does not remove these modes from the code.

Processing produces a proposal for review. If a document changes during processing, version checks prevent an old job result from overwriting current data.

### OpenAI API — configuration without Docker

```bash
.venv/bin/python configure_ai.py
.venv/bin/python run.py migrate
```

The configuration tool reads the API key without echoing the input and enables queued AI processing. Restart the web application after configuration. Start the worker in another terminal:

```bash
# Run from the project directory.
.venv/bin/python - <<'PY'
from dotenv import load_dotenv
import runpy

load_dotenv('.env', override=True, interpolate=False)
runpy.run_module('app.worker', run_name='__main__')
PY
```

This is a separate API-based method; an MCP connection does not supply its API key or API permissions. Container commands are covered under [Running with Docker](#running-with-docker).

The implementation sends requests to `https://api.openai.com/v1/responses`. The model must be available to the API key and support the required input and structured output. HTTP 429 can indicate a rate limit or exhausted quota; retrying does not resolve the underlying cause. The application limits attempts to three and defers jobs after transient errors.

### MCP — configuration without Docker

First provide a working public HTTPS address pointing to this application. Then, from the project directory:

```bash
.venv/bin/python configure_mcp.py
.venv/bin/python run.py migrate
.venv/bin/python run.py enable-mcp
```

Before switching, stop the API worker or any legacy OCR worker left running. `enable-mcp` checks worker activity and moves unfinished jobs to MCP mode. **Do not run `app.worker` in this mode.**

The tool asks for the HTTPS address and an existing staff account, preserves the database connection, and prints the `/mcp` URL and OAuth credentials. Enter the secret only in the client configuration. This version's configuration tool accepts `admin`, `accountant` and `reviewer` accounts. The server's access mechanism also supports `chief_accountant`, but the tool has a narrower account selection condition.

The MCP client uses these tools:

| Tool | Action |
|---|---|
| `invoice_queue_report` | Reports queue status and recently changed documents. |
| `next_invoice` | Reserves one document for 20 minutes. |
| `get_invoice_page` | Retrieves a page image from the reserved document. |
| `save_invoice_proposal` | Saves the proposal after all pages have been viewed. |
| `release_invoice` | Releases the reservation with a reason. |

The server does not expose posting, document deletion or arbitrary SQL through MCP. In this mode, `queued` means waiting for the MCP client. The portal does not invoke ChatGPT or create a schedule itself. Any recurring task runs on the client side and must have access to these tools.

To revoke all saved MCP access grants:

```bash
.venv/bin/python run.py revoke-mcp
```

## Splitting documents

### Automatic splitting

`app/auto_split.py` analyzes word positions from Tesseract TSV and gaps in the image. It looks for separate regions with document features and amounts; confident recognition also checks headings, dates and totals. It supports documents arranged side by side, stacked vertically and in nested subdivisions. This mechanism runs locally and does not call an API itself.

- A new JPG/PNG or single-page PDF is analyzed during upload.
- Confident results create separate documents and PNG crops. In queued mode, each document is queued separately.
- For uncertain results, the portal proposes regions to review and adjust before saving.
- An existing document flagged as containing multiple invoices is analyzed when opened, provided the form has no unsaved changes.
- **Split automatically** retries analysis on demand; **Select manually** retains the existing manual workflow. The current Polish interface labels these actions **Rozdziel automatycznie** and **Zaznacz ręcznie**.

Multi-page PDFs are not split automatically because several pages may belong to the same invoice. Overlapping, rotated or poorly readable documents, or scans without clear gaps, may require manual splitting. Detecting regions does not guarantee that the accounting data is correct.

### Saving and undoing a split

A split contains 2–20 non-overlapping regions. Manual selection supports page selection and crop rotation. Each region creates a separate document with its own status, processing result and subsequent invoice.

The original is retained, marked `archived` and removed from the working queue to prevent double counting. Import tables record the provenance of the crops. Automatic split batches also record the method and detector version.

A split can be undone while its child documents have no saved invoices and have not been processed further. Undoing preserves history: it withdraws the child documents and restores the original. Automatic analysis does not immediately recreate an undone split; another split can be requested explicitly.

The operation uses version checks and idempotency. Repeating the same request does not create another batch. Files are outside the SQL transaction: a failure may leave unreferenced files, but not a partially saved database split. Do not bulk-delete such files without checking their references.

## Posting, corrections and the client ledger

### Accounts and VAT

AI proposes an account and VAT code for each line. The form has one category/account field and a separate VAT code, which staff review and correct when needed. Account numbers help with searching and identification. The catalog distinguishes purchase and sales accounts; changing an account does not automatically set the VAT rate. Both values are checked independently.

| Interface label | Internal database code |
|---|---|
| VAT 0% | `PORTAL_0` |
| VAT 9% | `PORTAL_9` |
| VAT 21% | `PORTAL_21` |
| VAT exempt, shown as `VAT zwolniony` | `PORTAL_EXEMPT` |

Zero-rated VAT and exemption have separate identifiers and meanings in the model. Validation uses account numbers and code identifiers; changing a display label alone does not change posting rules.

### Posting in one transaction

The `/book` endpoint saves and posts an invoice in one transaction. If any stage fails, the entire operation is rolled back. Checks include line amounts, net + VAT = gross, active accounts, VAT direction and validity, and closed periods.

| Type | Basic journal entry |
|---|---|
| Purchase | Debit the selected account, debit `150 Te vorderen BTW` for deductible VAT, and credit `140 Crediteuren` for the gross amount. Non-deductible VAT increases the amount posted to the line's account. |
| Sale | Debit `130 Debiteuren` for the gross amount, credit the income account, and credit `151 Te betalen BTW` for output VAT. |

Entries use the `IDESK` journal. The engine uses control accounts and VAT accounts directly; they are not selected as ordinary line accounts. They must exist with the correct number, name, type and direct posting permission.

The current engine accepts `invoice_kind='invoice'`, a positive gross amount, EUR, and purchase/sale transactions. It rejects foreign VAT number prefixes, special accounts `460`, `490`, `840`, `860`, and VAT codes outside the supported set. **Splitting a receipt and preparing its data does not establish support for receipts as a separate posting type.** The form does not validate VAT numbers through VIES.

### Duplicates

1. **During upload:** an identical file SHA-256 for the same client returns the existing document. If the original has been split, its child documents are returned.
2. **During splitting:** repeating the same operation does not create more documents; identical crops within one batch are rejected.
3. **When saving an invoice:** checks use the normalized invoice number, client, invoice type, kind and year, together with counterparty data. The import key and transaction locks also protect against saving the same document again.

A different photograph of the same invoice may have a different SHA-256. In that case, protection relies on invoice data rather than the file alone. Documents without an invoice number require particular staff attention.

### Correcting a posted invoice

This feature is available to `admin` and `chief_accountant`. It requires a reason, the current version and an open period. It creates a reversal of the previous journal entry and a replacement invoice and journal entry, rather than overwriting posted lines.

Corrections are limited to entries posted through this portal. A settled payment, VAT return link or fixed asset master record may block the simple correction path. For an open period, VAT register records are replaced transactionally while preserving an audit trail of the previous data.

Depreciation schedules can be carried over to the replacement invoice when the corresponding lines have not changed in relevant fields. Changed lines may require the schedules to be reviewed again.

### Client ledger

The tab lists entries by posting date, with 30 entries per page. For corrections made through the portal, the main list shows the current entry; previous entries and reversals are available in its history. Details show the accounts and provide a link to the invoice.

**Debit (Wn)** and **Credit (Ma)** are the two sides of an entry; their totals are equal in a balanced entry. **Debiteuren** means receivables in account 130, while **Crediteuren** means payables in account 140. These are not alternative names for debit and credit.

The Debiteuren/Crediteuren figures at the top show cumulative balances through the end of the selected period, including payments, opening entries and reversals recorded in the ledger. They are neither the sum of gross invoice amounts on the current page nor simply movements between the start and end dates.

In **Accounts and balances** (**Konta i salda**), the account tree lists active accounts and historical accounts with posted activity. Each row shows the opening balance, debit and credit movements in the selected period, and the closing balance. Expand a parent account to see its children; select an account to filter the journal. Balances are shown in EUR and include reversals.

## Depreciation

The tab is available for a client with a positive Inventaris purchase line in account `002`, from an approved or posted invoice. Legacy data can use the Inventaris category without an account. Lines superseded by a reversal are not current sources for a new schedule.

A schedule contains the asset name, date placed in service, initial cost, residual value, useful life of **1–600 months**, a note of up to 1000 characters, and an optional loss type and date.

The application calculates a straight-line schedule, showing annual totals and monthly depreciation. Partial months are prorated by days, with cumulative rounding to cents. For theft (`stolen`) or loss (`lost`), the schedule stops depreciation from the loss date and shows the remaining value.

**This is a schedule, not automatic posting of depreciation.** The module does not create ledger entries, records in `assets.fixed_asset_depreciation_entries`, loss write-offs or insurance claims from these data. The accountant must review the period, values and treatment of the loss. Schedule changes use a revision number and an audit trail.

## Roles and security

| Role | Read | Upload, split, proposals, initial posting and depreciation schedules | Correct a `posted` invoice |
|---|---|---|---|
| `read_only` | Yes | No | No |
| `reviewer` | Yes | Yes | No |
| `accountant` | Yes | Yes | No |
| `chief_accountant` | Yes | Yes | Yes |
| `admin` | Yes | Yes | Yes |

This table reflects the current code: `reviewer` is not a view-only role. The permission model does not restrict staff to an assigned list of clients; an active account can access the client records available in the portal. MCP additionally requires the staff ID to be allowed and OAuth consent to be granted.

Passwords use PBKDF2-SHA256 with an individual salt and 1000000 iterations. Sessions are signed, valid for up to 8 hours, and stored in an HttpOnly, SameSite Strict cookie; Secure should be enabled for HTTPS. Write operations require a CSRF token and undergo origin checks. Account activity is checked on requests. Login attempt limits are stored in process memory.

Access to originals and previews requires authentication. Files are checked for type, size, permitted directory and SHA-256. PostgreSQL transactions, locks and triggers protect posted data and the audit trail. `updated_at` and revision checks detect concurrent changes.

These mechanisms do not replace proper server configuration, backups or access control for PostgreSQL and document storage.

## System service and HTTPS

### Systemd example with a local proxy

This example assumes a regular system user named `invoice-desk` and the directory `/opt/invoice-desk`. Adjust the user and group to the actual file owner. **Do not replace an existing working service without comparing its settings.**

File: `/etc/systemd/system/invoice-desk.service`:

```ini
[Unit]
Description=Invoice Desk
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=invoice-desk
Group=invoice-desk
WorkingDirectory=/opt/invoice-desk
ExecStart=/opt/invoice-desk/.venv/bin/python /opt/invoice-desk/run.py web
Restart=on-failure
RestartSec=5
Environment=PYTHONUNBUFFERED=1
UMask=0077

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now invoice-desk
sudo systemctl status invoice-desk --no-pager
```

The service above starts only the web application. AI through the API also requires a worker using the same configuration and storage. Do not add that worker for MCP.

### Reverse proxy

`run.py web` listens on **127.0.0.1 on the application server**. A proxy on the same host can reach this address. Example Caddy configuration using an example domain:

```caddyfile
faktury.example.org {
    reverse_proxy 127.0.0.1:8001
}
```

If Caddy runs on another server or inside a container, its `127.0.0.1` is not the application server. The proxy must target a reachable LAN address, and Uvicorn must listen on that interface. Replace `192.0.2.10` below with the actual address of the application server. For an application server at `192.0.2.10`, an example upstream is `192.0.2.10:8001`; the standard `run.py web` command does not expose that interface.

In a custom service, use `EnvironmentFile=/opt/invoice-desk/.env` and replace `ExecStart` with:

```ini
ExecStart=/opt/invoice-desk/.venv/bin/python -m uvicorn app.main:create_app --factory --host 192.0.2.10 --port 8001 --no-access-log
```

Here, systemd supplies `.env` because Uvicorn does not load it itself. Restrict network access to the backend port to the required proxy hosts. For a public address, set `APP_ORIGIN` to that HTTPS origin and `COOKIE_SECURE=1`.

The proxy should forward the entire application, including `/mcp`, `/oauth/` and `/.well-known/`, preserve Host and HTTPS information, and allow uploads of at least 26 MiB. Do not put a separate proxy login page in front of MCP endpoints. Do not log OAuth codes or tokens. With Caddy in Docker, edit the configuration mounted into the container; `/etc/caddy/Caddyfile` may not exist on the host.

## Running with Docker

This is the second deployment option for the same application. Do not run the native service and Compose on the same port simultaneously. A working Docker Engine compatible with the system/cgroups and the Compose plugin is required. PostgreSQL can remain on a separate server.

### 1. Database and shared configuration

Prepare the V5 database, SQL extensions and staff account as described in [Database and staff account setup](#database-and-staff-account-setup). If the portal is already configured, retain its database and `.env`.

For a fresh project, create the connection configuration on the host:

```bash
# Run from the project directory.
python3 prepare.py connect
```

`prepare.py` uses Python's standard library. It does not run the application or require a native `.venv`, and it does not overwrite an existing `.env`. Configure `APP_ORIGIN` and `COOKIE_SECURE` to match the address used in the browser.

The database host in `DATABASE_URL` must be reachable **from inside the container**. There, `127.0.0.1` refers to the container itself. `compose.yml` does not create a PostgreSQL server. For a database on the Docker host, `host.docker.internal` is provided; for another server, use its address.

Build the image:

```bash
docker compose -f compose.yml build
```

**Only for a new, empty database**, after a PostgreSQL administrator has created the database and user, import the base schema from a container. Skip this step for an existing database:

```bash
docker compose -f compose.yml run --rm --no-deps -T \
  --user "$(id -u):$(id -g)" \
  --volume "$PWD/database:/schema:ro" \
  app python -c '
from pathlib import Path
from app.db import Database
import os

with Database(os.environ["DATABASE_URL"]).connect() as c:
    existing = c.execute("SELECT to_regclass(%s) AS name", ("core.clients",)).fetchone()
    if existing["name"]:
        raise SystemExit("The database already has a schema. Import cancelled.")
    c.execute(Path("/schema/schema_v5.sql").read_text(encoding="utf-8"))
print("V5 schema created.")
'
```

Run application migrations from the image without installing the application's Python environment on the host:

```bash
docker compose -f compose.yml run --rm --no-deps app python -m app.manage migrate
```

The separate SQL extensions are not run by `migrate`. The current Dockerfile does not copy `database/`. To install them from a container instead of using the native command, mount the directory and execute the scripts in one transaction:

```bash
docker compose -f compose.yml run --rm --no-deps -T \
  --user "$(id -u):$(id -g)" \
  --volume "$PWD/database:/schema:ro" \
  app python -c '
from pathlib import Path
from app.db import Database
import os

names = ["gnucash_invoice_accounts.sql", "invoice_desk_posting.sql",
         "invoice_desk_inventaris.sql", "invoice_desk_inventaris_events.sql"]
scripts = [Path("/schema", name).read_text(encoding="utf-8") for name in names]
with Database(os.environ["DATABASE_URL"]).connect() as c:
    for script in scripts:
        c.execute(script)
print("Database extensions installed/verified.")
'
```

Back up the database before installing extensions and account for its current state. Do not reimport `schema_v5.sql` into an operational database. This command requires Docker Engine access and permission to read the SQL files.

If you need to create a staff account, do so after preparing the schema and extensions:

```bash
docker compose -f compose.yml run --rm --no-deps app python -m app.manage create-user
```

### 2. Choose one AI method

If `.env` already has the correct AI configuration, skip the configuration tool and proceed to starting the selected option.

When switching from Docker + API to Docker + MCP, first stop the old stack using `docker compose -f compose.yml -f compose.background.yml stop` so the API worker is not left running. Choose the configuration and Compose files deliberately; do not run both processing methods at once.

#### Docker + MCP

This requires the portal's public HTTPS address and an existing staff account. Run the configuration tool in a temporary container. It mounts the project directory to update its `.env` and installs the supporting `python-dotenv` package only in that container's `/tmp` directory:

```bash
docker compose -f compose.yml run --rm --no-deps \
  --user "$(id -u):$(id -g)" \
  --volume "$PWD:/project" --workdir /project \
  app sh -c 'python -m pip install --no-cache-dir --disable-pip-version-check --target /tmp/invoice-tools python-dotenv==1.2.1 && PYTHONPATH=/tmp/invoice-tools python configure_mcp.py'

docker compose -f compose.yml run --rm --no-deps app python -m app.manage enable-mcp
docker compose -f compose.yml up -d
```

The tool sets `BACKGROUND_PROCESSING=1`, `AI_PROVIDER=mcp`, `AI_ENABLED=0`, and the HTTPS/OAuth settings. Enter the secret only in the MCP client configuration. Connect the client to `/mcp` and test one invoice. **Do not run `compose.background.yml` or `app.worker` in this option**: AI submits proposals through the MCP client.

#### Docker + OpenAI API

If the API key has not been configured, run the configuration tool in a temporary container:

```bash
docker compose -f compose.yml run --rm --no-deps \
  --user "$(id -u):$(id -g)" \
  --volume "$PWD:/project" --workdir /project \
  app python configure_ai.py

docker compose -f compose.yml -f compose.background.yml build
docker compose -f compose.yml -f compose.background.yml up -d
```

This option sets `BACKGROUND_PROCESSING=1`, `AI_PROVIDER=openai`, `AI_ENABLED=1`, and uses `OPENAI_API_KEY`. `compose.background.yml` starts the web application and API worker with shared document storage. Documents are processed sequentially in the background.

### 3. Checks and operation

```bash
docker compose -f compose.yml ps
docker compose -f compose.yml logs --tail=100 app
```

For the API option, also check the worker:

```bash
docker compose -f compose.yml -f compose.background.yml logs --tail=100 worker
```

After changing `.env`, recreate containers with the appropriate `up -d` command so they receive the new variables. After changing code, use the appropriate `up --build -d` command. Restarting an existing container does not update its image or reload Compose environment settings.

### Ports, storage and switching from a native installation

The default address is **http://127.0.0.1:8001** on the Docker host. Uvicorn listens on port 8000 inside the container, which Compose maps to the host's loopback interface. If the reverse proxy is on another server, map the port to a reachable LAN address, such as `192.0.2.10:8001:8000` (replace the example IP), and restrict backend access to the required proxy hosts.

Files are stored in `/data/files` on the `invoice_files` volume. **Switching an existing `.venv` installation to Docker does not migrate documents automatically.** Mount the same storage appropriately or copy its full contents into the volume, preserving the directory structure and database. Both API-mode processes must see the same files. The image runs as UID `10001`; grant that user the required storage permissions.

Stopping containers normally does not delete volumes. Do not use `down -v` as an update procedure: it removes the project's volumes. A database backup does not include files stored in a volume.

### Separate demonstration environment

In a separate project copy:

```bash
python3 prepare.py demo
docker compose --env-file .env.demo -f compose.demo.yml up --build -d
```

The configuration tool prints the login credentials; the demo is available at `http://localhost:8000`. It creates an isolated PostgreSQL database, initializes the schema and account catalog, and generates a synthetic invoice on first startup. The demo uses local OCR and does not require an AI key. For AI form preparation, configure one of the AI methods in a separate installation. Never point the demo at an operational database.

## Updates and backups

### Updating an existing directory

Back up the database and document storage before updating. Keep `.env` and document storage outside version control. Update the application source, follow the release's database migration instructions, and restart the application and the API worker if used. For Docker, rebuild and recreate the services. Verify the update on a test database before using it on operational data.

After interface changes, refresh with **Ctrl+F5**. Changes to `.env` or backend code require restarting the relevant processes. For migrations, schedule a pause in writes and check the result before resuming work.

### What to back up

A recoverable system backup includes all of the following:

1. A full PostgreSQL dump, including the audit trail, queue and relationships.
2. `DOCUMENT_STORAGE` and any resources used through `EXISTING_FILE_ROOTS`.
3. `.env`, system service configuration and reverse proxy configuration, stored with restricted access.
4. The code version and update packages required to restore it.

Use `pg_dump` with custom format (`-Fc`). To restore a consistent database and file store, pause new uploads, splits and other writes during the backup. Keep copies off the application server and test restoration into a separate database.

The following example reads connection parameters from `.env` without putting the password in command-line arguments:

```bash
.venv/bin/python - <<'PY'
from datetime import datetime
from pathlib import Path
from dotenv import dotenv_values
from psycopg.conninfo import conninfo_to_dict
import os
import subprocess

os.umask(0o077)
config = dotenv_values('.env', interpolate=False)
params = conninfo_to_dict(config['DATABASE_URL'])
env = os.environ.copy()
mapping = {
    'host': 'PGHOST', 'hostaddr': 'PGHOSTADDR', 'port': 'PGPORT',
    'dbname': 'PGDATABASE', 'user': 'PGUSER', 'password': 'PGPASSWORD',
    'sslmode': 'PGSSLMODE', 'sslrootcert': 'PGSSLROOTCERT',
    'sslcert': 'PGSSLCERT', 'sslkey': 'PGSSLKEY',
    'service': 'PGSERVICE', 'connect_timeout': 'PGCONNECT_TIMEOUT',
}
unknown = set(params) - set(mapping)
if unknown:
    raise SystemExit('The DSN contains additional options; include them in the backup procedure: ' + ', '.join(sorted(unknown)))
env.update({mapping[k]: str(v) for k, v in params.items()})
folder = Path('backups')
folder.mkdir(mode=0o700, exist_ok=True)
target = folder / ('database-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '.dump')
subprocess.run(['pg_dump', '--format=custom', '--file', str(target)], env=env, check=True)
print('Saved:', target)
PY
```

This command creates **only a database dump**. Back up files, configuration and code separately. When restoring, first restore the dump into a new database, restore storage with matching paths, and then set a test `DATABASE_URL`. Do not test restoration by overwriting the operational database.

## Troubleshooting and tests

### Basic checks

```bash
# Run from the project directory.
.venv/bin/python --version
tesseract --list-langs
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8001/
sudo systemctl status invoice-desk --no-pager
sudo journalctl -u invoice-desk -n 100 --no-pager
```

HTTP 200 from `/` confirms that the page is served, but does not test login, saving invoices or MCP processing. If Uvicorn listens only on a LAN address rather than loopback, use its actual address in `curl`.

A read-only check of the connection and queue status:

```bash
.venv/bin/python - <<'PY'
from dotenv import load_dotenv
from app.db import Database
import os

load_dotenv('.env', override=True, interpolate=False)
with Database(os.environ['DATABASE_URL']).connect() as c:
    print(c.execute('SELECT current_database() AS database, version() AS version').fetchone())
    table = c.execute("SELECT to_regclass('documents.processing_jobs') AS name").fetchone()
    if table['name']:
        for row in c.execute('SELECT mode, status, count(*) AS count FROM documents.processing_jobs GROUP BY mode, status ORDER BY mode, status'):
            print(row)
PY
```

| Symptom | What to check |
|---|---|
| Cannot connect to the database | Host, port, SSL, user and PostgreSQL availability. Do not paste the DSN password into a support request. |
| Missing tables / `relation does not exist` | The selected database, `app/migrations/` and the separate SQL extensions. |
| Empty client list | Active records in `core.clients`; this portal does not perform client synchronization. |
| Missing accounts or VAT codes | Account catalog, active status, types, direction and validity of codes, and catalog script conflicts. |
| Jobs remain `queued` | For API-based AI: the worker and its heartbeat. For MCP: client connection, consent and task execution on the client side. |
| Repeated `processing` → `queued` transitions | Processing failure, expired reservation or deferral. Check the job's message. |
| API returns 429 | The underlying API rate limit/quota issue. Once resolved, retry failed jobs. |
| Login does not persist | HTTP with `COOKIE_SECURE=1`, mismatched `APP_ORIGIN`, a changed secret or the wrong portal address. |
| Version conflict 409 | The document changed in another session or during processing. Open the current data before saving. |
| Splitting is uncertain | Readability, spacing, complete documents and Tesseract language data. Use proposed regions or manual selection. |
| Service does not start / port in use | Another Uvicorn process on the same port, the `.venv` interpreter, file permissions and service configuration. |
| Old interface after an update | Ctrl+F5 and compatibility of the replaced files with the rest of the deployment. |

### Input limits

- Single file: up to **25 MiB**; the full HTTP upload limit is **26 MiB**.
- PDF: **1–20 pages**, without a password.
- Image: JPG/PNG, up to **35 million pixels**.
- Split: **2–20** non-overlapping regions.
- Document list API: batches of 50; ledger: 30; depreciation list: 50.

### Tests

Run database tests only against a separate database, such as `invoice_desk_test`, with the base schema, application migrations, posting and depreciation extensions. Leave the optional GnuCash catalog uninstalled: catalog tests install it temporarily. Tests create data and may modify auxiliary structures. Never set `TEST_DATABASE_URL` to the operational database.

```bash
.venv/bin/python -m pip install -r requirements-test.txt
read -r -s -p 'DSN of the separate invoice_desk_test database: ' TEST_DATABASE_URL
echo
export TEST_DATABASE_URL
.venv/bin/python -m pytest tests
unset TEST_DATABASE_URL
```

For the optional MCP SDK interoperability test, install `requirements-test-mcp.txt` instead of `requirements-test.txt`. Run the JavaScript tests with:

```bash
node --test tests/test_booking_ui.cjs
```

The tests generate synthetic documents. Check HTTPS, MCP integration and external AI processing separately in the target environment.

## Main endpoints

Data operations require appropriate permissions; writes also require CSRF. This table maps the API and does not bypass the form or its validation.

| Method and path | Function |
|---|---|
| `POST /api/login`, `POST /api/logout`, `GET /api/me` | Staff session. |
| `GET /api/clients`, `GET /api/references` | Clients and form reference data. |
| `GET /api/documents` | Filtered document list. |
| `POST /api/documents/upload` | Upload a file and attempt automatic splitting. |
| `GET /api/documents/{id}` | Document, files, proposal and current invoice. |
| `GET /api/documents/{id}/file`, `/pages`, `/preview/{page}` | Original, page metadata and preview. |
| `POST /api/documents/{id}/auto-split` | Local analysis and saving of a confident split, or proposed regions. |
| `POST /api/documents/{id}/split`, `/unsplit` | Save a manual split or undo a split. |
| `GET /api/processing`, `GET /api/documents/{id}/preparation` | Queue and job status. |
| `POST /api/processing/retry`, `POST /api/documents/{id}/prepare` | Retry processing. |
| `POST /api/documents/{id}/book` | Save and post; also correct an invoice when authorized. |
| `POST /api/documents/{id}/approve` | Retained legacy approval-only endpoint; does not post to the ledger. |
| `PUT /api/booking-mappings/{category_id}` | Compatibility with older client category/account mappings. |
| `GET /api/ledger`, `/api/ledger/{id}`, `/api/ledger/{id}/history` | Ledger (optionally filtered by account), details and history. |
| `GET /api/ledger/accounts` | Account hierarchy and balances for the selected client and period. |
| `GET /api/inventaris`, `GET/PUT /api/inventaris/{line_id}` | Inventaris items and depreciation schedules. |
| `/mcp`, `/oauth/…`, `/.well-known/…` | MCP tools and authorization, available in MCP mode. |

## About the project

This project was developed for **TwojDoradca.nl**.
