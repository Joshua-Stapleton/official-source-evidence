from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import os
import sqlite3
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, ClassVar
from urllib.parse import urlparse

from cdp.auth.utils.jwt import JwtOptions, generate_jwt
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware
from x402.extensions.bazaar import OutputConfig, declare_discovery_extension
from x402.http import (
    AuthHeaders,
    FacilitatorConfig,
    HTTPFacilitatorClient,
    PaymentOption,
)
from x402.http.middleware.fastapi import PaymentMiddlewareASGI
from x402.http.types import RouteConfig
from x402.mechanisms.evm.exact import ExactEvmServerScheme
from x402.schemas import Network
from x402.server import x402ResourceServer

from autonomous_data_api.evidence import (
    SEC_PARSER_VERSION,
    EvidenceError,
    EvidenceService,
    OfacExactRequest,
    PreparedResult,
    SecDeltaRequest,
)

X402_TEST_RECIPIENT = "0x000000000000000000000000000000000000dEaD"
X402_NETWORK: Network = os.getenv("AUTONOMOUS_X402_NETWORK", "eip155:84532")
X402_PAY_TO_CONFIGURED = bool(os.getenv("AUTONOMOUS_X402_PAY_TO"))
X402_PAY_TO = os.getenv("AUTONOMOUS_X402_PAY_TO", X402_TEST_RECIPIENT)
X402_SEC_PRICE = os.getenv("AUTONOMOUS_X402_SEC_PRICE", "$0.10")
X402_OFAC_PRICE = os.getenv("AUTONOMOUS_X402_OFAC_PRICE", "$0.05")
X402_FACILITATOR_URL = os.getenv(
    "AUTONOMOUS_X402_FACILITATOR_URL",
    (
        "https://api.cdp.coinbase.com/platform/v2/x402"
        if X402_NETWORK == "eip155:8453"
        else "https://x402.org/facilitator"
    ),
)
X402_CDP_API_KEY_ID = os.getenv("CDP_API_KEY_ID", "")
X402_CDP_API_KEY_SECRET = os.getenv("CDP_API_KEY_SECRET", "")
X402_USES_CDP_FACILITATOR = (
    urlparse(X402_FACILITATOR_URL).hostname == "api.cdp.coinbase.com"
)
X402_CDP_AUTH_CONFIGURED = bool(X402_CDP_API_KEY_ID and X402_CDP_API_KEY_SECRET)
X402_REVENUE_READY = (
    X402_NETWORK == "eip155:8453"
    and X402_PAY_TO_CONFIGURED
    and (not X402_USES_CDP_FACILITATOR or X402_CDP_AUTH_CONFIGURED)
)

if X402_NETWORK == "eip155:8453" and not X402_PAY_TO_CONFIGURED:
    raise RuntimeError("AUTONOMOUS_X402_PAY_TO is required on Base mainnet")
if X402_USES_CDP_FACILITATOR and not X402_CDP_AUTH_CONFIGURED:
    raise RuntimeError(
        "CDP_API_KEY_ID and CDP_API_KEY_SECRET are required for the CDP facilitator"
    )


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


evidence_service = EvidenceService()


async def refresh_ofac_sources() -> None:
    while True:
        for list_name in ("SDN", "CONSOLIDATED"):
            try:
                await run_in_threadpool(evidence_service.refresh_ofac, list_name)
            except EvidenceError:
                # Health/status surfaces the stale state; the loop keeps retrying.
                continue
        await asyncio.sleep(300)


@asynccontextmanager
async def lifespan(_: FastAPI):
    refresh_task: asyncio.Task[None] | None = None
    if os.getenv("AUTONOMOUS_EVIDENCE_BACKGROUND_REFRESH", "0") == "1":
        refresh_task = asyncio.create_task(refresh_ofac_sources())
    try:
        yield
    finally:
        if refresh_task:
            refresh_task.cancel()
            try:
                await refresh_task
            except asyncio.CancelledError:
                pass


