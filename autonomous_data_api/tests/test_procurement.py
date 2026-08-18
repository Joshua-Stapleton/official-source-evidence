import asyncio
import hashlib
import json
import sqlite3
from decimal import Decimal
from types import SimpleNamespace

import pytest

from autonomous_data_api.evidence import PreparedResult
from autonomous_data_api.procurement import (
    BLOCKRUN_PAY_TO,
    BLOCKRUN_SUPPLIER,
    SUPPLIER_ASSET,
    TAVILY_SUPPLIER,
    CompanyProfileProcurementRequest,
    ProcurementBrokerError,
    ProcurementBrokerService,
    ProcurementSupplierError,
)


class FakeEvidenceService:
    def __init__(self, db_path):
        self.db_path = db_path
        self.prepare_calls = 0
        self.supplier_records = None

    def prepare_company_profile_procurement(self, request_payload, supplier_records):
        self.prepare_calls += 1
        self.supplier_records = supplier_records
        result = {
            "product": "COMPANY_PROFILE_PROCUREMENT",
            "request": request_payload,
            "suppliers": supplier_records,
            "status": (
                "COMPLETE"
                if all(record["status"] == "FULFILLED" for record in supplier_records)
                else "PARTIAL"
            ),
        }
        request_hash = hashlib.sha256(
            json.dumps(request_payload, sort_keys=True).encode()
        ).hexdigest()
        result_hash = hashlib.sha256(
            json.dumps(result, sort_keys=True).encode()
        ).hexdigest()
        return PreparedResult(
            request_id=f"request-{self.prepare_calls}",
            product="company_profile_procurement",
            request_hash=request_hash,
            source_bundle_hash="source-bundle",
            result_hash=result_hash,
            result=result,
        )


def broker(tmp_path, daily_cap="0.02"):
    evidence = FakeEvidenceService(tmp_path / "evidence.sqlite3")
    service = ProcurementBrokerService(
        evidence,
        private_key="0x" + "11" * 32,
        daily_cap_usd=Decimal(daily_cap),
    )
    return service, evidence


def requirement(
    *,
    amount="10000",
    pay_to="0x1111111111111111111111111111111111111111",
    scheme="exact",
    network="eip155:8453",
    asset=SUPPLIER_ASSET,
):
    return SimpleNamespace(
        scheme=scheme,
        network=network,
        asset=asset,
        amount=amount,
        payTo=pay_to,
    )


def request(company="Example Corporation", domain="example.com"):
    return CompanyProfileProcurementRequest(
        company_name=company,
        domain=domain,
        ticker="exm",
    )


def sources():
    return [
        {
            "title": "Example Corporation",
            "url": "https://example.com/",
            "snippet": "Example Corporation provides business software.",
            "score": 0.95,
        }
    ]


def profile():
    return {
        "company_name": "Example Corporation",
        "domain": "example.com",
        "ticker": "EXM",
        "summary": "A business software company.",
        "industry": "Software",
        "products_services": ["Business software"],
        "headquarters": None,
        "field_confidence": {"summary": 0.9, "industry": 0.8},
        "contradictions": [],
        "source_urls": ["https://example.com/"],
    }


def install_successful_supplier_mocks(service, counters=None):
    counters = counters if counters is not None else {"tavily": 0, "blockrun": 0}

    async def fake_tavily(_request):
        counters["tavily"] += 1
        return (
            {
                "sources": sources(),
                "source_response_sha256": "sha256:" + "a" * 64,
            },
            "0xtavily",
            10_000,
        )

    async def fake_blockrun(_request, source_records):
        counters["blockrun"] += 1
        assert source_records == sources()
        return profile(), "0xblockrun", 2_000

    service._call_tavily = fake_tavily
    service._call_blockrun = fake_blockrun
    return counters


def test_tavily_accepts_valid_request_scoped_recipient_but_pins_other_terms():
    tavily_valid = requirement()
    assert (
        ProcurementBrokerService._select_tavily_payment_requirement(2, [tavily_valid])
        is tavily_valid
    )

    rotated = requirement(pay_to="0x2222222222222222222222222222222222222222")
    assert (
        ProcurementBrokerService._select_tavily_payment_requirement(2, [rotated])
        is rotated
    )

    for kwargs in [
        {"amount": "10001"},
        {"pay_to": "0x0000000000000000000000000000000000000000"},
        {"pay_to": "not-an-address"},
        {"scheme": "upto"},
        {"network": "eip155:1"},
        {"asset": "0x0000000000000000000000000000000000000000"},
    ]:
        with pytest.raises(
            ProcurementBrokerError, match="offered no allowed Base USDC exact payment"
        ):
            ProcurementBrokerService._select_tavily_payment_requirement(
                2, [requirement(**kwargs)]
            )


