from reviewpulse.worker import Worker


def test_dashboard_and_api_are_available_without_login(client):
    assert client.post('/api/login').status_code == 404
    assert client.post('/api/logout').status_code == 404
    assert client.get('/api/jobs').status_code == 200
    assert client.get('/api/sources').status_code == 200
    assert client.post('/api/demo').status_code == 202
    assert client.get('/').status_code == 200


def test_security_headers(client):
    response = client.get('/api/me')
    assert response.headers['cache-control'] == 'no-store'
    assert "script-src 'self'" in response.headers['content-security-policy']


def test_cross_origin_mutation_rejected(client):
    assert client.post('/api/demo', headers={'Origin': 'https://evil.example'}).status_code == 403


def test_source_urls_must_not_be_javascript(client):
    result = client.post('/api/sources', json={'title': 'Unsafe source', 'url': 'javascript:alert(1)', 'content': 'A source document with enough words and characters for the minimum limit.'})
    assert result.status_code == 422


def test_ingestion_token_has_limited_job_access(client, settings, rows):
    headers = {'Authorization': 'Bearer ' + settings.ingest_token}
    result = client.post('/api/jobs', json={'name': 'API job', 'reviews': rows}, headers=headers)
    assert result.status_code == 202
    identifier = result.json()['id']
    status = client.get('/api/jobs/' + identifier, headers=headers)
    assert status.status_code == 200 and 'result' not in status.json()
    assert client.get('/api/jobs').status_code == 200
    assert Worker(client.app.state.store, settings).once()
    assert client.get('/api/jobs/' + identifier + '/report.md').status_code == 200
    assert client.post('/api/jobs/' + identifier + '/approve', json={}).status_code == 422
    assert client.post('/api/sources', json={'title': 'Open source', 'url': '', 'content': 'A source document with enough words and characters for the minimum limit.'}).status_code == 201


def test_invalid_ingestion_token(client, rows):
    response = client.post('/api/jobs', json={'reviews': rows}, headers={'Authorization': 'Bearer invalid'})
    assert response.status_code == 401


def test_oversize_input_rejected(client, settings):
    body = '{"csv":"' + ('a' * (settings.max_upload_bytes + 1)) + '"}'
    assert client.post('/api/jobs', content=body, headers={'Content-Type': 'application/json'}).status_code == 413


def test_secrets_absent_from_config(client, settings):
    response = client.get('/api/me')
    assert settings.ingest_token not in response.text


def test_full_api_local_flow(client, settings, rows):
    created = client.post('/api/jobs', json={'reviews': rows, 'name': 'Browser test'}).json()
    identifier = created['id']
    store = client.app.state.store
    worker = Worker(store, settings)
    assert worker.once()
    job = client.get('/api/jobs/' + identifier).json()
    assert job['status'] == 'ready'
    payload_hash = job['result']['payload_hash']
    invalid = client.post('/api/jobs/' + identifier + '/approve', json={'confirmation': 'approve', 'payload_hash': payload_hash})
    assert invalid.status_code == 422
    good = client.post('/api/jobs/' + identifier + '/approve', json={'confirmation': 'APPROVE', 'payload_hash': payload_hash})
    assert good.status_code == 202
    assert worker.once()
    published = client.get('/api/jobs/' + identifier).json()
    assert published['approval']['status'] == 'published'
    repeated = client.post('/api/jobs/' + identifier + '/approve', json={'confirmation': 'APPROVE', 'payload_hash': payload_hash})
    assert repeated.status_code == 202
    assert repeated.json()['id'] == good.json()['id']
    assert len(client.get('/api/local-effects').json()['effects']) == 2
    assert client.get('/api/jobs/' + identifier + '/report.md').status_code == 200
