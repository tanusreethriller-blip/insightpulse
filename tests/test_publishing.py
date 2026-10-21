import json
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import replace

import pytest

from reviewpulse.mcp_client import CONTRACT, document_text, validate_tools
from reviewpulse.publish import publish_approval
from reviewpulse.service import Service
from reviewpulse.util import UserError, canonical
from reviewpulse.worker import Worker


def test_idempotent_submission(service, rows):
    first = service.submit(rows, 'Test', request_key='same-key')
    second = service.submit(rows, 'Test', request_key='same-key')
    assert first['id']==second['id']
    with pytest.raises(UserError, match='different input'):
        service.submit(rows, 'Different name', request_key='same-key')


def test_hash_mismatch_blocks_approval(ready_job, store, settings):
    with pytest.raises(UserError, match='hash mismatch'):
        store.approve(ready_job['id'], 'f'*64, settings.target())
    assert store.approval_for_job(ready_job['id']) is None


def test_changed_targets_block_approval(ready_job, store, settings):
    result=json.loads(store.job(ready_job['id'])['result_json'])
    changed={**settings.target(),'recipient':'different@example.com'}
    with pytest.raises(UserError, match='destinations changed'):
        store.approve(ready_job['id'], result['payload_hash'], changed)


def test_concurrent_approval_is_unique(ready_job, store, settings):
    result=json.loads(store.job(ready_job['id'])['result_json'])
    def approve(_):
        return store.approve(ready_job['id'], result['payload_hash'], settings.target())['id']
    with ThreadPoolExecutor(max_workers=4) as pool:
        ids=list(pool.map(approve,range(4)))
    assert len(set(ids))==1


def test_rejected_report_never_publishes(ready_job, store, settings):
    result=json.loads(store.job(ready_job['id'])['result_json'])
    store.reject(ready_job['id'])
    with pytest.raises(UserError, match='non-rejected'):
        store.approve(ready_job['id'],result['payload_hash'],settings.target())


def test_stale_analysis_requeued(service, store, rows):
    job=service.submit(rows,'Recovery')
    store.claim_job('dead-worker')
    with store.connect(True) as db:
        db.execute('UPDATE jobs SET lease_until=? WHERE id=?',(time.time()-1,job['id']))
    claimed=store.claim_job('new-worker')
    assert claimed['id']==job['id'] and claimed['owner']=='new-worker'


def test_stale_publish_becomes_uncertain(ready_job, store, settings):
    result=json.loads(store.job(ready_job['id'])['result_json'])
    approval=store.approve(ready_job['id'],result['payload_hash'],settings.target())
    store.claim_approval('dead-worker')
    store.step(approval['id'],'dead-worker','doc_state','in_flight')
    with store.connect(True) as db:
        db.execute('UPDATE approvals SET lease_until=? WHERE id=?',(time.time()-1,approval['id']))
    assert store.claim_approval('new-worker') is None
    row=store.approval(approval['id'])
    assert row['status']=='needs_attention' and row['doc_state']=='uncertain'


class FakeGoogleTools:
    def __init__(self, fail_draft=False):
        self.text='';self.calls=[];self.fail_draft=fail_draft
    async def call(self,name,arguments):
        self.calls.append(name)
        if name=='read_doc':
            return {'documentId':arguments['document_id'],'body':{'content':[{'textRun':{'content':self.text}}]}}
        if name=='append_to_doc':
            self.text+=arguments['entry'];return {'ok':True}
        if self.fail_draft:
            raise TimeoutError('Lost response after potential Gmail write')
        return {'content':[{'type':'text','text':json.dumps({'id':'draft-123'})}]}


def live_case(settings,store,rows):
    config=replace(settings,publish_mode='mcp',mcp_url='https://n8n.example.com/mcp/reviews',mcp_token='m'*40,document_id='document123456',recipient='owner@example.com')
    job=Service(config,store).submit(rows,'Live mock')
    Worker(store,config).once()
    result=json.loads(store.job(job['id'])['result_json'])
    approval=store.approve(job['id'],result['payload_hash'],config.target())
    claimed=store.claim_approval('test-worker')
    return config,claimed


