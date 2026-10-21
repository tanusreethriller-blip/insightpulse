import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]


def test_streamlit_dashboard_opens_without_login(tmp_path, monkeypatch):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    api_url = f"http://127.0.0.1:{port}"
    env = os.environ.copy()
    env.update({
        "DATA_DIR": str(tmp_path),
        "PUBLIC_URL": api_url,
        "INGEST_API_TOKEN": "streamlit-test-token-" + "x" * 30,
        "WORKER_ENABLED": "true",
    })
    server = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn", "reviewpulse.api:create_app", "--factory",
            "--host", "127.0.0.1", "--port", str(port), "--workers", "1", "--no-access-log",
        ],
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
    )
    monkeypatch.setenv("REVIEW_PULSE_API_URL", api_url)
    try:
        for _ in range(100):
            if server.poll() is not None:
                raise AssertionError("The test API process exited during startup.")
            try:
                if httpx.get(api_url + "/healthz", timeout=0.5).is_success:
                    break
            except httpx.RequestError:
                time.sleep(0.1)
        else:
            raise AssertionError("The test API process did not become ready.")

        app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
        assert not app.exception
        assert "Your feedback, in focus." in [item.value for item in app.title]
        assert "Explore the synthetic demo" in [button.label for button in app.button]

        next(button for button in app.button if button.label == "Explore the synthetic demo").click().run()
        jobs = httpx.get(api_url + "/api/jobs", timeout=2).json()["jobs"]
        job_id = jobs[0]["id"]
        for _ in range(100):
            job = httpx.get(api_url + f"/api/jobs/{job_id}", timeout=2).json()
            if job["status"] in {"ready", "failed"}:
                break
            time.sleep(0.1)
        assert job["status"] == "ready", job.get("error")
        next(button for button in app.button if button.label == "Refresh status").click().run()
        app.radio[0].set_value("Weekly pulse").run()
        assert "Weekly pulse" in [item.value for item in app.title]
        assert any(item.label == "Top theme" and item.value for item in app.metric)
        assert {"Rating trend", "Weekly feedback", "What customers talked about"} <= {
            item.value for item in app.subheader
        }
        app.radio[0].set_value("Reports").run()
        assert any("Report · Synthetic demo" in item.value for item in app.subheader)
        next(item for item in app.text_input if item.label == "Type APPROVE to confirm").set_value("APPROVE")
        next(button for button in app.button if button.label == "Approve & publish").click().run()
        assert httpx.get(api_url + f"/api/jobs/{job_id}", timeout=2).json()["approval"]
        assert not app.exception
    finally:
        server.terminate()
        server.wait(timeout=10)
