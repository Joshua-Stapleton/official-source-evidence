from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from eth_account import Account
from starlette.concurrency import run_in_threadpool
from x402 import x402Client
from x402.http import x402HTTPClient
from x402.http.clients import x402HttpxClient
from x402.mechanisms.evm import EthAccountSigner
from x402.mechanisms.evm.exact.register import register_exact_evm_client

from autonomous_data_api.evidence import (
    EvidenceError,
    EvidenceService,
    FormDCompanyDossierRequest,
    PreparedResult,
)

SUPPLIER_URL = "https://x402.tavily.com/search"
SUPPLIER_NETWORK = "eip155:8453"
SUPPLIER_ASSET = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
SUPPLIER_MAX_AMOUNT_ATOMIC = 10_000


class DossierSupplierError(RuntimeError):
    def __init__(
        self,
        code: str,
        detail: str,
        status_code: int,
        *,
        direct_cost_usd: Decimal = Decimal(0),
    ) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.status_code = status_code
        self.direct_cost_usd = direct_cost_usd


class FundingDossierService:
    def __init__(
        self,
        evidence_service: EvidenceService,
        *,
        private_key: str,
        daily_cap_usd: Decimal,
    ) -> None:
        self.evidence_service = evidence_service
        self.db_path: Path = evidence_service.db_path
        self.daily_cap_usd = daily_cap_usd
        self.lock = asyncio.Lock()
        self.account = Account.from_key(private_key) if private_key else None
        self._init_db()

    @property
    def configured(self) -> bool:
        return self.account is not None and self.daily_cap_usd >= Decimal("0.01")

    @property
    def wallet_address(self) -> str | None:
        return self.account.address if self.account is not None else None

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def _init_db(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS supplier_purchases (
                    inbound_payment_hash TEXT PRIMARY KEY,
                    request_hash TEXT NOT NULL,
                    supplier TEXT NOT NULL,
                    amount_usd REAL NOT NULL,
                    status TEXT NOT NULL,
                    supplier_transaction TEXT,
                    supplier_request_id TEXT,
                    error_code TEXT,
                    reserved_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_supplier_purchases_daily
                    ON supplier_purchases(reserved_at, status);
                """
            )

    def _reserve(self, payment_signature: str, request_hash: str) -> str:
        payment_hash = hashlib.sha256(payment_signature.encode("utf-8")).hexdigest()
        now = datetime.now(timezone.utc)
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        now_text = now.isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT status FROM supplier_purchases WHERE inbound_payment_hash = ?",
                (payment_hash,),
            ).fetchone()
            if existing:
                connection.rollback()
                raise DossierSupplierError(
                    "SUPPLIER_PAYMENT_REPLAY_BLOCKED",
                    "This inbound payment proof already reserved a supplier purchase",
                    409,
                )
            reserved = connection.execute(
                """
                SELECT COALESCE(SUM(amount_usd), 0) AS amount
                FROM supplier_purchases
                WHERE reserved_at >= ?
                  AND status IN ('RESERVED', 'FULFILLED', 'UNKNOWN')
                """,
                (day_start,),
            ).fetchone()
            used = Decimal(str(reserved["amount"] or 0))
            cost = Decimal("0.01")
            if used + cost > self.daily_cap_usd:
                connection.rollback()
                raise DossierSupplierError(
                    "SUPPLIER_DAILY_CAP_REACHED",
                    "The funded-company dossier supplier budget is exhausted for today",
                    503,
                )
            connection.execute(
                """
                INSERT INTO supplier_purchases (
                    inbound_payment_hash, request_hash, supplier, amount_usd,
                    status, reserved_at, updated_at
                ) VALUES (?, ?, 'tavily-x402', 0.01, 'RESERVED', ?, ?)
                """,
                (payment_hash, request_hash, now_text, now_text),
            )
            connection.commit()
        return payment_hash

    def _complete(
        self,
        payment_hash: str,
        *,
        status: str,
        supplier_transaction: str | None = None,
        supplier_request_id: str | None = None,
        error_code: str | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE supplier_purchases
                SET status = ?, supplier_transaction = ?, supplier_request_id = ?,
                    error_code = ?, updated_at = ?
                WHERE inbound_payment_hash = ?
                """,
                (
                    status,
                    supplier_transaction,
                    supplier_request_id,
                    error_code,
                    datetime.now(timezone.utc).isoformat(),
                    payment_hash,
                ),
            )
            connection.commit()

    @staticmethod
    def _select_payment_requirement(_version: int, requirements: list[Any]) -> Any:
        for requirement in requirements:
            try:
                amount = int(Decimal(str(requirement.amount)))
            except (AttributeError, InvalidOperation, ValueError):
                continue
            if (
                str(getattr(requirement, "scheme", "")) == "exact"
                and str(getattr(requirement, "network", "")) == SUPPLIER_NETWORK
                and str(getattr(requirement, "asset", "")).casefold()
                == SUPPLIER_ASSET.casefold()
                and 0 < amount <= SUPPLIER_MAX_AMOUNT_ATOMIC
            ):
                return requirement
        raise DossierSupplierError(
            "SUPPLIER_PRICE_OR_ASSET_CHANGED",
            "Supplier offered no Base USDC payment at or below $0.01",
            502,
        )

    async def _search(self, query: str) -> tuple[dict[str, Any], str | None]:
        if self.account is None:
            raise DossierSupplierError(
                "SUPPLIER_WALLET_NOT_CONFIGURED",
                "The funded-company dossier supplier wallet is not configured",
                503,
            )
        client = x402Client(
            payment_requirements_selector=self._select_payment_requirement
        )
        register_exact_evm_client(client, EthAccountSigner(self.account))
        payment_client = x402HTTPClient(client)
        async with x402HttpxClient(client, timeout=60) as http:
            response = await http.post(
                SUPPLIER_URL,
                json={
                    "query": query,
                    "search_depth": "advanced",
                    "topic": "general",
                    "max_results": 5,
                    "include_answer": False,
                    "include_raw_content": False,
                    "include_images": False,
                },
            )
            body = await response.aread()
            if len(body) > 512_000:
                raise DossierSupplierError(
                    "SUPPLIER_RESPONSE_TOO_LARGE",
                    "Supplier response exceeded the 512 KiB safety limit",
                    502,
                    direct_cost_usd=Decimal("0.01"),
                )
            if not response.is_success:
                raise DossierSupplierError(
                    "SUPPLIER_REQUEST_FAILED",
                    f"Supplier returned HTTP {response.status_code}",
                    502,
                    direct_cost_usd=Decimal("0.01"),
                )
            try:
                payload = json.loads(body)
            except (ValueError, TypeError, UnicodeDecodeError) as exc:
                raise DossierSupplierError(
                    "SUPPLIER_RESPONSE_INVALID",
                    "Supplier returned invalid JSON",
                    502,
                    direct_cost_usd=Decimal("0.01"),
                ) from exc
            if not isinstance(payload, dict) or not isinstance(
                payload.get("results"), list
            ):
                raise DossierSupplierError(
                    "SUPPLIER_RESPONSE_INVALID",
                    "Supplier response did not contain a results array",
                    502,
                    direct_cost_usd=Decimal("0.01"),
                )
            settlement = payment_client.get_payment_settle_response(
                lambda name: response.headers.get(name)
            )
            if isinstance(settlement, dict):
                transaction = settlement.get("transaction") or settlement.get(
                    "transactionHash"
                )
            else:
                transaction = (
                    getattr(settlement, "transaction", None) if settlement else None
                )
            return payload, transaction

    async def build(
        self,
        request: FormDCompanyDossierRequest,
        payment_signature: str,
    ) -> tuple[PreparedResult, Decimal]:
        if not self.configured:
            raise DossierSupplierError(
                "SUPPLIER_WALLET_NOT_CONFIGURED",
                "The funded-company dossier supplier wallet is not configured",
                503,
            )
        request_hash = hashlib.sha256(
            json.dumps(
                request.model_dump(mode="json"),
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        async with self.lock:
            payment_hash = await run_in_threadpool(
                self._reserve, payment_signature, request_hash
            )
            supplier_attempted = False
            try:
                source = await run_in_threadpool(
                    self.evidence_service.load_form_d_dossier_source, request
                )
                issuer_name = str(source.lead["issuer"].get("name") or "").strip()
                if not issuer_name:
                    raise DossierSupplierError(
                        "ISSUER_NAME_UNAVAILABLE",
                        "The official Form D filing contained no issuer name",
                        422,
                    )
                location = source.lead["issuer"].get("location") or {}
                query = " ".join(
                    part
                    for part in (
                        f'"{issuer_name}"',
                        str(location.get("city") or ""),
                        str(location.get("state_or_country") or ""),
                        "official website products customers company",
                    )
                    if part
                )
                supplier_attempted = True
                search_payload, transaction = await self._search(query)
                prepared = await run_in_threadpool(
                    self.evidence_service.prepare_form_d_company_dossier,
                    request,
                    source,
                    search_payload,
                    transaction,
                )
                await run_in_threadpool(
                    self._complete,
                    payment_hash,
                    status="FULFILLED",
                    supplier_transaction=transaction,
                    supplier_request_id=str(search_payload.get("request_id") or "")
                    or None,
                )
                return prepared, Decimal("0.01")
            except EvidenceError:
                await run_in_threadpool(
                    self._complete,
                    payment_hash,
                    status="FAILED_PRE_PAYMENT",
                    error_code="OFFICIAL_SOURCE_FAILED",
                )
                raise
            except DossierSupplierError as exc:
                status = "UNKNOWN" if exc.direct_cost_usd else "FAILED_PRE_PAYMENT"
                await run_in_threadpool(
                    self._complete,
                    payment_hash,
                    status=status,
                    error_code=exc.code,
                )
                raise
            except Exception as exc:
                status = "UNKNOWN" if supplier_attempted else "FAILED_PRE_PAYMENT"
                await run_in_threadpool(
                    self._complete,
                    payment_hash,
                    status=status,
                    error_code=type(exc).__name__,
                )
                raise DossierSupplierError(
                    "DOSSIER_BUILD_FAILED",
                    "The dossier could not be assembled",
                    502,
                    direct_cost_usd=(
                        Decimal("0.01") if supplier_attempted else Decimal(0)
                    ),
                ) from exc
