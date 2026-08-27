import asyncio
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

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from fastapi.testclient import TestClient

from autonomous_data_api.app import (
    FORM_D_PROBE_PAYLOAD,
    PORTFOLIO_WATCH_PROBE_PAYLOAD,
    SOURCE_SNAPSHOT_PROBE_PAYLOAD,
    SOURCE_WATCH_PROBE_PAYLOAD,
    CdpFacilitatorAuthProvider,
    EvidencePrecomputeMiddleware,
    MainnetRevenueCapMiddleware,
    app,
    evidence_mcp_service,
    evidence_service,
    load_cdp_api_key_secret,
    payment_failure_diagnostics,
    payment_failure_reason_code,
)
from autonomous_data_api.evidence import PreparedResult, SourceStaleError
from autonomous_data_api.procurement import PythonRunRequest


@pytest.fixture(scope="module")
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
        evidence_service,
        "prepare_form_d_funding_leads",
        lambda _: make("form_d_funding_leads"),
    )
    monkeypatch.setattr(
        evidence_service, "prepare_sec_signal", lambda _: make("sec_signal")
    )
    monkeypatch.setattr(evidence_service, "prepare_ofac", lambda _: make("ofac"))
    monkeypatch.setattr(
        evidence_service,
        "prepare_ofac_preflight",
        lambda _: make("ofac_preflight"),
    )
    monkeypatch.setattr(
        evidence_service,
        "prepare_web_monitor",
        lambda _: make("source_change_watch"),
    )
    monkeypatch.setattr(
        evidence_service,
        "prepare_portfolio_monitor",
        lambda _: make("source_change_portfolio"),
    )
    monkeypatch.setattr(
        evidence_service,
        "prepare_public_source_snapshot",
        lambda _: make("public_source_snapshot"),
    )
    monkeypatch.setattr(
        evidence_service,
        "prepare_public_source_snapshot_quote",
        lambda _: make("public_source_snapshot_quote"),
    )


def decode_challenge(response):
    encoded = response.headers["payment-required"]
    encoded += "=" * (-len(encoded) % 4)
    return json.loads(base64.urlsafe_b64decode(encoded))


def encoded_header(payload):
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")


def test_payment_failure_diagnostics_distinguishes_verification_and_settlement():
    verification = JSONResponse(
        content={},
        status_code=402,
        headers={
            "payment-required": encoded_header(
                {"error": "invalid_exact_evm_payload_authorization_valid_before"}
            )
        },
    )
    settlement = JSONResponse(
        content={},
        status_code=402,
        headers={
            "payment-response": encoded_header(
                {"errorReason": "settle_exact_node_failure"}
            )
        },
    )

    assert payment_failure_diagnostics(verification, "signed") == (
        "verification",
        "invalid_exact_evm_payload_authorization_valid_before",
    )
    assert payment_failure_diagnostics(settlement, "signed") == (
        "settlement",
        "settle_exact_node_failure",
    )
    assert payment_failure_diagnostics(verification, None) == (None, None)


def test_payment_failure_reason_preserves_safe_coinbase_evm_enums_only():
    assert payment_failure_reason_code("invalid_exact_evm_insufficient_funds") == (
        "invalid_exact_evm_insufficient_funds"
    )
    assert payment_failure_reason_code("permit2_insufficient_balance") == (
        "permit2_insufficient_balance"
    )
    assert payment_failure_reason_code("wallet 0x123 private detail") == "unclassified"
    assert payment_failure_reason_code("invalid_exact_evm_" + "x" * 200) == (
        "unclassified"
    )


def test_payment_failure_diagnostics_does_not_persist_unknown_error_text():
    response = JSONResponse(
        content={},
        status_code=402,
        headers={
            "payment-required": encoded_header(
                {"error": "private upstream detail customer@example.com"}
            )
        },
    )

    assert payment_failure_diagnostics(response, "signed") == (
        "verification",
        "unclassified",
    )


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