app = FastAPI(
    title="Official Source Evidence API",
    version="0.2.0",
    description=(
        "Deterministic, source-hashed SEC filing deltas and exact OFAC identifier evidence. "
        "No investment, sanctions-clearance, compliance, or legal conclusions."
    ),
    lifespan=lifespan,
)


def get_discovery_extension(method: str = "GET", **kwargs: Any) -> dict[str, Any]:
    # x402 validates Bazaar declarations before its runtime route-method enrichment.
    if method == "POST":
        kwargs["body_type"] = "json"
    extension = declare_discovery_extension(**kwargs)
    extension["bazaar"]["info"]["input"]["method"] = method
    return extension


class CdpFacilitatorAuthProvider:
    def __init__(
        self, api_key_id: str, api_key_secret: str, facilitator_url: str
    ) -> None:
        parsed = urlparse(facilitator_url)
        if not parsed.hostname:
            raise ValueError("CDP facilitator URL must include a hostname")
        self.api_key_id = api_key_id
        self.api_key_secret = api_key_secret
        self.host = parsed.hostname
        self.base_path = parsed.path.rstrip("/")

    def _header(self, method: str, suffix: str) -> dict[str, str]:
        token = generate_jwt(
            JwtOptions(
                api_key_id=self.api_key_id,
                api_key_secret=self.api_key_secret,
                request_method=method,
                request_host=self.host,
                request_path=f"{self.base_path}{suffix}",
                expires_in=120,
            )
        )
        return {"Authorization": f"Bearer {token}"}

    def get_auth_headers(self) -> AuthHeaders:
        return AuthHeaders(
            verify=self._header("POST", "/verify"),
            settle=self._header("POST", "/settle"),
            supported=self._header("GET", "/supported"),
            bazaar=self._header("GET", "/discovery/resources"),
        )


x402_auth_provider = (
    CdpFacilitatorAuthProvider(
        X402_CDP_API_KEY_ID,
        X402_CDP_API_KEY_SECRET,
        X402_FACILITATOR_URL,
    )
    if X402_USES_CDP_FACILITATOR
    else None
)
x402_facilitator = HTTPFacilitatorClient(
    FacilitatorConfig(url=X402_FACILITATOR_URL, auth_provider=x402_auth_provider)
)
x402_server = x402ResourceServer(x402_facilitator)
x402_server.register(X402_NETWORK, ExactEvmServerScheme())

