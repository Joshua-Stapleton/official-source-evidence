import base64
import json

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from autonomous_data_api.evidence import (
    EvidenceService,
    OfacExactRequest,
    SecDeltaRequest,
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
