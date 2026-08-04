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
    load_cdp_api_key_secret,
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
    monkeypatch.setattr(
        evidence_service, "prepare_sec_signal", lambda _: make("sec_signal")
    )
    monkeypatch.setattr(evidence_service, "prepare_ofac", lambda _: make("ofac"))
    monkeypatch.setattr(
        evidence_service,
        "prepare_ofac_preflight",
        lambda _: make("ofac_preflight"),
    )


def decode_challenge(response):
    encoded = response.headers["payment-required"]
    encoded += "=" * (-len(encoded) % 4)
    return json.loads(base64.urlsafe_b64decode(encoded))


def test_cdp_secret_can_be_loaded_from_base64(monkeypatch):
    monkeypatch.delenv("CDP_API_KEY_SECRET", raising=False)
    monkeypatch.setenv(
        "CDP_API_KEY_SECRET_B64",
        base64.b64encode(b"private-key-material").decode("ascii"),
    )
    assert load_cdp_api_key_secret() == "private-key-material"


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
    assert index.headers["content-type"].startswith("text/html")
    assert "Official Source Evidence" in index.text
    assert 'href="/.well-known/agent-service.json"' in index.text

    index_json = client.get("/index.json")
    assert index_json.status_code == 200
    assert index_json.json()["manifest"].endswith("/.well-known/agent-service.json")

    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["x402"]["network"] == "eip155:84532"
    assert health.json()["x402"]["prices"] == {
        "ofac_preflight": "$0.01",
        "sec_signal": "$0.01",
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
        "http://localhost:8765/v1/ofac/payment-preflight",
        "http://localhost:8765/v1/sec/filing-change-signal",
        "http://localhost:8765/v1/sec/filing-trigger-delta",
        "http://localhost:8765/v1/ofac/exact-identifier-evidence",
    ]
    assert client.get("/v1/x402/pfas/leads").status_code == 410
    assert client.get("/v1/x402/grid/projects").status_code == 410


def test_machine_discovery_and_crawler_surfaces(client):
    schema = client.get("/openapi.json").json()
    assert schema["info"]["contact"]["email"] == "joshua@regulavita.com"
    assert "x402 v2 USDC payment" in schema["info"]["x-guidance"]
    assert schema["servers"] == [{"url": "http://localhost:8765"}]

    expected = {
        "/v1/ofac/payment-preflight": "0.010000",
        "/v1/sec/filing-change-signal": "0.010000",
        "/v1/sec/filing-trigger-delta": "0.100000",
        "/v1/ofac/exact-identifier-evidence": "0.050000",
    }
    for path, price in expected.items():
        operation = schema["paths"][path]["post"]
        assert "security" not in operation
        assert operation["x-payment-info"] == {
            "price": {"mode": "fixed", "currency": "USD", "amount": price},
            "protocols": [{"x402": {}}],
        }
        assert operation["responses"]["402"]["description"] == "Payment Required"
        assert operation["requestBody"]["content"]["application/json"]["example"]
        assert operation["requestBody"].get("required", False) is False
        assert operation["x-monitoring-probe"] == {
            "method": "POST",
            "body": "omitted",
            "expected_status": 402,
        }

    sec_properties = schema["components"]["schemas"]["SecDeltaRequest"]["properties"]
    assert sec_properties["cik"]["example"] == "0000320193"
    assert sec_properties["ticker"]["example"] == "AAPL"
    assert sec_properties["since_accession"]["example"] == ("0000320193-26-000018")
    assert sec_properties["since"]["example"] == "2026-07-30T00:00:00Z"

    assert schema["paths"]["/health"]["get"]["security"] == []
    assert "/v1/evidence/replay/{request_id}" not in schema["paths"]

    llms = client.get("/llms.txt")
    assert llms.status_code == 200
    assert "/v1/ofac/payment-preflight - $0.01 USDC" in llms.text
    assert "/v1/sec/filing-change-signal - $0.01 USDC" in llms.text
    assert "/v1/ofac/exact-identifier-evidence - $0.05 USDC" in llms.text
    assert "/v1/sec/filing-trigger-delta - $0.10 USDC" in llms.text

    robots = client.get("/robots.txt")
    assert robots.status_code == 200
    assert "Sitemap: http://localhost:8765/sitemap.xml" in robots.text

    icon = client.get("/favicon.ico")
    assert icon.status_code == 200
    assert icon.headers["content-type"].startswith("image/png")
    assert len(icon.content) > 1_000


@pytest.mark.parametrize(
    ("path", "body", "expected_tag", "expected_amount"),
    [
        (
            "/v1/ofac/payment-preflight",
            {
                "address": "0x0000000000000000000000000000000000000000",
                "network": "eip155:8453",
            },
            "payment-preflight",
            "10000",
        ),
        (
            "/v1/sec/filing-change-signal",
            {
                "ticker": "AAPL",
                "since": "2026-07-30T00:00:00Z",
                "forms": ["8-K"],
            },
            "filing-change",
            "10000",
        ),
        (
            "/v1/sec/filing-trigger-delta",
            {
                "cik": "320193",
                "since_accession": "0000320193-26-000018",
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


@pytest.mark.parametrize(
    ("path", "expected_amount"),
    [
        ("/v1/ofac/payment-preflight", "10000"),
        ("/v1/sec/filing-change-signal", "10000"),
        ("/v1/sec/filing-trigger-delta", "100000"),
        ("/v1/ofac/exact-identifier-evidence", "50000"),
    ],
)
def test_empty_post_is_a_monitorable_payment_probe(
    client, prepared, path, expected_amount
):
    response = client.post(path)

    assert response.status_code == 402
    assert response.headers["x-evidence-request-id"].endswith("_fixture")
    challenge = decode_challenge(response)
    assert challenge["accepts"][0]["amount"] == expected_amount
    assert challenge["resource"]["url"].endswith(path)


def test_nonempty_invalid_json_is_still_rejected_before_payment(client):
    response = client.post(
        "/v1/ofac/exact-identifier-evidence",
        content=b"not-json",
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 400
    assert "payment-required" not in response.headers
    assert response.json()["error"]["code"] == "INVALID_JSON"


def test_empty_json_object_is_still_rejected_before_payment(client):
    response = client.post("/v1/ofac/exact-identifier-evidence", json={})

    assert response.status_code == 422
    assert "payment-required" not in response.headers
    assert response.json()["error"]["code"] == "INVALID_INPUT"


def test_legacy_fly_origin_retires_paid_routes_and_redirects_public_pages(client):
    paid = client.post(
        "/v1/ofac/payment-preflight",
        headers={"host": "iti-official-source-evidence.fly.dev"},
    )
    assert paid.status_code == 410
    assert paid.json()["error"]["code"] == "LEGACY_ORIGIN_RETIRED"
    assert paid.json()["error"]["canonical_url"].endswith("/v1/ofac/payment-preflight")

    public = client.get(
        "/health",
        headers={"host": "iti-official-source-evidence.fly.dev"},
        follow_redirects=False,
    )
    assert public.status_code == 308
    assert public.headers["location"] == "http://localhost:8765/health"


def test_x402_list_domain_proof_is_disabled_without_token(client):
    assert client.get("/.well-known/x402list.txt").status_code == 404


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