x402_routes: dict[str, RouteConfig] = {
    "POST /v1/sec/filing-trigger-delta": RouteConfig(
        accepts=[
            PaymentOption(
                scheme="exact",
                pay_to=X402_PAY_TO,
                price=X402_SEC_PRICE,
                network=X402_NETWORK,
            )
        ],
        mime_type="application/json",
        description=(
            "Return a source-attested deterministic SEC filing event and selected "
            "XBRL fact delta for one issuer"
        ),
        service_name="Official Source Evidence",
        tags=["sec", "edgar", "filing-delta", "xbrl", "source-proof"],
        extensions=get_discovery_extension(
            method="POST",
            input={
                "cik": "0000320193",
                "since_accession": "0000320193-26-000081",
                "forms": ["8-K", "10-Q", "10-K"],
                "rules": ["FORM:8-K:ITEM:2.02", "XBRL:us-gaap:Revenues"],
                "max_source_age_seconds": 600,
            },
            input_schema={
                "type": "object",
                "properties": {
                    "cik": {"type": "string", "pattern": "^[0-9]{1,10}$"},
                    "since_accession": {
                        "type": "string",
                        "pattern": "^[0-9]{10}-[0-9]{2}-[0-9]{6}$",
                    },
                    "forms": {
                        "type": "array",
                        "items": {"enum": ["8-K", "10-Q", "10-K"]},
                        "minItems": 1,
                        "maxItems": 3,
                    },
                    "rules": {
                        "type": "array",
                        "items": {"type": "string"},
                        "maxItems": 20,
                    },
                    "max_source_age_seconds": {
                        "type": "integer",
                        "minimum": 60,
                        "maximum": 3600,
                    },
                },
                "required": ["cik", "since_accession"],
                "additionalProperties": False,
            },
            output=OutputConfig(
                example={
                    "request_id": "sec_example",
                    "decision": "NEW_FILING",
                    "filings": [],
                    "selected_fact_deltas": [],
                    "provenance": {"parser_version": SEC_PARSER_VERSION},
                }
            ),
        ),
    ),
    "POST /v1/ofac/exact-identifier-evidence": RouteConfig(
        accepts=[
            PaymentOption(
                scheme="exact",
                pay_to=X402_PAY_TO,
                price=X402_OFAC_PRICE,
                network=X402_NETWORK,
            )
        ],
        mime_type="application/json",
        description=(
            "Perform an exact, non-advisory identifier lookup against versioned "
            "OFAC source data and return source proof"
        ),
        service_name="Official Source Evidence",
        tags=["ofac", "exact-match", "crypto-address", "source-proof", "non-advisory"],
        extensions=get_discovery_extension(
            method="POST",
            input={
                "identifier_type": "crypto_address",
                "identifier": "0x0000000000000000000000000000000000000000",
                "networks": ["eip155:1", "eip155:8453"],
                "lists": ["SDN", "CONSOLIDATED"],
            },
            input_schema={
                "type": "object",
                "properties": {
                    "identifier_type": {
                        "enum": ["crypto_address", "ofac_uid", "exact_name"]
                    },
                    "identifier": {"type": "string", "minLength": 1, "maxLength": 256},
                    "networks": {
                        "type": "array",
                        "items": {"type": "string"},
                        "maxItems": 8,
                    },
                    "lists": {
                        "type": "array",
                        "items": {"enum": ["SDN", "CONSOLIDATED"]},
                        "minItems": 1,
                        "maxItems": 2,
                    },
                },
                "required": ["identifier_type", "identifier"],
                "additionalProperties": False,
            },
            output=OutputConfig(
                example={
                    "request_id": "ofac_example",
                    "match_scope": "exact_normalized_identifier_only",
                    "match_status": "NO_EXACT_MATCH",
                    "matches": [],
                    "source_versions": [],
                }
            ),
        ),
    ),
}
app.add_middleware(PaymentMiddlewareASGI, routes=x402_routes, server=x402_server)


