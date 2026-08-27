from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import threading
import uuid
from collections.abc import Callable, Mapping
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

MCP_REQUEST_CONTEXT: ContextVar[dict[str, str] | None] = ContextVar(
    "mcp_request_context", default=None
)


def user_agent_family(user_agent: str | None) -> str:
    value = (user_agent or "").casefold()
    if not value:
        return "unknown"
    if "regulavita-owner-smoke" in value:
        return "owner-smoke"
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


class MCPRequestContextMiddleware:
    """Attach privacy-preserving client context to MCP tool telemetry."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        headers = {
            key.decode("latin-1").casefold(): value.decode("latin-1")
            for key, value in scope.get("headers", [])
        }
        client = headers.get("fly-client-ip", "").strip()
        if not client:
            forwarded = headers.get("x-forwarded-for", "")
            client = forwarded.split(",", 1)[0].strip()
        if not client:
            peer = scope.get("client")
            client = str(peer[0]) if peer else "unknown"
        user_agent = headers.get("user-agent", "")
        token = MCP_REQUEST_CONTEXT.set(
            {
                "client_identifier": client,
                "user_agent": user_agent,
                "user_agent_family": user_agent_family(user_agent),
            }
        )
        try:
            await self.app(scope, receive, send)
        finally:
            MCP_REQUEST_CONTEXT.reset(token)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class MCPProduct:
    key: str
    path: str
    price: str
    description: str
    example: dict[str, Any]


class MCPDemandStore:
    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self.lock = threading.Lock()
        self.analytics_hmac_key = self._load_analytics_key()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    def _initialize(self) -> None:
        with self.lock, self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS mcp_capability_requests (
                    request_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    request_hash TEXT NOT NULL UNIQUE,
                    job_to_be_done TEXT NOT NULL,
                    current_alternative TEXT,
                    decision_criteria_json TEXT NOT NULL,
                    max_budget_usd TEXT,
                    max_latency_seconds INTEGER,
                    required_output_fields_json TEXT NOT NULL,
                    contact_uri TEXT,
                    status TEXT NOT NULL DEFAULT 'NEW'
                );

                CREATE TABLE IF NOT EXISTS mcp_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    product TEXT,
                    request_hash TEXT,
                    http_status INTEGER,
                    failure_code TEXT,
                    client_hmac TEXT,
                    user_agent_hmac TEXT,
                    user_agent_family TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_mcp_events_created_at
                    ON mcp_events(created_at);
                CREATE INDEX IF NOT EXISTS idx_mcp_events_type
                    ON mcp_events(event_type, product);
                """
            )
            existing_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(mcp_events)"
                ).fetchall()
            }
            telemetry_columns = {
                "http_status": "INTEGER",
                "failure_code": "TEXT",
                "client_hmac": "TEXT",
                "user_agent_hmac": "TEXT",
                "user_agent_family": "TEXT",
            }
            for column, column_type in telemetry_columns.items():
                if column not in existing_columns:
                    connection.execute(
                        f"ALTER TABLE mcp_events ADD COLUMN {column} {column_type}"
                    )

    def _load_analytics_key(self) -> bytes:
        configured = os.getenv("AUTONOMOUS_ANALYTICS_HMAC_KEY", "").encode("utf-8")
        if configured:
            return hashlib.sha256(configured).digest()
        key_path = self.db_path.parent / "analytics_hmac.key"
        if key_path.exists():
            return key_path.read_bytes()
        key = secrets.token_bytes(32)
        key_path.parent.mkdir(parents=True, exist_ok=True)
        key_path.write_bytes(key)
        key_path.chmod(0o600)
        return key

    def _analytics_hmac(self, value: str | None) -> str | None:
        if not value:
            return None
        return hmac.new(
            self.analytics_hmac_key, value.encode("utf-8"), hashlib.sha256
        ).hexdigest()

    def record_event(
        self,
        tool_name: str,
        event_type: str,
        *,
        product: str | None = None,
        request_hash: str | None = None,
        http_status: int | None = None,
        failure_code: str | None = None,
    ) -> None:
        request_context = MCP_REQUEST_CONTEXT.get() or {}
        with self.lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO mcp_events (
                    created_at, tool_name, event_type, product, request_hash,
                    http_status, failure_code, client_hmac, user_agent_hmac,
                    user_agent_family
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    utc_now(),
                    tool_name,
                    event_type,
                    product,
                    request_hash,
                    http_status,
                    failure_code,
                    self._analytics_hmac(request_context.get("client_identifier")),
                    self._analytics_hmac(request_context.get("user_agent")),
                    request_context.get("user_agent_family"),
                ),
            )

    def submit_capability(self, payload: dict[str, Any]) -> tuple[str, bool]:
        request_hash = canonical_hash(payload)
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        with self.lock, self._connect() as connection:
            existing = connection.execute(
                """
                SELECT request_id FROM mcp_capability_requests
                WHERE request_hash = ?
                """,
                (request_hash,),
            ).fetchone()
            if existing:
                return str(existing["request_id"]), True

            recent = connection.execute(
                """
                SELECT COUNT(*) AS count FROM mcp_capability_requests
                WHERE created_at >= ?
                """,
                (cutoff,),
            ).fetchone()
            if recent and int(recent["count"]) >= 30:
                raise ValueError(
                    "Capability request rate limit reached; retry in 60 seconds"
                )

            request_id = f"cap_{uuid.uuid4().hex[:24]}"
            connection.execute(
                """
                INSERT INTO mcp_capability_requests (
                    request_id, created_at, request_hash, job_to_be_done,
                    current_alternative, decision_criteria_json, max_budget_usd,
                    max_latency_seconds, required_output_fields_json, contact_uri
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request_id,
                    utc_now(),
                    request_hash,
                    payload["job_to_be_done"],
                    payload.get("current_alternative"),
                    json.dumps(
                        payload.get("decision_criteria", []), separators=(",", ":")
                    ),
                    payload.get("max_budget_usd"),
                    payload.get("max_latency_seconds"),
                    json.dumps(
                        payload.get("required_output_fields", []),
                        separators=(",", ":"),
                    ),
                    payload.get("contact_uri"),
                ),
            )
        return request_id, False

    def public_stats(self) -> dict[str, Any]:
        with self.lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT event_type, product, COUNT(*) AS count
                FROM mcp_events
                GROUP BY event_type, product
                ORDER BY event_type, product
                """
            ).fetchall()
            capability_count = connection.execute(
                "SELECT COUNT(*) AS count FROM mcp_capability_requests"
            ).fetchone()
            failures = connection.execute(
                """
                SELECT product, http_status, failure_code, COUNT(*) AS count
                FROM mcp_events
                WHERE event_type IN ('PREPARE_FAILED', 'PAID_FAILED')
                GROUP BY product, http_status, failure_code
                ORDER BY count DESC, product
                """
            ).fetchall()
            clients = connection.execute(
                """
                SELECT COUNT(DISTINCT client_hmac) AS count
                FROM mcp_events
                WHERE client_hmac IS NOT NULL
                """
            ).fetchone()
        return {
            "capability_requests": int(capability_count["count"]),
            "attributed_client_clusters": int(clients["count"]),
            "events": [
                {
                    "event_type": row["event_type"],
                    "product": row["product"],
                    "count": int(row["count"]),
                }
                for row in rows
            ],
            "failures": [
                {
                    "product": row["product"],
                    "http_status": row["http_status"],
                    "failure_code": row["failure_code"],
                    "count": int(row["count"]),
                }
                for row in failures
            ],
        }


