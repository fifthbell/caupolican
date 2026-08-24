from pathlib import Path
import sys
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from prometheus_client.parser import text_string_to_metric_families

from app import channel_worker, server
from app.channel_worker import ChannelWorker
from app.metrics import render_metrics


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr(server, "ROUTER_TOKEN", "test-router-token")
    monkeypatch.setattr(server, "OUT_ROOT", str(tmp_path))
    server.workers.clear()
    yield TestClient(server.app, raise_server_exceptions=False)


def parsed_metrics(text: str):
    return list(text_string_to_metric_families(text))


def test_metrics_requires_configured_authentication(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(server, "ROUTER_TOKEN", None)
    client = TestClient(server.app, raise_server_exceptions=False)

    assert client.get("/metrics").status_code == 503

    monkeypatch.setattr(server, "ROUTER_TOKEN", "test-router-token")
    assert client.get("/metrics").status_code == 401


def test_authenticated_scrape_is_parseable_and_uses_bounded_labels(client: TestClient):
    assert client.get("/api/health").status_code == 200
    assert client.get("/a/private/source/url").status_code == 404

    response = client.get(
        "/metrics", headers={"Authorization": "Bearer test-router-token"}
    )

    assert response.status_code == 200
    families = parsed_metrics(response.text)
    names = {family.name for family in families}
    assert "caupolican_service_info" in names
    assert "python_info" in names
    if sys.platform.startswith("linux"):
        assert "process_cpu_seconds" in names
    assert "caupolican_http_requests" in names
    assert "/a/private/source/url" not in response.text
    assert 'route="unmatched"' in response.text

    forbidden_labels = {
        "channel_id",
        "source_url",
        "path",
        "token",
        "error",
        "exception",
    }
    for family in families:
        for sample in family.samples:
            assert forbidden_labels.isdisjoint(sample.labels)


def test_ffmpeg_success_and_failure_do_not_export_source_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    worker = ChannelWorker("sensitive-channel-id", str(tmp_path), 1, 2)
    sensitive_url = "https://example.invalid/private-stream?token=secret"
    worker.live_url = sensitive_url

    monkeypatch.setattr(channel_worker.subprocess, "Popen", Mock(return_value=Mock()))
    worker._start_live()

    monkeypatch.setattr(
        channel_worker.subprocess,
        "Popen",
        Mock(side_effect=OSError("raw provider failure")),
    )
    with pytest.raises(OSError):
        worker._start_live()

    output = render_metrics().decode()
    assert sensitive_url not in output
    assert "sensitive-channel-id" not in output
    assert "raw provider failure" not in output
    assert 'operation="start_live",result="success"' in output
    assert 'operation="start_live",result="failure"' in output
