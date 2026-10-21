# Review Pulse Starter

**Your own customer reviews, a review dashboard, an evidence-backed report, and human-approved publishing through n8n MCP.**

This starter uses a **Streamlit dashboard, FastAPI ingestion API, and SQLite**, with n8n for scheduled ingestion and MCP publishing.

Start locally without API keys. Then connect your own reviews, documentation, model provider, Google Doc, Gmail account, and n8n instance.

## What is included

- Open dashboard: overview, rating trends, sentiment, primary themes, review search/filtering, source library, reports, approval, and activity.
- CSV upload and a job API with optional ingestion-token status limits. A durable queue runs analysis outside request handlers; tokens do not restrict the open dashboard.
- Local keyword classification or optional Groq/OpenAI-compatible JSON classification. Numerical metrics always come from the accepted dataset, not a model's arithmetic.
- Support evidence retrieved from your uploaded text/Markdown documents. Returned citations must reference known chunks and contain exact source excerpts; unsupported answers are rejected or withheld.
- Immutable publishing previews, SHA-256-bound approval, server-configured destinations, a document marker, persisted step status, and manual reconciliation for uncertain writes.
- Two n8n workflow exports: an authenticated MCP publishing server, and scheduled ingestion with a report-ready notification.
- Synthetic sample data, Python setup scripts, n8n workflow exports, and automated tests.

This is a **single-owner starter**, not a multi-tenant SaaS product or an independently security-audited production system. Read the deployment and data-handling limits below.

> **No login is required. Anyone who can reach the application can read, change, or publish its data. Do not deploy it on a public network or use it with private customer reviews unless you add an access-control layer in front of it.**

## 1. Run the local application

Use Python 3.12 or 3.13. No Node.js build or database server is needed. Streamlit serves the dashboard; FastAPI provides the API used by the dashboard and n8n. Run them as separate processes.

macOS/Linux, from the extracted project directory:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python scripts/init_env.py
```

Start the API in one terminal:

```bash
uvicorn reviewpulse.api:create_app --factory --host 127.0.0.1 --port 8000 --workers 1 --no-access-log
```

Start the Streamlit dashboard in a second terminal:

```bash
streamlit run app.py
```

The dashboard opens at **http://localhost:8501** and connects to the API at **http://127.0.0.1:8000** by default. Set `REVIEW_PULSE_API_URL` if the API is hosted elsewhere. Keep the API reachable by n8n over HTTPS when using scheduled ingestion.

Windows PowerShell: replace the first two commands with:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
```

The Streamlit dashboard opens directly; there is no workspace password. The setup script generates random API tokens and will not overwrite an existing `.env`.

Click **Explore the synthetic demo** in the Streamlit sidebar. This creates 168 fictional reviews and fictional documentation, runs local analysis, and opens the dashboard. On **Reports & approval**, inspect the exact previews and enter `APPROVE`.

**In the default `PUBLISH_MODE=local`, approval only writes demonstration document/draft records to SQLite. No Google or n8n calls occur.** Inspect the records in **Activity & setup**. Local analysis also makes no model-provider requests.

## 2. Use your own data

Edit these values in `.env`, then restart the app:

```dotenv
PRODUCT_NAME=YourProduct
PRODUCT_DESCRIPTION=A short factual description of your product and customers.
ANALYSIS_MODE=local
PUBLISH_MODE=local
```

Upload a UTF-8 CSV through **New analysis**:

```csv
text,date,rating
"The application crashes when I upload a large attachment.",2026-10-05,2
"The updated dashboard makes our daily reporting much easier.",2026-10-06,5
"I cannot understand the second charge on my subscription.",2026-10-07,1
```

Those three rows illustrate the format, not a sufficient dataset. The default minimum is **5 usable reviews**, with a warning below 100. The default maximum is **5,000 input rows** and the **entire encoded request** must fit within 5 MB. Extra CSV fields are ignored. A supplied review ID is not persisted: the app creates stable internal IDs from normalized content.

Required fields are `text`, `date`, and `rating`. Common aliases such as `review`, `created_at`, and `score` are accepted. Use ISO dates (`YYYY-MM-DD`) or ISO timestamps, and integer ratings from 1 to 5. At least five words are required per review. Invalid, duplicate, too-short, oversized, future-dated, and out-of-window records are reported by the validator.

The retained window is **84 calendar days ending at the latest accepted review**, not necessarily today. Because weeks start Monday, that window can intersect 13 calendar-week buckets. Compare reporting periods with that distinction in mind.

