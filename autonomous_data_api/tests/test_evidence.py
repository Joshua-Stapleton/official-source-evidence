import base64
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from autonomous_data_api.evidence import (
    EvidenceService,
    FormDFundingLeadsRequest,
    OfacExactRequest,
    OfacPreflightRequest,
    PreparedResult,
    PublicSourceSnapshotRequest,
    SecDeltaRequest,
    SecSignalRequest,
    SourceSnapshot,
    sha256_bytes,
)

OFAC_FIXTURE = b"""<?xml version="1.0"?>
<sdnList xmlns="https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/XML">
  <publshInformation>
    <Publish_Date>07/30/2026</Publish_Date>
    <Record_Count>1</Record_Count>
  </publshInformation>
  <sdnEntry>
    <uid>123</uid>
    <firstName>Example</firstName>
    <lastName>Party</lastName>
    <sdnType>Individual</sdnType>
    <programList><program>TEST</program></programList>
    <akaList>
      <aka><uid>124</uid><firstName>Alias</firstName><lastName>Party</lastName></aka>
    </akaList>
    <idList>
      <id>
        <uid>125</uid>
        <idType>Digital Currency Address - ETH</idType>
        <idNumber>0x098B716B8Aaf21512996dC57EB0615e2383E2f96</idNumber>
      </id>
    </idList>
  </sdnEntry>
</sdnList>
"""


def service(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "AUTONOMOUS_RECEIPT_SIGNING_KEY",
        base64.urlsafe_b64encode(b"s" * 32).decode().rstrip("="),
    )
    monkeypatch.setenv("AUTONOMOUS_ANALYTICS_HMAC_KEY", "evidence-test")
    return EvidenceService(tmp_path / "evidence.sqlite3")


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
def test_public_source_snapshot_request_rejects_unsafe_urls(url):
    with pytest.raises(ValueError):
        PublicSourceSnapshotRequest(url=url)


def test_public_source_snapshot_is_bounded_searchable_and_signed(tmp_path, monkeypatch):
    from autonomous_data_api import monitors

    evidence = service(tmp_path, monkeypatch)
    normalized = "Alpha filing notice. Enforcement action one. Enforcement action two."
    monkeypatch.setattr(
        monitors,
        "fetch_public_source",
        lambda _url: (200, "text/html", normalized),
    )

    prepared = evidence.prepare_public_source_snapshot(
        PublicSourceSnapshotRequest(
            url="https://example.com/notices",
            query="enforcement action",
            max_characters=1000,
        )
    )

    assert prepared.result["product"] == "PUBLIC_SOURCE_SNAPSHOT"
    assert prepared.result["content"]["normalized_text"] == normalized
    assert prepared.result["content"]["truncated"] is False
    assert prepared.result["query"]["literal_match_count_returned"] == 2
    assert prepared.result["upgrade"] == {
        "path": "/v1/monitors/source-change",
        "price": "$1.00",
        "duration_days": 30,
        "purpose": "Detect and deliver future changes to this source.",
    }
    assert prepared.result["receipt"]["algorithm"] == "Ed25519"
    assert prepared.result["provenance"]["result_sha256"] == (
        f"sha256:{prepared.result_hash}"
    )


def test_public_source_snapshot_quote_does_not_fetch(tmp_path, monkeypatch):
    from autonomous_data_api import monitors

    evidence = service(tmp_path, monkeypatch)
    monkeypatch.setattr(
        monitors,
        "fetch_public_source",
        lambda _url: (_ for _ in ()).throw(AssertionError("unexpected fetch")),
    )

    prepared = evidence.prepare_public_source_snapshot_quote(
        PublicSourceSnapshotRequest(url="https://example.com/notices")
    )

    assert prepared.result["status"] == "PAYMENT_REQUIRED"
    assert "receipt" not in prepared.result


