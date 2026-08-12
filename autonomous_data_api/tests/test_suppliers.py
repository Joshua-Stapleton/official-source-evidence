import base64
from decimal import Decimal
from types import SimpleNamespace

import pytest
from eth_account import Account

from autonomous_data_api.evidence import EvidenceService
from autonomous_data_api.suppliers import (
    SUPPLIER_ASSET,
    DossierSupplierError,
    FundingDossierService,
)


def supplier_service(tmp_path, monkeypatch, daily_cap="0.01"):
    monkeypatch.setenv(
        "AUTONOMOUS_RECEIPT_SIGNING_KEY",
        base64.urlsafe_b64encode(b"d" * 32).decode().rstrip("="),
    )
    monkeypatch.setenv("AUTONOMOUS_ANALYTICS_HMAC_KEY", "supplier-test")
    evidence = EvidenceService(tmp_path / "evidence.sqlite3")
    private_key = Account.create().key.hex()
    return FundingDossierService(
        evidence,
        private_key=private_key,
        daily_cap_usd=Decimal(daily_cap),
    )


def requirement(amount="10000", asset=SUPPLIER_ASSET, network="eip155:8453"):
    return SimpleNamespace(
        scheme="exact",
        network=network,
        asset=asset,
        amount=amount,
    )


def test_supplier_selector_enforces_base_usdc_and_one_cent_cap():
    valid = requirement()
    assert FundingDossierService._select_payment_requirement(2, [valid]) is valid

    with pytest.raises(DossierSupplierError, match="at or below"):
        FundingDossierService._select_payment_requirement(
            2, [requirement(amount="10001")]
        )
    with pytest.raises(DossierSupplierError, match="at or below"):
        FundingDossierService._select_payment_requirement(
            2, [requirement(asset="0x0000000000000000000000000000000000000000")]
        )


def test_supplier_reservation_blocks_replay_and_daily_overspend(tmp_path, monkeypatch):
    supplier = supplier_service(tmp_path, monkeypatch)
    supplier._reserve("payment-one", "request-one")

    with pytest.raises(DossierSupplierError, match="already reserved"):
        supplier._reserve("payment-one", "request-one")
    with pytest.raises(DossierSupplierError, match="budget is exhausted"):
        supplier._reserve("payment-two", "request-two")