For unrated tickets, adapt `ingest.py` and the rating-based metrics explicitly; do not manufacture star ratings. This implementation does not scrape app stores or ship a connector for every possible feedback platform.

### Customize themes and investigations

Edit `config/themes.json` to set the primary themes, local-mode keywords, descriptions, and suggested investigation actions. Keep unique IDs and the `other` and `praise` fallback entries. New jobs snapshot the taxonomy; previously prepared reports stay unchanged.

Local rules are a useful offline baseline, especially for English feedback. They are not an LLM or a validated multilingual classifier. Suggested actions are templates, not model-proven root causes.

### Add product documentation

Open **Knowledge base -> Add documentation**. Paste relevant text or load a `.txt`/`.md` file, give it a title, and optionally include its reference URL. The app deliberately **does not fetch the URL**. PDFs and DOCX files are not parsed by this version; supply reviewed plain text.

Enter a support question during **New analysis**. All active non-demo documents are snapshotted into the job unless an API caller supplies an explicit `source_ids` list. Synthetic demo documents are excluded from your own-data jobs. Deleting a document removes it from future selection but does not erase evidence already snapshotted into reports.

## 3. Enable the LLM provider

Local analysis is sufficient for a first run. To use the implemented Groq-compatible integration, edit `.env`:

```dotenv
ANALYSIS_MODE=llm
LLM_API_KEY=your_provider_key
LLM_BASE_URL=https://api.groq.com/openai/v1
LLM_MODEL=openai/gpt-oss-20b
```

Restart the app and create a **new** analysis. Old runs are not silently regenerated. Groq compatibility/model references are in [docs/SOURCES.md](docs/SOURCES.md).

The configured provider must support `/chat/completions`, `response_format` JSON object mode, and `max_completion_tokens`; not every service advertised as OpenAI-compatible implements all three identically. Adapt `reviewpulse/llm.py` for provider-specific differences.

In LLM mode, cleaned reviews, product context, the taxonomy, your support question, and selected documentation excerpts are sent to the provider. Basic email/phone masking is **not complete anonymization**. Remove other sensitive information and confirm that your data-processing permissions and provider settings permit this use. Model output still requires human review.

## 4. Connect n8n MCP and Google

Install the optional SDK:

```bash
python -m pip install -r requirements-mcp.txt
```

Import **`n8n/mcp-publishing.json`** into n8n. Select your Google Docs and Gmail OAuth credentials. Configure a Bearer credential on the MCP Server Trigger using the token generated in your `.env`. Publish the workflow and copy the exact **Production Streamable HTTP URL**.

Set your live destinations:

```dotenv
PUBLISH_MODE=mcp
N8N_MCP_URL=https://your-n8n.example/mcp/the-production-path
N8N_MCP_TOKEN=the_same_random_token_as_the_trigger_credential
GOOGLE_DOC_ID=your_google_document_id_not_the_whole_url
REPORT_RECIPIENT=you@your-company.example
```

Restart, then validate the connection without invoking any publishing tool:

```bash
python -m reviewpulse.cli check-mcp
```

Create a **new non-demo report**, review it, and approve it. The sequence is: read/check the destination document, append the approved text when its marker is absent, verify the marker, and create an HTML Gmail **draft**. The application has no report-send operation.

The app and supplied workflow share this exact contract:

| Tool | String arguments |
| --- | --- |
| `read_doc` | `document_id` |
| `append_to_doc` | `document_id`, `entry` |
| `create_email_draft` | `recipient`, `subject`, `body` |

**Use this project's export, not the original repository's workflow unchanged.** The tool arguments differ so destinations can be bound into the approved payload. See [docs/N8N_SETUP.md](docs/N8N_SETUP.md) for the complete credential, node, and troubleshooting walkthrough.

A saved report binds its publishing mode, endpoint identity, document ID, and recipient. Changing any destination requires a new analysis before approval. Synthetic demo reports are always local and cannot be approved against live MCP configuration.

## 5. Schedule ingestion through n8n

Import **`n8n/scheduled-ingestion.json`**. It contains a manual trigger and a Monday 09:00 `Asia/Kolkata` schedule. Configure the source HTTP node, the public application URL, and the two ingestion HTTP nodes' Header Auth credentials:

```text
Header name: Authorization
Header value: Bearer <INGEST_API_TOKEN from your .env>
```

