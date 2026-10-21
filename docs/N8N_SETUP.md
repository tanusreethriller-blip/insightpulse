# n8n setup: ingestion and approved MCP publishing

There are **two independent workflows**. Start with publishing, test it using your own test Google Doc and Gmail address, and then add ingestion.

```text
Review source -> n8n scheduled ingestion -> POST /api/jobs
                                                |
                                  durable analysis + dashboard
                                                |
                                        human approval
                                                |
                                 Python MCP client -> n8n MCP
                                                       |  |
                                               Google Doc  Gmail draft
```

n8n's MCP Server Trigger is the server; a separate custom MCP server or an n8n AI Agent is not needed for this implementation. Official documentation: https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-langchain.mcptrigger/

## A. Prepare your accounts

Create a Google Doc for the report log. Copy its document ID, the part between `/d/` and `/edit` in its normal URL. Share access only with the Google account whose credentials you will use in n8n.

In n8n, create Google Docs and Gmail OAuth credentials. n8n Cloud supports managed Google OAuth for these nodes. For self-hosting with custom OAuth, enable the Google Docs and Gmail APIs in your Google Cloud project, configure the consent screen, create a web OAuth client, and register the exact callback URL shown by n8n. Authorize your account and confirm any test-user/consent requirements in your Google project.

Credential setup reference: https://docs.n8n.io/integrations/builtin/credentials/google/oauth-single-service/

Use a dedicated Google account with only the content access necessary for this workflow. The MCP endpoint's Bearer token grants the ability to invoke its tools. Anyone holding it can bypass the application's approval screen and invoke tools directly; the n8n server is not a second human-approval enforcement layer.

## B. Import and configure the MCP workflow

1. In n8n, use the workflow menu's **Import from File** and select `n8n/mcp-publishing.json`.
2. On **MCP Server Trigger**, keep Authentication set to **Bearer auth**. Create/select a Bearer credential whose token value matches `N8N_MCP_TOKEN` in the application's `.env`. The initial setup script generated a random value. Do not include the literal `Bearer ` prefix in the token field.
3. Select your Google Docs OAuth credential on **read_doc** and **append_to_doc**. Keep all parameter expressions intact.
4. Select your Gmail OAuth credential on **create_email_draft**. Verify **Resource: Draft**, **Operation: Create**, and **Email type: HTML**. Do not change this into a send operation.
5. Save/publish the workflow. Copy its exact **Production URL** into `N8N_MCP_URL`. Use the Streamable HTTP endpoint, not an SSE-only URL or an n8n REST API endpoint.

Both exports are deliberately inactive and contain no account credentials. Node names and arguments form an API contract and must match exactly:

| Node/tool | Parameter | Expression or value |
| --- | --- | --- |
| `read_doc` | Document URL/ID | `{{ $fromAI('document_id', 'Approved Google document ID', 'string') }}` |
| `read_doc` | Simplify | Off / `false` |
| `append_to_doc` | Document URL/ID | Same `document_id` expression |
| `append_to_doc` | Object / action | Text / Insert |
| `append_to_doc` | Segment / location | Body / End of segment |
| `append_to_doc` | Text | `{{ $fromAI('entry', 'Exact approved document entry including its marker', 'string') }}` |
| `create_email_draft` | To | `{{ $fromAI('recipient', 'The one approved recipient email address', 'string') }}` |
| `create_email_draft` | Subject | `{{ $fromAI('subject', 'Exact approved email subject', 'string') }}` |
| `create_email_draft` | Message | `{{ $fromAI('body', 'Exact approved HTML email body', 'string') }}` |

`$fromAI` declares tool arguments in n8n; this app supplies those arguments directly from an approved saved payload. It does not ask an LLM to decide the recipient, document, or tool call.

The MCP workflow must expose exactly these three tools, with the exact string parameter names above. Adding an unrelated tool causes this client's connection check to reject the server. Descriptions may be edited without renaming fields.

Gmail draft operation reference: https://docs.n8n.io/integrations/builtin/app-nodes/n8n-nodes-base.gmail/draft-operations/

## C. Configure and test the application

Install the pinned optional MCP SDK:

```bash
python -m pip install -r requirements-mcp.txt
```

Update `.env`:

```dotenv
PUBLISH_MODE=mcp
N8N_MCP_URL=https://your-n8n.example/mcp/the-exact-production-path
N8N_MCP_TOKEN=your_generated_token
GOOGLE_DOC_ID=the_id_of_your_test_document
REPORT_RECIPIENT=your_test_address@your-company.example
```

Restart the web application. Then run:

```bash
python -m reviewpulse.cli check-mcp
```

Expected tool names are `read_doc`, `append_to_doc`, and `create_email_draft`. This command initializes the connection and lists/validates schemas; it does **not** execute a tool.

Create a new **non-demo** analysis. Inspect **The exact payload you approve** in the report page, including the document entry, email subject/body, destination values, and hash. Approve by typing `APPROVE`. Confirm the marker appears once in your Google Doc, and one matching draft appears in Gmail. Confirm the report is not in Sent Mail.

Try refreshing and reopening the report; it must remain published with approval disabled. Use a test document/account to deliberately exercise a failed call before trusting a real workflow.

A demo report is never sent to your live destination. A report created before changing publishing settings is also blocked by its destination snapshot. Create a new analysis rather than editing the database or bypassing the check.