def test_ofac_exact_match_is_deterministic_and_signed(tmp_path, monkeypatch):
    evidence = service(tmp_path, monkeypatch)
    digest = sha256_bytes(OFAC_FIXTURE)
    snapshot = evidence.import_ofac_file(
        "SDN",
        OFAC_FIXTURE,
        source_version=digest,
        official_digest_sha256=digest,
    )
    assert snapshot.official_digest_verified is True

    request = OfacExactRequest(
        identifier_type="crypto_address",
        identifier="0x098b716b8aaf21512996dc57eb0615e2383e2f96",
        networks=["eip155:1"],
        lists=["SDN"],
    )
    first = evidence.prepare_ofac(request)
    second = evidence.prepare_ofac(request)
    assert first == second
    assert first.result["match_status"] == "EXACT_MATCH"
    assert first.result["matches"][0]["entry_uid"] == "123"
    assert first.result["provenance"]["result_sha256"] == f"sha256:{first.result_hash}"
    assert (
        first.result["receipt"]["signed_payload_sha256"]
        == f"sha256:{first.result_hash}"
    )

    receipt = first.result["receipt"]
    public_key = base64.urlsafe_b64decode(
        receipt["public_key_base64url"]
        + "=" * (-len(receipt["public_key_base64url"]) % 4)
    )
    signature = base64.urlsafe_b64decode(
        receipt["signature_base64url"]
        + "=" * (-len(receipt["signature_base64url"]) % 4)
    )
    Ed25519PublicKey.from_public_bytes(public_key).verify(
        signature,
        first.result_hash.encode("ascii"),
    )

    assert evidence.bind_payment(
        "payment-proof", first, "/v1/ofac/exact-identifier-evidence"
    )
    assert evidence.bind_payment(
        "payment-proof", first, "/v1/ofac/exact-identifier-evidence"
    )
    other = evidence.prepare_ofac(
        OfacExactRequest(
            identifier_type="ofac_uid",
            identifier="123",
            lists=["SDN"],
        )
    )
    assert not evidence.bind_payment(
        "payment-proof",
        other,
        "/v1/ofac/exact-identifier-evidence",
    )


def test_ofac_no_match_never_claims_clearance(tmp_path, monkeypatch):
    evidence = service(tmp_path, monkeypatch)
    evidence.import_ofac_file("SDN", OFAC_FIXTURE)
    prepared = evidence.prepare_ofac(
        OfacExactRequest(
            identifier_type="crypto_address",
            identifier="0x0000000000000000000000000000000000000000",
            lists=["SDN"],
        )
    )
    serialized = json.dumps(prepared.result).lower()
    assert prepared.result["match_status"] == "NO_EXACT_MATCH"
    assert "not sanctions clearance" in serialized
    assert '"cleared"' not in serialized


def test_ofac_preflight_is_compact_unsigned_and_never_claims_clearance(
    tmp_path, monkeypatch
):
    evidence = service(tmp_path, monkeypatch)
    evidence.import_ofac_file("SDN", OFAC_FIXTURE)
    evidence.import_ofac_file("CONSOLIDATED", OFAC_FIXTURE)

    prepared = evidence.prepare_ofac_preflight(
        OfacPreflightRequest(
            address="0x0000000000000000000000000000000000000000",
            network="eip155:8453",
        )
    )

    assert prepared.product == "ofac_preflight"
    assert prepared.result["decision"] == "NO_EXACT_OFAC_MATCH_FOUND"
    assert prepared.result["match_count"] == 0
    assert "receipt" not in prepared.result
    assert prepared.result["premium_evidence_path"] == (
        "/v1/ofac/exact-identifier-evidence"
    )
    serialized = json.dumps(prepared.result).lower()
    assert "not sanctions clearance" in serialized
    assert '"cleared"' not in serialized


def make_snapshot(source_id, content):
    return SourceSnapshot(
        source_id=source_id,
        source_version=sha256_bytes(content),
        content_sha256=sha256_bytes(content),
        retrieved_at="2026-08-01T12:00:00+00:00",
        verified_at="2026-08-01T12:00:00+00:00",
        published_at=None,
        http_last_modified=None,
        official_digest_sha256=None,
        official_digest_verified=False,
        content=content,
    )


