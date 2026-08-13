from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from autonomous_data_api import monitors
from autonomous_data_api.evidence import (
    PortfolioMonitorCreateRequest,
    WebMonitorCreateRequest,
)
from autonomous_data_api.monitors import MonitorError, WebMonitorService


def activate(service: WebMonitorService, **overrides):
    values = {
        "request_id": "source_change_watch_fixture",
        "payment_signature": "paid-proof-1",
        "url": "https://example.com/status",
        "label": "Example status",
        "webhook_url": None,
        "base_url": "https://evidence.regulavita.com",
    }
    values.update(overrides)
    return service.activate(**values)


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/status",
        "https://localhost/status",
        "https://127.0.0.1/status",
        "https://user:pass@example.com/status",
        "https://example.com:8443/status",
        "https://example.com/status#fragment",
    ],
)
def test_request_rejects_unsafe_or_unsupported_urls(url):
    with pytest.raises(ValidationError):
        WebMonitorCreateRequest(url=url)


def test_normalization_removes_html_code_and_canonicalizes_json():
    html = b"<main><h1>Status</h1><script>secret()</script><p>All good</p></main>"
    assert monitors.normalize_content(html, "text/html") == "Status\n\nAll good"
    assert monitors.normalize_content(b'{"b": 2, "a": 1}', "application/json") == (
        '{"a":1,"b":2}'
    )


def test_dns_rebinding_guard_rejects_any_non_public_resolution(monkeypatch):
    monkeypatch.setattr(
        monitors.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (2, 1, 6, "", ("93.184.216.34", 443)),
            (2, 1, 6, "", ("127.0.0.1", 443)),
        ],
    )
    with pytest.raises(MonitorError, match="non-public") as captured:
        monitors._public_addresses("example.com")
    assert captured.value.code == "NON_PUBLIC_HOST"


def test_activation_is_idempotent_and_access_is_private(tmp_path):
    service = WebMonitorService(tmp_path / "monitor.sqlite3")
    created = activate(service)
    repeated = activate(service)

    assert repeated["monitor_id"] == created["monitor_id"]
    assert repeated["access_token"] == created["access_token"]
    assert service.public_stats()["active_monitors"] == 1

    status = service.status(created["monitor_id"], created["access_token"])
    assert status["status"] == "ACTIVE"
    assert status["successful_checks"] == 0
    with pytest.raises(MonitorError) as captured:
        service.status(created["monitor_id"], "wrong-token")
    assert captured.value.status_code == 404

    assert service.cancel(created["monitor_id"], created["access_token"]) == {
        "monitor_id": created["monitor_id"],
        "status": "CANCELLED",
    }


def test_payment_proof_cannot_be_reused_for_another_request(tmp_path):
    service = WebMonitorService(tmp_path / "monitor.sqlite3")
    activate(service)
    with pytest.raises(MonitorError) as captured:
        activate(service, request_id="different-request")
    assert captured.value.code == "PAYMENT_ALREADY_USED"
    assert captured.value.status_code == 409


def test_portfolio_activation_creates_private_idempotent_monitors(tmp_path):
    service = WebMonitorService(tmp_path / "monitor.sqlite3")
    sources = PortfolioMonitorCreateRequest(
        sources=[
            {"url": "https://example.com/one", "label": "One"},
            {"url": "https://example.com/two", "label": "Two"},
        ]
    ).sources

    created = service.activate_portfolio(
        request_id="portfolio-fixture",
        payment_signature="portfolio-proof",
        sources=sources,
        base_url="https://evidence.regulavita.com",
    )
    repeated = service.activate_portfolio(
        request_id="portfolio-fixture",
        payment_signature="portfolio-proof",
        sources=sources,
        base_url="https://evidence.regulavita.com",
    )

    assert repeated == created
    assert created["source_count"] == 2
    assert len({item["monitor_id"] for item in created["monitors"]}) == 2
    assert service.public_stats()["active_monitors"] == 2
    for item in created["monitors"]:
        status = service.status(item["monitor_id"], item["access_token"])
        assert status["status"] == "ACTIVE"


def test_portfolio_request_rejects_duplicate_urls():
    with pytest.raises(ValidationError, match="unique"):
        PortfolioMonitorCreateRequest(
            sources=[
                {"url": "https://example.com/same"},
                {"url": "https://example.com/same"},
            ]
        )


def test_due_checks_store_baseline_then_detect_change(tmp_path, monkeypatch):
    service = WebMonitorService(tmp_path / "monitor.sqlite3")
    created = activate(service)
    source = {"text": "Version one"}

    monkeypatch.setattr(
        monitors,
        "fetch_public_source",
        lambda _url: (200, "text/plain", source["text"]),
    )
    assert service.run_due() == 1

    first = service.status(created["monitor_id"], created["access_token"])
    assert first["successful_checks"] == 1
    assert first["change_count"] == 0
    assert first["events"] == []

    source["text"] = "Version two"
    with service._connect() as connection:
        connection.execute(
            "UPDATE web_monitors SET next_check_at = ? WHERE monitor_id = ?",
            (
                (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
                created["monitor_id"],
            ),
        )
        connection.commit()

    assert service.run_due() == 1
    changed = service.status(created["monitor_id"], created["access_token"])
    assert changed["successful_checks"] == 2
    assert changed["change_count"] == 1
    assert len(changed["events"]) == 1
    assert "-Version one" in changed["events"][0]["diff_text"]
    assert "+Version two" in changed["events"][0]["diff_text"]


def test_due_check_records_fetch_failure_without_disabling_monitor(
    tmp_path, monkeypatch
):
    service = WebMonitorService(tmp_path / "monitor.sqlite3")
    created = activate(service)

    def fail(_url):
        raise MonitorError("FETCH_FAILED", "fixture failed", 502)

    monkeypatch.setattr(monitors, "fetch_public_source", fail)
    assert service.run_due() == 1

    status = service.status(created["monitor_id"], created["access_token"])
    assert status["status"] == "ACTIVE"
    assert status["successful_checks"] == 0
    assert status["consecutive_failures"] == 1
    assert status["last_error"]["code"] == "FETCH_FAILED"