def test_mainnet_revenue_cap_does_not_hold_lock_during_fulfillment():
    class StubService:
        @staticmethod
        def fulfilled_revenue_since(_timestamp_utc, _network):
            return Decimal(0)

    async def run_concurrently():
        async def unused_app(_scope, _receive, _send):
            return None

        middleware = MainnetRevenueCapMiddleware(
            unused_app,
            service=StubService(),
            network="eip155:8453",
            daily_cap=Decimal("1.00"),
            route_prices={("POST", "/paid"): Decimal("0.10")},
        )
        entered = 0
        both_entered = asyncio.Event()
        release = asyncio.Event()

        async def call_next(_request):
            nonlocal entered
            entered += 1
            if entered == 2:
                both_entered.set()
            await release.wait()
            return Response(status_code=200)

        def request():
            return Request(
                {
                    "type": "http",
                    "method": "POST",
                    "path": "/paid",
                    "raw_path": b"/paid",
                    "query_string": b"",
                    "headers": [],
                    "scheme": "https",
                    "server": ("testserver", 443),
                    "client": ("127.0.0.1", 1234),
                }
            )

        first = asyncio.create_task(middleware.dispatch(request(), call_next))
        second = asyncio.create_task(middleware.dispatch(request(), call_next))
        await asyncio.wait_for(both_entered.wait(), timeout=1)
        release.set()
        await asyncio.gather(first, second)

    asyncio.run(run_concurrently())


def test_health_and_retired_wedges(client):
    index = client.get("/")
    assert index.status_code == 200
    assert index.headers["content-type"].startswith("text/html")
    assert "Source Change Watch" in index.text
    assert "/v1/procure/company-profile" in index.text
    assert 'href="/.well-known/agent-service.json"' in index.text

    index_json = client.get("/index.json")
    assert index_json.status_code == 200
    assert index_json.json()["manifest"].endswith("/.well-known/agent-service.json")

    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["x402"]["network"] == "eip155:84532"
    assert health.json()["x402"]["prices"] == {
        "public_source_snapshot": "$0.03",
        "source_change_watch_30_day": "$1.00",
        "source_change_portfolio_30_day": "$9.00",
        "form_d_funding_leads": "$0.05",
        "ofac_preflight": "$0.01",
        "sec_signal": "$0.01",
        "sec_delta": "$0.10",
        "ofac_exact": "$0.05",
    }
    assert health.json()["x402"]["revenue_ready"] is False
    assert health.json()["company_profile_procurement"]["enabled"] is False
    assert (
        health.json()["company_profile_procurement"][
            "maximum_supplier_cost_per_call_usd"
        ]
        == "0.02"
    )
    for path in (
        "/v1/public/pfas/sample",
        "/v1/public/grid/sample",
        "/v1/pfas/leads",
        "/v1/grid/projects",
    ):
        response = client.get(path)
        assert response.status_code == 410
        assert response.json()["error"] == "RETIRED_WEDGE"


def test_procurement_quote_and_sample_are_free_and_strict(client):
    sample = client.get("/v1/procure/company-profile/sample")
    assert sample.status_code == 200
    assert sample.json()["price"] == "$0.25"
    assert sample.json()["example_result"]["product"] == ("PROCURED_COMPANY_PROFILE")

    quote = client.post(
        "/v1/procure/company-profile/quote",
        json={"company_name": "Stripe", "domain": "stripe.com"},
    )
    assert quote.status_code == 200
    assert quote.json()["price"] == "$0.25"
    assert quote.json()["maximum_supplier_cost_usd"] == "0.02"
    assert len(quote.json()["supplier_plan"]) == 2

    assert sample.json()["example_result"]["source_records"] == [
        {"title": "Stripe", "url": "https://stripe.com"}
    ]

    invalid = client.post(
        "/v1/procure/company-profile/quote",
        json={"company_name": "Stripe", "domain": "https://stripe.com/path"},
    )
    assert invalid.status_code == 422

    python_sample = client.get("/v1/compute/python-run/sample")
    assert python_sample.status_code == 200
    assert python_sample.json()["price"] == "$0.03"
    assert python_sample.json()["economics"] == {
        "maximum_supplier_cost_usd": "0.015",
        "maximum_gross_margin_usd": "0.015",
    }

    python_quote = client.post(
        "/v1/compute/python-run/quote",
        json={"code": "print(sum(range(11)))", "timeout_seconds": 5},
    )
    assert python_quote.status_code == 200
    assert python_quote.json()["price"] == "$0.03"
    assert python_quote.json()["maximum_supplier_cost_usd"] == "0.015"
    assert len(python_quote.json()["supplier_plan"]) == 3

    invalid_python = client.post(
        "/v1/compute/python-run/quote",
        json={"code": "print(1)", "timeout_seconds": 31},
    )
    assert invalid_python.status_code == 422


