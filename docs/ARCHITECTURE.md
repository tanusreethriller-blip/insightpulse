# Architecture, safety boundaries, and operations

## Components

The FastAPI process serves the static dashboard and authenticated HTTP API. SQLite stores normalized input, source snapshots, analysis results, approvals, durable work state, and audit records. By default one embedded worker thread claims queued work; a separate heartbeat thread renews leases during long model/MCP calls. There is no task data solely held in a web request or browser tab.

The optional model provider only classifies reviews and proposes source-backed support statements. It receives no Google credentials, no publishing token, and no publishing tools. Numeric aggregation, quote selection, payload construction, approval checks, and publishing orchestration are ordinary deterministic code.

MCP is the **publishing integration**, not the mechanism for analysis. n8n exposes exactly three tools. The worker supplies destination values and text from the saved approved payload. A completely separate n8n workflow performs scheduled collection using the less-privileged ingestion API token.

## State and durability

An analysis job follows:

```text
queued -> running -> ready
             |
             +--> failed
ready -> rejected (human decision)
```

Publishing state lives on a separate approval record:

```text
queued -> publishing -> published
               |
               +--> needs_attention -> manual verification -> queued/published
```

The analysis job remains `ready` after publication; its approval record carries `published`. The dashboard prioritizes approval status in its visible status badge. Ingestion status checks only concern completion of analysis.

Jobs snapshot normalized reviews, validation counts, selected source documents and hashes, product context, theme configuration, selected model, analysis mode, and publishing target. The provider base URL and API key remain runtime configuration, not secrets embedded in a job. Avoid changing model/provider settings while queued/running jobs exist; existing snapshots do not fully pin a provider's model implementation.

SQLite transactions use `BEGIN IMMEDIATE` when claiming work or approving. Lease expiry is 90 seconds, renewed every 10 seconds. A recovered analysis can be recomputed, but a stale worker cannot commit its results after its lease owner changes. A repeated model computation can incur provider cost; leases do not eliminate that possibility after a crash.

Interrupted publishing does not automatically repeat an external operation. Its persisted states require reconciliation because a timed-out request might already have changed Google state.

Use one application worker on local durable disk for the initial deployment. The queue uses ownership fencing, but the starter is not benchmarked or certified for distributed deployment. Do not place SQLite on a network filesystem or run unrelated copies against a shared volume.

## Approval contract

The prepared result includes the exact plain-text Google Docs entry, email subject, HTML email body, marker, and target. A canonical JSON SHA-256 binds all of them. Anyone with access to the open dashboard can submit the exact hash displayed in the preview and type `APPROVE`.

Only one approval record may exist per job. The target includes mode, document ID, recipient, and a hash of the MCP endpoint. Current runtime targets must match the saved target both at approval and before publishing. Changing destinations cannot silently redirect an already prepared report.

SHA-256 is an integrity binding, **not a signature against an administrator who can rewrite the database and code**. Protect the server, secrets, filesystem, and backups. The app does not provide independent tamper-proof audit storage.

## External side effects and reconciliation

The publisher checks the exact destination document's identity. It searches for the stable report marker before appending. It persists `in_flight` before each write, verifies the document marker after append, and records completion. It then creates a Gmail draft and records the returned identifier.

A lost response, process interruption, failed verification, or uncertain draft creation leads to `needs_attention`. No automated analysis, source content, or ingestion token can authorize a retry.

To recover:

1. Open the exact destination Google Doc and search for the report marker from the saved preview. Open Gmail Drafts and search for its subject/job ID, then verify the body/marker.
2. Inspect n8n and stop any still-running execution. An absent marker while a write is still in progress is not proof of absence.
3. In the report page, open **Reconcile external writes**. Mark a step `done` only when verified, or `pending` only when you verified no write occurred and no execution can still finish it. Enter an explanatory note and type `VERIFIED`.
4. Only pending steps become eligible to execute. An already completed step cannot be reset to pending through this API.

A human's incorrect pending selection can create duplicate writes. Google Docs and Gmail are not covered by one atomic transaction; **exactly-once publication is not guaranteed**. Marker checks reduce document duplication but are not a cross-system lock. Do not run multiple independent deployments publishing the same reports.

