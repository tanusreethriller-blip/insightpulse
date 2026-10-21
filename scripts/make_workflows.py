"""Generate importable workflows without secrets, personal recipients, or credential IDs."""
from pathlib import Path
import json
import uuid

ROOT = Path(__file__).resolve().parents[1]


def node(name, kind, version, parameters, x, y, **extra):
    return {"id": str(uuid.uuid4()), "name": name, "type": kind, "typeVersion": version, "position": [x, y], "parameters": parameters, **extra}


def connection(destination, kind="main"):
    return [[{"node": destination, "type": kind, "index": 0}]]


def write(name, data):
    (ROOT / "n8n" / name).write_text(json.dumps(data, indent=2) + "\n")


if __name__ == "__main__":
    mcp = node("MCP Server Trigger", "@n8n/n8n-nodes-langchain.mcpTrigger", 2.1,
               {"authentication": "bearerAuth", "path": "review-pulse-publishing"}, 160, 80,
               webhookId=str(uuid.uuid4()))
    read = node("read_doc", "n8n-nodes-base.googleDocsTool", 2,
                {"authentication": "oAuth2", "descriptionType": "manual", "toolDescription": "Read the approved destination document and return its documentId and full content. Read-only.", "operation": "get", "documentURL": "={{ $fromAI('document_id', 'Approved Google document ID', 'string') }}", "simple": False}, -60, 320)
    append = node("append_to_doc", "n8n-nodes-base.googleDocsTool", 2,
                  {"authentication": "oAuth2", "descriptionType": "manual", "toolDescription": "Append the exact human-approved report to its approved Google Doc. This writes data.", "operation": "update", "documentURL": "={{ $fromAI('document_id', 'Approved Google document ID', 'string') }}", "actionsUi": {"actionFields": [{"object": "text", "action": "insert", "insertSegment": "body", "locationChoice": "endOfSegmentLocation", "text": "={{ $fromAI('entry', 'Exact approved document entry including its marker', 'string') }}"}]}}, 160, 320)
    draft = node("create_email_draft", "n8n-nodes-base.gmailTool", 2.2,
                 {"descriptionType": "manual", "toolDescription": "Create one Gmail DRAFT with the exact approved recipient, subject, and HTML body. Never send a message.", "resource": "draft", "operation": "create", "subject": "={{ $fromAI('subject', 'Exact approved email subject', 'string') }}", "emailType": "html", "message": "={{ $fromAI('body', 'Exact approved HTML email body', 'string') }}", "options": {"sendTo": "={{ $fromAI('recipient', 'The one approved recipient email address', 'string') }}"}}, 380, 320)
    note = node("Configure before publishing", "n8n-nodes-base.stickyNote", 1,
                {"content": "## Review Pulse publishing tools\n1. Select Bearer credentials on the MCP trigger.\n2. Connect your Google Docs and Gmail OAuth credentials.\n3. Keep tool and parameter names unchanged.\n4. Publish, then copy the Production Streamable HTTP URL into the application .env.\n\nDocument ID and recipient are supplied deterministically from the app's approved configuration, not by an LLM. The MCP token grants write access: keep it private and use a dedicated Google account.\n\nNo AI Agent node is needed. This workflow does not create/send email reports without a client tool call.", "height": 280, "width": 620}, -120, -280)
    write("mcp-publishing.json", {
        "name": "Review Pulse | Approved publishing via MCP", "active": False,
        "nodes": [mcp, read, append, draft, note],
        "connections": {n["name"]: {"ai_tool": connection("MCP Server Trigger", "ai_tool")} for n in [read, append, draft]},
        "settings": {"executionOrder": "v1", "saveDataSuccessExecution": "none", "saveDataErrorExecution": "none", "saveManualExecutions": False}, "pinData": {}, "tags": [],
    })
    schedule = node("Every Monday at 9", "n8n-nodes-base.scheduleTrigger", 1.2,
                    {"rule": {"interval": [{"field": "cronExpression", "expression": "0 9 * * 1"}]}}, 0, 0)
    manual = node("Run manually", "n8n-nodes-base.manualTrigger", 1, {}, 0, 180)
    fetch = node("Fetch your review JSON", "n8n-nodes-base.httpRequest", 4.2,
                 {"url": "https://YOUR-DATA-SOURCE.example/reviews", "options": {"timeout": 30000}}, 240, 80)
    code = """// Replace field aliases here for your source, or connect Google Sheets rows to this node.
// Input can be one { reviews: [...] }, one { data: [...] }, or one item per review.
const rows = $input.all().flatMap(item => {
  const value = item.json;
  if (Array.isArray(value.reviews)) return value.reviews;
  if (Array.isArray(value.data)) return value.data;
  return [value];
});
if (!rows.length) throw new Error('No reviews were returned by the source');
const reviews = rows.map(row => ({
  text: row.text ?? row.review ?? row.content ?? row.comment,
  date: row.date ?? row.created_at ?? row.at,
  rating: row.rating ?? row.score ?? row.stars,
}));
// This daily key avoids replaying the same scheduled ingestion. Change it when
// intentionally submitting changed data/configuration on the same calendar day.
return [{ json: { payload: {
  name: 'Scheduled customer feedback',
  idempotency_key: 'scheduled-source1-' + $now.toFormat('yyyy-MM-dd'),
  question: '',
  reviews,
} } }];"""
    normalize = node("Normalize and aggregate", "n8n-nodes-base.code", 2, {"jsCode": code}, 460, 80)
    submit = node("Submit analysis job", "n8n-nodes-base.httpRequest", 4.2,
                  {"method": "POST", "url": "https://YOUR-APP.example/api/jobs", "authentication": "genericCredentialType", "genericAuthType": "httpHeaderAuth", "sendBody": True, "specifyBody": "json", "jsonBody": "={{ JSON.stringify($json.payload) }}", "options": {"timeout": 30000}}, 700, 80)
    wait = node("Wait before polling", "n8n-nodes-base.wait", 1.1,
                {"resume": "timeInterval", "amount": 1, "unit": "minutes"}, 940, 80, webhookId=str(uuid.uuid4()))
    poll = node("Read analysis status", "n8n-nodes-base.httpRequest", 4.2,
                {"url": "={{ $('Submit analysis job').first().json.status_url }}", "authentication": "genericCredentialType", "genericAuthType": "httpHeaderAuth", "options": {"timeout": 30000}}, 1160, 80)
    guard = node("Check failure and deadline", "n8n-nodes-base.code", 2,
                 {"jsCode": "const job = $input.first().json;\nif (['failed','rejected'].includes(job.status)) throw new Error('Review Pulse job did not finish successfully: ' + (job.error || job.status));\nif (job.status !== 'ready' && Date.now() - Date.parse(job.created_at) > 60 * 60 * 1000) throw new Error('Polling timed out after one hour. The durable job may still be running; inspect the app.');\nreturn [{json: job}];"}, 1380, 80)
    ready = node("Report ready", "n8n-nodes-base.if", 2.2,
                 {"conditions": {"options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict", "version": 2}, "conditions": [{"id": str(uuid.uuid4()), "leftValue": "={{ $json.status }}", "rightValue": "ready", "operator": {"type": "string", "operation": "equals"}}], "combinator": "and"}, "options": {}}, 1590, 80)
    note = node("Read before activation", "n8n-nodes-base.stickyNote", 1,
                {"content": "## Ingestion only - no publishing privilege\n- Replace the source and application URL placeholders.\n- Create Header Auth credentials: Name = Authorization; Value = Bearer YOUR_INGEST_API_TOKEN. Select them on Submit and Read Status.\n- Add your source authentication if required.\n- This workflow only prepares an analysis; it sends no email and does not append documents or create drafts. Review the report in the app and explicitly approve it before the separate MCP workflow appends the document and creates a Gmail draft.\n- Workflow timezone: Asia/Kolkata. Monday 09:00.\n- Set question in Normalize and aggregate to request a support answer.\n- Publish after a successful manual test.\n- n8n Cloud must reach your app over HTTPS; localhost is not reachable from the cloud.", "height": 390, "width": 630}, 300, -420)
    write("scheduled-ingestion.json", {
        "name": "Review Pulse | Scheduled ingestion for review", "active": False,
        "nodes": [schedule, manual, fetch, normalize, submit, wait, poll, guard, ready, note],
        "connections": {
            "Every Monday at 9": {"main": connection("Fetch your review JSON")},
            "Run manually": {"main": connection("Fetch your review JSON")},
            "Fetch your review JSON": {"main": connection("Normalize and aggregate")},
            "Normalize and aggregate": {"main": connection("Submit analysis job")},
            "Submit analysis job": {"main": connection("Wait before polling")},
            "Wait before polling": {"main": connection("Read analysis status")},
            "Read analysis status": {"main": connection("Check failure and deadline")},
            "Check failure and deadline": {"main": connection("Report ready")},
            "Report ready": {"main": [[], [{"node": "Wait before polling", "type": "main", "index": 0}]]},
        },
        "settings": {"executionOrder": "v1", "timezone": "Asia/Kolkata", "saveDataSuccessExecution": "none", "saveDataErrorExecution": "none", "saveManualExecutions": False}, "pinData": {}, "tags": [],
    })
    print("Created n8n/mcp-publishing.json and n8n/scheduled-ingestion.json")