def test_agent_manifest_promotes_only_verdict_endpoints(client):
    manifest = client.get("/.well-known/agent-service.json")
    assert manifest.status_code == 200
    payload = manifest.json()
    assert payload["openapi_url"].endswith("/openapi.json")
    assert payload["x402_manifest_url"].endswith("/.well-known/x402")
    assert payload["payment"]["protocol"] == "x402-v2"
    assert payload["agent_paid_endpoints"] == [
        "http://localhost:8765/v1/web/source-snapshot",
        "http://localhost:8765/v1/monitors/source-change",
        "http://localhost:8765/v1/monitors/source-change-portfolio",
        "http://localhost:8765/v1/gtm/form-d-funding-leads",
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
        "/v1/web/source-snapshot": "0.030000",
        "/v1/monitors/source-change": "1.000000",
        "/v1/monitors/source-change-portfolio": "9.000000",
        "/v1/gtm/form-d-funding-leads": "0.050000",
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
    assert "/v1/web/source-snapshot - $0.03 USDC" in llms.text
    assert "/v1/ofac/payment-preflight - $0.01 USDC" in llms.text
    assert "/v1/gtm/form-d-funding-leads - $0.05 USDC" in llms.text
    assert "/v1/sec/filing-change-signal - $0.01 USDC" in llms.text
    assert "/v1/ofac/exact-identifier-evidence - $0.05 USDC" in llms.text
    assert "/v1/sec/filing-trigger-delta - $0.10 USDC" in llms.text
    assert "/v1/monitors/source-change - $1.00 USDC" in llms.text
    assert "/v1/monitors/source-change-portfolio - $9.00 USDC" in llms.text

    robots = client.get("/robots.txt")
    assert robots.status_code == 200
    assert "Sitemap: http://localhost:8765/sitemap.xml" in robots.text

    icon = client.get("/favicon.ico")
    assert icon.status_code == 200
    assert icon.headers["content-type"].startswith("image/png")
    assert len(icon.content) > 1_000

    x402_manifest = client.get("/.well-known/x402")
    assert x402_manifest.status_code == 200
    x402_payload = x402_manifest.json()
    assert x402_payload["x402Version"] == 2
    assert (
        x402_payload["network"]
        == client.get("/.well-known/agent-service.json").json()["payment"]["network"]
    )
    assert x402_payload["mcp"]["example_payment_tool"] == "get_example_payment"
    assert any(
        resource["url"].endswith("/v1/ofac/payment-preflight")
        and resource["price"] == "$0.01"
        for resource in x402_payload["resources"]
    )


@pytest.mark.parametrize(
    ("path", "body", "expected_tag", "expected_amount", "expected_service"),
    [
        (
            "/v1/web/source-snapshot",
            SOURCE_SNAPSHOT_PROBE_PAYLOAD,
            "content-extraction",
            "30000",
            "Public Source Snapshot",
        ),
        (
            "/v1/monitors/source-change",
            SOURCE_WATCH_PROBE_PAYLOAD,
            "long-running-job",
            "1000000",
            "Source Change Watch",
        ),
        (
            "/v1/monitors/source-change-portfolio",
            PORTFOLIO_WATCH_PROBE_PAYLOAD,
            "portfolio-monitoring",
            "9000000",
            "Source Change Portfolio",
        ),
        (
            "/v1/gtm/form-d-funding-leads",
            FORM_D_PROBE_PAYLOAD,
            "funding-signal",
            "50000",
            "Official Source Evidence",
        ),
        (
            "/v1/ofac/payment-preflight",
            {
                "address": "0x0000000000000000000000000000000000000000",
                "network": "eip155:8453",
            },
            "payment-preflight",
            "10000",
            "Agent Payment Safety Preflight",
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
            "Official Source Evidence",
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
            "Official Source Evidence",
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
            "Agent Payment Safety Evidence",
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
    expected_service,
):
    response = client.post(path, json=body)
    assert response.status_code == 402
    assert response.headers["x-evidence-request-id"].endswith("_fixture")

    challenge = decode_challenge(response)
    assert challenge["x402Version"] == 2
    assert challenge["accepts"][0]["network"] == "eip155:84532"
    assert challenge["accepts"][0]["amount"] == expected_amount
    assert challenge["resource"]["serviceName"] == expected_service
    assert expected_tag in challenge["resource"]["tags"]
    assert challenge["extensions"]["bazaar"]["info"]["input"]["method"] == "POST"
    if path == "/v1/gtm/form-d-funding-leads":
        output = challenge["extensions"]["bazaar"]["info"]["output"]
        assert output["example"]["upgrade"] == {
            "path": "/v1/gtm/form-d-company-dossier",
            "price": "$0.25",
            "requests": [],
        }
        output_schema = challenge["extensions"]["bazaar"]["schema"]["properties"]
        assert "upgrade" in output_schema["output"]["properties"]["example"]["required"]


@pytest.mark.parametrize(
    ("path", "expected_amount"),
    [
        ("/v1/web/source-snapshot", "30000"),
        ("/v1/monitors/source-change", "1000000"),
        ("/v1/monitors/source-change-portfolio", "9000000"),
        ("/v1/gtm/form-d-funding-leads", "50000"),
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


def test_portfolio_probe_does_not_fetch_sources_before_payment(
    client, prepared, monkeypatch
):
    monkeypatch.setattr(
        "autonomous_data_api.app.fetch_public_source",
        lambda _url: pytest.fail("unpaid portfolio probe fetched a source"),
    )

    response = client.post(
        "/v1/monitors/source-change-portfolio",
        json=PORTFOLIO_WATCH_PROBE_PAYLOAD,
    )

    assert response.status_code == 402
    assert decode_challenge(response)["accepts"][0]["amount"] == "9000000"


def test_payment_attempt_captures_privacy_safe_agent_attribution(
    client, prepared, monkeypatch
):
    captured = {}

    def capture_attempt(_prepared, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(evidence_service, "record_attempt", capture_attempt)
    response = client.post(
        "/v1/ofac/payment-preflight",
        headers={
            "Fly-Client-IP": "203.0.113.42",
            "Fly-Region": "IAD",
            "Fly-Request-Id": "01test-iad",
            "User-Agent": "Coinbase-CDP-Test/1.0",
            "Referer": "https://agents.example/discover?q=private",
            "X-Agent-Discovery-Source": "Coinbase-Bazaar",
            "X-Agent-Run-Id": "private-run-123",
        },
    )

    assert response.status_code == 402
    assert captured == {
        "route": "/v1/ofac/payment-preflight",
        "quoted_price": "$0.01",
        "network": "eip155:84532",
        "response_status": "PAYMENT_REQUIRED",
        "latency_ms": captured["latency_ms"],
        "payment_signature": None,
        "settlement_tx_hash": None,
        "payer_wallet": None,
        "client_identifier": "203.0.113.42",
        "user_agent": "Coinbase-CDP-Test/1.0",
        "user_agent_family": "coinbase-cdp",
        "referrer_origin": "https://agents.example",
        "edge_region": "iad",
        "proxy_request_id": "01test-iad",
        "discovery_source": "coinbase-bazaar",
        "agent_run_id": "private-run-123",
        "http_status": 402,
        "payment_failure_stage": None,
        "payment_failure_reason": None,
    }
    assert captured["latency_ms"] >= 0


def test_nonempty_invalid_json_is_still_rejected_before_payment(client):
    response = client.post(
        "/v1/ofac/exact-identifier-evidence",
        content=b"not-json",
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 400
    assert "payment-required" not in response.headers
    assert response.json()["error"]["code"] == "INVALID_JSON"


@pytest.mark.parametrize(
    "path",
    [
        "/v1/gtm/form-d-funding-leads",
        "/v1/ofac/exact-identifier-evidence",
    ],
)
def test_empty_json_object_is_a_marketplace_payment_probe(client, prepared, path):
    response = client.post(path, json={})

    assert response.status_code == 402
    challenge = decode_challenge(response)
    assert challenge["resource"]["url"].endswith(path)


def test_post_payment_marketplace_probe_uses_declared_input(monkeypatch):
    probe_payload = {"code": "print(42)", "timeout_seconds": 5}
    mini_app = FastAPI()

    @mini_app.post("/v1/compute/python-run")
    def challenge(request: Request, payload: dict | None = None):
        del payload
        assert isinstance(request.state.evidence_validated, PythonRunRequest)
        return Response(status_code=402)

    monkeypatch.setattr(
        EvidencePrecomputeMiddleware,
        "POST_PAYMENT_ROUTES",
        {
            "/v1/compute/python-run": (
                PythonRunRequest,
                "$0.03",
                probe_payload,
            )
        },
    )
    mini_app.add_middleware(EvidencePrecomputeMiddleware, service=evidence_service)

    with TestClient(mini_app) as test_client:
        response = test_client.post("/v1/compute/python-run", json={})

    assert response.status_code == 402


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


def test_remote_mcp_lists_free_and_paid_workflow_tools(client):
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "MCP-Protocol-Version": "2025-11-25",
    }
    initialized = client.post(
        "/mcp/",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "pytest", "version": "1.0"},
            },
        },
    )
    assert initialized.status_code == 200
    assert initialized.json()["result"]["serverInfo"]["name"] == (
        "official-source-evidence"
    )

    listed = client.post(
        "/mcp/",
        headers=headers,
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    )
    assert listed.status_code == 200
    names = {tool["name"] for tool in listed.json()["result"]["tools"]}
    assert names == {
        "get_service_status",
        "get_quote",
        "get_example_payment",
        "request_capability",
        "get_source_snapshot_payment",
        "get_form_d_funding_leads_payment",
        "get_payment_preflight_payment",
        "submit_x402_payment",
    }

    quoted = client.post(
        "/mcp/",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "get_quote",
                "arguments": {"product": "payment_preflight"},
            },
        },
    )
    assert quoted.status_code == 200
    structured = quoted.json()["result"]["structuredContent"]
    assert structured["price"] == "$0.01"
    assert structured["account_required"] is False

    example_tool = next(
        tool
        for tool in listed.json()["result"]["tools"]
        if tool["name"] == "get_example_payment"
    )
    assert "known-valid" in example_tool["description"]


def test_remote_mcp_example_payment_reaches_challenge(client, monkeypatch):
    async def challenge(*_args, **_kwargs):
        return httpx.Response(
            402,
            headers={
                "payment-required": "fixture-payment-required",
                "x-evidence-request-id": "fixture-request-id",
                "x-evidence-request-hash": "fixture-request-hash",
            },
            json={"error": "payment required"},
        )

    monkeypatch.setattr(evidence_mcp_service, "_post_paid_route", challenge)
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "MCP-Protocol-Version": "2025-11-25",
        "User-Agent": "pytest-mcp-agent/1.0",
    }
    response = client.post(
        "/mcp/",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "get_example_payment",
                "arguments": {"product": "payment_preflight"},
            },
        },
    )
    assert response.status_code == 200
    structured = response.json()["result"]["structuredContent"]
    assert structured["status"] == "payment_required"
    assert structured["payment_required"] == "fixture-payment-required"
    assert structured["example"] is True
    assert structured["arguments"]["network"] == "eip155:8453"
    with evidence_mcp_service.store._connect() as connection:
        event = connection.execute(
            "SELECT tool_name, http_status FROM mcp_events ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert event["tool_name"] == "get_example_payment"
    assert event["http_status"] == 402


def test_mcp_registry_manifest_is_public(client):
    response = client.get("/server.json")
    assert response.status_code == 200
    assert response.json()["remotes"][0]["url"].endswith("/mcp/")