class EvidenceMCPService:
    def __init__(
        self,
        *,
        db_path: Path | str,
        public_base_url: str,
        status_provider: Callable[[], dict[str, Any]],
        products: Mapping[str, MCPProduct],
    ) -> None:
        self.public_base_url = public_base_url.rstrip("/")
        self.status_provider = status_provider
        self.products = dict(products)
        self.store = MCPDemandStore(db_path)
        self.server = MCPServer(
            "official-source-evidence",
            title="Official Source Evidence",
            description=(
                "Accountless x402 tools for public-source extraction, SEC Form D "
                "signals, OFAC payment preflight, and agent capability requests."
            ),
            instructions=(
                "Call get_service_status and get_quote before spending. For a paid "
                "job, call get_example_payment for a guaranteed valid example or the "
                "matching get_*_payment tool for custom input, have an x402 wallet "
                "sign the returned PAYMENT-REQUIRED value, then call "
                "submit_x402_payment. "
                "Never put a private key or seed phrase in any tool argument."
            ),
            website_url=self.public_base_url,
            version="1.1.0",
        )
        self._register_tools()

    def transport_security(self) -> TransportSecuritySettings:
        host = urlparse(self.public_base_url).hostname
        allowed_hosts = ["127.0.0.1:*", "localhost:*", "testserver"]
        allowed_origins = ["http://127.0.0.1:*", "http://localhost:*"]
        if host:
            allowed_hosts.extend([host, f"{host}:*"])
            allowed_origins.append(f"https://{host}")
        return TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=allowed_hosts,
            allowed_origins=allowed_origins,
        )

    def _product(self, product: str) -> MCPProduct:
        resolved = self.products.get(product)
        if not resolved:
            choices = ", ".join(sorted(self.products))
            raise ValueError(f"Unknown product. Choose one of: {choices}")
        return resolved

    async def _post_paid_route(
        self,
        product: MCPProduct,
        arguments: dict[str, Any],
        *,
        payment_signature: str | None = None,
    ) -> httpx.Response:
        headers = {
            "User-Agent": "Regulavita-MCP/1.0",
            "X-Agent-Discovery-Source": "official-mcp",
        }
        if payment_signature:
            headers["Payment-Signature"] = payment_signature
        async with httpx.AsyncClient(timeout=90, follow_redirects=False) as client:
            return await client.post(
                f"{self.public_base_url}{product.path}",
                json=arguments,
                headers=headers,
            )

    @staticmethod
    def _response_json(response: httpx.Response) -> Any:
        try:
            return response.json()
        except (ValueError, json.JSONDecodeError):
            return {"detail": response.text[:1000]}

    @staticmethod
    def _failure_code(payload: Any, status_code: int) -> str:
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict) and isinstance(error.get("code"), str):
                return error["code"][:128]
            detail = payload.get("detail")
            if isinstance(detail, dict) and isinstance(detail.get("code"), str):
                return detail["code"][:128]
        return f"HTTP_{status_code}"

    async def _prepare_payment(
        self,
        product_name: str,
        arguments: dict[str, Any],
        *,
        tool_name: str | None = None,
    ) -> dict[str, Any]:
        product = self._product(product_name)
        request_hash = canonical_hash(arguments)
        response = await self._post_paid_route(product, arguments)
        response_payload = self._response_json(response)
        self.store.record_event(
            tool_name or f"get_{product.key}_payment",
            "PAYMENT_CHALLENGE" if response.status_code == 402 else "PREPARE_FAILED",
            product=product.key,
            request_hash=request_hash,
            http_status=response.status_code,
            failure_code=(
                None
                if response.status_code == 402
                else self._failure_code(response_payload, response.status_code)
            ),
        )
        if response.status_code != 402:
            return {
                "status": "failed",
                "http_status": response.status_code,
                "failure_code": self._failure_code(
                    response_payload, response.status_code
                ),
                "error": response_payload,
                "recovery": (
                    "Call get_quote for a valid custom-input example or call "
                    "get_example_payment for a guaranteed valid challenge."
                ),
            }
        return {
            "status": "payment_required",
            "protocol": "x402-v2",
            "product": product.key,
            "price": product.price,
            "currency": "USDC",
            "network": "eip155:8453",
            "payment_required": response.headers.get("payment-required"),
            "payment_header_name": "PAYMENT-SIGNATURE",
            "request_id": response.headers.get("x-evidence-request-id"),
            "request_hash": response.headers.get("x-evidence-request-hash"),
            "expires_note": "Decode PAYMENT-REQUIRED and treat its live terms as authoritative.",
            "next_step": (
                "Sign the challenge with an x402-compatible Base wallet, then call "
                "submit_x402_payment with the same product and arguments."
            ),
        }

    def _register_tools(self) -> None:
        server = self.server

        @server.tool(
            description=(
                "Check live readiness, x402 network and prices before spending. "
                "This free call has no side effects."
            )
        )
        def get_service_status() -> dict[str, Any]:
            self.store.record_event("get_service_status", "FREE_STATUS")
            health = self.status_provider()
            return {
                "service": "Official Source Evidence",
                "status": "operational" if health.get("ok") else "degraded",
                "generated_at": health.get("generated_at"),
                "mcp_endpoint": f"{self.public_base_url}/mcp/",
                "x402": health.get("x402", {}),
                "products": {
                    key: {
                        "price": product.price,
                        "path": product.path,
                        "description": product.description,
                    }
                    for key, product in self.products.items()
                },
            }

        @server.tool(
            description=(
                "Return a free, side-effect-free quote and exact input example for "
                "one paid product. Does not create a payment challenge."
            )
        )
        def get_quote(product: str) -> dict[str, Any]:
            resolved = self._product(product)
            self.store.record_event("get_quote", "FREE_QUOTE", product=resolved.key)
            return {
                "product": resolved.key,
                "description": resolved.description,
                "price": resolved.price,
                "currency": "USDC",
                "network": "eip155:8453",
                "payment_protocol": "x402-v2",
                "account_required": False,
                "api_key_required": False,
                "example_arguments": resolved.example,
                "authoritative_terms": (
                    "The live PAYMENT-REQUIRED challenge returned immediately before "
                    "payment overrides cached price metadata."
                ),
            }

        @server.tool(
            description=(
                "Create a live x402 challenge using this service's known-valid "
                "published example for one product. This never moves funds. The "
                "returned arguments can be passed unchanged to submit_x402_payment."
            )
        )
        async def get_example_payment(product: str) -> dict[str, Any]:
            resolved = self._product(product)
            arguments = dict(resolved.example)
            result = await self._prepare_payment(
                resolved.key, arguments, tool_name="get_example_payment"
            )
            return {**result, "example": True, "arguments": arguments}

        @server.tool(
            description=(
                "Tell the operator about a machine-service capability you would pay "
                "for. The structured request is stored privately for product research; "
                "contact_uri is optional and is never called automatically."
            )
        )
        def request_capability(
            job_to_be_done: str,
            current_alternative: str = "",
            decision_criteria: list[str] | None = None,
            max_budget_usd: str | None = None,
            max_latency_seconds: int | None = None,
            required_output_fields: list[str] | None = None,
            contact_uri: str | None = None,
        ) -> dict[str, Any]:
            job = job_to_be_done.strip()
            if not 10 <= len(job) <= 2000:
                raise ValueError("job_to_be_done must contain 10-2000 characters")
            alternative = current_alternative.strip()
            if len(alternative) > 1000:
                raise ValueError("current_alternative must be at most 1000 characters")
            criteria = [
                item.strip() for item in (decision_criteria or []) if item.strip()
            ]
            fields = [
                item.strip() for item in (required_output_fields or []) if item.strip()
            ]
            if len(criteria) > 12 or any(len(item) > 240 for item in criteria):
                raise ValueError(
                    "decision_criteria supports up to 12 items of 240 characters"
                )
            if len(fields) > 40 or any(len(item) > 120 for item in fields):
                raise ValueError(
                    "required_output_fields supports up to 40 short field names"
                )
            if (
                max_latency_seconds is not None
                and not 1 <= max_latency_seconds <= 2_592_000
            ):
                raise ValueError("max_latency_seconds must be between 1 and 2592000")
            if max_budget_usd is not None and len(max_budget_usd) > 32:
                raise ValueError("max_budget_usd must be a short decimal string")
            if contact_uri:
                contact_uri = contact_uri.strip()
                parsed = urlparse(contact_uri)
                if len(contact_uri) > 500 or parsed.scheme not in {"https", "mailto"}:
                    raise ValueError("contact_uri must be an HTTPS or mailto URI")
            payload = {
                "job_to_be_done": job,
                "current_alternative": alternative or None,
                "decision_criteria": criteria,
                "max_budget_usd": max_budget_usd,
                "max_latency_seconds": max_latency_seconds,
                "required_output_fields": fields,
                "contact_uri": contact_uri,
            }
            request_id, duplicate = self.store.submit_capability(payload)
            self.store.record_event(
                "request_capability",
                "CAPABILITY_DUPLICATE" if duplicate else "CAPABILITY_ACCEPTED",
                request_hash=canonical_hash(payload),
            )
            return {
                "accepted": True,
                "request_id": request_id,
                "duplicate": duplicate,
                "payment_required": False,
                "operator_review": "Requests are reviewed for repeated demand and economic viability.",
            }

        @server.tool(
            description=(
                "Create a live x402 challenge for one public HTTPS source snapshot. "
                "This does not move funds."
            )
        )
        async def get_source_snapshot_payment(
            url: str,
            query: str = "",
            max_characters: int = 12000,
        ) -> dict[str, Any]:
            arguments: dict[str, Any] = {
                "url": url,
                "max_characters": max_characters,
            }
            if query:
                arguments["query"] = query
            return await self._prepare_payment("source_snapshot", arguments)

        @server.tool(
            description=(
                "Create a live x402 challenge for SEC Form D funding signals. "
                "This does not move funds."
            )
        )
        async def get_form_d_funding_leads_payment(
            since: str,
            states: list[str] | None = None,
            industry_keywords: list[str] | None = None,
            minimum_amount_sold_usd: str = "0",
            include_amendments: bool = False,
            limit: int = 10,
        ) -> dict[str, Any]:
            return await self._prepare_payment(
                "form_d_funding_leads",
                {
                    "since": since,
                    "states": states or [],
                    "industry_keywords": industry_keywords or [],
                    "minimum_amount_sold_usd": minimum_amount_sold_usd,
                    "include_amendments": include_amendments,
                    "limit": limit,
                    "max_source_age_seconds": 600,
                },
            )

        @server.tool(
            description=(
                "Create a live x402 challenge for an exact-address OFAC payment "
                "preflight. This does not move funds and is not sanctions clearance."
            )
        )
        async def get_payment_preflight_payment(
            address: str,
            network: str = "eip155:8453",
        ) -> dict[str, Any]:
            return await self._prepare_payment(
                "payment_preflight", {"address": address, "network": network}
            )

        @server.tool(
            description=(
                "Submit a wallet-generated x402 PAYMENT-SIGNATURE for a previously "
                "quoted product. Use exactly the same arguments. Never provide a "
                "private key or seed phrase. request_id enables safe result replay."
            )
        )
        async def submit_x402_payment(
            product: str,
            arguments: dict[str, Any],
            payment_signature: str,
            request_id: str | None = None,
        ) -> dict[str, Any]:
            resolved = self._product(product)
            if not payment_signature or len(payment_signature) > 32768:
                raise ValueError("payment_signature is missing or too large")
            if request_id:
                async with httpx.AsyncClient(
                    timeout=30, follow_redirects=False
                ) as client:
                    replay = await client.get(
                        f"{self.public_base_url}/v1/evidence/replay/{request_id}",
                        headers={
                            "Payment-Signature": payment_signature,
                            "User-Agent": "Regulavita-MCP/1.0",
                            "X-Agent-Discovery-Source": "official-mcp",
                        },
                    )
                if replay.status_code == 200:
                    self.store.record_event(
                        "submit_x402_payment",
                        "PAID_REPLAY",
                        product=resolved.key,
                        request_hash=canonical_hash(arguments),
                    )
                    return {
                        "status": "fulfilled",
                        "replayed": True,
                        "request_id": request_id,
                        "result": self._response_json(replay),
                    }
            response = await self._post_paid_route(
                resolved, arguments, payment_signature=payment_signature
            )
            response_payload = self._response_json(response)
            event_type = (
                "PAID_FULFILLED" if response.status_code < 400 else "PAID_FAILED"
            )
            self.store.record_event(
                "submit_x402_payment",
                event_type,
                product=resolved.key,
                request_hash=canonical_hash(arguments),
                http_status=response.status_code,
                failure_code=(
                    None
                    if response.status_code < 400
                    else self._failure_code(response_payload, response.status_code)
                ),
            )
            return {
                "status": "fulfilled" if response.status_code < 400 else "failed",
                "replayed": False,
                "http_status": response.status_code,
                "request_id": response.headers.get("x-evidence-request-id")
                or request_id,
                "payment_response": response.headers.get("payment-response"),
                "payment_failure_stage": response.headers.get(
                    "x-evidence-payment-failure-stage"
                ),
                "payment_failure_reason": response.headers.get(
                    "x-evidence-payment-failure-reason"
                ),
                "result" if response.status_code < 400 else "error": response_payload,
            }
