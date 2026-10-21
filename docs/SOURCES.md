# Design and API references

Reviewed during implementation on 2026-10-21. External node schemas, packages, provider models, and authentication requirements can change. Recheck these sources before upgrading dependencies.

## Inspiration and scope

Original application idea and requested reference: https://github.com/imharshal11/review-pulse

This starter is a new implementation. It does not bundle the original project's source, branding assets, sample financial-product documentation, user credentials, or customer data. The license included here applies only to this new code.

## n8n

MCP Server Trigger, authentication, production URL, Streamable HTTP and proxy requirements:
https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-langchain.mcptrigger/

Workflow import/export:
https://docs.n8n.io/workflows/export-import/

Google OAuth:
https://docs.n8n.io/integrations/builtin/credentials/google/oauth-single-service/

Google Docs node:
https://docs.n8n.io/integrations/builtin/app-nodes/n8n-nodes-base.googledocs/

Gmail draft operations:
https://docs.n8n.io/integrations/builtin/app-nodes/n8n-nodes-base.gmail/draft-operations/

HTTP Request:
https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.httpRequest/

Schedule Trigger:
https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.scheduletrigger/

Source-level fields for the MCP trigger and Google Docs insertion:
https://raw.githubusercontent.com/n8n-io/n8n/master/packages/@n8n/nodes-langchain/nodes/mcp/McpTrigger/McpTrigger.node.ts
https://raw.githubusercontent.com/n8n-io/n8n/master/packages/nodes-base/nodes/Google/Docs/DocumentDescription.ts

## MCP Python SDK 2.3.0

The optional adapter intentionally uses the two-stream transport, seconds-based timeouts, and wire aliases of this pinned SDK version rather than older examples:

https://raw.githubusercontent.com/modelcontextprotocol/python-sdk/v2.3.0/src/mcp/client/streamable_http.py
https://raw.githubusercontent.com/modelcontextprotocol/python-sdk/v2.3.0/src/mcp/client/session.py
https://raw.githubusercontent.com/modelcontextprotocol/python-sdk/v2.3.0/src/mcp/client/_transport.py
https://raw.githubusercontent.com/modelcontextprotocol/python-sdk/v2.3.0/src/mcp/shared/_httpx_utils.py
https://pypi.org/project/mcp/2.3.0/

## Groq

OpenAI-compatible API and base URL:
https://console.groq.com/docs/openai

Model IDs and availability:
https://console.groq.com/docs/models

The default `openai/gpt-oss-20b` identifier is a configurable provider model, not a guarantee of future model availability. This implementation's JSON model client is separate from its MCP client.