def test_sec_delta_uses_accession_document_hash_and_xbrl_delta(tmp_path, monkeypatch):
    evidence = service(tmp_path, monkeypatch)
    current_accession = "0000320193-26-000002"
    baseline_accession = "0000320193-26-000001"
    submissions = {
        "name": "Example Issuer",
        "tickers": ["EXM"],
        "filings": {
            "recent": {
                "accessionNumber": [current_accession, baseline_accession],
                "form": ["8-K", "8-K"],
                "filingDate": ["2026-08-01", "2026-07-01"],
                "acceptanceDateTime": ["20260801114211", "20260701114211"],
                "reportDate": ["2026-06-30", "2026-03-31"],
                "primaryDocument": ["current.htm", "baseline.htm"],
                "items": ["2.02,9.01", "2.02,9.01"],
            },
            "files": [],
        },
    }
    facts = {
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "units": {
                        "USD": [
                            {
                                "accn": baseline_accession,
                                "form": "8-K",
                                "filed": "2026-07-01",
                                "end": "2026-03-31",
                                "val": 100,
                            },
                            {
                                "accn": current_accession,
                                "form": "8-K",
                                "filed": "2026-08-01",
                                "end": "2026-06-30",
                                "val": 115,
                            },
                        ]
                    }
                }
            }
        }
    }
    submissions_bytes = json.dumps(submissions).encode()
    facts_bytes = json.dumps(facts).encode()
    document_bytes = b"<html>official filing fixture</html>"

    def fake_fetch(source_id, url, max_age_seconds):
        del max_age_seconds
        if "companyfacts" in url:
            return make_snapshot(source_id, facts_bytes)
        if url.endswith("current.htm"):
            return make_snapshot(source_id, document_bytes)
        return make_snapshot(source_id, submissions_bytes)

    monkeypatch.setattr(evidence, "_fetch_sec_source", fake_fetch)
    request = SecDeltaRequest(
        cik="320193",
        since_accession=baseline_accession,
        forms=["8-K"],
        rules=["FORM:8-K:ITEM:2.02", "XBRL:us-gaap:Revenues"],
    )
    first = evidence.prepare_sec(request)
    second = evidence.prepare_sec(request)

    assert first == second
    assert first.result["decision"] == "NEW_FILING"
    filing = first.result["filings"][0]
    assert filing["accession"] == current_accession
    assert filing["document_sha256"] == f"sha256:{sha256_bytes(document_bytes)}"
    assert filing["matched_rules"] == ["FORM:8-K:ITEM:2.02"]
    delta = first.result["selected_fact_deltas"][0]
    assert delta["previous_value"] == 100
    assert delta["current_value"] == 115
    assert first.result["provenance"]["result_sha256"] == f"sha256:{first.result_hash}"


