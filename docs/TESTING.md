# Verification and test plan

## Executed for this delivery

The final automated suite was run with Python 3.14 on macOS:

```text
51 passed
```

Installed core versions exercised included FastAPI 0.128.2, Starlette 0.50.0, Pydantic 2.13.5, HTTPX 0.28.1, AnyIO 4.13.0, and pytest 9.1.1. Requirements declare supported version ranges, not a complete transitive lock file. Python 3.12, clean-machine installation, Windows, and macOS execution were not separately tested.

The suite covers:

- CSV field validation, aliases, row limits, date windows, redaction, invalid ratings, and deduplication.
- All-review metric counts, quote provenance, missing baselines, local no-network analysis, and source snapshot isolation.
- LLM response validation, unsupported/fabricated citations, immutable payload hashing, and HTML escaping.
- Direct unauthenticated dashboard/API access, origin checks for browser mutations, ingestion-token role limits, request bounds, and source URL handling.
- Durable leases, interrupted work, duplicate approval prevention, exact destination binding, mocked MCP success/failure, and manual publishing recovery without automatically replaying uncertain writes.
- Workflow JSON shape, absence of embedded account credentials, required tool names, draft-vs-send behavior, and ingestion-only scheduling flow.

A Chromium smoke test exercised direct dashboard loading, the 168-review synthetic demo, negative-review filtering, adding documentation, CSV upload, source-backed report display, the exact approval form, local publication, and activity records. It verified exactly one local document record and one local draft, no page JavaScript errors, and no horizontal overflow at desktop and 390-pixel mobile widths.

The browser used the **actual static UI and real FastAPI/SQLite code through an in-process test bridge**. The bridge does not validate response CSP enforcement, proxy behavior, or deployed HTTP/TLS transport. Server-side security behavior has separate API tests.

The API suite directly checked public job and report access, demo creation, exact-payload approval, and the two local publishing records without creating an authenticated session.

Python compilation and JavaScript syntax checks were also run. Screenshots in the delivery show actual rendered synthetic/demo results, not a design mock-up.

## Not executed here

No real customer dataset or private account credentials were supplied. Consequently, these were **not** verified end to end:

- Live Groq or other provider requests, model quality, rate limits, latency, cost, or data retention.
- Installation and runtime transport of the optional MCP SDK in this environment.
- Import and execution inside a real n8n instance.
- Google OAuth, Google Docs append/read behavior, or Gmail draft creation in an account.
- Production HTTPS, reverse-proxy streaming, or multiple worker deployments.

The optional adapter was written against official MCP Python SDK **v2.3.0** source, and workflow fields were checked against official n8n documentation/source. That is source-level compatibility work, not a claim of live integration success.

## Re-run locally

```bash
python -m pip install -r requirements.txt -r requirements-dev.txt
python -m pytest
python -m compileall -q reviewpulse scripts tests
```

Optional browser smoke test:

```bash
python -m pip install playwright
python -m playwright install chromium
python scripts/browser_smoke.py
```

Set `CHROMIUM_EXECUTABLE` to an existing Chromium executable instead of installing the managed browser when appropriate. Screenshots are written to ignored `test-output/`; `BROWSER_OUTPUT_DIR` overrides that location. The script creates isolated temporary data and never reads your real `.env` or calls external publishing tools.

## Required live acceptance checks

Before using real feedback or publishing to a shared destination:

1. Start the app from a clean environment and verify it opens directly without a password. Confirm that private API data and all write actions are accessible without login; this is intentional and must be restricted at the network/access-control layer before deploying beyond a trusted local environment.
2. Use a small, authorized test dataset and known documentation. Manually compare totals, sampled classifications, every selected quote, and policy answers with the inputs.
3. Install `requirements-mcp.txt`, import the supplied workflow, configure accounts, publish, and run `python -m reviewpulse.cli check-mcp`. Confirm precisely the three documented tools and their arguments.
4. Approve a test report addressed to yourself. Verify one document marker, one correct Gmail draft, and no sent report. Check the returned receipts in the application.
5. Test failure before writing, failure after a potentially completed write, app restart during an operation, and manual reconciliation. Verify no automatic replay of uncertain writes. Do this only with disposable test destinations.
6. Execute the ingestion workflow manually from n8n, verify token privileges and report links, then enable the schedule. Check the optional link-only notification separately from publishing.
7. Perform a database backup and restore in an isolated instance with publishing disabled. Implement data retention and restore procedures before business use.

A passing local test suite is a starting point, not a substitute for those environment-specific checks or an independent security review.