This token is different from `N8N_MCP_TOKEN`. It can submit jobs and read their limited status; it cannot approve, publish, list reports, or manage documentation.

The source must return review JSON, or replace that node with your source connector and map its rows in **Normalize and aggregate**. That node aggregates the reporting window into one job rather than launching a job per review.

After successful analysis, the final node sends the owner a **link-only notification email**. This optional notification is sent automatically; it is not the reviewed report. Delete that final node to avoid automatic email of any kind. The separate publishing workflow only creates report drafts after approval.

An n8n cloud instance needs a reachable HTTPS deployment; it cannot call the app bound to your laptop's localhost. More setup and HTTP examples: [docs/N8N_SETUP.md](docs/N8N_SETUP.md).

## 6. Tests and operating tools

```bash
python -m pip install -r requirements-dev.txt
python -m pytest
```

The delivered version passed **52 tests**, including Streamlit direct loading, open API access, CSV cleaning, numerical metrics, source checks, immutable approval, leases, duplicate prevention, and uncertain publishing recovery with mocked tools. Live provider, n8n, and Google integrations were not exercised in the build environment.

The API suite exercises direct API access, analysis, approval, and local publishing. See [docs/TESTING.md](docs/TESTING.md) for the Streamlit and API validation notes.

Useful commands:

```bash
# Analyze only; never approves or publishes.
python -m reviewpulse.cli demo
python -m reviewpulse.cli analyze data/synthetic_reviews.csv --name "My first CSV test"

# Consistent backup; choose a new destination. Backups contain sensitive data.
python -m reviewpulse.cli backup /your/private/backup/location/reviewpulse.sqlite3

# Use only when the web app has WORKER_ENABLED=false.
python -m reviewpulse.cli worker
```

## 7. Deployment and limits

Run the dashboard directly with Python and Uvicorn as shown above. When `REVIEW_PULSE_API_URL` is not configured, the dashboard starts a local FastAPI service automatically; no Streamlit Secrets are required. Use n8n Cloud or an independently hosted n8n instance for scheduled ingestion and MCP publishing; Docker is not required by this project. For scheduled ingestion, n8n must be able to reach a separately hosted API over HTTPS. Set `PUBLIC_URL` to the externally reachable application origin and restart after changes. The app assumes it is served at the origin root, not a URL subpath.

On Streamlit Community Cloud, the dashboard starts its FastAPI service in the same app container automatically. This keeps the API private to that container, but its SQLite data is not guaranteed to survive app restarts. The API has no login; the public dashboard itself allows anyone to read, change, or publish its data. Do not use it with private customer reviews or enable publishing without adding an access-control layer. n8n scheduled ingestion cannot reach this private API; use a separately hosted, access-controlled API if you need that integration.

Keep `.env`, SQLite/WAL files, private CSVs, knowledge text, report exports, and backups out of Git and public storage. Run under a dedicated non-root account. Use one web worker and local durable disk; do not put SQLite on a shared network filesystem. There is no SSO, per-user authorization, tenant isolation, automatic retention/erasure workflow, or migration system for a larger SaaS deployment.

If a publishing operation might have succeeded but its response was lost, the application stops at **needs attention**. Inspect Google Docs, Gmail Drafts, and any in-flight n8n execution before using **Reconcile external writes**. Marking an actually completed write as pending can create duplicates. This is deliberately not advertised as an exactly-once distributed transaction.

Detailed architecture, operational recovery, and security boundaries: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Project map

```text
reviewpulse/api.py          Open dashboard/API
reviewpulse/store.py        Durable SQLite state, leases, audit, approvals
reviewpulse/ingest.py       CSV normalization and basic redaction
reviewpulse/analysis.py     Theme metrics, reports, saved publishing payload
reviewpulse/llm.py          Optional JSON model client
reviewpulse/knowledge.py    Local retrieval and citation validation
reviewpulse/publish.py      Approval-only, stateful publishing orchestration
reviewpulse/mcp_client.py   MCP SDK adapter and explicitly local demo sink
reviewpulse/worker.py       Analysis/publishing queue worker
app.py                     Streamlit dashboard
config/themes.json         Your editable product taxonomy
n8n/                       Credential-free workflow exports
scripts/                   Setup and workflow generation
tests/             Automated unit/integration suite
```

The MIT license covers this new implementation, not the upstream inspiration or third-party dependencies. No API keys, connected accounts, original-repository assets, or real customer data are included.