def test_sec_signal_resolves_ticker_and_uses_timestamp_without_receipt(
    tmp_path, monkeypatch
):
    evidence = service(tmp_path, monkeypatch)
    ticker_map = {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple"}}
    submissions = {
        "name": "Apple Inc.",
        "tickers": ["AAPL"],
        "filings": {
            "recent": {
                "accessionNumber": ["0000320193-26-000002", "0000320193-26-000001"],
                "form": ["8-K", "10-Q"],
                "filingDate": ["2026-08-01", "2026-07-01"],
                "acceptanceDateTime": ["20260801114211", "20260701114211"],
                "primaryDocument": ["current.htm", "older.htm"],
            },
            "files": [],
        },
    }

    def fake_fetch(source_id, url, max_age_seconds):
        del max_age_seconds
        content = (
            json.dumps(ticker_map).encode()
            if "company_tickers" in url
            else json.dumps(submissions).encode()
        )
        return make_snapshot(source_id, content)

    monkeypatch.setattr(evidence, "_fetch_sec_source", fake_fetch)
    prepared = evidence.prepare_sec_signal(
        SecSignalRequest(
            ticker="aapl",
            since="2026-07-15T00:00:00Z",
            forms=["8-K", "10-Q"],
        )
    )

    assert prepared.product == "sec_signal"
    assert prepared.result["decision"] == "NEW_RELEVANT_FILING"
    assert prepared.result["issuer"]["cik"] == "0000320193"
    assert prepared.result["filing_count"] == 1
    assert prepared.result["filings"][0]["accession"] == "0000320193-26-000002"
    assert prepared.result["premium_evidence_path"] == "/v1/sec/filing-trigger-delta"
    assert "receipt" not in prepared.result


def test_sec_premium_accepts_ticker_and_timestamp(tmp_path, monkeypatch):
    evidence = service(tmp_path, monkeypatch)
    ticker_map = {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple"}}
    submissions = {
        "name": "Apple Inc.",
        "tickers": ["AAPL"],
        "filings": {
            "recent": {
                "accessionNumber": ["0000320193-26-000002"],
                "form": ["8-K"],
                "filingDate": ["2026-08-01"],
                "acceptanceDateTime": ["20260801114211"],
                "reportDate": ["2026-06-30"],
                "primaryDocument": ["current.htm"],
                "items": ["2.02"],
            },
            "files": [],
        },
    }

    def fake_fetch(source_id, url, max_age_seconds):
        del max_age_seconds
        if "company_tickers" in url:
            return make_snapshot(source_id, json.dumps(ticker_map).encode())
        if url.endswith("current.htm"):
            return make_snapshot(source_id, b"<html>filing</html>")
        return make_snapshot(source_id, json.dumps(submissions).encode())

    monkeypatch.setattr(evidence, "_fetch_sec_source", fake_fetch)
    prepared = evidence.prepare_sec(
        SecDeltaRequest(
            ticker="AAPL",
            since="2026-07-15T00:00:00Z",
            forms=["8-K"],
            rules=["FORM:8-K:ITEM:2.02"],
        )
    )

    assert prepared.result["baseline"] == {
        "type": "timestamp",
        "value": "2026-07-15T00:00:00+00:00",
    }
    assert prepared.result["issuer"]["cik"] == "0000320193"
    assert prepared.result["filings"][0]["matched_rules"] == ["FORM:8-K:ITEM:2.02"]
    assert "receipt" in prepared.result


def test_form_d_funding_leads_filters_paginates_and_preserves_source_basis(
    tmp_path, monkeypatch
):
    evidence = service(tmp_path, monkeypatch)
    now = datetime.now(timezone.utc)
    filed = (now - timedelta(days=1)).date()
    while filed.weekday() >= 5:
        filed -= timedelta(days=1)
    since = datetime.combine(
        filed - timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc
    )
    cik = "0001050743"
    accessions = ["0001050743-26-000002", "0001050743-26-000001"]
    index_content = (
        "CIK|Company Name|Form Type|Date Filed|File Name\n"
        + "\n".join(
            f"{cik}|Example Issuer {index}|D|{filed:%Y%m%d}|"
            f"edgar/data/{int(cik)}/{accession}.txt"
            for index, accession in enumerate(accessions, start=1)
        )
    ).encode()
    empty_index = b"CIK|Company Name|Form Type|Date Filed|File Name\n"

    def filing_content(accession, amount_sold, company_name):
        return f"""<SEC-DOCUMENT>{accession}.txt : {filed:%Y%m%d}
<SEC-HEADER><ACCEPTANCE-DATETIME>{filed:%Y%m%d}120000</SEC-HEADER>
<DOCUMENT><TYPE>D<FILENAME>primary_doc.xml<TEXT><XML>
<edgarSubmission>
  <submissionType>D</submissionType><testOrLive>LIVE</testOrLive>
  <primaryIssuer><cik>{cik}</cik><entityName>{company_name}</entityName>
    <issuerAddress><city>Bedminster</city><stateOrCountry>NJ</stateOrCountry><zipCode>07921</zipCode></issuerAddress>
    <jurisdictionOfInc>NEW JERSEY</jurisdictionOfInc><entityType>Corporation</entityType>
  </primaryIssuer>
  <relatedPersonsList><relatedPersonInfo><relatedPersonName><firstName>Alex</firstName><lastName>Example</lastName></relatedPersonName><relatedPersonRelationshipList><relationship>Executive Officer</relationship></relatedPersonRelationshipList></relatedPersonInfo></relatedPersonsList>
  <offeringData><industryGroup><industryGroupType>Commercial Banking</industryGroupType></industryGroup>
    <typeOfFiling><newOrAmendment><isAmendment>false</isAmendment></newOrAmendment><dateOfFirstSale><value>{filed.isoformat()}</value></dateOfFirstSale></typeOfFiling>
    <typesOfSecuritiesOffered><isEquityType>true</isEquityType></typesOfSecuritiesOffered>
    <minimumInvestmentAccepted>1000</minimumInvestmentAccepted>
    <offeringSalesAmounts><totalOfferingAmount>10000000</totalOfferingAmount><totalAmountSold>{amount_sold}</totalAmountSold><totalRemaining>5000000</totalRemaining></offeringSalesAmounts>
    <investors><totalNumberAlreadyInvested>3</totalNumberAlreadyInvested></investors>
  </offeringData>
</edgarSubmission>
</XML></TEXT></DOCUMENT></SEC-DOCUMENT>""".encode()

    documents = {
        accessions[0]: filing_content(accessions[0], "5000000", "Example One"),
        accessions[1]: filing_content(accessions[1], "2000000", "Example Two"),
    }

    def fake_fetch(source_id, url, max_age_seconds):
        del max_age_seconds
        if url.endswith(".idx"):
            content = index_content if f"{filed:%Y%m%d}" in url else empty_index
        else:
            content = next(
                document
                for accession, document in documents.items()
                if accession in url
            )
        return make_snapshot(source_id, content)

    monkeypatch.setattr(evidence, "_fetch_sec_source", fake_fetch)
    first_request = FormDFundingLeadsRequest(
        since=since.isoformat(),
        states=["nj"],
        industry_keywords=["bank"],
        minimum_amount_sold_usd="1000000",
        limit=1,
    )
    first = evidence.prepare_form_d_funding_leads(first_request)

    assert first.product == "form_d_funding_leads"
    assert first.result["decision"] == "FORM_D_FUNDING_SIGNALS_FOUND"
    assert first.result["lead_count"] == 1
    assert first.result["leads"][0]["issuer"]["name"] == "Example One"
    assert first.result["leads"][0]["funding_signal"]["amount_sold_usd"] == ("5000000")
    assert first.result["leads"][0]["funding_signal"]["date_of_first_sale"] == (
        filed.isoformat()
    )
    assert first.result["leads"][0]["related_people"] == [
        {"name": "Alex Example", "roles": ["Executive Officer"]}
    ]
    assert first.result["pagination"]["has_more"] is True
    assert first.result["pagination"]["next_cursor"] == accessions[0]
    assert "receipt" in first.result
    assert "not proof" in " ".join(first.result["limitations"]).lower()

    second = evidence.prepare_form_d_funding_leads(
        FormDFundingLeadsRequest(
            **{
                **first_request.model_dump(),
                "cursor": first.result["pagination"]["next_cursor"],
            }
        )
    )
    assert second.result["lead_count"] == 1
    assert second.result["leads"][0]["issuer"]["name"] == "Example Two"
    assert second.result["pagination"]["has_more"] is False
    assert second.result["pagination"]["next_cursor"] is None


def test_conversion_experiment_excludes_probes_and_owner_payments(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("AUTONOMOUS_X402_NETWORK", "eip155:8453")
    evidence = service(tmp_path, monkeypatch)
    rows = [
        (
            "2026-08-04T10:00:00+00:00",
            "/v1/ofac/payment-preflight",
            "$0.01",
            None,
            "NON_OWNER_UNVERIFIED",
            "PAYMENT_REQUIRED",
        ),
        (
            "2026-08-04T10:01:00+00:00",
            "/v1/ofac/payment-preflight",
            "$0.01",
            "buyer-a",
            "NON_OWNER_UNVERIFIED",
            "FULFILLED",
        ),
        (
            "2026-08-05T10:01:00+00:00",
            "/v1/ofac/payment-preflight",
            "$0.01",
            "buyer-a",
            "NON_OWNER_UNVERIFIED",
            "FULFILLED",
        ),
        (
            "2026-08-04T10:02:00+00:00",
            "/v1/sec/filing-change-signal",
            "$0.01",
            "buyer-b",
            "NON_OWNER_UNVERIFIED",
            "FULFILLED",
        ),
        (
            "2026-08-04T10:03:00+00:00",
            "/v1/sec/filing-change-signal",
            "$0.01",
            "buyer-c",
            "NON_OWNER_UNVERIFIED",
            "PAYMENT_OR_SETTLEMENT_FAILED",
        ),
        (
            "2026-08-04T10:04:00+00:00",
            "/v1/sec/filing-trigger-delta",
            "$0.10",
            "owner",
            "OWNER",
            "FULFILLED",
        ),
        (
            "2026-08-10T10:00:00+00:00",
            "/v1/monitors/source-change",
            "$1.00",
            "buyer-d",
            "NON_OWNER_UNVERIFIED",
            "FULFILLED",
        ),
    ]
    with evidence._connect() as connection:
        for index, row in enumerate(rows):
            timestamp, route, price, payer, owner_flag, response_status = row
            connection.execute(
                """
                INSERT INTO evidence_attempts (
                    request_id, timestamp_utc, route, canonical_request_hash,
                    response_hash, source_bundle_hash, quoted_price, network,
                    payer_wallet_hmac, owner_or_test_flag, response_status,
                    latency_ms, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'eip155:8453', ?, ?, ?, 10, ?)
                """,
                (
                    f"request-{index}",
                    timestamp,
                    route,
                    "request-hash",
                    "response-hash",
                    "source-hash",
                    price,
                    payer,
                    owner_flag,
                    response_status,
                    timestamp,
                ),
            )
        connection.commit()

    status = evidence.experiment_status("2026-08-04T09:13:21Z")
    experiment = status["conversion_experiment"]

    assert experiment["independent_buyer_clusters"] == 3
    assert experiment["repeat_independent_buyer_clusters"] == 1
    assert experiment["independent_fulfilled_calls"] == 4
    assert experiment["independent_revenue_usd"] == "1.03"
    assert experiment["independent_paid_fulfillment_rate_percent"] == 80.0
    assert experiment["max_independent_buyer_call_share"] == 0.5
    assert experiment["gates"]["no_buyer_above_50_percent_of_calls"] is True
    source_watch = next(
        route
        for route in experiment["routes"]
        if route["route"] == "/v1/monitors/source-change"
    )
    assert source_watch["tier"] == "long-running-job"
    assert source_watch["independent_fulfilled_calls"] == 1
    assert source_watch["independent_revenue_usd"] == "1.00"
    form_d = next(
        route
        for route in experiment["routes"]
        if route["route"] == "/v1/gtm/form-d-funding-leads"
    )
    assert form_d["price_usd"] == "0.05"
    assert form_d["independent_fulfilled_calls"] == 0
    preflight = next(
        route
        for route in experiment["routes"]
        if route["route"] == "/v1/ofac/payment-preflight"
    )
    assert preflight["payment_challenges"] == 1
    assert preflight["independent_fulfilled_calls"] == 2
    assert preflight["repeat_independent_buyer_clusters"] == 1
    sec_signal = next(
        route
        for route in experiment["routes"]
        if route["route"] == "/v1/sec/filing-change-signal"
    )
    assert sec_signal["independent_paid_or_settlement_failures"] == 1
    assert sec_signal["independent_paid_fulfillment_rate_percent"] == 50.0


def test_attempt_attribution_is_migrated_hashed_and_safely_aggregated(
    tmp_path, monkeypatch
):
    db_path = tmp_path / "evidence.sqlite3"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE evidence_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id TEXT NOT NULL,
                timestamp_utc TEXT NOT NULL,
                route TEXT NOT NULL,
                canonical_request_hash TEXT NOT NULL,
                response_hash TEXT NOT NULL,
                source_bundle_hash TEXT NOT NULL,
                quoted_price TEXT NOT NULL,
                network TEXT NOT NULL,
                payment_identifier TEXT,
                settlement_tx_hash TEXT,
                payer_wallet_hmac TEXT,
                owner_or_test_flag TEXT NOT NULL DEFAULT 'UNKNOWN',
                response_status TEXT NOT NULL,
                latency_ms INTEGER NOT NULL,
                direct_cost_estimate REAL NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
            """
        )

    monkeypatch.setenv("AUTONOMOUS_X402_NETWORK", "eip155:8453")
    evidence = service(tmp_path, monkeypatch)
    columns = {
        row["name"]
        for row in evidence._connect()
        .execute("PRAGMA table_info(evidence_attempts)")
        .fetchall()
    }
    assert {
        "client_hmac",
        "user_agent_hmac",
        "user_agent_family",
        "discovery_source",
        "agent_run_id_hmac",
        "request_fingerprint_hmac",
        "http_status",
    }.issubset(columns)

    prepared = PreparedResult(
        request_id="attribution_fixture",
        product="ofac_preflight",
        request_hash="1" * 64,
        source_bundle_hash="2" * 64,
        result_hash="3" * 64,
        result={},
    )
    evidence.record_attempt(
        prepared,
        route="/v1/ofac/payment-preflight",
        quoted_price="$0.01",
        network="eip155:8453",
        response_status="FULFILLED",
        latency_ms=123,
        payer_wallet="0x1111111111111111111111111111111111111111",
        client_identifier="203.0.113.42",
        user_agent="Coinbase-CDP-Test/1.0",
        user_agent_family="coinbase-cdp",
        referrer_origin="https://api.cdp.coinbase.com",
        edge_region="iad",
        proxy_request_id="request-123-iad",
        discovery_source="coinbase-bazaar",
        agent_run_id="private-run-123",
        http_status=200,
    )

    with evidence._connect() as connection:
        row = connection.execute(
            "SELECT * FROM evidence_attempts WHERE request_id = ?",
            (prepared.request_id,),
        ).fetchone()
    assert row["client_hmac"] != "203.0.113.42"
    assert row["user_agent_hmac"] != "Coinbase-CDP-Test/1.0"
    assert row["agent_run_id_hmac"] != "private-run-123"
    assert len(row["client_hmac"]) == 64
    assert len(row["user_agent_hmac"]) == 64
    assert len(row["agent_run_id_hmac"]) == 64
    assert len(row["request_fingerprint_hmac"]) == 64
    assert row["user_agent_family"] == "coinbase-cdp"
    assert row["referrer_origin"] == "https://api.cdp.coinbase.com"
    assert row["discovery_source"] == "coinbase-bazaar"
    assert row["http_status"] == 200

    attribution = evidence.experiment_status()["attribution"]
    assert attribution["client_fingerprinted_attempts"] == 1
    assert attribution["fingerprinted_fulfilled_attempts"] == 1
    assert attribution["user_agent_families"] == [
        {
            "user_agent_family": "coinbase-cdp",
            "attempts": 1,
            "fulfilled_attempts": 1,
        }
    ]
    assert attribution["declared_discovery_sources"] == [
        {
            "discovery_source": "coinbase-bazaar",
            "attempts": 1,
            "fulfilled_attempts": 1,
        }
    ]