def test_blockrun_pins_exact_recipient_and_x402_version():
    blockrun_valid = requirement(amount="2000", pay_to=BLOCKRUN_PAY_TO)
    assert (
        ProcurementBrokerService._select_blockrun_payment_requirement(
            2, [blockrun_valid]
        )
        is blockrun_valid
    )

    with pytest.raises(
        ProcurementBrokerError, match="offered no allowed Base USDC exact payment"
    ):
        ProcurementBrokerService._select_blockrun_payment_requirement(
            2,
            [
                requirement(
                    amount="2000",
                    pay_to="0x2222222222222222222222222222222222222222",
                )
            ],
        )
    with pytest.raises(ProcurementBrokerError):
        ProcurementBrokerService._select_blockrun_payment_requirement(
            1, [blockrun_valid]
        )


@pytest.mark.parametrize(
    "domain",
    [
        "localhost",
        "service.internal",
        "127.0.0.1",
        "https://example.com",
        "user@example.com",
        "example.com:443",
        "example.com/path",
        "singlelabel",
    ],
)
def test_request_rejects_non_public_or_non_bare_domains(domain):
    with pytest.raises(ValueError):
        CompanyProfileProcurementRequest(
            company_name="Example Corporation", domain=domain
        )


def test_request_normalizes_fields():
    normalized = CompanyProfileProcurementRequest(
        company_name="  Example\t Corporation  ",
        domain="BUECHER.DE",
        ticker=" exm ",
    )
    assert normalized.company_name == "Example Corporation"
    assert normalized.domain == "buecher.de"
    assert normalized.ticker == "EXM"


def test_daily_cap_is_atomic_across_separate_supplier_reservations(tmp_path):
    service, _evidence = broker(tmp_path, daily_cap="0.01")
    service._reserve_supplier("payment-one", "request-one", TAVILY_SUPPLIER)

    with pytest.raises(ProcurementBrokerError) as captured:
        service._reserve_supplier("payment-two", "request-two", BLOCKRUN_SUPPLIER)

    assert captured.value.code == "PROCUREMENT_DAILY_CAP_REACHED"
    with sqlite3.connect(service.db_path) as connection:
        rows = connection.execute(
            "SELECT supplier, status, amount_atomic FROM procurement_supplier_purchases"
        ).fetchall()
    assert rows == [("tavily", "RESERVED", 10_000)]


def test_completed_replay_does_not_buy_suppliers_twice(tmp_path):
    service, evidence = broker(tmp_path)
    counters = install_successful_supplier_mocks(service)

    async def run_scenario():
        first_result = await service.build(request(), "verified-payment-proof")
        replay_result = await service.build(request(), "verified-payment-proof")
        return first_result, replay_result

    (first, first_cost), (replay, replay_cost) = asyncio.run(run_scenario())

    assert replay == first
    assert first_cost == Decimal("0.012")
    assert replay_cost == Decimal(0)
    assert counters == {"tavily": 1, "blockrun": 1}
    assert evidence.prepare_calls == 1


def test_payment_replay_with_different_request_is_409(tmp_path):
    service, _evidence = broker(tmp_path)
    install_successful_supplier_mocks(service)

    async def run_scenario():
        await service.build(request(), "verified-payment-proof")
        return await service.build(
            request(company="Another Corporation", domain="another.example.com"),
            "verified-payment-proof",
        )

    with pytest.raises(ProcurementBrokerError) as captured:
        asyncio.run(run_scenario())

    assert captured.value.status_code == 409
    assert captured.value.code == "PROCUREMENT_PAYMENT_REQUEST_MISMATCH"


def test_in_progress_replay_is_409(tmp_path):
    service, _evidence = broker(tmp_path)
    payload = request().model_dump(mode="json")
    request_hash = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    service._begin_or_replay("verified-payment-proof", request_hash)

    with pytest.raises(ProcurementBrokerError) as captured:
        service._begin_or_replay("verified-payment-proof", request_hash)

    assert captured.value.status_code == 409
    assert captured.value.code == "PROCUREMENT_IN_PROGRESS"


def test_blockrun_failure_returns_bounded_tavily_partial(tmp_path):
    service, evidence = broker(tmp_path)

    async def fake_tavily(_request):
        return (
            {
                "sources": sources(),
                "source_response_sha256": "sha256:" + "a" * 64,
            },
            "0xtavily",
            10_000,
        )

    async def failed_blockrun(_request, _sources):
        raise ProcurementSupplierError(
            "BLOCKRUN_RESPONSE_INVALID",
            "BlockRun returned no valid company profile",
            amount_atomic=2_000,
        )

    service._call_tavily = fake_tavily
    service._call_blockrun = failed_blockrun

    prepared, cost = asyncio.run(service.build(request(), "verified-payment-proof"))

    assert prepared.result["status"] == "PARTIAL"
    assert cost == Decimal("0.012")
    assert evidence.supplier_records[0]["payload"]["sources"] == sources()
    assert "results" not in evidence.supplier_records[0]
    assert evidence.supplier_records[1] == {
        "supplier": "blockrun",
        "endpoint": BLOCKRUN_SUPPLIER.url,
        "status": "UNKNOWN",
        "error_code": "BLOCKRUN_RESPONSE_INVALID",
    }


