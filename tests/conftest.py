from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from reviewpulse.api import create_app
from reviewpulse.config import Settings
from reviewpulse.service import Service
from reviewpulse.store import Store
from reviewpulse.worker import Worker


@pytest.fixture
def settings(tmp_path):
    return Settings(data_dir=tmp_path / "var", ingest_token="t" * 40, worker_enabled=False)


@pytest.fixture
def rows():
    return [{"date": (date(2026, 10, 1) - timedelta(days=i * 3)).isoformat(), "rating": 1 if i % 3 == 0 else 4, "text": f"The application crashes when I upload a file in workspace number {i}."} for i in range(20)]


@pytest.fixture
def store(settings):
    return Store(settings.data_dir / "test.sqlite3")


@pytest.fixture
def service(settings, store):
    return Service(settings, store)


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as c:
        yield c


@pytest.fixture
def ready_job(service, rows, settings, store):
    job = service.submit(rows, "Test feedback")
    assert Worker(store, settings).once()
    assert store.job(job["id"])["status"] == "ready"
    return job