def decode_x402_json_header(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        padded = value + "=" * (-len(value) % 4)
        decoded = base64.urlsafe_b64decode(padded)
        payload = json.loads(decoded)
        return payload if isinstance(payload, dict) else {}
    except (ValueError, TypeError, UnicodeDecodeError, binascii.Error):
        return {}


def find_wallet(value: Any) -> str | None:
    if isinstance(value, dict):
        for key in ("payer", "from", "owner"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.startswith("0x"):
                return candidate
        for child in value.values():
            candidate = find_wallet(child)
            if candidate:
                return candidate
    elif isinstance(value, list):
        for child in value:
            candidate = find_wallet(child)
            if candidate:
                return candidate
    return None


class EvidencePrecomputeMiddleware(BaseHTTPMiddleware):
    ROUTES: ClassVar[dict[str, tuple[type[BaseModel], str, str]]] = {
        "/v1/sec/filing-trigger-delta": (
            SecDeltaRequest,
            "prepare_sec",
            X402_SEC_PRICE,
        ),
        "/v1/ofac/exact-identifier-evidence": (
            OfacExactRequest,
            "prepare_ofac",
            X402_OFAC_PRICE,
        ),
    }

    def __init__(self, app: Any, service: EvidenceService) -> None:
        super().__init__(app)
        self.service = service
        self.rate_lock = asyncio.Lock()
        self.rate_events: dict[str, list[float]] = {}

    async def _within_rate_limit(self, request: Request) -> bool:
        now = time.monotonic()
        payment_signature = request.headers.get(
            "payment-signature"
        ) or request.headers.get("x-payment")
        identity = (
            hashlib.sha256(payment_signature.encode("utf-8")).hexdigest()
            if payment_signature
            else request.client.host
            if request.client
            else "unknown"
        )
        key = f"{request.url.path}:{identity}"
        limit = int(os.getenv("AUTONOMOUS_EVIDENCE_REQUESTS_PER_MINUTE", "30"))
        async with self.rate_lock:
            events = [
                event for event in self.rate_events.get(key, []) if now - event < 60
            ]
            if len(events) >= max(1, limit):
                self.rate_events[key] = events
                return False
            events.append(now)
            self.rate_events[key] = events
            if len(self.rate_events) > 10_000:
                self.rate_events = {
                    item_key: item_events
                    for item_key, item_events in self.rate_events.items()
                    if item_events and now - item_events[-1] < 60
                }
        return True

    async def dispatch(self, request: Request, call_next: Any) -> Any:
        route = self.ROUTES.get(request.url.path)
        if request.method != "POST" or route is None:
            return await call_next(request)
        if os.getenv("AUTONOMOUS_EVIDENCE_ENABLED", "1") != "1":
            return JSONResponse(
                status_code=503,
                content={
                    "error": {
                        "code": "SERVICE_DISABLED",
                        "detail": "New paid evidence requests are disabled",
                    }
                },
            )
        if not await self._within_rate_limit(request):
            return JSONResponse(
                status_code=429,
                headers={"Retry-After": "60"},
                content={
                    "error": {
                        "code": "RATE_LIMITED",
                        "detail": "Evidence request rate limit exceeded",
                    }
                },
            )
        content_length = request.headers.get("content-length")
        if content_length and content_length.isdigit() and int(content_length) > 8192:
            return JSONResponse(
                status_code=413,
                content={
                    "error": {
                        "code": "REQUEST_TOO_LARGE",
                        "detail": "Body exceeds 8 KiB",
                    }
                },
            )

        model_class, prepare_method_name, quoted_price = route
        started = time.monotonic()
        try:
            payload = await request.json()
        except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError):
            return JSONResponse(
                status_code=400,
                content={
                    "error": {"code": "INVALID_JSON", "detail": "Body must be JSON"}
                },
            )
        if len(json.dumps(payload, separators=(",", ":")).encode("utf-8")) > 8192:
            return JSONResponse(
                status_code=413,
                content={
                    "error": {
                        "code": "REQUEST_TOO_LARGE",
                        "detail": "Body exceeds 8 KiB",
                    }
                },
            )
        try:
            validated = model_class.model_validate(payload)
        except ValidationError as exc:
            return JSONResponse(
                status_code=422,
                content={
                    "error": {
                        "code": "INVALID_INPUT",
                        "detail": exc.errors(include_url=False, include_context=False),
                    }
                },
            )
        try:
            prepare_method = getattr(self.service, prepare_method_name)
            prepared: PreparedResult = await run_in_threadpool(
                prepare_method, validated
            )
        except EvidenceError as exc:
            return JSONResponse(
                status_code=exc.status_code,
                content={"error": {"code": exc.code, "detail": exc.detail}},
            )

        request.state.evidence_prepared = prepared
        payment_signature = request.headers.get(
            "payment-signature"
        ) or request.headers.get("x-payment")
        if payment_signature and not await run_in_threadpool(
            self.service.bind_payment,
            payment_signature,
            prepared,
            request.url.path,
        ):
            return JSONResponse(
                status_code=409,
                content={
                    "error": {
                        "code": "PAYMENT_RESOURCE_MISMATCH",
                        "detail": "Payment proof is already bound to a different request",
                    }
                },
            )
        response = await call_next(request)
        response.headers["X-Evidence-Request-Id"] = prepared.request_id
        response.headers["X-Evidence-Request-Hash"] = f"sha256:{prepared.request_hash}"
        response.headers["X-Evidence-Result-Hash"] = f"sha256:{prepared.result_hash}"

        payment_payload = decode_x402_json_header(payment_signature)
        settlement = decode_x402_json_header(response.headers.get("payment-response"))
        settlement_tx_hash = settlement.get("transaction") or settlement.get(
            "transactionHash"
        )
        payer_wallet = find_wallet(payment_payload)
        if response.status_code == 402 and not payment_signature:
            response_status = "PAYMENT_REQUIRED"
        elif response.status_code < 400 and payment_signature:
            response_status = "FULFILLED"
        elif response.status_code == 402:
            response_status = "PAYMENT_OR_SETTLEMENT_FAILED"
        else:
            response_status = f"HTTP_{response.status_code}"
        try:
            await run_in_threadpool(
                self.service.record_attempt,
                prepared,
                route=request.url.path,
                quoted_price=quoted_price,
                network=X402_NETWORK,
                response_status=response_status,
                latency_ms=int((time.monotonic() - started) * 1000),
                payment_signature=payment_signature,
                settlement_tx_hash=settlement_tx_hash,
                payer_wallet=payer_wallet,
            )
        except (sqlite3.Error, OSError):
            # A ledger outage must be visible in monitoring, but must not corrupt a valid receipt.
            response.headers["X-Evidence-Ledger"] = "write-failed"
        return response


app.add_middleware(EvidencePrecomputeMiddleware, service=evidence_service)


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "generated_at": utc_now(),
        "x402": {
            "network": X402_NETWORK,
            "prices": {"sec_delta": X402_SEC_PRICE, "ofac_exact": X402_OFAC_PRICE},
            "revenue_ready": X402_REVENUE_READY,
            "mode": (
                "base-mainnet"
                if X402_REVENUE_READY
                else "testnet-configured-recipient"
                if X402_PAY_TO_CONFIGURED
                else "testnet-demo-recipient"
            ),
        },
    }