def test_tavily_failure_fails_without_calling_blockrun(tmp_path):
    service, evidence = broker(tmp_path)
    blockrun_calls = 0

    async def failed_tavily(_request):
        raise ProcurementSupplierError(
            "TAVILY_RESPONSE_INVALID", "Tavily returned no usable source records"
        )

    async def forbidden_blockrun(_request, _sources):
        nonlocal blockrun_calls
        blockrun_calls += 1
        raise AssertionError("BlockRun must not run without Tavily sources")

    service._call_tavily = failed_tavily
    service._call_blockrun = forbidden_blockrun

    with pytest.raises(ProcurementBrokerError) as captured:
        asyncio.run(service.build(request(), "verified-payment-proof"))

    assert captured.value.code == "PROCUREMENT_SOURCE_SUPPLIER_FAILED"
    assert captured.value.direct_cost_usd == Decimal(0)
    assert blockrun_calls == 0
    assert evidence.prepare_calls == 0


def test_blockrun_profile_discards_unsupplied_source_references():
    payload = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            **profile(),
                            "source_urls": ["https://untrusted.example.net/"],
                        }
                    )
                }
            }
        ]
    }

    normalized = ProcurementBrokerService._derive_blockrun_profile(
        payload, {"https://example.com/"}, request()
    )

    assert normalized["source_urls"] == ["https://example.com/"]


def test_blockrun_request_uses_verified_non_reasoning_json_mode():
    payload = ProcurementBrokerService._blockrun_payload(request(), sources())

    assert payload["model"] == "deepseek/deepseek-chat"
    assert "reasoning_effort" not in payload
    assert payload["response_format"] == {"type": "json_object"}
    assert '"company_name"' in payload["messages"][0]["content"]
    assert payload["max_tokens"] == 800


def test_blockrun_profile_accepts_fenced_json_and_safe_optional_defaults():
    payload = {
        "choices": [
            {
                "message": {
                    "content": "```json\n"
                    + json.dumps(
                        {
                            "company_name": "Example Corporation",
                            "domain": "example.com",
                            "summary": "A business software company.",
                        }
                    )
                    + "\n```"
                }
            }
        ]
    }

    normalized = ProcurementBrokerService._derive_blockrun_profile(
        payload, {"https://example.com/"}, request()
    )

    assert normalized["ticker"] == "EXM"
    assert normalized["products_services"] == []
    assert normalized["field_confidence"] == {}
    assert normalized["source_urls"] == ["https://example.com/"]


def test_blockrun_profile_rejects_different_company_domain():
    payload = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {**profile(), "domain": "different.example.com"}
                    )
                }
            }
        ]
    }

    with pytest.raises(ProcurementSupplierError) as captured:
        ProcurementBrokerService._derive_blockrun_profile(
            payload, {"https://example.com/"}, request()
        )

    assert captured.value.code == "BLOCKRUN_COMPANY_MISMATCH"


def test_blockrun_profile_sanitizes_optional_supplier_shape():
    shaped = {
        **profile(),
        "company_name": "Supplier-controlled name",
        "ticker": "WRONG",
        "products_services": ["  Business software  ", {"bad": "shape"}],
        "field_confidence": {
            "summary": "0.8",
            "bad key": 1,
            "industry": 4,
        },
        "contradictions": [
            {
                "field": " industry ",
                "description": " Conflicting descriptions. ",
                "source_urls": [
                    "https://example.com",
                    "https://not-allowed.example/",
                ],
                "ignored": True,
            }
        ],
        "source_urls": [
            "https://example.com",
            "https://not-allowed.example/",
        ],
        "unexpected": "ignored",
    }
    payload = {
        "choices": [{"message": {"content": json.dumps(shaped)}}],
    }

    normalized = ProcurementBrokerService._derive_blockrun_profile(
        payload, {"https://example.com/"}, request()
    )

    assert normalized["company_name"] == "Example Corporation"
    assert normalized["ticker"] == "EXM"
    assert normalized["products_services"] == ["Business software"]
    assert normalized["field_confidence"] == {"summary": 0.8}
    assert normalized["source_urls"] == ["https://example.com/"]
    assert normalized["contradictions"][0]["source_urls"] == ["https://example.com/"]