@pytest.mark.asyncio
async def test_mcp_success_and_tool_order(settings,store,rows,monkeypatch):
    config,approval=live_case(settings,store,rows)
    fake=FakeGoogleTools()
    @asynccontextmanager
    async def connector(_):yield fake
    monkeypatch.setattr('reviewpulse.publish.connect_mcp',connector)
    await publish_approval(store,approval,'test-worker',config)
    assert fake.calls==['read_doc','append_to_doc','read_doc','create_email_draft']
    assert store.approval(approval['id'])['status']=='published'


@pytest.mark.asyncio
async def test_uncertain_draft_is_not_replayed(settings,store,rows,monkeypatch):
    config,approval=live_case(settings,store,rows)
    fake=FakeGoogleTools(fail_draft=True)
    @asynccontextmanager
    async def connector(_):yield fake
    monkeypatch.setattr('reviewpulse.publish.connect_mcp',connector)
    await publish_approval(store,approval,'test-worker',config)
    row=store.approval(approval['id'])
    assert row['doc_state']=='done' and row['draft_state']=='uncertain'
    assert row['status']=='needs_attention'
    assert store.claim_approval('another-worker') is None
    # An owner must independently check that no draft exists before authorizing retry.
    store.reconcile(approval['id'],'done','pending','Verified in the test sink that the draft did not get created.')
    next_try=store.claim_approval('next-worker')
    fake.fail_draft=False
    await publish_approval(store,next_try,'next-worker',config)
    assert fake.calls.count('append_to_doc')==1
    assert fake.calls.count('create_email_draft')==2
    assert store.approval(approval['id'])['status']=='published'


@pytest.mark.asyncio
async def test_integrity_tampering_blocks_all_tools(settings,store,rows,monkeypatch):
    config,approval=live_case(settings,store,rows)
    payload=json.loads(approval['payload_json']);payload['doc_entry']='tampered'
    approval['payload_json']=canonical(payload)
    fake=FakeGoogleTools()
    @asynccontextmanager
    async def connector(_):yield fake
    monkeypatch.setattr('reviewpulse.publish.connect_mcp',connector)
    await publish_approval(store,approval,'test-worker',config)
    assert fake.calls==[]
    assert store.approval(approval['id'])['status']=='needs_attention'


@pytest.mark.asyncio
async def test_transport_cleanup_error_does_not_repeat_completed_writes(settings,store,rows,monkeypatch):
    config,approval=live_case(settings,store,rows)
    fake=FakeGoogleTools()
    @asynccontextmanager
    async def connector(_):
        yield fake
        raise ConnectionError('Cleanup failed after successful writes')
    monkeypatch.setattr('reviewpulse.publish.connect_mcp',connector)
    await publish_approval(store,approval,'test-worker',config)
    assert store.approval(approval['id'])['status']=='published'
    assert store.claim_approval('next-worker') is None


def test_marker_split_over_google_docs_runs():
    value={'documentId':'doc','body':{'content':[{'textRun':{'content':'[review-'}},{'textRun':{'content':'pulse:test]'}}]}}
    assert '[review-pulse:test]' in document_text(value,'doc')


def test_wrong_doc_id_rejected():
    with pytest.raises(UserError, match='expected document'):
        document_text({'documentId':'wrong','content':'anything'},'correct')


def test_tool_contract_validation():
    tools=[{'name':name,'inputSchema':{'type':'object','properties':{key:{'type':'string'} for key in arguments},'required':list(arguments)}} for name,arguments in CONTRACT.items()]
    validate_tools(tools)
    with pytest.raises(UserError, match='exactly'):
        validate_tools(tools+[{'name':'send_email','inputSchema':{}}])
    tools[0]['inputSchema']['properties']['unapproved']={'type':'string'}
    with pytest.raises(UserError, match='schema mismatch'):
        validate_tools(tools)
