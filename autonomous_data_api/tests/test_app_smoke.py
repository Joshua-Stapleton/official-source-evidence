import base64
import json
import os
from decimal import Decimal

os.environ.setdefault("AUTONOMOUS_EVIDENCE_BACKGROUND_REFRESH", "0")
os.environ.setdefault(
    "AUTONOMOUS_EVIDENCE_DB_PATH",
    f"/tmp/autonomous-evidence-app-test-{os.getpid()}.sqlite3",
)
os.environ.setdefault(
    "AUTONOMOUS_RECEIPT_SIGNING_KEY",
    base64.urlsafe_b64encode(b"r" * 32).decode().rstrip("="),
)
os.environ.setdefault("AUTONOMOUS_ANALYTICS_HMAC_KEY", "test-analytics-key")

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from autonomous_data_api.app import (
    CdpFacilitatorAuthProvider,
    MainnetRevenueCapMiddleware,
    app,
    evidence_service,
)
from autonomous_data_api.evidence import PreparedResult, SourceStaleError


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def prepared(monkeypatch):
    def make(product):
        return PreparedResult(
            request_id=f"{product}_fixture",
            product=product,
            request_hash="1" * 64,
            source_bundle_hash="2" * 64,
            result_hash="3" * 64,
            result={
                "request_id": f"{product}_fixture",
                "provenance": {"result_sha256": f"sha256:{'3' * 64}"},
            },
        )

    monkeypatch.setattr(evidence_service, "prepare_sec", lambda _: make("sec"))
    monkeypatch.setattr(evidence_service, "prepare_ofac", lambda _: make("ofac"))


def decode_challenge(response):
    encoded = response.headers["payment-required"]
    encoded += "=" * (-len(encoded) % 4)
    return json.loads(base64.urlsafe_b64decode(encoded))


def test_mainnet_revenue_cap_blocks_before_route_execution():
    class StubService:
        @staticmethod
        def fulfilled_revenue_since(_timestamp_utc, _network):
            return Decimal("9.95")

    limited_app = FastAPI()

    @limited_app.post("/paid")
    def paid():
        return JSONResponse({"ok": True})

    limited_app.add_middleware(
        MainnetRevenueCapMiddleware,
        service=StubService(),
        network="eip155:8453",
        daily_cap=Decimal("10.00"),
        route_prices={("POST", "/paid"): Decimal("0.10")},
    )
    with TestClient(limited_app) as limited_client:
        response = limited_client.post("/paid")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DAILY_REVENUE_CAP_REACHED"
    assert int(response.headers["retry-after"]) > 0


def test_health_and_retired_wedges(client):
    index = client.get("/")
    assert index.status_code == 200
    assert index.json()["manifest"].endswith("/.well-known/agent-service.json")

    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["x402"]["network"] == "eip155:84532"
    assert health.json()["x402"]["prices"] == {
        "sec_delta": "$0.10",
        "ofac_exact": "$0.05",
    }
    assert health.json()["x402"]["revenue_ready"] is False
    for path in (
        "/v1/public/pfas/sample",
        "/v1/public/grid/sample",
        "/v1/pfas/leads",
        "/v1/grid/projects",
    ):
        response = client.get(path)
        assert response.status_code == 410
        assert response.json()["error"] == "RETIRED_WEDGE"


def test_agent_manifest_promotes_only_verdict_endpoints(client):
    manifest = client.get("/.well-known/agent-service.json")
    assert manifest.status_code == 200
    payload = manifest.json()
    assert payload["openapi_url"].endswith("/openapi.json")
    assert payload["payment"]["protocol"] == "x402-v2"
    assert payload["agent_paid_endpoints"] == [
        "http://localhost:8765/v1/sec/filing-trigger-delta",
        "http://localhost:8765/v1/ofac/exact-identifier-evidence",
    ]
    assert client.get("/v1/x402/pfas/leads").status_code == 410
    assert client.get("/v1/x402/grid/projects").status_code == 410


@pytest.mark.parametrize(
    ("path", "body", "expected_tag", "expected_amount"),
    [
        (
            "/v1/sec/filing-trigger-delta",
            {
                "cik": "320193",
                "since_accession": "0000320193-26-000081",
                "forms": ["8-K"],
                "rules": ["FORM:8-K:ITEM:2.02"],
            },
            "filing-delta",
            "100000",
        ),
        (
            "/v1/ofac/exact-identifier-evidence",
            {
                "identifier_type": "crypto_address",
                "identifier": "0x0000000000000000000000000000000000000000",
                "networks": ["eip155:1"],
                "lists": ["SDN"],
            },
            "exact-match",
            "50000",
        ),
    ],
)
def test_verdict_routes_advertise_payment_and_bazaar_post_schema(
    client,
    prepared,
    path,
    body,
    expected_tag,
    expected_amount,
):
    response = client.post(path, json=body)
    assert response.status_code == 402
    assert response.headers["x-evidence-request-id"].endswith("_fixture")

    challenge = decode_challenge(response)
    assert challenge["x402Version"] == 2
    assert challenge["accepts"][0]["network"] == "eip155:84532"
    assert challenge["accepts"][0]["amount"] == expected_amount
    assert challenge["resource"]["serviceName"] == "Official Source Evidence"
    assert expected_tag in challenge["resource"]["tags"]
    assert challenge["extensions"]["bazaar"]["info"]["input"]["method"] == "POST"


def test_invalid_input_is_rejected_before_payment(client):
    response = client.post(
        "/v1/ofac/exact-identifier-evidence",
        json={"identifier_type": "fuzzy_name", "identifier": "example"},
    )
    assert response.status_code == 422
    assert "payment-required" not in response.headers
    assert response.json()["error"]["code"] == "INVALID_INPUT"


def test_stale_source_is_rejected_before_payment(client, monkeypatch):
    monkeypatch.setattr(
        evidence_service,
        "prepare_ofac",
        lambda _: (_ for _ in ()).throw(SourceStaleError("fixture stale source")),
    )
    response = client.post(
        "/v1/ofac/exact-identifier-evidence",
        json={
            "identifier_type": "ofac_uid",
            "identifier": "36",
            "lists": ["SDN"],
        },
    )
    assert response.status_code == 503
    assert "payment-required" not in response.headers
    assert response.json()["error"]["code"] == "SOURCE_STALE"


def test_cdp_facilitator_auth_signs_each_endpoint_for_mainnet():
    private_key = ec.generate_private_key(ec.SECP256R1())
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    provider = CdpFacilitatorAuthProvider(
        "organizations/test/apiKeys/test-key",
        private_pem,
        "https://api.cdp.coinbase.com/platform/v2/x402",
    )

    headers = provider.get_auth_headers()
    expected_uris = {
        headers.verify[
            "Authorization"
        ]: "POST api.cdp.coinbase.com/platform/v2/x402/verify",
        headers.settle[
            "Authorization"
        ]: "POST api.cdp.coinbase.com/platform/v2/x402/settle",
        headers.supported[
            "Authorization"
        ]: "GET api.cdp.coinbase.com/platform/v2/x402/supported",
        headers.bazaar["Authorization"]: (
            "GET api.cdp.coinbase.com/platform/v2/x402/discovery/resources"
        ),
    }
    for authorization, expected_uri in expected_uris.items():
        claims = jwt.decode(
            authorization.removeprefix("Bearer "), options={"verify_signature": False}
        )
        assert claims["uris"] == [expected_uri]