## Open access and privileges

- The dashboard and data API have no user login or password. Anyone who can reach the application can read reviews and reports, upload data, change sources, and approve or reconcile publishing. Restrict network access outside the app, or add a trusted access-control layer before using private data.
- Browser mutations reject conflicting Origin headers, but this is not authentication or authorization. Do not treat it as a security boundary.
- The ingestion token permits limited job submission/status access when supplied, but it does not protect the open dashboard or other endpoints from callers who omit it.
- The n8n MCP token is a write-capable credential. n8n's native tools do not independently validate this app's saved approval. A holder of that token can invoke tools directly, so do not share it with arbitrary agents, browser code, ingestion systems, or model prompts. Use a dedicated Google account and network restrictions. For stronger server-side controls, add explicit destination allowlisting and signed approval verification to custom n8n tools before expanding access.

Security headers and browser escaping are defense-in-depth, not access controls or proof of a completed security audit.

## Content and grounding

The default local mode assigns one primary theme per review using configurable keywords. Rating-based sentiment is 1-2 negative, 3 neutral, and 4-5 positive, regardless of the words in the review. Percentages describe the uploaded sample and are not population estimates.

The retained window is 84 days anchored to the latest valid review. Theme change compares two consecutive 28-day windows, using all reviews in each window as the denominator and negative reviews assigned to that theme as the numerator. Missing prior data is not replaced by zero. Empty calendar weeks do not receive invented average ratings.

Every selected customer quote is copied from accepted review text and carries its internal ID, rating, and date. The LLM must assign exactly one allowed theme to every review ID; invalid responses fail rather than silently inventing assignments.

Support retrieval is a compact BM25-style lexical retriever over uploaded text chunks. It does not crawl sites, embed documents, or automatically verify that a supplied URL is official. Local mode returns exact excerpts. LLM mode requires known chunk IDs and exact source quotes for statements. These checks establish citation/excerpt provenance, **not that a paraphrase is entailed or a policy is current**. The owner must review relevance and accuracy. No matching source means an explicit insufficient-evidence result.

Untrusted review/document text is presented to the model as data, not authority. The model has no tool access, and HTML output escapes the report text. Prompt injection can still degrade an analysis answer; the architecture limits its access rather than claiming prompt text eliminates injection.

## Storage, privacy, and retention

Raw uploaded CSV files are not written to disk. Normalized, deduplicated, basically redacted rows are persisted inside the job specification and result. Simple email/phone patterns do not remove names, addresses, credentials, financial details, or all international identifier formats. Pre-clean sensitive input.

Uploaded documentation and historical snapshots may contain confidential information. They are stored as plaintext in SQLite. Filesystem permissions are limited where supported, but database/application-level encryption is not implemented. Use encrypted storage/backups and an appropriate hosting environment.

There is no automated per-job erasure or retention policy in this starter. Deleting an active source does not remove it from historical job snapshots. Before using regulated data, implement retention, report/job deletion, backup expiry, and any required data-access workflow. Downloaded reports, n8n logs, provider logs, Google Docs, and Gmail drafts are independent copies to account for.

For backup, use the CLI's SQLite backup command rather than casually copying a live database while ignoring its WAL. Test restore on a separate instance with `PUBLISH_MODE=disabled`, because restored queued approvals must not accidentally repeat external writes. A backup contains review evidence and approvals; keep it private.

## Configuration and deployment boundaries

New settings require application restart. Use `PUBLIC_URL` as the externally visible origin without a path prefix; it supplies report links and the allowed browser Origin. Ingestion cannot redirect callbacks: status/report URLs are built by the server.

The operator controls the model base URL and MCP endpoint through server configuration. User-supplied source URLs are references only and are never fetched. No API accepts shell commands or filesystem paths from the browser.

Run the dashboard as a Python/Uvicorn process and connect it to n8n Cloud or an independently hosted n8n instance. Docker is not required. TLS, reverse-proxy policy, monitoring, OS patching, secret rotation, provider governance, storage encryption, disaster recovery, and independent security testing remain deployment responsibilities.
