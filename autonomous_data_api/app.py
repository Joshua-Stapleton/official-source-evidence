from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import ipaddress
import json
import os
import re
import sqlite3
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, ClassVar
from urllib.parse import urlparse

from cdp.auth.utils.jwt import JwtOptions, generate_jwt
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.openapi.utils import get_openapi
from fastapi.responses import (
    FileResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)
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
    FORM_D_PARSER_VERSION,
    SEC_PARSER_VERSION,
    EvidenceError,
    EvidenceService,
    FormDFundingLeadsRequest,
    OfacExactRequest,
    OfacPreflightRequest,
    PreparedResult,
    PublicSourceSnapshotRequest,
    SecDeltaRequest,
    SecSignalRequest,
    WebMonitorCreateRequest,
)
from autonomous_data_api.marketplace import MarketplaceError, The402Provider
from autonomous_data_api.monitors import MonitorError, WebMonitorService

X402_TEST_RECIPIENT = "0x000000000000000000000000000000000000dEaD"
X402_NETWORK: Network = os.getenv("AUTONOMOUS_X402_NETWORK", "eip155:84532")
X402_PAY_TO_CONFIGURED = bool(os.getenv("AUTONOMOUS_X402_PAY_TO"))
X402_PAY_TO = os.getenv("AUTONOMOUS_X402_PAY_TO", X402_TEST_RECIPIENT)
X402_SEC_PRICE = os.getenv("AUTONOMOUS_X402_SEC_PRICE", "$0.10")
X402_OFAC_PRICE = os.getenv("AUTONOMOUS_X402_OFAC_PRICE", "$0.05")
X402_SEC_SIGNAL_PRICE = os.getenv("AUTONOMOUS_X402_SEC_SIGNAL_PRICE", "$0.01")
X402_OFAC_PREFLIGHT_PRICE = os.getenv("AUTONOMOUS_X402_OFAC_PREFLIGHT_PRICE", "$0.01")
X402_FORM_D_PRICE = os.getenv("AUTONOMOUS_X402_FORM_D_PRICE", "$0.05")
X402_SOURCE_WATCH_PRICE = os.getenv("AUTONOMOUS_X402_SOURCE_WATCH_PRICE", "$1.00")
X402_SOURCE_SNAPSHOT_PRICE = os.getenv("AUTONOMOUS_X402_SOURCE_SNAPSHOT_PRICE", "$0.03")
CONVERSION_EXPERIMENT_START_UTC = os.getenv(
    "AUTONOMOUS_CONVERSION_EXPERIMENT_START_UTC", ""
).strip()
LEGACY_FLY_HOST = os.getenv(
    "AUTONOMOUS_LEGACY_FLY_HOST", "iti-official-source-evidence.fly.dev"
).casefold()
try:
    X402_DAILY_REVENUE_CAP_USD = Decimal(
        os.getenv("AUTONOMOUS_X402_DAILY_REVENUE_CAP_USD", "0")
    )
except InvalidOperation as exc:
    raise RuntimeError("AUTONOMOUS_X402_DAILY_REVENUE_CAP_USD must be numeric") from exc
X402_FACILITATOR_URL = os.getenv(
    "AUTONOMOUS_X402_FACILITATOR_URL",
    (
        "https://api.cdp.coinbase.com/platform/v2/x402"
        if X402_NETWORK == "eip155:8453"
        else "https://x402.org/facilitator"
    ),
)
PUBLIC_BASE_URL = os.getenv("AUTONOMOUS_API_BASE_URL", "http://localhost:8765")
PUBLIC_SCHEME = urlparse(PUBLIC_BASE_URL).scheme
X402_CDP_API_KEY_ID = os.getenv("CDP_API_KEY_ID", "")
STATIC_DIR = Path(__file__).resolve().parent / "static"
SERVICE_ICON_URL = f"{PUBLIC_BASE_URL.rstrip('/')}/icon.png"
SEC_PROBE_SINCE_UTC = (
    (datetime.now(timezone.utc) - timedelta(days=30))
    .replace(microsecond=0)
    .isoformat()
    .replace("+00:00", "Z")
)
SEC_PROBE_PAYLOAD = {
    "ticker": "AAPL",
    "since": SEC_PROBE_SINCE_UTC,
    "forms": ["8-K", "10-Q", "10-K"],
    "rules": ["FORM:8-K:ITEM:2.02", "XBRL:us-gaap:Revenues"],
    "max_source_age_seconds": 600,
}
SEC_SIGNAL_PROBE_PAYLOAD = {
    "ticker": "AAPL",
    "since": SEC_PROBE_SINCE_UTC,
    "forms": ["8-K", "10-Q", "10-K"],
    "max_source_age_seconds": 600,
}
OFAC_PROBE_PAYLOAD = {
    "identifier_type": "crypto_address",
    "identifier": "0x0000000000000000000000000000000000000000",
    "networks": ["eip155:1", "eip155:8453"],
    "lists": ["SDN", "CONSOLIDATED"],
}
OFAC_PREFLIGHT_PROBE_PAYLOAD = {
    "address": "0x0000000000000000000000000000000000000000",
    "network": "eip155:8453",
}
FORM_D_PROBE_PAYLOAD = {
    "since": (
        (datetime.now(timezone.utc) - timedelta(days=3))
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    ),
    "states": [],
    "industry_keywords": [],
    "minimum_amount_sold_usd": "0",
    "include_amendments": False,
    "limit": 10,
    "max_source_age_seconds": 600,
}
SOURCE_WATCH_PROBE_PAYLOAD = {
    "url": "https://www.sec.gov/newsroom/press-releases",
    "label": "SEC press releases",
}
SOURCE_SNAPSHOT_PROBE_PAYLOAD = {
    "url": "https://www.sec.gov/newsroom/press-releases",
    "query": "enforcement",
    "max_characters": 12000,
}


def load_cdp_api_key_secret() -> str:
    raw = os.getenv("CDP_API_KEY_SECRET", "")
    if raw:
        return raw
    encoded = os.getenv("CDP_API_KEY_SECRET_B64", "")
    if not encoded:
        return ""
    try:
        return base64.b64decode(encoded, validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError, binascii.Error) as exc:
        raise RuntimeError("CDP_API_KEY_SECRET_B64 is not valid base64 UTF-8") from exc


X402_CDP_API_KEY_SECRET = load_cdp_api_key_secret()
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
monitor_service = WebMonitorService(evidence_service.db_path)
the402_provider = The402Provider(evidence_service.db_path, monitor_service)


async def refresh_ofac_sources() -> None:
    while True:
        for list_name in ("SDN", "CONSOLIDATED"):
            try:
                await run_in_threadpool(evidence_service.refresh_ofac, list_name)
            except EvidenceError:
                # Health/status surfaces the stale state; the loop keeps retrying.
                continue
        await asyncio.sleep(300)


async def run_source_watch_checks() -> None:
    while True:
        try:
            await run_in_threadpool(monitor_service.run_due)
        except (OSError, sqlite3.Error):
            pass
        try:
            await run_in_threadpool(the402_provider.run_pending)
        except (OSError, sqlite3.Error):
            pass
        await asyncio.sleep(60)


@asynccontextmanager
async def lifespan(_: FastAPI):
    refresh_task: asyncio.Task[None] | None = None
    monitor_task: asyncio.Task[None] | None = None
    if os.getenv("AUTONOMOUS_EVIDENCE_BACKGROUND_REFRESH", "0") == "1":
        refresh_task = asyncio.create_task(refresh_ofac_sources())
    if os.getenv("AUTONOMOUS_SOURCE_WATCH_ENABLED", "1") == "1":
        monitor_task = asyncio.create_task(run_source_watch_checks())
    try:
        yield
    finally:
        tasks = [task for task in (refresh_task, monitor_task) if task]
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass


app = FastAPI(
    title="Agent Evidence and Source Watch API",
    version="0.7.0",
    description=(
        "One-shot public-source extraction, long-running source-change monitoring, "
        "pay-per-call SEC Form D GTM signals, source-hashed EDGAR filing deltas, "
        "and exact OFAC identifier evidence for autonomous agents. No accounts or API keys. "
        "No investment advice, sanctions clearance, compliance determination, or legal advice."
    ),
    contact={"name": "Regulavita", "email": "joshua@regulavita.com"},
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
    "POST /v1/web/source-snapshot": RouteConfig(
        accepts=[
            PaymentOption(
                scheme="exact",
                pay_to=X402_PAY_TO,
                price=X402_SOURCE_SNAPSHOT_PRICE,
                network=X402_NETWORK,
            )
        ],
        mime_type="application/json",
        description=(
            "Fetch one public HTTPS HTML, JSON, XML, or text source and return "
            "normalized agent-ready text, optional literal-match excerpts, a "
            "content hash, and an Ed25519-signed receipt. No account or API key."
        ),
        service_name="Public Source Snapshot",
        tags=[
            "content-extraction",
            "web-reader",
            "web-scraping",
            "llm-context",
            "source-hash",
            "provenance",
            "signed-evidence",
            "text-normalization",
        ],
        icon_url=SERVICE_ICON_URL,
        extensions=get_discovery_extension(
            method="POST",
            input=SOURCE_SNAPSHOT_PROBE_PAYLOAD,
            input_schema={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "format": "uri",
                        "pattern": "^https://",
                        "maxLength": 2048,
                        "description": "Public HTTPS source to fetch once.",
                    },
                    "query": {
                        "type": "string",
                        "maxLength": 200,
                        "description": "Optional literal case-insensitive phrase for excerpts.",
                    },
                    "max_characters": {
                        "type": "integer",
                        "minimum": 1000,
                        "maximum": 50000,
                        "default": 12000,
                    },
                },
                "required": ["url"],
                "additionalProperties": False,
            },
            output=OutputConfig(
                example={
                    "request_id": "public_source_snapshot_example",
                    "product": "PUBLIC_SOURCE_SNAPSHOT",
                    "url": "https://www.sec.gov/newsroom/press-releases",
                    "retrieved_at": "2026-08-10T00:00:00Z",
                    "http_status": 200,
                    "content_type": "text/html",
                    "content": {
                        "normalized_text": "SEC press release text...",
                        "content_sha256": f"sha256:{'0' * 64}",
                        "returned_characters": 25,
                        "total_normalized_characters": 25,
                        "truncated": False,
                    },
                    "query": {
                        "value": "enforcement",
                        "literal_match_count_returned": 1,
                        "excerpts": [],
                    },
                    "upgrade": {"path": "/v1/monitors/source-change"},
                    "provenance": {"engine_version": "public-source-snapshot/0.1.0"},
                    "receipt": {"algorithm": "Ed25519"},
                },
                schema={
                    "type": "object",
                    "required": [
                        "request_id",
                        "product",
                        "url",
                        "retrieved_at",
                        "http_status",
                        "content_type",
                        "content",
                        "query",
                        "upgrade",
                        "provenance",
                        "receipt",
                    ],
                    "properties": {
                        "request_id": {"type": "string"},
                        "product": {"const": "PUBLIC_SOURCE_SNAPSHOT"},
                        "url": {"type": "string", "format": "uri"},
                        "retrieved_at": {"type": "string", "format": "date-time"},
                        "http_status": {"type": "integer"},
                        "content_type": {"type": "string"},
                        "content": {"type": "object"},
                        "query": {"type": "object"},
                        "upgrade": {"type": "object"},
                        "provenance": {"type": "object"},
                        "receipt": {"type": "object"},
                    },
                },
            ),
        ),
    ),
    "POST /v1/monitors/source-change": RouteConfig(
        accepts=[
            PaymentOption(
                scheme="exact",
                pay_to=X402_PAY_TO,
                price=X402_SOURCE_WATCH_PRICE,
                network=X402_NETWORK,
            )
        ],
        mime_type="application/json",
        description=(
            "Monitor one public HTTPS text, HTML, JSON, or XML source every six "
            "hours for 30 days. Stores normalized diffs, exposes private polling, "
            "and optionally sends HMAC-signed change webhooks. No account or API key."
        ),
        service_name="Source Change Watch",
        tags=[
            "monitoring",
            "change-detection",
            "webhook",
            "long-running-job",
            "website-monitor",
            "source-diff",
        ],
        icon_url=SERVICE_ICON_URL,
        extensions=get_discovery_extension(
            method="POST",
            input=SOURCE_WATCH_PROBE_PAYLOAD,
            input_schema={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "format": "uri",
                        "pattern": "^https://",
                        "maxLength": 2048,
                        "description": "Public HTTPS source to monitor.",
                    },
                    "label": {"type": "string", "maxLength": 120},
                    "webhook_url": {
                        "type": "string",
                        "format": "uri",
                        "pattern": "^https://",
                        "maxLength": 2048,
                        "description": "Optional public HTTPS webhook for signed change events.",
                    },
                },
                "required": ["url"],
                "additionalProperties": False,
            },
            output=OutputConfig(
                example={
                    "monitor_id": "mon_example",
                    "status": "ACTIVE",
                    "expires_at": "2026-09-09T00:00:00+00:00",
                    "check_interval_seconds": 21600,
                    "access_token": "returned_once_after_payment",
                    "status_url": f"{PUBLIC_BASE_URL.rstrip('/')}/v1/monitors/mon_example",
                    "webhook": {"configured": False},
                },
                schema={
                    "type": "object",
                    "required": [
                        "monitor_id",
                        "status",
                        "expires_at",
                        "check_interval_seconds",
                        "access_token",
                        "status_url",
                    ],
                    "properties": {
                        "monitor_id": {"type": "string"},
                        "status": {"type": "string"},
                        "expires_at": {"type": "string", "format": "date-time"},
                        "check_interval_seconds": {"type": "integer"},
                        "access_token": {"type": "string"},
                        "status_url": {"type": "string", "format": "uri"},
                        "webhook": {"type": "object"},
                    },
                },
            ),
        ),
    ),
    "POST /v1/gtm/form-d-funding-leads": RouteConfig(
        accepts=[
            PaymentOption(
                scheme="exact",
                pay_to=X402_PAY_TO,
                price=X402_FORM_D_PRICE,
                network=X402_NETWORK,
            )
        ],
        mime_type="application/json",
        description=(
            "Find newly filed SEC Form D private-offering signals for autonomous "
            "GTM workflows. Filter by issuer state, industry keyword, and reported "
            "amount sold. Returns official links, company context, related people, "
            "and a cursor. Form D is a notice, not proof of total funding raised."
        ),
        service_name="Official Source Evidence",
        tags=[
            "sec",
            "form-d",
            "funding-signal",
            "sales-trigger",
            "gtm",
            "lead-generation",
            "private-company",
            "cursor",
        ],
        icon_url=SERVICE_ICON_URL,
        extensions=get_discovery_extension(
            method="POST",
            input=FORM_D_PROBE_PAYLOAD,
            input_schema={
                "type": "object",
                "properties": {
                    "since": {
                        "type": "string",
                        "format": "date-time",
                        "description": "UTC baseline within the last 14 days.",
                    },
                    "cursor": {
                        "type": "string",
                        "pattern": "^[0-9]{10}-[0-9]{2}-[0-9]{6}$",
                        "description": "The next_cursor from the previous page.",
                    },
                    "states": {
                        "type": "array",
                        "items": {"type": "string", "pattern": "^[A-Z]{2}$"},
                        "maxItems": 20,
                    },
                    "industry_keywords": {
                        "type": "array",
                        "items": {"type": "string", "maxLength": 64},
                        "maxItems": 10,
                    },
                    "minimum_amount_sold_usd": {
                        "type": ["number", "string"],
                        "minimum": 0,
                    },
                    "include_amendments": {"type": "boolean", "default": False},
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 25,
                        "default": 10,
                    },
                    "max_source_age_seconds": {
                        "type": "integer",
                        "minimum": 60,
                        "maximum": 3600,
                    },
                },
                "required": ["since"],
                "additionalProperties": False,
            },
            output=OutputConfig(
                example={
                    "request_id": "form_d_funding_leads_example",
                    "decision": "FORM_D_FUNDING_SIGNALS_FOUND",
                    "lead_count": 1,
                    "leads": [],
                    "pagination": {"next_cursor": None, "has_more": False},
                    "provenance": {"parser_version": FORM_D_PARSER_VERSION},
                    "receipt": {"algorithm": "Ed25519"},
                },
                schema={
                    "type": "object",
                    "required": [
                        "request_id",
                        "decision",
                        "lead_count",
                        "leads",
                        "pagination",
                        "provenance",
                        "receipt",
                    ],
                    "properties": {
                        "request_id": {"type": "string"},
                        "decision": {
                            "enum": [
                                "FORM_D_FUNDING_SIGNALS_FOUND",
                                "NO_MATCHING_FORM_D_FUNDING_SIGNALS",
                            ]
                        },
                        "lead_count": {"type": "integer"},
                        "leads": {"type": "array"},
                        "pagination": {"type": "object"},
                        "provenance": {"type": "object"},
                        "receipt": {"type": "object"},
                    },
                },
            ),
        ),
    ),
    "POST /v1/ofac/payment-preflight": RouteConfig(
        accepts=[
            PaymentOption(
                scheme="exact",
                pay_to=X402_PAY_TO,
                price=X402_OFAC_PREFLIGHT_PRICE,
                network=X402_NETWORK,
            )
        ],
        mime_type="application/json",
        description=(
            "Before an autonomous agent sends funds, check whether the exact EVM "
            "destination address appears in current official OFAC SDN or "
            "Consolidated data. Returns a compact stop/no-exact-match decision, "
            "freshness, and the premium evidence path; no clearance or legal advice."
        ),
        service_name="Official Source Evidence",
        tags=[
            "ofac",
            "sanctions",
            "wallet-screening",
            "payment-preflight",
            "transaction-gate",
            "crypto-address",
            "decision",
        ],
        icon_url=SERVICE_ICON_URL,
        extensions=get_discovery_extension(
            method="POST",
            input=OFAC_PREFLIGHT_PROBE_PAYLOAD,
            input_schema={
                "type": "object",
                "properties": {
                    "address": {
                        "type": "string",
                        "pattern": "^0x[0-9A-Fa-f]{40}$",
                        "description": "Exact EVM destination wallet address.",
                    },
                    "network": {
                        "type": "string",
                        "pattern": "^eip155:[0-9]+$",
                        "default": "eip155:8453",
                        "description": "CAIP-2 EVM network identifier.",
                    },
                },
                "required": ["address"],
                "additionalProperties": False,
            },
            output=OutputConfig(
                example={
                    "request_id": "ofac_preflight_example",
                    "decision": "NO_EXACT_OFAC_MATCH_FOUND",
                    "match_count": 0,
                    "checked_lists": ["SDN", "CONSOLIDATED"],
                    "checked_at": "2026-08-04T00:00:00Z",
                    "source_age_seconds": 60,
                    "premium_evidence_path": "/v1/ofac/exact-identifier-evidence",
                },
                schema={
                    "type": "object",
                    "required": [
                        "request_id",
                        "decision",
                        "match_count",
                        "checked_at",
                    ],
                    "properties": {
                        "request_id": {"type": "string"},
                        "decision": {
                            "enum": [
                                "STOP_EXACT_OFAC_MATCH",
                                "NO_EXACT_OFAC_MATCH_FOUND",
                            ]
                        },
                        "match_count": {"type": "integer"},
                        "checked_at": {"type": "string", "format": "date-time"},
                        "source_age_seconds": {"type": "integer"},
                    },
                },
            ),
        ),
    ),
    "POST /v1/sec/filing-change-signal": RouteConfig(
        accepts=[
            PaymentOption(
                scheme="exact",
                pay_to=X402_PAY_TO,
                price=X402_SEC_SIGNAL_PRICE,
                network=X402_NETWORK,
            )
        ],
        mime_type="application/json",
        description=(
            "Check whether a company filed a new SEC 8-K, 10-Q, or 10-K after "
            "a timestamp. Accepts a ticker or CIK and returns a compact filing "
            "decision, accession links, and a next-check cursor; no materiality "
            "opinion or investment advice."
        ),
        service_name="Official Source Evidence",
        tags=[
            "sec",
            "edgar",
            "filing-change",
            "ticker",
            "8-k",
            "10-q",
            "10-k",
            "event-signal",
            "decision",
        ],
        icon_url=SERVICE_ICON_URL,
        extensions=get_discovery_extension(
            method="POST",
            input=SEC_SIGNAL_PROBE_PAYLOAD,
            input_schema={
                "type": "object",
                "properties": {
                    "ticker": {
                        "type": "string",
                        "pattern": "^[A-Za-z0-9.-]{1,10}$",
                        "description": "US-listed company ticker, for example AAPL.",
                    },
                    "cik": {
                        "type": "string",
                        "pattern": "^[0-9]{1,10}$",
                        "description": "SEC Central Index Key; use instead of ticker.",
                    },
                    "since": {
                        "type": "string",
                        "format": "date-time",
                        "description": "Return selected forms accepted after this UTC timestamp.",
                    },
                    "forms": {
                        "type": "array",
                        "items": {"enum": ["8-K", "10-Q", "10-K"]},
                        "minItems": 1,
                        "maxItems": 3,
                    },
                    "max_source_age_seconds": {
                        "type": "integer",
                        "minimum": 60,
                        "maximum": 3600,
                    },
                },
                "required": ["ticker", "since"],
                "additionalProperties": False,
            },
            output=OutputConfig(
                example={
                    "request_id": "sec_signal_example",
                    "decision": "NEW_RELEVANT_FILING",
                    "filing_count": 1,
                    "filings": [],
                    "next_since": "2026-08-04T00:00:00Z",
                    "premium_evidence_path": "/v1/sec/filing-trigger-delta",
                },
                schema={
                    "type": "object",
                    "required": [
                        "request_id",
                        "decision",
                        "filing_count",
                        "next_since",
                    ],
                    "properties": {
                        "request_id": {"type": "string"},
                        "decision": {
                            "enum": [
                                "NEW_RELEVANT_FILING",
                                "NO_NEW_RELEVANT_FILING",
                            ]
                        },
                        "filing_count": {"type": "integer"},
                        "next_since": {"type": "string", "format": "date-time"},
                        "filings": {"type": "array"},
                    },
                },
            ),
        ),
    ),
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
            "Produce premium SEC EDGAR evidence for new 8-K, 10-Q, and 10-K "
            "filings after an accession or timestamp. Accepts ticker or CIK and "
            "returns document hashes, selected XBRL fact deltas, freshness, "
            "official URLs, and a signed receipt; no investment advice."
        ),
        service_name="Official Source Evidence",
        tags=[
            "sec",
            "edgar",
            "8-k",
            "10-q",
            "10-k",
            "xbrl",
            "filing-delta",
            "filing-change",
            "source-proof",
        ],
        icon_url=SERVICE_ICON_URL,
        extensions=get_discovery_extension(
            method="POST",
            input=SEC_PROBE_PAYLOAD,
            input_schema={
                "type": "object",
                "properties": {
                    "ticker": {
                        "type": "string",
                        "pattern": "^[A-Za-z0-9.-]{1,10}$",
                        "description": "US-listed company ticker; use instead of CIK.",
                    },
                    "cik": {
                        "type": "string",
                        "pattern": "^[0-9]{1,10}$",
                        "description": "SEC Central Index Key; use instead of ticker.",
                    },
                    "since_accession": {
                        "type": "string",
                        "pattern": "^[0-9]{10}-[0-9]{2}-[0-9]{6}$",
                        "description": "Known SEC accession baseline; use instead of since.",
                    },
                    "since": {
                        "type": "string",
                        "format": "date-time",
                        "description": "UTC filing timestamp baseline; use instead of accession.",
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
                "required": ["ticker", "since"],
                "additionalProperties": False,
            },
            output=OutputConfig(
                example={
                    "request_id": "sec_example",
                    "decision": "NEW_FILING",
                    "filings": [],
                    "selected_fact_deltas": [],
                    "provenance": {"parser_version": SEC_PARSER_VERSION},
                    "receipt": {"algorithm": "Ed25519"},
                },
                schema={
                    "type": "object",
                    "required": [
                        "request_id",
                        "decision",
                        "filings",
                        "provenance",
                        "receipt",
                    ],
                    "properties": {
                        "request_id": {"type": "string"},
                        "decision": {"enum": ["NEW_FILING", "NO_NEW_FILING"]},
                        "filings": {"type": "array"},
                        "selected_fact_deltas": {"type": "array"},
                        "provenance": {"type": "object"},
                        "receipt": {"type": "object"},
                    },
                },
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
            "Run an exact-match OFAC SDN and Consolidated identifier lookup for "
            "crypto wallets, OFAC UIDs, or exact names. Returns versioned U.S. "
            "Treasury source hashes and a signed evidence receipt; no fuzzy "
            "screening or sanctions clearance."
        ),
        service_name="Official Source Evidence",
        tags=[
            "ofac",
            "sanctions",
            "sdn",
            "wallet-screening",
            "exact-match",
            "crypto-address",
            "compliance-data",
            "source-proof",
        ],
        icon_url=SERVICE_ICON_URL,
        extensions=get_discovery_extension(
            method="POST",
            input=OFAC_PROBE_PAYLOAD,
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
                    "receipt": {"algorithm": "Ed25519"},
                },
                schema={
                    "type": "object",
                    "required": [
                        "request_id",
                        "match_status",
                        "matches",
                        "source_versions",
                        "receipt",
                    ],
                    "properties": {
                        "request_id": {"type": "string"},
                        "match_status": {"enum": ["EXACT_MATCH", "NO_EXACT_MATCH"]},
                        "matches": {"type": "array"},
                        "source_versions": {"type": "array"},
                        "provenance": {"type": "object"},
                        "receipt": {"type": "object"},
                    },
                },
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


def request_client_identifier(request: Request) -> str:
    fly_client_ip = request.headers.get("fly-client-ip", "").strip()
    if fly_client_ip:
        try:
            return str(ipaddress.ip_address(fly_client_ip))
        except ValueError:
            pass
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def user_agent_family(user_agent: str | None) -> str:
    value = (user_agent or "").casefold()
    if not value:
        return "unknown"
    if "coinbase" in value or "coinbase-cdp" in value:
        return "coinbase-cdp"
    if "x402-list" in value:
        return "x402-list"
    if "x402scan" in value:
        return "x402scan"
    if "httpx" in value:
        return "python-httpx"
    if "python-requests" in value:
        return "python-requests"
    if "axios" in value:
        return "node-axios"
    if "node" in value or "undici" in value:
        return "node-http"
    if "curl" in value:
        return "curl"
    if "mozilla/" in value:
        return "browser"
    return "other"


def normalized_header_token(value: str | None, max_length: int) -> str | None:
    if not value:
        return None
    normalized = value.strip().casefold()
    if len(normalized) > max_length or not re.fullmatch(
        r"[a-z0-9][a-z0-9._:-]*", normalized
    ):
        return None
    return normalized


def referrer_origin(value: str | None) -> str | None:
    if not value or len(value) > 2048:
        return None
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    try:
        port = parsed.port
    except ValueError:
        return None
    default_port = (parsed.scheme == "http" and port == 80) or (
        parsed.scheme == "https" and port == 443
    )
    port_suffix = f":{port}" if port and not default_port else ""
    return f"{parsed.scheme}://{parsed.hostname.casefold()}{port_suffix}"


class EvidencePrecomputeMiddleware(BaseHTTPMiddleware):
    ROUTES: ClassVar[dict[str, tuple[type[BaseModel], str, str, dict[str, Any]]]] = {
        "/v1/web/source-snapshot": (
            PublicSourceSnapshotRequest,
            "prepare_public_source_snapshot",
            X402_SOURCE_SNAPSHOT_PRICE,
            SOURCE_SNAPSHOT_PROBE_PAYLOAD,
        ),
        "/v1/monitors/source-change": (
            WebMonitorCreateRequest,
            "prepare_web_monitor",
            X402_SOURCE_WATCH_PRICE,
            SOURCE_WATCH_PROBE_PAYLOAD,
        ),
        "/v1/gtm/form-d-funding-leads": (
            FormDFundingLeadsRequest,
            "prepare_form_d_funding_leads",
            X402_FORM_D_PRICE,
            FORM_D_PROBE_PAYLOAD,
        ),
        "/v1/ofac/payment-preflight": (
            OfacPreflightRequest,
            "prepare_ofac_preflight",
            X402_OFAC_PREFLIGHT_PRICE,
            OFAC_PREFLIGHT_PROBE_PAYLOAD,
        ),
        "/v1/sec/filing-change-signal": (
            SecSignalRequest,
            "prepare_sec_signal",
            X402_SEC_SIGNAL_PRICE,
            SEC_SIGNAL_PROBE_PAYLOAD,
        ),
        "/v1/sec/filing-trigger-delta": (
            SecDeltaRequest,
            "prepare_sec",
            X402_SEC_PRICE,
            SEC_PROBE_PAYLOAD,
        ),
        "/v1/ofac/exact-identifier-evidence": (
            OfacExactRequest,
            "prepare_ofac",
            X402_OFAC_PRICE,
            OFAC_PROBE_PAYLOAD,
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
            else request_client_identifier(request)
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
        if PUBLIC_SCHEME in {"http", "https"}:
            request.scope["scheme"] = PUBLIC_SCHEME
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

        model_class, prepare_method_name, quoted_price, probe_payload = route
        started = time.monotonic()
        body = await request.body()
        if not body:
            payload = probe_payload
        else:
            try:
                payload = json.loads(body)
            except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError):
                return JSONResponse(
                    status_code=400,
                    content={
                        "error": {
                            "code": "INVALID_JSON",
                            "detail": "Body must be JSON",
                        }
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
        request.state.evidence_validated = validated
        payment_signature = request.headers.get(
            "payment-signature"
        ) or request.headers.get("x-payment")
        try:
            if request.url.path == "/v1/web/source-snapshot" and not payment_signature:
                prepare_method = self.service.prepare_public_source_snapshot_quote
            else:
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
            client_identifier = request_client_identifier(request)
            raw_user_agent = request.headers.get("user-agent")
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
                client_identifier=client_identifier,
                user_agent=raw_user_agent,
                user_agent_family=user_agent_family(raw_user_agent),
                referrer_origin=referrer_origin(request.headers.get("referer")),
                edge_region=normalized_header_token(
                    request.headers.get("fly-region"), 16
                ),
                proxy_request_id=normalized_header_token(
                    request.headers.get("fly-request-id"), 128
                ),
                discovery_source=normalized_header_token(
                    request.headers.get("x-agent-discovery-source"), 64
                ),
                agent_run_id=request.headers.get("x-agent-run-id"),
                http_status=response.status_code,
            )
        except (sqlite3.Error, OSError):
            # A ledger outage must be visible in monitoring, but must not corrupt a valid receipt.
            response.headers["X-Evidence-Ledger"] = "write-failed"
        return response


app.add_middleware(EvidencePrecomputeMiddleware, service=evidence_service)


class MainnetRevenueCapMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app: Any,
        *,
        service: EvidenceService,
        network: str,
        daily_cap: Decimal,
        route_prices: dict[tuple[str, str], Decimal],
    ) -> None:
        super().__init__(app)
        self.service = service
        self.network = network
        self.daily_cap = daily_cap
        self.route_prices = route_prices
        self.lock = asyncio.Lock()

    async def dispatch(self, request: Request, call_next: Any):
        price = self.route_prices.get((request.method, request.url.path))
        if self.network != "eip155:8453" or self.daily_cap <= 0 or price is None:
            return await call_next(request)

        async with self.lock:
            now = datetime.now(timezone.utc)
            day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            fulfilled = await run_in_threadpool(
                self.service.fulfilled_revenue_since,
                day_start.isoformat(),
                self.network,
            )
            if fulfilled + price > self.daily_cap:
                next_day = day_start + timedelta(days=1)
                retry_after = max(1, int((next_day - now).total_seconds()))
                return JSONResponse(
                    status_code=503,
                    headers={"Retry-After": str(retry_after)},
                    content={
                        "error": {
                            "code": "DAILY_REVENUE_CAP_REACHED",
                            "detail": "Mainnet sales resume at 00:00 UTC",
                        }
                    },
                )
            return await call_next(request)


def price_decimal(value: str) -> Decimal:
    try:
        return Decimal(value.removeprefix("$"))
    except InvalidOperation as exc:
        raise RuntimeError(f"Invalid x402 price: {value}") from exc


app.add_middleware(
    MainnetRevenueCapMiddleware,
    service=evidence_service,
    network=X402_NETWORK,
    daily_cap=X402_DAILY_REVENUE_CAP_USD,
    route_prices={
        ("POST", "/v1/web/source-snapshot"): price_decimal(X402_SOURCE_SNAPSHOT_PRICE),
        ("POST", "/v1/monitors/source-change"): price_decimal(X402_SOURCE_WATCH_PRICE),
        ("POST", "/v1/gtm/form-d-funding-leads"): price_decimal(X402_FORM_D_PRICE),
        ("POST", "/v1/ofac/payment-preflight"): price_decimal(
            X402_OFAC_PREFLIGHT_PRICE
        ),
        ("POST", "/v1/sec/filing-change-signal"): price_decimal(X402_SEC_SIGNAL_PRICE),
        ("POST", "/v1/sec/filing-trigger-delta"): price_decimal(X402_SEC_PRICE),
        ("POST", "/v1/ofac/exact-identifier-evidence"): price_decimal(X402_OFAC_PRICE),
    },
)


class LegacyOriginRetirementMiddleware(BaseHTTPMiddleware):
    PAID_PATHS: ClassVar[set[str]] = {
        route_key.split(" ", 1)[1] for route_key in x402_routes
    }

    async def dispatch(self, request: Request, call_next: Any):
        host = (request.url.hostname or "").casefold()
        if host != LEGACY_FLY_HOST:
            return await call_next(request)
        canonical_url = f"{PUBLIC_BASE_URL.rstrip('/')}{request.url.path}"
        if request.url.query:
            canonical_url = f"{canonical_url}?{request.url.query}"
        if request.url.path in self.PAID_PATHS:
            return JSONResponse(
                status_code=410,
                headers={"Link": f'<{canonical_url}>; rel="canonical"'},
                content={
                    "error": {
                        "code": "LEGACY_ORIGIN_RETIRED",
                        "detail": "Use the canonical Regulavita evidence domain",
                        "canonical_url": canonical_url,
                    }
                },
            )
        return RedirectResponse(canonical_url, status_code=308)


app.add_middleware(LegacyOriginRetirementMiddleware)


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "generated_at": utc_now(),
        "x402": {
            "network": X402_NETWORK,
            "prices": {
                "public_source_snapshot": X402_SOURCE_SNAPSHOT_PRICE,
                "source_change_watch_30_day": X402_SOURCE_WATCH_PRICE,
                "form_d_funding_leads": X402_FORM_D_PRICE,
                "ofac_preflight": X402_OFAC_PREFLIGHT_PRICE,
                "sec_signal": X402_SEC_SIGNAL_PRICE,
                "ofac_exact": X402_OFAC_PRICE,
                "sec_delta": X402_SEC_PRICE,
            },
            "revenue_ready": X402_REVENUE_READY,
            "daily_revenue_cap_usd": (
                f"${X402_DAILY_REVENUE_CAP_USD:.2f}"
                if X402_DAILY_REVENUE_CAP_USD > 0
                else None
            ),
            "mode": (
                "base-mainnet"
                if X402_REVENUE_READY
                else "testnet-configured-recipient"
                if X402_PAY_TO_CONFIGURED
                else "testnet-demo-recipient"
            ),
        },
        "source_watch": monitor_service.public_stats(),
        "marketplace": {"the402": the402_provider.public_status()},
    }


@app.get("/", include_in_schema=False)
def service_home() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html", media_type="text/html")


@app.get("/index.json", include_in_schema=False)
def service_index() -> dict[str, str]:
    base_url = PUBLIC_BASE_URL.rstrip("/")
    return {
        "name": "Agent Evidence and Source Watch API",
        "health": f"{base_url}/health",
        "docs": f"{base_url}/docs",
        "manifest": f"{base_url}/.well-known/agent-service.json",
    }


@app.get("/favicon.ico", include_in_schema=False)
@app.get("/icon.png", include_in_schema=False)
def service_icon() -> FileResponse:
    return FileResponse(
        STATIC_DIR / "official-source-evidence.png", media_type="image/png"
    )


@app.get("/robots.txt", include_in_schema=False)
def robots() -> PlainTextResponse:
    base_url = PUBLIC_BASE_URL.rstrip("/")
    return PlainTextResponse(
        f"User-agent: *\nAllow: /\nSitemap: {base_url}/sitemap.xml\n"
    )


@app.get("/.well-known/x402list.txt", include_in_schema=False)
def x402list_ownership_proof() -> PlainTextResponse:
    token = os.getenv("X402LIST_OWNERSHIP_TOKEN", "").strip()
    if not token:
        raise HTTPException(status_code=404, detail="No active ownership proof")
    return PlainTextResponse(f"{token}\n")


@app.get("/sitemap.xml", include_in_schema=False)
def sitemap() -> Response:
    base_url = PUBLIC_BASE_URL.rstrip("/")
    urls = (
        "/",
        "/docs",
        "/openapi.json",
        "/v1/web/source-snapshot/sample",
        "/v1/monitors/source-change/sample",
        "/v1/gtm/form-d-funding-leads/sample",
        "/v1/sec/sample",
        "/v1/ofac/sample",
    )
    entries = "".join(f"<url><loc>{base_url}{path}</loc></url>" for path in urls)
    return Response(
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{entries}</urlset>',
        media_type="application/xml",
    )


@app.get("/llms.txt", include_in_schema=False)
def llms_txt() -> PlainTextResponse:
    base_url = PUBLIC_BASE_URL.rstrip("/")
    return PlainTextResponse(
        f"""# Agent Evidence and Source Watch API

Long-running monitoring jobs and pay-per-call official-source evidence for autonomous agents. No account or API key.

## Paid endpoints
- POST {base_url}/v1/web/source-snapshot - $0.03 USDC. Fetch one public HTTPS HTML, JSON, XML, or text source as normalized agent-ready text with optional literal excerpts, a content hash, and an Ed25519-signed receipt.
- POST {base_url}/v1/monitors/source-change - $1.00 USDC. Monitor one public HTTPS text, HTML, JSON, or XML source every six hours for 30 days. Private polling and optional HMAC-signed change webhooks are included.
- POST {base_url}/v1/gtm/form-d-funding-leads - $0.05 USDC. Find newly filed SEC Form D private-offering signals for GTM workflows. Filter by issuer state, industry keyword, and reported amount sold; returns official links, related people, and a cursor.
- POST {base_url}/v1/ofac/payment-preflight - $0.01 USDC. Before sending funds, check whether an exact EVM destination address appears in current official OFAC SDN or Consolidated data. Compact decision output; no signed receipt.
- POST {base_url}/v1/sec/filing-change-signal - $0.01 USDC. Check a ticker or CIK for a new 8-K, 10-Q, or 10-K after a timestamp. Compact filing signal and next-check cursor; no signed receipt.
- POST {base_url}/v1/ofac/exact-identifier-evidence - $0.05 USDC. Premium exact OFAC evidence with source versions, hashes, matching records, limitations, and an Ed25519-signed receipt.
- POST {base_url}/v1/sec/filing-trigger-delta - $0.10 USDC. Premium SEC evidence from a ticker or CIK and timestamp or accession, with document hashes, selected deterministic XBRL fact deltas, and an Ed25519-signed receipt.

## Machine-readable contracts
- OpenAPI: {base_url}/openapi.json
- Agent manifest: {base_url}/.well-known/agent-service.json
- Interactive docs: {base_url}/docs
- Public Source Snapshot sample: {base_url}/v1/web/source-snapshot/sample
- Source Watch sample: {base_url}/v1/monitors/source-change/sample
- Form D sample: {base_url}/v1/gtm/form-d-funding-leads/sample
- OFAC sample: {base_url}/v1/ofac/sample
- SEC sample: {base_url}/v1/sec/sample

## Optional attribution headers
- X-Agent-Discovery-Source: a short source token such as coinbase-bazaar, x402-list, or direct
- X-Agent-Run-Id: a stable run identifier; stored only as a keyed HMAC and useful for repeat-run measurement

## Boundaries
Source Watch supports public HTTPS sources only and does not render JavaScript or access authenticated pages. Exact source evidence and factual GTM signals only. Form D is a notice, not proof of total funding raised. No fuzzy sanctions screening, sanctions clearance, transaction authorization, materiality opinion, investment advice, or legal advice.
"""
    )


@app.get("/.well-known/agent-service.json")
def agent_manifest() -> dict[str, Any]:
    base_url = os.getenv("AUTONOMOUS_API_BASE_URL", "http://localhost:8765").rstrip("/")
    return {
        "name": "Agent Evidence and Source Watch API",
        "description": (
            "One-shot public-source extraction and long-running source-change "
            "monitors plus pay-per-call SEC Form D GTM signals, OFAC payment "
            "preflight, SEC filing decisions, and signed receipts."
        ),
        "contact": "joshua@regulavita.com",
        "icon_url": SERVICE_ICON_URL,
        "auth": {"type": "none", "payment": "x402-v2"},
        "payment": {
            "protocol": "x402-v2",
            "network": X402_NETWORK,
            "prices": {
                "/v1/web/source-snapshot": X402_SOURCE_SNAPSHOT_PRICE,
                "/v1/monitors/source-change": X402_SOURCE_WATCH_PRICE,
                "/v1/gtm/form-d-funding-leads": X402_FORM_D_PRICE,
                "/v1/ofac/payment-preflight": X402_OFAC_PREFLIGHT_PRICE,
                "/v1/sec/filing-change-signal": X402_SEC_SIGNAL_PRICE,
                "/v1/sec/filing-trigger-delta": X402_SEC_PRICE,
                "/v1/ofac/exact-identifier-evidence": X402_OFAC_PRICE,
            },
            "revenue_ready": X402_REVENUE_READY,
            "daily_revenue_cap_usd": (
                f"${X402_DAILY_REVENUE_CAP_USD:.2f}"
                if X402_DAILY_REVENUE_CAP_USD > 0
                else None
            ),
        },
        "openapi_url": f"{base_url}/openapi.json",
        "llms_url": f"{base_url}/llms.txt",
        "sample_endpoints": [
            f"{base_url}/v1/web/source-snapshot/sample",
            f"{base_url}/v1/monitors/source-change/sample",
            f"{base_url}/v1/gtm/form-d-funding-leads/sample",
            f"{base_url}/v1/sec/sample",
            f"{base_url}/v1/ofac/sample",
        ],
        "agent_paid_endpoints": [
            f"{base_url}/v1/web/source-snapshot",
            f"{base_url}/v1/monitors/source-change",
            f"{base_url}/v1/gtm/form-d-funding-leads",
            f"{base_url}/v1/ofac/payment-preflight",
            f"{base_url}/v1/sec/filing-change-signal",
            f"{base_url}/v1/sec/filing-trigger-delta",
            f"{base_url}/v1/ofac/exact-identifier-evidence",
        ],
        "status_endpoint": f"{base_url}/v1/experiments/status",
        "source_watch_stats_endpoint": f"{base_url}/v1/monitors/stats",
        "boundaries": [
            "Source Watch supports public HTTPS text-like sources only.",
            "Form D is an issuer-filed notice, not proof of the total funding raised.",
            "No investment advice or materiality opinion.",
            "No sanctions clearance, transaction authorization, or fuzzy screening.",
        ],
    }


@app.get("/v1/web/source-snapshot/sample")
def public_source_snapshot_sample() -> dict[str, Any]:
    return {
        "sample_type": "static_contract_fixture",
        "endpoint": {
            "path": "/v1/web/source-snapshot",
            "price": X402_SOURCE_SNAPSHOT_PRICE,
            "payment": "x402 v2 USDC on Base",
        },
        "request": SOURCE_SNAPSHOT_PROBE_PAYLOAD,
        "service": {
            "supported_content": ["HTML", "JSON", "plain text", "XML"],
            "maximum_response_bytes": 1000000,
            "maximum_returned_characters": 50000,
            "query_matching": "literal case-insensitive excerpts",
        },
        "response_shape": {
            "content": {
                "normalized_text": "Source text...",
                "content_sha256": f"sha256:{'0' * 64}",
                "truncated": False,
            },
            "query": {"literal_match_count_returned": 1, "excerpts": []},
            "upgrade": {"path": "/v1/monitors/source-change", "price": "$1.00"},
            "receipt": {"algorithm": "Ed25519"},
        },
        "limitations": [
            "Public HTTPS sources only.",
            "No JavaScript rendering, login, cookies, or redirect following.",
        ],
    }


@app.get("/v1/gtm/form-d-funding-leads/sample")
def form_d_funding_leads_sample() -> dict[str, Any]:
    return {
        "sample_type": "static_contract_fixture",
        "as_of": "2026-08-04",
        "live_source_result": False,
        "endpoint": {
            "path": "/v1/gtm/form-d-funding-leads",
            "price": X402_FORM_D_PRICE,
            "payment": "x402 v2 USDC on Base",
        },
        "request": {
            "since": "2026-08-03T00:00:00Z",
            "states": ["CA", "NY"],
            "industry_keywords": ["technology", "health care"],
            "minimum_amount_sold_usd": "1000000",
            "include_amendments": False,
            "limit": 10,
        },
        "response_shape": {
            "decision": (
                "FORM_D_FUNDING_SIGNALS_FOUND | NO_MATCHING_FORM_D_FUNDING_SIGNALS"
            ),
            "leads": [
                {
                    "trigger": "NEW_SEC_FORM_D_FILING",
                    "issuer": {"cik": "0000000000", "name": "Example Issuer"},
                    "industry": "Technology",
                    "funding_signal": {
                        "basis": "FORM_D_REPORTED_EXEMPT_OFFERING",
                        "amount_sold_usd": "2500000",
                        "date_of_first_sale": "2026-08-01",
                    },
                    "related_people": [],
                    "official_source_urls": {},
                }
            ],
            "pagination": {"next_cursor": None, "has_more": False},
            "provenance": {"parser_version": FORM_D_PARSER_VERSION},
            "receipt": {"algorithm": "Ed25519"},
        },
        "limitations": [
            "Form D is an issuer-filed notice, not proof that the total offering amount was raised."
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
            "ticker": "AAPL",
            "since": "2026-07-30T00:00:00Z",
            "forms": ["8-K", "10-Q", "10-K"],
            "rules": ["FORM:8-K:ITEM:2.02", "XBRL:us-gaap:Revenues"],
        },
        "decision_endpoint": {
            "path": "/v1/sec/filing-change-signal",
            "price": X402_SEC_SIGNAL_PRICE,
            "response": "NEW_RELEVANT_FILING | NO_NEW_RELEVANT_FILING",
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
        "decision_request": {
            "address": "0x0000000000000000000000000000000000000000",
            "network": "eip155:8453",
        },
        "decision_endpoint": {
            "path": "/v1/ofac/payment-preflight",
            "price": X402_OFAC_PREFLIGHT_PRICE,
            "response": "STOP_EXACT_OFAC_MATCH | NO_EXACT_OFAC_MATCH_FOUND",
        },
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


@app.get("/v1/monitors/source-change/sample")
def source_change_watch_sample() -> dict[str, Any]:
    return {
        "sample_type": "static_contract_fixture",
        "endpoint": {
            "path": "/v1/monitors/source-change",
            "price": X402_SOURCE_WATCH_PRICE,
            "payment": "x402 v2 USDC on Base",
        },
        "request": SOURCE_WATCH_PROBE_PAYLOAD,
        "service": {
            "duration_days": 30,
            "check_interval_seconds": 21600,
            "supported_content": ["HTML", "JSON", "plain text", "XML"],
            "maximum_response_bytes": 1000000,
            "delivery": ["authenticated polling", "optional signed webhook"],
        },
        "response_shape": {
            "monitor_id": "mon_example",
            "status": "ACTIVE",
            "access_token": "returned after successful payment",
            "status_url": f"{PUBLIC_BASE_URL.rstrip('/')}/v1/monitors/mon_example",
        },
        "limitations": [
            "Public HTTPS sources only.",
            "No JavaScript rendering, login, cookies, or redirect following.",
        ],
    }


def _monitor_access_token(authorization: str | None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Bearer access token is required")
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=401, detail="Bearer access token is required")
    return token


@app.post("/v1/monitors/source-change")
def create_source_change_watch(
    request: Request,
    payload: WebMonitorCreateRequest | None = None,
    payment_signature: str | None = Header(default=None, alias="Payment-Signature"),
    x_payment: str | None = Header(default=None, alias="X-Payment"),
) -> dict[str, Any]:
    del payload
    prepared: PreparedResult | None = getattr(request.state, "evidence_prepared", None)
    validated: WebMonitorCreateRequest | None = getattr(
        request.state, "evidence_validated", None
    )
    signature = payment_signature or x_payment
    if prepared is None or validated is None or not signature:
        raise HTTPException(
            status_code=503, detail="Paid monitor activation unavailable"
        )
    try:
        return monitor_service.activate(
            request_id=prepared.request_id,
            payment_signature=signature,
            url=validated.url,
            label=validated.label,
            webhook_url=validated.webhook_url,
            base_url=PUBLIC_BASE_URL,
        )
    except MonitorError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@app.get("/v1/monitors/stats", include_in_schema=False)
def source_change_watch_stats() -> dict[str, Any]:
    return monitor_service.public_stats()


@app.post("/v1/integrations/the402/webhook", include_in_schema=False)
async def the402_webhook(
    request: Request,
    platform_secret: str | None = Header(default=None, alias="X-Platform-Secret"),
    webhook_signature: str | None = Header(default=None, alias="X-Webhook-Signature"),
    webhook_timestamp: str | None = Header(default=None, alias="X-Webhook-Timestamp"),
) -> dict[str, Any]:
    raw_body = await request.body()
    try:
        return the402_provider.accept_webhook(
            raw_body,
            platform_secret=platform_secret,
            signature=webhook_signature,
            timestamp=webhook_timestamp,
        )
    except MarketplaceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@app.get("/v1/integrations/the402/status", include_in_schema=False)
def the402_integration_status() -> dict[str, Any]:
    return the402_provider.public_status()


@app.get("/v1/monitors/{monitor_id}")
def get_source_change_watch(
    monitor_id: str,
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> dict[str, Any]:
    try:
        return monitor_service.status(monitor_id, _monitor_access_token(authorization))
    except MonitorError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@app.delete("/v1/monitors/{monitor_id}")
def cancel_source_change_watch(
    monitor_id: str,
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> dict[str, Any]:
    try:
        return monitor_service.cancel(monitor_id, _monitor_access_token(authorization))
    except MonitorError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@app.post("/v1/gtm/form-d-funding-leads")
def form_d_funding_leads(
    request: Request,
    payload: FormDFundingLeadsRequest | None = None,
) -> dict[str, Any]:
    del payload
    prepared: PreparedResult | None = getattr(request.state, "evidence_prepared", None)
    if prepared is None:
        raise HTTPException(status_code=503, detail="Prepared result unavailable")
    return prepared.result


@app.post("/v1/web/source-snapshot")
def public_source_snapshot(
    request: Request,
    payload: PublicSourceSnapshotRequest | None = None,
) -> dict[str, Any]:
    del payload
    prepared: PreparedResult | None = getattr(request.state, "evidence_prepared", None)
    if prepared is None:
        raise HTTPException(status_code=503, detail="Prepared result unavailable")
    return prepared.result


@app.post("/v1/ofac/payment-preflight")
def ofac_payment_preflight(
    request: Request,
    payload: OfacPreflightRequest | None = None,
) -> dict[str, Any]:
    del payload
    prepared: PreparedResult | None = getattr(request.state, "evidence_prepared", None)
    if prepared is None:
        raise HTTPException(status_code=503, detail="Prepared result unavailable")
    return prepared.result


@app.post("/v1/sec/filing-change-signal")
def sec_filing_change_signal(
    request: Request,
    payload: SecSignalRequest | None = None,
) -> dict[str, Any]:
    del payload
    prepared: PreparedResult | None = getattr(request.state, "evidence_prepared", None)
    if prepared is None:
        raise HTTPException(status_code=503, detail="Prepared result unavailable")
    return prepared.result


@app.post("/v1/sec/filing-trigger-delta")
def sec_filing_trigger_delta(
    request: Request, payload: SecDeltaRequest | None = None
) -> dict[str, Any]:
    del payload
    prepared: PreparedResult | None = getattr(request.state, "evidence_prepared", None)
    if prepared is None:
        raise HTTPException(status_code=503, detail="Prepared result unavailable")
    return prepared.result


@app.post("/v1/ofac/exact-identifier-evidence")
def ofac_exact_identifier_evidence(
    request: Request,
    payload: OfacExactRequest | None = None,
) -> dict[str, Any]:
    del payload
    prepared: PreparedResult | None = getattr(request.state, "evidence_prepared", None)
    if prepared is None:
        raise HTTPException(status_code=503, detail="Prepared result unavailable")
    return prepared.result


@app.get("/v1/evidence/replay/{request_id}", include_in_schema=False)
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
    return evidence_service.experiment_status(CONVERSION_EXPERIMENT_START_UTC or None)


@app.get("/v1/summary")
def summary() -> dict[str, Any]:
    return {
        "service": "Agent Evidence and Source Watch API",
        "products": [
            "/v1/monitors/source-change",
            "/v1/gtm/form-d-funding-leads",
            "/v1/ofac/payment-preflight",
            "/v1/sec/filing-change-signal",
            "/v1/sec/filing-trigger-delta",
            "/v1/ofac/exact-identifier-evidence",
        ],
        "experiment": evidence_service.experiment_status(
            CONVERSION_EXPERIMENT_START_UTC or None
        ),
        "source_watch": monitor_service.public_stats(),
    }


def retired_wedge(detail: str) -> JSONResponse:
    return JSONResponse(
        status_code=410,
        content={"error": "RETIRED_WEDGE", "detail": detail},
    )


@app.get("/v1/public/pfas/sample", include_in_schema=False)
def public_pfas_sample() -> JSONResponse:
    return retired_wedge(
        "PFAS lead scoring is not part of the autonomous evidence experiment."
    )


@app.get("/v1/public/grid/sample", include_in_schema=False)
def public_grid_sample() -> JSONResponse:
    return retired_wedge(
        "Grid lead scoring is not part of the autonomous evidence experiment."
    )


@app.get("/v1/pfas/leads", include_in_schema=False)
def pfas_leads() -> JSONResponse:
    return retired_wedge(
        "PFAS lead scoring is not part of the autonomous evidence experiment."
    )


@app.get("/v1/grid/projects", include_in_schema=False)
def grid_projects() -> JSONResponse:
    return retired_wedge(
        "Grid lead scoring is not part of the autonomous evidence experiment."
    )


@app.get("/v1/x402/pfas/leads", include_in_schema=False)
def retired_x402_pfas_leads() -> JSONResponse:
    return retired_wedge(
        "PFAS lead scoring is not part of the autonomous evidence experiment."
    )


@app.get("/v1/x402/grid/projects", include_in_schema=False)
def retired_x402_grid_projects() -> JSONResponse:
    return retired_wedge(
        "Grid lead scoring is not part of the autonomous evidence experiment."
    )


def custom_openapi() -> dict[str, Any]:
    if app.openapi_schema:
        return app.openapi_schema

    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )
    schema["servers"] = [{"url": PUBLIC_BASE_URL.rstrip("/")}]
    schema["info"]["contact"] = {
        "name": "Regulavita",
        "email": "joshua@regulavita.com",
    }
    schema.setdefault("components", {}).setdefault("securitySchemes", {})[
        "MonitorBearer"
    ] = {"type": "http", "scheme": "bearer"}
    schema["info"]["x-guidance"] = (
        "Use POST /v1/web/source-snapshot for a one-shot normalized public-source "
        "extract with a content hash and signed receipt. Upgrade to POST "
        "/v1/monitors/source-change to buy a 30-day, six-hour-cadence "
        "monitor for one public HTTPS source, with private polling and optional "
        "signed webhooks. Use POST /v1/gtm/form-d-funding-leads for cursor-based private-company "
        "sales triggers derived from official SEC Form D notices. Use POST "
        "/v1/ofac/payment-preflight before sending funds to an EVM "
        "address. Use POST /v1/sec/filing-change-signal to check a ticker since "
        "a timestamp. These compact decision routes cost $0.01. Buy the matching "
        "premium route only when document/source hashes, detailed records, XBRL "
        "deltas, or an Ed25519-signed receipt are required. All paid routes accept "
        "automatic x402 v2 USDC payment on Base mainnet without an account or API "
        "key. An empty POST is a supported monitoring probe."
    )

    paid_operations: dict[str, dict[str, Any]] = {
        "/v1/web/source-snapshot": {
            "price": f"{price_decimal(X402_SOURCE_SNAPSHOT_PRICE):.6f}",
            "example": SOURCE_SNAPSHOT_PROBE_PAYLOAD,
            "description": (
                "One-shot public HTTPS content extraction with normalized text, "
                "optional literal excerpts, a content hash, and a signed receipt."
            ),
        },
        "/v1/monitors/source-change": {
            "price": f"{price_decimal(X402_SOURCE_WATCH_PRICE):.6f}",
            "example": SOURCE_WATCH_PROBE_PAYLOAD,
            "description": (
                "Create a 30-day source-change monitor for one public HTTPS text, "
                "HTML, JSON, or XML source. Checks every six hours and includes "
                "private polling plus optional HMAC-signed webhooks."
            ),
        },
        "/v1/gtm/form-d-funding-leads": {
            "price": f"{price_decimal(X402_FORM_D_PRICE):.6f}",
            "example": FORM_D_PROBE_PAYLOAD,
            "description": (
                "Cursor-based SEC Form D private-offering signals for autonomous "
                "GTM workflows, with reported amount sold, related people, and "
                "official filing links."
            ),
        },
        "/v1/ofac/payment-preflight": {
            "price": f"{price_decimal(X402_OFAC_PREFLIGHT_PRICE):.6f}",
            "example": OFAC_PREFLIGHT_PROBE_PAYLOAD,
            "description": (
                "Compact exact OFAC address decision for an autonomous payment "
                "preflight. The result is not a sanctions clearance."
            ),
        },
        "/v1/sec/filing-change-signal": {
            "price": f"{price_decimal(X402_SEC_SIGNAL_PRICE):.6f}",
            "example": SEC_SIGNAL_PROBE_PAYLOAD,
            "description": (
                "Compact SEC 8-K, 10-Q, or 10-K filing-presence signal from a "
                "ticker or CIK and UTC timestamp."
            ),
        },
        "/v1/sec/filing-trigger-delta": {
            "price": f"{price_decimal(X402_SEC_PRICE):.6f}",
            "example": SEC_PROBE_PAYLOAD,
            "description": (
                "Premium SEC filing evidence with official document hashes, "
                "selected deterministic XBRL deltas, and a signed receipt."
            ),
        },
        "/v1/ofac/exact-identifier-evidence": {
            "price": f"{price_decimal(X402_OFAC_PRICE):.6f}",
            "example": OFAC_PROBE_PAYLOAD,
            "description": (
                "Premium exact OFAC identifier evidence with source hashes, "
                "matching records, and a signed receipt."
            ),
        },
    }
    sec_properties = schema["components"]["schemas"]["SecDeltaRequest"]["properties"]
    sec_properties["ticker"]["example"] = "AAPL"
    sec_properties["cik"]["example"] = "0000320193"
    sec_properties["since"]["example"] = "2026-07-30T00:00:00Z"
    sec_properties["since_accession"]["example"] = "0000320193-26-000018"
    form_d_properties = schema["components"]["schemas"]["FormDFundingLeadsRequest"][
        "properties"
    ]
    form_d_properties["since"]["example"] = "2026-08-03T00:00:00Z"
    form_d_properties["states"]["example"] = ["CA", "NY"]
    form_d_properties["industry_keywords"]["example"] = [
        "technology",
        "health care",
    ]
    form_d_properties["minimum_amount_sold_usd"]["example"] = "1000000"
    for path, path_item in schema.get("paths", {}).items():
        for method, operation in path_item.items():
            if method not in {"get", "post", "put", "patch", "delete", "head"}:
                continue
            paid = paid_operations.get(path) if method == "post" else None
            if paid:
                operation.pop("security", None)
                operation["description"] = paid["description"]
                operation["x-payment-info"] = {
                    "price": {
                        "mode": "fixed",
                        "currency": "USD",
                        "amount": paid["price"],
                    },
                    "protocols": [{"x402": {}}],
                }
                operation.setdefault("responses", {})["402"] = {
                    "description": "Payment Required"
                }
                operation["x-monitoring-probe"] = {
                    "method": "POST",
                    "body": "omitted",
                    "expected_status": 402,
                }
                content = (
                    operation.setdefault("requestBody", {})
                    .setdefault("content", {})
                    .setdefault("application/json", {})
                )
                content["example"] = paid["example"]
            elif path == "/v1/monitors/{monitor_id}" and method in {"get", "delete"}:
                operation["security"] = [{"MonitorBearer": []}]
            else:
                operation["security"] = []

    app.openapi_schema = schema
    return schema


app.openapi = custom_openapi
