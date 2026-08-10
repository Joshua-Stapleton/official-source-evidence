import hashlib
import hmac
import json
import time

import pytest

from autonomous_data_api.marketplace import MarketplaceError, The402Provider
from autonomous_data_api.monitors import WebMonitorService

API_KEY = "sk_test_provider"
WEBHOOK_SECRET = "whsec_test_provider"
SERVICE_ID = "svc_source_watch"


def provider(tmp_path, monkeypatch):
    monkeypatch.setenv("THE402_API_KEY", API_KEY)
    monkeypatch.setenv("THE402_WEBHOOK_SECRET", WEBHOOK_SECRET)
    monkeypatch.setenv("THE402_SOURCE_WATCH_SERVICE_ID", SERVICE_ID)
    monkeypatch.setenv("AUTONOMOUS_API_BASE_URL", "https://evidence.regulavita.com")
    db_path = tmp_path / "marketplace.sqlite3"
    monitor_service = WebMonitorService(db_path)
    return The402Provider(db_path, monitor_service)


def job_payload(**overrides):
    payload = {
        "type": "job_dispatch",
        "job_id": "job_fixture_123",
        "service_id": SERVICE_ID,
        "brief": {
            "url": "https://example.com/changelog",
            "label": "Example changelog",
        },
        "callback_url": ("https://api.the402.ai/v1/threads/thread_fixture_123/update"),
    }
    payload.update(overrides)
    return payload


def signed_headers(raw_body: bytes, timestamp: int | None = None):
    sent_at = timestamp or int(time.time())
    signature = (
        "sha256="
        + hmac.new(
            WEBHOOK_SECRET.encode(),
            f"{sent_at}.".encode() + raw_body,
            hashlib.sha256,
        ).hexdigest()
    )
    return {
        "platform_secret": API_KEY,
        "signature": signature,
        "timestamp": str(sent_at),
    }


def test_valid_job_is_fulfilled_once_and_creates_private_monitor(tmp_path, monkeypatch):
    integration = provider(tmp_path, monkeypatch)
    raw_body = json.dumps(job_payload(), separators=(",", ":")).encode()
    delivered = []
    monkeypatch.setattr(
        integration,
        "_post_callback",
        lambda callback_url, payload: delivered.append((callback_url, payload)),
    )

    accepted = integration.accept_webhook(raw_body, **signed_headers(raw_body))
    assert accepted["status"] == "PENDING"
    assert integration.run_pending() == 1
    assert integration.public_status()["events"] == {"completed": 1}

    callback_url, callback = delivered[0]
    assert callback_url.startswith("https://api.the402.ai/v1/threads/")
    assert callback["status"] == "completed"
    deliverables = callback["deliverables"]
    assert deliverables["status"] == "ACTIVE"
    assert deliverables["source_url"] == "https://example.com/changelog"
    assert deliverables["access_token"]
    monitor = integration.monitor_service.status(
        deliverables["monitor_id"], deliverables["access_token"]
    )
    assert monitor["status"] == "ACTIVE"

    repeated = integration.accept_webhook(raw_body, **signed_headers(raw_body))
    assert repeated["status"] == "COMPLETED"
    assert integration.run_pending() == 0
    assert len(delivered) == 1


def test_invalid_signature_and_stale_timestamp_are_rejected(tmp_path, monkeypatch):
    integration = provider(tmp_path, monkeypatch)
    raw_body = json.dumps(job_payload()).encode()
    headers = signed_headers(raw_body)
    headers["signature"] = "sha256=" + "0" * 64
    with pytest.raises(MarketplaceError) as invalid:
        integration.accept_webhook(raw_body, **headers)
    assert invalid.value.code == "INVALID_WEBHOOK_SIGNATURE"

    with pytest.raises(MarketplaceError) as stale:
        integration.accept_webhook(
            raw_body, **signed_headers(raw_body, int(time.time()) - 301)
        )
    assert stale.value.code == "STALE_WEBHOOK"


def test_callback_is_pinned_to_the402_api(tmp_path, monkeypatch):
    integration = provider(tmp_path, monkeypatch)
    raw_body = json.dumps(
        job_payload(callback_url="https://example.com/steal-provider-key")
    ).encode()
    with pytest.raises(MarketplaceError) as captured:
        integration.accept_webhook(raw_body, **signed_headers(raw_body))
    assert captured.value.code == "INVALID_CALLBACK"


def test_reused_job_id_with_changed_payload_is_rejected(tmp_path, monkeypatch):
    integration = provider(tmp_path, monkeypatch)
    first = json.dumps(job_payload()).encode()
    changed = json.dumps(
        job_payload(brief={"url": "https://example.com/different"})
    ).encode()
    integration.accept_webhook(first, **signed_headers(first))
    with pytest.raises(MarketplaceError) as captured:
        integration.accept_webhook(changed, **signed_headers(changed))
    assert captured.value.code == "EVENT_ID_REUSED"
