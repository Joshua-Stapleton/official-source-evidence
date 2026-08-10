from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from pydantic import ValidationError

from autonomous_data_api.evidence import WebMonitorCreateRequest, canonical_json
from autonomous_data_api.monitors import (
    MonitorError,
    WebMonitorService,
    _safe_https_request,
)

MAX_WEBHOOK_BYTES = 100_000
MAX_ATTEMPTS = 5
EVENT_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{3,128}$")
CALLBACK_PATH_PATTERN = re.compile(
    r"^/v1/(?:threads|jobs)/[A-Za-z0-9_-]{3,128}/update$"
)


class MarketplaceError(Exception):
    def __init__(self, code: str, detail: str, status_code: int) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.status_code = status_code


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class The402Provider:
    def __init__(self, db_path: Path, monitor_service: WebMonitorService) -> None:
        self.db_path = db_path
        self.monitor_service = monitor_service
        self.api_key = os.getenv("THE402_API_KEY", "").strip()
        self.webhook_secret = os.getenv("THE402_WEBHOOK_SECRET", "").strip()
        self.service_id = os.getenv("THE402_SOURCE_WATCH_SERVICE_ID", "").strip()
        self.api_origin = os.getenv(
            "THE402_API_ORIGIN", "https://api.the402.ai"
        ).rstrip("/")
        self._init_db()

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.webhook_secret)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def _init_db(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS marketplace_events (
                    event_id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    payload_sha256 TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    deliverables_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_marketplace_events_pending
                    ON marketplace_events(status, attempts, created_at);
                """
            )

    def _verify_callback_url(self, value: str) -> str:
        parsed = urlparse(value)
        expected = urlparse(self.api_origin)
        if (
            parsed.scheme != "https"
            or parsed.hostname != expected.hostname
            or parsed.port not in (None, 443)
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or not CALLBACK_PATH_PATTERN.fullmatch(parsed.path)
        ):
            raise MarketplaceError(
                "INVALID_CALLBACK",
                "Marketplace callback URL is outside the allowed API origin",
                422,
            )
        return value

    def accept_webhook(
        self,
        raw_body: bytes,
        *,
        platform_secret: str | None,
        signature: str | None,
        timestamp: str | None,
    ) -> dict[str, Any]:
        if not self.configured:
            raise MarketplaceError(
                "INTEGRATION_NOT_CONFIGURED", "Marketplace integration is disabled", 503
            )
        if len(raw_body) > MAX_WEBHOOK_BYTES:
            raise MarketplaceError(
                "PAYLOAD_TOO_LARGE", "Webhook body is too large", 413
            )
        if not platform_secret or not hmac.compare_digest(
            platform_secret, self.api_key
        ):
            raise MarketplaceError("INVALID_PLATFORM_SECRET", "Unauthorized", 401)
        try:
            sent_at = int(timestamp or "")
        except ValueError as exc:
            raise MarketplaceError(
                "INVALID_WEBHOOK_TIMESTAMP", "Webhook timestamp is invalid", 401
            ) from exc
        if abs(time.time() - sent_at) > 300:
            raise MarketplaceError(
                "STALE_WEBHOOK", "Webhook timestamp is outside the replay window", 401
            )
        expected_signature = (
            "sha256="
            + hmac.new(
                self.webhook_secret.encode("utf-8"),
                f"{sent_at}.".encode() + raw_body,
                hashlib.sha256,
            ).hexdigest()
        )
        if not signature or not hmac.compare_digest(signature, expected_signature):
            raise MarketplaceError("INVALID_WEBHOOK_SIGNATURE", "Unauthorized", 401)

        try:
            payload = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            raise MarketplaceError(
                "INVALID_JSON", "Webhook body is not JSON", 400
            ) from exc
        if not isinstance(payload, dict):
            raise MarketplaceError(
                "INVALID_EVENT", "Webhook event must be an object", 422
            )
        event_type = str(payload.get("type", ""))
        if event_type != "job_dispatch":
            return {"accepted": True, "event_type": event_type, "action": "ignored"}

        event_id = str(payload.get("job_id", ""))
        if not EVENT_ID_PATTERN.fullmatch(event_id):
            raise MarketplaceError("INVALID_JOB_ID", "Job ID is invalid", 422)
        service_id = str(payload.get("service_id", ""))
        if self.service_id and service_id != self.service_id:
            raise MarketplaceError(
                "UNKNOWN_SERVICE", "Job is not for the configured service", 422
            )
        callback_url = payload.get("callback_url")
        if not isinstance(callback_url, str):
            raise MarketplaceError(
                "INVALID_CALLBACK", "Job callback URL is required", 422
            )
        self._verify_callback_url(callback_url)
        if not isinstance(payload.get("brief"), dict):
            raise MarketplaceError("INVALID_BRIEF", "Job brief must be an object", 422)

        payload_bytes = canonical_json(payload)
        payload_hash = hashlib.sha256(payload_bytes).hexdigest()
        now = _utc_now()
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT payload_sha256, status FROM marketplace_events WHERE event_id = ?",
                (event_id,),
            ).fetchone()
            if existing and existing["payload_sha256"] != payload_hash:
                raise MarketplaceError(
                    "EVENT_ID_REUSED",
                    "Job ID was already used for a different payload",
                    409,
                )
            connection.execute(
                """
                INSERT OR IGNORE INTO marketplace_events (
                    event_id, event_type, payload_sha256, payload_json, status,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'PENDING', ?, ?)
                """,
                (
                    event_id,
                    event_type,
                    payload_hash,
                    payload_bytes.decode("utf-8"),
                    now,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT status FROM marketplace_events WHERE event_id = ?",
                (event_id,),
            ).fetchone()
            connection.commit()
        return {"accepted": True, "event_id": event_id, "status": row["status"]}

    def _post_callback(self, callback_url: str, payload: dict[str, Any]) -> None:
        callback_url = self._verify_callback_url(callback_url)
        status, _, body = _safe_https_request(
            "POST",
            callback_url,
            body=canonical_json(payload),
            headers={
                "Content-Type": "application/json",
                "X-API-Key": self.api_key,
            },
            max_bytes=64_000,
        )
        if status < 200 or status >= 300:
            detail = body.decode("utf-8", errors="replace")[:500]
            raise MarketplaceError(
                "CALLBACK_FAILED",
                f"Marketplace callback returned HTTP {status}: {detail}",
                502,
            )

    def _complete_job(self, row: sqlite3.Row) -> dict[str, Any]:
        payload = json.loads(row["payload_json"])
        try:
            brief = WebMonitorCreateRequest.model_validate(payload["brief"])
        except ValidationError as exc:
            raise MarketplaceError(
                "INVALID_BRIEF", "Job brief does not match Source Watch input", 422
            ) from exc
        created = self.monitor_service.activate(
            request_id=f"the402_{row['event_id']}",
            payment_signature=f"the402-escrow:{row['event_id']}",
            url=brief.url,
            label=brief.label,
            webhook_url=brief.webhook_url,
            base_url=os.getenv(
                "AUTONOMOUS_API_BASE_URL", "https://evidence.regulavita.com"
            ),
        )
        deliverables = {
            "monitor_id": created["monitor_id"],
            "status": created["status"],
            "source_url": created["url"],
            "status_url": created["status_url"],
            "access_token": created["access_token"],
            "expires_at": created["expires_at"],
            "check_interval_seconds": created["check_interval_seconds"],
            "webhook": created["webhook"],
        }
        self._post_callback(
            payload["callback_url"],
            {
                "status": "completed",
                "deliverables": deliverables,
                "notes": (
                    "The 30-day monitor is active. The first baseline check runs "
                    "asynchronously; use the private status URL for results."
                ),
            },
        )
        return deliverables

    def run_pending(self, limit: int = 10) -> int:
        if not self.configured:
            return 0
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM marketplace_events
                WHERE status IN ('PENDING', 'RETRY') AND attempts < ?
                ORDER BY created_at LIMIT ?
                """,
                (MAX_ATTEMPTS, max(1, min(limit, 50))),
            ).fetchall()
        for row in rows:
            try:
                deliverables = self._complete_job(row)
            except (MarketplaceError, MonitorError, OSError, sqlite3.Error) as exc:
                attempts = int(row["attempts"]) + 1
                status = "FAILED" if attempts >= MAX_ATTEMPTS else "RETRY"
                with self._connect() as connection:
                    connection.execute(
                        """
                        UPDATE marketplace_events SET status = ?, attempts = ?,
                            last_error = ?, updated_at = ? WHERE event_id = ?
                        """,
                        (
                            status,
                            attempts,
                            str(exc)[:1000],
                            _utc_now(),
                            row["event_id"],
                        ),
                    )
                    connection.commit()
                continue
            with self._connect() as connection:
                connection.execute(
                    """
                    UPDATE marketplace_events SET status = 'COMPLETED',
                        attempts = attempts + 1, last_error = NULL,
                        deliverables_json = ?, updated_at = ? WHERE event_id = ?
                    """,
                    (
                        canonical_json(deliverables).decode("utf-8"),
                        _utc_now(),
                        row["event_id"],
                    ),
                )
                connection.commit()
        return len(rows)

    def public_status(self) -> dict[str, Any]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT status, COUNT(*) AS count
                FROM marketplace_events GROUP BY status ORDER BY status
                """
            ).fetchall()
        return {
            "configured": self.configured,
            "service_configured": bool(self.service_id),
            "events": {row["status"].casefold(): row["count"] for row in rows},
        }