@app.get("/.well-known/agent-service.json")
def agent_manifest() -> dict[str, Any]:
    base_url = os.getenv("AUTONOMOUS_API_BASE_URL", "http://localhost:8765").rstrip("/")
    return {
        "name": "Official Source Evidence API",
        "description": (
            "Deterministic SEC filing deltas and exact OFAC identifier evidence with "
            "versioned source proof; never advice or clearance."
        ),
        "auth": {"type": "none", "payment": "x402-v2"},
        "payment": {
            "protocol": "x402-v2",
            "network": X402_NETWORK,
            "prices": {
                "/v1/sec/filing-trigger-delta": X402_SEC_PRICE,
                "/v1/ofac/exact-identifier-evidence": X402_OFAC_PRICE,
            },
            "revenue_ready": X402_REVENUE_READY,
        },
        "openapi_url": f"{base_url}/openapi.json",
        "sample_endpoints": [
            f"{base_url}/v1/sec/sample",
            f"{base_url}/v1/ofac/sample",
        ],
        "agent_paid_endpoints": [
            f"{base_url}/v1/sec/filing-trigger-delta",
            f"{base_url}/v1/ofac/exact-identifier-evidence",
        ],
        "status_endpoint": f"{base_url}/v1/experiments/status",
        "boundaries": [
            "No investment advice or materiality opinion.",
            "No sanctions clearance, transaction authorization, or fuzzy screening.",
        ],
    }


@app.get("/v1/sec/availability")
def sec_availability(cik: str) -> dict[str, Any]:
    normalized = cik.strip()
    if not normalized.isdigit() or len(normalized) > 10:
        raise HTTPException(
            status_code=422, detail="cik must contain at most 10 digits"
        )
    user_agent = os.getenv("AUTONOMOUS_SEC_USER_AGENT", "")
    return {
        "supported": True,
        "cik": normalized.zfill(10),
        "forms": ["8-K", "10-Q", "10-K"],
        "max_filings_per_result": 10,
        "source_access_configured": bool(
            user_agent and ("@" in user_agent or "http" in user_agent)
        ),
        "live_filing_result_included": False,
    }


@app.get("/v1/sec/sample")
def sec_sample() -> dict[str, Any]:
    return {
        "sample_type": "static_contract_fixture",
        "as_of": "2026-08-01",
        "live_source_result": False,
        "request": {
            "cik": "0000320193",
            "since_accession": "0000320193-26-000081",
            "forms": ["8-K", "10-Q", "10-K"],
            "rules": ["FORM:8-K:ITEM:2.02", "XBRL:us-gaap:Revenues"],
        },
        "response_shape": {
            "decision": "NEW_FILING | NO_NEW_FILING",
            "filings": [],
            "selected_fact_deltas": [],
            "provenance": {
                "publisher": "U.S. Securities and Exchange Commission",
                "parser_version": SEC_PARSER_VERSION,
            },
            "limitations": ["Factual filing record only; not investment advice."],
        },
    }


