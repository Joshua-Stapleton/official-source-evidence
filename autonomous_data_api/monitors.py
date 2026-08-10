from __future__ import annotations

import base64
import difflib
import hashlib
import hmac
import http.client
import ipaddress
import json
import os
import secrets
import socket
import sqlite3
import ssl
import zlib
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, ClassVar
from urllib.parse import urlparse

from autonomous_data_api.evidence import canonical_json, sha256_bytes

MAX_RESPONSE_BYTES = 1_000_000
MAX_NORMALIZED_TEXT_CHARS = 250_000
MAX_DIFF_CHARS = 12_000
CHECK_INTERVAL_SECONDS = 6 * 60 * 60
MONITOR_DURATION_DAYS = 30
SUPPORTED_CONTENT_TYPES = {
    "application/json",
    "application/ld+json",
    "application/xml",
    "text/html",
    "text/plain",
    "text/xml",
}


class MonitorError(Exception):
    def __init__(self, code: str, detail: str, status_code: int = 422) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.status_code = status_code


class _VisibleTextParser(HTMLParser):
    BLOCK_TAGS: ClassVar[set[str]] = {
        "article",
        "br",
        "dd",
        "div",
        "dl",
        "dt",
        "footer",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "li",
        "main",
        "nav",
        "ol",
        "p",
        "pre",
        "section",
        "table",
        "td",
        "th",
        "tr",
        "ul",
    }
    HIDDEN_TAGS: ClassVar[set[str]] = {
        "script",
        "style",
        "noscript",
        "svg",
        "template",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        normalized = tag.casefold()
        if normalized in self.HIDDEN_TAGS:
            self.hidden_depth += 1
        elif not self.hidden_depth and normalized in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.casefold()
        if normalized in self.HIDDEN_TAGS and self.hidden_depth:
            self.hidden_depth -= 1
        elif not self.hidden_depth and normalized in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.hidden_depth:
            self.parts.append(data)


def _normalize_lines(value: str) -> str:
    lines: list[str] = []
    previous_blank = False
    for raw_line in value.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = " ".join(raw_line.split())
        if line:
            lines.append(line)
            previous_blank = False
        elif lines and not previous_blank:
            lines.append("")
            previous_blank = True
    return "\n".join(lines).strip()[:MAX_NORMALIZED_TEXT_CHARS]


def normalize_content(content: bytes, content_type: str) -> str:
    text = content.decode("utf-8", errors="replace")
    if content_type in {"application/json", "application/ld+json"}:
        try:
            return json.dumps(
                json.loads(text),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            )[:MAX_NORMALIZED_TEXT_CHARS]
        except json.JSONDecodeError:
            return _normalize_lines(text)
    if content_type == "text/html":
        parser = _VisibleTextParser()
        parser.feed(text)
        parser.close()
        return _normalize_lines("".join(parser.parts))
    return _normalize_lines(text)


def _url_parts(value: str) -> tuple[str, str, str]:
    parsed = urlparse(value)
    if parsed.scheme.casefold() != "https":
        raise MonitorError("URL_NOT_HTTPS", "Only public HTTPS URLs are supported")
    if not parsed.hostname or parsed.username or parsed.password:
        raise MonitorError("INVALID_URL", "URL must include a public hostname")
    if parsed.port not in (None, 443):
        raise MonitorError(
            "UNSUPPORTED_PORT", "Only the default HTTPS port is supported"
        )
    if parsed.fragment:
        raise MonitorError("INVALID_URL", "URL fragments are not supported")
    hostname = parsed.hostname.encode("idna").decode("ascii").casefold()
    if hostname == "localhost" or hostname.endswith(
        (".localhost", ".local", ".internal")
    ):
        raise MonitorError(
            "NON_PUBLIC_HOST", "Only public internet hosts are supported"
        )
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    host_header = hostname
    return hostname, host_header, path


def _public_addresses(hostname: str) -> list[str]:
    try:
        literal = ipaddress.ip_address(hostname)
        addresses = [str(literal)]
    except ValueError:
        try:
            records = socket.getaddrinfo(
                hostname, 443, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP
            )
        except socket.gaierror as exc:
            raise MonitorError(
                "DNS_FAILED", "Hostname could not be resolved", 502
            ) from exc
        addresses = sorted({record[4][0] for record in records})
    if not addresses:
        raise MonitorError("DNS_FAILED", "Hostname did not resolve", 502)
    if any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise MonitorError(
            "NON_PUBLIC_HOST", "Hostname resolves to a non-public network address"
        )
    return addresses


def _safe_https_request(
    method: str,
    url: str,
    *,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
    max_bytes: int = MAX_RESPONSE_BYTES,
) -> tuple[int, dict[str, str], bytes]:
    hostname, host_header, path = _url_parts(url)
    addresses = _public_addresses(hostname)
    context = ssl.create_default_context()
    last_error: OSError | ssl.SSLError | None = None
    for address in addresses:
        raw_socket: socket.socket | None = None
        connection: http.client.HTTPSConnection | None = None
        try:
            raw_socket = socket.create_connection((address, 443), timeout=12)
            tls_socket = context.wrap_socket(raw_socket, server_hostname=hostname)
            raw_socket = None
            connection = http.client.HTTPSConnection(
                hostname, port=443, timeout=12, context=context
            )
            connection.sock = tls_socket
            request_headers = {
                "Accept": "text/html,application/json,text/plain,application/xml,text/xml",
                "Accept-Encoding": "identity",
                "Host": host_header,
                "User-Agent": os.getenv(
                    "AUTONOMOUS_MONITOR_USER_AGENT",
                    "Regulavita-Source-Watch/1.0 joshua@regulavita.com",
                ),
            }
            request_headers.update(headers or {})
            connection.request(method, path, body=body, headers=request_headers)
            response = connection.getresponse()
            response_body = response.read(max_bytes + 1)
            if len(response_body) > max_bytes:
                raise MonitorError(
                    "RESPONSE_TOO_LARGE", f"Response exceeds {max_bytes} bytes", 413
                )
            response_headers = {
                key.casefold(): value for key, value in response.getheaders()
            }
            return response.status, response_headers, response_body
        except MonitorError:
            raise
        except (OSError, ssl.SSLError, http.client.HTTPException) as exc:
            last_error = exc
        finally:
            if connection:
                connection.close()
            if raw_socket:
                raw_socket.close()
    raise MonitorError(
        "FETCH_FAILED", "Public source could not be fetched", 502
    ) from last_error


def fetch_public_source(url: str) -> tuple[int, str, str]:
    status, headers, body = _safe_https_request("GET", url)
    if status < 200 or status >= 300:
        raise MonitorError("SOURCE_HTTP_ERROR", f"Source returned HTTP {status}", 502)
    content_type = headers.get("content-type", "").split(";", 1)[0].strip().casefold()
    if content_type not in SUPPORTED_CONTENT_TYPES:
        raise MonitorError(
            "UNSUPPORTED_CONTENT_TYPE",
            f"Source content type {content_type or 'unknown'} is not monitorable",
            415,
        )
    normalized = normalize_content(body, content_type)
    if not normalized:
        raise MonitorError("EMPTY_SOURCE", "Source produced no monitorable text", 422)
    return status, content_type, normalized


class WebMonitorService:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._secret = self._load_secret()
        self._init_db()

    def _load_secret(self) -> bytes:
        configured = (
            os.getenv("AUTONOMOUS_MONITOR_HMAC_KEY", "").strip()
            or os.getenv("AUTONOMOUS_ANALYTICS_HMAC_KEY", "").strip()
        )
        if configured:
            return hashlib.sha256(configured.encode("utf-8")).digest()
        key_path = self.db_path.parent / "monitor_hmac.key"
        if key_path.exists():
            return key_path.read_bytes()
        key = secrets.token_bytes(32)
        key_path.write_bytes(key)
        key_path.chmod(0o600)
        return key

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _init_db(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS web_monitors (
                    monitor_id TEXT PRIMARY KEY,
                    request_id TEXT NOT NULL,
                    payment_signature_hash TEXT NOT NULL UNIQUE,
                    url TEXT NOT NULL,
                    label TEXT,
                    webhook_url TEXT,
                    access_token_hash TEXT NOT NULL,
                    webhook_secret_hash TEXT,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    next_check_at TEXT NOT NULL,
                    last_checked_at TEXT,
                    last_http_status INTEGER,
                    last_content_type TEXT,
                    last_content_hash TEXT,
                    last_text_zlib BLOB,
                    successful_checks INTEGER NOT NULL DEFAULT 0,
                    change_count INTEGER NOT NULL DEFAULT 0,
                    consecutive_failures INTEGER NOT NULL DEFAULT 0,
                    last_error_code TEXT,
                    last_error_detail TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_web_monitors_due
                    ON web_monitors(status, next_check_at);
                CREATE TABLE IF NOT EXISTS web_monitor_events (
                    event_id TEXT PRIMARY KEY,
                    monitor_id TEXT NOT NULL,
                    detected_at TEXT NOT NULL,
                    previous_hash TEXT,
                    current_hash TEXT NOT NULL,
                    diff_text TEXT NOT NULL,
                    webhook_status TEXT,
                    FOREIGN KEY (monitor_id) REFERENCES web_monitors(monitor_id)
                );
                CREATE INDEX IF NOT EXISTS idx_web_monitor_events_monitor
                    ON web_monitor_events(monitor_id, detected_at DESC);
                """
            )

    def _derive(self, purpose: str, monitor_id: str, payment_hash: str) -> str:
        digest = hmac.new(
            self._secret,
            f"{purpose}:{monitor_id}:{payment_hash}".encode(),
            hashlib.sha256,
        ).digest()
        return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")

    def activate(
        self,
        *,
        request_id: str,
        payment_signature: str,
        url: str,
        label: str | None,
        webhook_url: str | None,
        base_url: str,
    ) -> dict[str, Any]:
        payment_hash = sha256_bytes(payment_signature.encode("utf-8"))
        monitor_id = f"mon_{sha256_bytes(f'{request_id}:{payment_hash}'.encode())[:24]}"
        access_token = self._derive("access", monitor_id, payment_hash)
        webhook_secret = (
            self._derive("webhook", monitor_id, payment_hash) if webhook_url else None
        )
        now = datetime.now(timezone.utc)
        expires = now + timedelta(days=MONITOR_DURATION_DAYS)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO web_monitors (
                    monitor_id, request_id, payment_signature_hash, url, label,
                    webhook_url, access_token_hash, webhook_secret_hash, status,
                    created_at, expires_at, next_check_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE', ?, ?, ?)
                """,
                (
                    monitor_id,
                    request_id,
                    payment_hash,
                    url,
                    label,
                    webhook_url,
                    sha256_bytes(access_token.encode("utf-8")),
                    sha256_bytes(webhook_secret.encode("utf-8"))
                    if webhook_secret
                    else None,
                    now.isoformat(),
                    expires.isoformat(),
                    now.isoformat(),
                ),
            )
            row = connection.execute(
                "SELECT * FROM web_monitors WHERE payment_signature_hash = ?",
                (payment_hash,),
            ).fetchone()
            connection.commit()
        if row is None or row["monitor_id"] != monitor_id:
            raise MonitorError(
                "PAYMENT_ALREADY_USED",
                "Payment proof is already bound to a different monitor",
                409,
            )
        base = base_url.rstrip("/")
        return {
            "monitor_id": monitor_id,
            "status": row["status"],
            "url": row["url"],
            "created_at": row["created_at"],
            "expires_at": row["expires_at"],
            "check_interval_seconds": CHECK_INTERVAL_SECONDS,
            "access_token": access_token,
            "status_url": f"{base}/v1/monitors/{monitor_id}",
            "cancel_url": f"{base}/v1/monitors/{monitor_id}",
            "webhook": {
                "configured": bool(webhook_url),
                "signature_header": "X-Source-Watch-Signature",
                "signing_secret": webhook_secret,
            },
            "next_step": "Poll status_url with Authorization: Bearer <access_token>.",
        }

    def _authorized_row(self, monitor_id: str, access_token: str) -> sqlite3.Row:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM web_monitors WHERE monitor_id = ?", (monitor_id,)
            ).fetchone()
        if row is None or not hmac.compare_digest(
            row["access_token_hash"], sha256_bytes(access_token.encode("utf-8"))
        ):
            raise MonitorError("MONITOR_NOT_FOUND", "Monitor was not found", 404)
        return row

    def status(self, monitor_id: str, access_token: str) -> dict[str, Any]:
        row = self._authorized_row(monitor_id, access_token)
        with self._connect() as connection:
            events = connection.execute(
                """
                SELECT event_id, detected_at, previous_hash, current_hash,
                       diff_text, webhook_status
                FROM web_monitor_events
                WHERE monitor_id = ?
                ORDER BY detected_at DESC LIMIT 20
                """,
                (monitor_id,),
            ).fetchall()
        return {
            "monitor_id": row["monitor_id"],
            "status": row["status"],
            "label": row["label"],
            "url": row["url"],
            "created_at": row["created_at"],
            "expires_at": row["expires_at"],
            "next_check_at": row["next_check_at"],
            "last_checked_at": row["last_checked_at"],
            "last_http_status": row["last_http_status"],
            "last_content_hash": row["last_content_hash"],
            "successful_checks": row["successful_checks"],
            "change_count": row["change_count"],
            "consecutive_failures": row["consecutive_failures"],
            "last_error": (
                {
                    "code": row["last_error_code"],
                    "detail": row["last_error_detail"],
                }
                if row["last_error_code"]
                else None
            ),
            "events": [dict(event) for event in events],
        }

    def cancel(self, monitor_id: str, access_token: str) -> dict[str, Any]:
        self._authorized_row(monitor_id, access_token)
        with self._connect() as connection:
            connection.execute(
                "UPDATE web_monitors SET status = 'CANCELLED' WHERE monitor_id = ?",
                (monitor_id,),
            )
            connection.commit()
        return {"monitor_id": monitor_id, "status": "CANCELLED"}

    def _deliver_webhook(
        self, webhook_url: str, webhook_secret: str, payload: dict[str, Any]
    ) -> str:
        body = canonical_json(payload)
        signature = hmac.new(
            webhook_secret.encode("utf-8"), body, hashlib.sha256
        ).hexdigest()
        try:
            status, _, _ = _safe_https_request(
                "POST",
                webhook_url,
                body=body,
                headers={
                    "Content-Type": "application/json",
                    "X-Source-Watch-Signature": f"sha256={signature}",
                },
                max_bytes=64_000,
            )
        except MonitorError as exc:
            return f"ERROR:{exc.code}"
        return f"HTTP_{status}"

    def _check_one(self, row: sqlite3.Row) -> None:
        monitor_id = row["monitor_id"]
        now = datetime.now(timezone.utc)
        next_check = now + timedelta(seconds=CHECK_INTERVAL_SECONDS)
        try:
            http_status, content_type, normalized = fetch_public_source(row["url"])
            current_hash = sha256_bytes(normalized.encode("utf-8"))
        except MonitorError as exc:
            with self._connect() as connection:
                connection.execute(
                    """
                    UPDATE web_monitors SET
                        next_check_at = ?, last_checked_at = ?,
                        consecutive_failures = consecutive_failures + 1,
                        last_error_code = ?, last_error_detail = ?
                    WHERE monitor_id = ?
                    """,
                    (
                        next_check.isoformat(),
                        now.isoformat(),
                        exc.code,
                        exc.detail,
                        monitor_id,
                    ),
                )
                connection.commit()
            return

        previous_hash = row["last_content_hash"]
        previous_text = (
            zlib.decompress(row["last_text_zlib"]).decode("utf-8")
            if row["last_text_zlib"]
            else None
        )
        changed = bool(previous_hash and previous_hash != current_hash)
        diff_text = ""
        event_id: str | None = None
        if changed and previous_text is not None:
            diff_text = "\n".join(
                difflib.unified_diff(
                    previous_text.splitlines(),
                    normalized.splitlines(),
                    fromfile="previous",
                    tofile="current",
                    lineterm="",
                    n=2,
                )
            )[:MAX_DIFF_CHARS]
            event_id = f"evt_{sha256_bytes(f'{monitor_id}:{now.isoformat()}:{current_hash}'.encode())[:24]}"

        with self._connect() as connection:
            connection.execute(
                """
                UPDATE web_monitors SET
                    next_check_at = ?, last_checked_at = ?, last_http_status = ?,
                    last_content_type = ?, last_content_hash = ?, last_text_zlib = ?,
                    successful_checks = successful_checks + 1,
                    change_count = change_count + ?, consecutive_failures = 0,
                    last_error_code = NULL, last_error_detail = NULL
                WHERE monitor_id = ?
                """,
                (
                    next_check.isoformat(),
                    now.isoformat(),
                    http_status,
                    content_type,
                    current_hash,
                    zlib.compress(normalized.encode("utf-8"), level=6),
                    int(changed),
                    monitor_id,
                ),
            )
            if event_id:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO web_monitor_events (
                        event_id, monitor_id, detected_at, previous_hash,
                        current_hash, diff_text, webhook_status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event_id,
                        monitor_id,
                        now.isoformat(),
                        previous_hash,
                        current_hash,
                        diff_text,
                        "PENDING" if row["webhook_url"] else None,
                    ),
                )
            connection.commit()

        if event_id and row["webhook_url"]:
            webhook_secret = self._derive(
                "webhook", monitor_id, row["payment_signature_hash"]
            )
            payload = {
                "type": "source.changed",
                "event_id": event_id,
                "monitor_id": monitor_id,
                "url": row["url"],
                "detected_at": now.isoformat(),
                "previous_hash": previous_hash,
                "current_hash": current_hash,
                "diff": diff_text,
            }
            delivery_status = self._deliver_webhook(
                row["webhook_url"], webhook_secret, payload
            )
            with self._connect() as connection:
                connection.execute(
                    "UPDATE web_monitor_events SET webhook_status = ? WHERE event_id = ?",
                    (delivery_status, event_id),
                )
                connection.commit()

    def run_due(self, limit: int = 20) -> int:
        now = datetime.now(timezone.utc)
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE web_monitors SET status = 'EXPIRED'
                WHERE status = 'ACTIVE' AND expires_at <= ?
                """,
                (now.isoformat(),),
            )
            rows = connection.execute(
                """
                SELECT * FROM web_monitors
                WHERE status = 'ACTIVE' AND next_check_at <= ?
                ORDER BY next_check_at LIMIT ?
                """,
                (now.isoformat(), max(1, min(limit, 100))),
            ).fetchall()
            connection.commit()
        for row in rows:
            self._check_one(row)
        return len(rows)

    def public_stats(self) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN status = 'ACTIVE' THEN 1 ELSE 0 END) AS active,
                    COALESCE(SUM(successful_checks), 0) AS successful_checks,
                    COALESCE(SUM(change_count), 0) AS changes_detected
                FROM web_monitors
                """
            ).fetchone()
        return {
            "total_monitors": row["total"],
            "active_monitors": row["active"] or 0,
            "successful_checks": row["successful_checks"],
            "changes_detected": row["changes_detected"],
            "check_interval_seconds": CHECK_INTERVAL_SECONDS,
            "duration_days": MONITOR_DURATION_DAYS,
        }