## D. Scheduled ingestion workflow

Import `n8n/scheduled-ingestion.json`. It starts unpublished and has manual and Monday 09:00 triggers; the timezone is `Asia/Kolkata`.

Configure **Fetch your review JSON** to read your authorized source. Its response may be:

```json
{
  "reviews": [
    {"text": "The application crashes whenever I attach a large file.", "date": "2026-10-01", "rating": 1},
    {"text": "The reporting dashboard is very useful for our team.", "date": "2026-10-02", "rating": 5}
  ]
}
```

The example only illustrates the shape; supply at least five valid reviews. A `data` array or individual n8n row items are also supported. With Google Sheets or another connector, replace the source HTTP node and connect the row output to **Normalize and aggregate**. Adjust the field aliases there and add a support question as appropriate.

On **Submit analysis job**:

```text
Method: POST
URL: https://your-application.example/api/jobs
Authentication: Generic Credential Type -> Header Auth
Header name: Authorization
Header value: Bearer YOUR_INGEST_API_TOKEN
Body: JSON, using the supplied payload expression
```

Select the **same ingestion credential** on **Read analysis status**. Do not use `N8N_MCP_TOKEN` or a Google credential in these two HTTP nodes. The response contains a job ID, status URL, and dashboard link. The dashboard itself is open to anyone who can reach it; the token only limits the response when callers choose to authenticate with it.

The workflow stops when analysis is ready and sends no email. Review and approve the report in the dashboard; only then does the separate MCP publishing workflow append the approved report to the Google Doc and create a Gmail draft.

Run manually first. Once successful, publish the schedule. The workflow polls every minute, stops on failed/rejected jobs, and stops polling after an hour for an unfinished job. A poll timeout does not cancel the durable analysis; inspect the app before resubmitting.

The default idempotency key is `scheduled-source1-YYYY-MM-DD`. Replaying the same input/configuration with that key reuses the job. Deliberately changed data or source snapshots on the same day cause a conflict; change the key for an intentional new run. Give different sources distinct prefixes.

### Optional placeholder renderer

This helper substitutes URLs and the notification recipient before import, but never embeds secrets:

```bash
python scripts/render_ingestion.py \
  --app-url https://your-application.example \
  --source-url https://your-authorized-source.example/reviews
```

Import the resulting `n8n/configured/scheduled-ingestion.json`, then select credentials inside n8n. That configured folder is ignored by Git.

## E. API test without n8n

From the project root with `.env` configured, submit a CSV using the ingestion token. The sample is explicitly synthetic; replace the path for your own data.

```python
from pathlib import Path
import os
import httpx
from dotenv import load_dotenv

load_dotenv()
base = os.environ["PUBLIC_URL"].rstrip("/")
headers = {"Authorization": "Bearer " + os.environ["INGEST_API_TOKEN"]}
request = {
    "name": "Synthetic CSV API test",
    "csv": Path("data/synthetic_reviews.csv").read_text(encoding="utf-8"),
    "question": "",
    "idempotency_key": "api-test-001",
}
with httpx.Client(timeout=30) as client:
    response = client.post(base + "/api/jobs", headers=headers, json=request)
    response.raise_for_status()
    job = response.json()
    print(job["id"], job["status"], job["report_url"])
    status = client.get(job["status_url"], headers=headers)
    status.raise_for_status()
    print(status.json()["status"])
```

Approval has no separate login or password gate. Anyone who can reach the dashboard can submit the exact payload hash and confirmation word; keep the app on a trusted network.

## Troubleshooting and live readiness

| Symptom | Check |
| --- | --- |
| MCP authorization failure | Same token in `.env` and the MCP trigger credential; no duplicated `Bearer ` prefix. |
| MCP endpoint not found | Workflow published, exact Production URL, correct reverse-proxy route. |
| Schema mismatch | Use this project's export, preserve names, expose only three tools. |
| MCP package/import error | Install `requirements-mcp.txt` in the same Python environment. The adapter targets SDK 2.3.0, not older three-stream examples. |
| Document ID missing in tool response | `read_doc` Simplify must be off; inspect the full document output shape. |
| A saved report cannot be approved | Configuration changed, report rejected, or approval already exists. Create a new analysis for changed targets. |
| Browser origin error | Open the exact `PUBLIC_URL`; set the real HTTPS origin when deployed. |
| n8n cannot reach the application | Cloud localhost is not your laptop; provide a reachable HTTPS origin or colocated private network. |
| Publishing needs attention | Inspect Google Docs/Drafts and stop in-flight execution before manual reconciliation. Never blindly retry an uncertain write. |
| Import shows unknown/changed nodes | Update n8n or recreate the three native tool nodes using the table above. Exports were source-checked, not live-imported here. |

For reverse proxies, preserve Streamable HTTP/SSE behavior and disable response buffering on the MCP route. With multiple n8n webhook replicas, follow n8n's documented routing requirements. Restrict endpoint access and avoid verbose logs containing credentials or feedback. The supplied workflow settings disable execution-data persistence by default; use synthetic data if temporarily enabling logs for troubleshooting.

The included code has no live-account setup completed on your behalf. Successful local tests and valid workflow JSON do not establish Google OAuth, network reachability, or end-to-end n8n runtime compatibility.