@app.get("/v1/ofac/sample")
def ofac_sample() -> dict[str, Any]:
    return {
        "sample_type": "static_contract_fixture",
        "as_of": "2026-08-01",
        "live_source_result": False,
        "request": {
            "identifier_type": "crypto_address",
            "identifier": "0x0000000000000000000000000000000000000000",
            "networks": ["eip155:1", "eip155:8453"],
            "lists": ["SDN", "CONSOLIDATED"],
        },
        "response_shape": {
            "match_scope": "exact_normalized_identifier_only",
            "match_status": "EXACT_MATCH | NO_EXACT_MATCH",
            "matches": [],
            "source_versions": [],
            "limitations": ["No-match is not sanctions clearance or legal advice."],
        },
    }


@app.post("/v1/sec/filing-trigger-delta")
def sec_filing_trigger_delta(
    payload: SecDeltaRequest, request: Request
) -> dict[str, Any]:
    del payload
    prepared: PreparedResult | None = getattr(request.state, "evidence_prepared", None)
    if prepared is None:
        raise HTTPException(status_code=503, detail="Prepared result unavailable")
    return prepared.result


@app.post("/v1/ofac/exact-identifier-evidence")
def ofac_exact_identifier_evidence(
    payload: OfacExactRequest,
    request: Request,
) -> dict[str, Any]:
    del payload
    prepared: PreparedResult | None = getattr(request.state, "evidence_prepared", None)
    if prepared is None:
        raise HTTPException(status_code=503, detail="Prepared result unavailable")
    return prepared.result


@app.get("/v1/evidence/replay/{request_id}")
def replay_evidence_result(
    request_id: str,
    payment_signature: str | None = Header(default=None, alias="Payment-Signature"),
    x_payment: str | None = Header(default=None, alias="X-Payment"),
) -> dict[str, Any]:
    signature = payment_signature or x_payment
    if not signature:
        raise HTTPException(status_code=401, detail="Payment proof is required")
    result = evidence_service.replay(request_id, signature)
    if result is None:
        raise HTTPException(
            status_code=404, detail="No fulfilled result matches that payment proof"
        )
    return result


@app.get("/v1/experiments/status")
def evidence_experiment_status() -> dict[str, Any]:
    return evidence_service.experiment_status()


@app.get("/v1/summary")
def summary() -> dict[str, Any]:
    return {
        "service": "Official Source Evidence API",
        "products": [
            "/v1/sec/filing-trigger-delta",
            "/v1/ofac/exact-identifier-evidence",
        ],
        "experiment": evidence_service.experiment_status(),
    }


def retired_wedge(detail: str) -> JSONResponse:
    return JSONResponse(
        status_code=410,
        content={"error": "RETIRED_WEDGE", "detail": detail},
    )


@app.get("/v1/public/pfas/sample")
def public_pfas_sample() -> JSONResponse:
    return retired_wedge(
        "PFAS lead scoring is not part of the autonomous evidence experiment."
    )


@app.get("/v1/public/grid/sample")
def public_grid_sample() -> JSONResponse:
    return retired_wedge(
        "Grid lead scoring is not part of the autonomous evidence experiment."
    )


@app.get("/v1/pfas/leads")
def pfas_leads() -> JSONResponse:
    return retired_wedge(
        "PFAS lead scoring is not part of the autonomous evidence experiment."
    )


@app.get("/v1/grid/projects")
def grid_projects() -> JSONResponse:
    return retired_wedge(
        "Grid lead scoring is not part of the autonomous evidence experiment."
    )


@app.get("/v1/x402/pfas/leads")
def retired_x402_pfas_leads() -> JSONResponse:
    return retired_wedge(
        "PFAS lead scoring is not part of the autonomous evidence experiment."
    )


@app.get("/v1/x402/grid/projects")
def retired_x402_grid_projects() -> JSONResponse:
    return retired_wedge(
        "Grid lead scoring is not part of the autonomous evidence experiment."
    )
