import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def test_mcp_workflow_defaults_safe():
    workflow=json.loads((ROOT/'n8n/mcp-publishing.json').read_text())
    assert workflow['active'] is False
    nodes={n['name']:n for n in workflow['nodes']}
    assert nodes['MCP Server Trigger']['parameters']['authentication']=='bearerAuth'
    assert nodes['create_email_draft']['parameters']['resource']=='draft'
    assert nodes['create_email_draft']['parameters']['operation']=='create'
    assert nodes['read_doc']['parameters']['simple'] is False
    assert set(workflow['connections'])=={'read_doc','append_to_doc','create_email_draft'}
    assert all('credentials' not in n for n in workflow['nodes'])


def test_ingestion_workflow_stops_for_human_review():
    text=(ROOT/'n8n/scheduled-ingestion.json').read_text()
    workflow=json.loads(text)
    assert workflow['active'] is False
    assert workflow['settings']['timezone']=='Asia/Kolkata'
    assert '/approve' not in text and 'N8N_MCP_TOKEN' not in text
    nodes={n['name']:n for n in workflow['nodes']}
    assert not any(n['type'].startswith('n8n-nodes-base.gmail') for n in workflow['nodes'])
    assert nodes['Submit analysis job']['parameters']['genericAuthType']=='httpHeaderAuth'
    assert '60 * 60 * 1000' in nodes['Check failure and deadline']['parameters']['jsCode']
    assert workflow['connections']['Report ready']['main'][0] == []
