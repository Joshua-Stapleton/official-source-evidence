from __future__ import annotations

import base64
import hashlib
import hmac
import io
import ipaddress
import json
import os
import re
import secrets
import sqlite3
import unicodedata
import zlib
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from defusedxml import ElementTree
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIR = ROOT / "autonomous_data_api" / "runtime"
EVIDENCE_DB_PATH = Path(
    os.getenv("AUTONOMOUS_EVIDENCE_DB_PATH", RUNTIME_DIR / "evidence.sqlite3")
)
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
SEC_COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_ARCHIVES_ROOT = "https://www.sec.gov/Archives/edgar/data"
SEC_DAILY_INDEX_ROOT = "https://www.sec.gov/Archives/edgar/daily-index"
SEC_ARCHIVES_BASE = "https://www.sec.gov/Archives"
OFAC_SOURCE_URLS = {
    "SDN": (
        "https://sanctionslistservice.ofac.treas.gov/api/"
        "PublicationPreview/exports/SDN.XML"
    ),
    "CONSOLIDATED": (
        "https://sanctionslistservice.ofac.treas.gov/api/"
        "PublicationPreview/exports/CONSOLIDATED.XML"
    ),
}
SEC_PARSER_VERSION = "sec-trigger-delta/0.2.0"
FORM_D_PARSER_VERSION = "sec-form-d-funding-leads/0.1.0"
OFAC_PARSER_VERSION = "ofac-exact/0.2.0"
OFAC_FRESHNESS_SECONDS = 900
MAX_SEC_FILINGS = 10
MAX_SEC_SUPPLEMENTAL_FILES = 5
MAX_FORM_D_LOOKBACK_DAYS = 14
MAX_FORM_D_SCAN_PER_REQUEST = 25
MAX_FORM_D_RELATED_PEOPLE = 12


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def age_seconds(value: str) -> int:
    return max(0, int((datetime.now(timezone.utc) - parse_utc(value)).total_seconds()))


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_json(value: Any) -> str:
    return sha256_bytes(canonical_json(value))


def prefixed_sha256(value: bytes | str) -> str:
    raw = value.encode("utf-8") if isinstance(value, str) else value
    return f"sha256:{sha256_bytes(raw)}"


def normalize_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    return " ".join(normalized.split()).casefold()


def normalize_crypto_identifier(value: str) -> str:
    value = value.strip()
    return value.lower() if value.startswith(("0x", "0X")) else value


class EvidenceError(Exception):
    def __init__(self, code: str, detail: str, status_code: int = 503) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.status_code = status_code


class SourceStaleError(EvidenceError):
    def __init__(self, detail: str) -> None:
        super().__init__("SOURCE_STALE", detail, 503)


class SourceSchemaError(EvidenceError):
    def __init__(self, detail: str) -> None:
        super().__init__("SOURCE_SCHEMA_ERROR", detail, 503)


class SourceConfigurationError(EvidenceError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(code, detail, 503)


class ContractError(EvidenceError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(code, detail, 422)


class SecIssuerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cik: str | None = Field(default=None, min_length=1, max_length=10)
    ticker: str | None = Field(default=None, min_length=1, max_length=10)
    max_source_age_seconds: int = Field(default=600, ge=60, le=3600)

    @field_validator("cik", mode="before")
    @classmethod
    def normalize_cik(cls, value: Any) -> str | None:
        if value is None:
            return None
        raw = str(value).strip()
        if not raw.isdigit() or len(raw) > 10:
            raise ValueError("cik must contain at most 10 digits")
        return raw.zfill(10)

    @field_validator("ticker", mode="before")
    @classmethod
    def normalize_ticker(cls, value: Any) -> str | None:
        if value is None:
            return None
        ticker = str(value).strip().upper()
        if not re.fullmatch(r"[A-Z0-9.-]{1,10}", ticker):
            raise ValueError("ticker must be 1-10 letters, digits, dots, or hyphens")
        return ticker

    @model_validator(mode="after")
    def require_one_issuer(self) -> SecIssuerRequest:
        if bool(self.cik) == bool(self.ticker):
            raise ValueError("provide exactly one of cik or ticker")
        return self


def normalize_since_timestamp(value: Any) -> str:
    raw = str(value).strip()
    try:
        parsed = parse_utc(raw).astimezone(timezone.utc)
    except (TypeError, ValueError) as exc:
        raise ValueError("since must be an ISO 8601 timestamp") from exc
    if parsed > datetime.now(timezone.utc) + timedelta(minutes=5):
        raise ValueError("since cannot be in the future")
    return parsed.isoformat()


class SecDeltaRequest(SecIssuerRequest):
    since_accession: str | None = Field(default=None, pattern=r"^\d{10}-\d{2}-\d{6}$")
    since: str | None = None
    forms: list[Literal["8-K", "10-Q", "10-K"]] = Field(
        default_factory=lambda: ["8-K", "10-Q", "10-K"],
        min_length=1,
        max_length=3,
    )
    rules: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("since", mode="before")
    @classmethod
    def normalize_since(cls, value: Any) -> str | None:
        return None if value is None else normalize_since_timestamp(value)

    @model_validator(mode="after")
    def require_one_baseline(self) -> SecDeltaRequest:
        if bool(self.since_accession) == bool(self.since):
            raise ValueError("provide exactly one of since_accession or since")
        return self

    @field_validator("forms")
    @classmethod
    def unique_forms(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(value))

    @field_validator("rules")
    @classmethod
    def validate_rules(cls, value: list[str]) -> list[str]:
        rule_pattern = re.compile(
            r"^(?:FORM:(?:8-K|10-Q|10-K):ITEM:[0-9A-Za-z.]+|"
            r"XBRL:[A-Za-z0-9_-]+:[A-Za-z0-9_-]+)$"
        )
        cleaned = list(dict.fromkeys(rule.strip() for rule in value))
        invalid = [rule for rule in cleaned if not rule_pattern.fullmatch(rule)]
        if invalid:
            raise ValueError(f"unsupported deterministic rules: {invalid}")
        return cleaned


class SecSignalRequest(SecIssuerRequest):
    since: str
    forms: list[Literal["8-K", "10-Q", "10-K"]] = Field(
        default_factory=lambda: ["8-K", "10-Q", "10-K"],
        min_length=1,
        max_length=3,
    )

    @field_validator("since", mode="before")
    @classmethod
    def normalize_since(cls, value: Any) -> str:
        return normalize_since_timestamp(value)

    @field_validator("forms")
    @classmethod
    def unique_forms(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(value))


class FormDFundingLeadsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    since: str
    cursor: str | None = Field(
        default=None,
        pattern=r"^\d{10}-\d{2}-\d{6}$",
        description="Accession returned as next_cursor by a previous call.",
    )
    states: list[str] = Field(default_factory=list, max_length=20)
    industry_keywords: list[str] = Field(default_factory=list, max_length=10)
    minimum_amount_sold_usd: Decimal = Field(default=Decimal(0), ge=0)
    include_amendments: bool = False
    limit: int = Field(default=10, ge=1, le=25)
    max_source_age_seconds: int = Field(default=600, ge=60, le=3600)

    @field_validator("since", mode="before")
    @classmethod
    def normalize_since(cls, value: Any) -> str:
        normalized = normalize_since_timestamp(value)
        baseline = parse_utc(normalized).astimezone(timezone.utc)
        if datetime.now(timezone.utc) - baseline > timedelta(
            days=MAX_FORM_D_LOOKBACK_DAYS
        ):
            raise ValueError(
                f"since must be within the last {MAX_FORM_D_LOOKBACK_DAYS} days"
            )
        return normalized

    @field_validator("states")
    @classmethod
    def normalize_states(cls, value: list[str]) -> list[str]:
        cleaned = list(dict.fromkeys(item.strip().upper() for item in value))
        if any(not re.fullmatch(r"[A-Z]{2}", item) for item in cleaned):
            raise ValueError("states must be two-letter US postal abbreviations")
        return cleaned

    @field_validator("industry_keywords")
    @classmethod
    def normalize_industry_keywords(cls, value: list[str]) -> list[str]:
        cleaned = list(
            dict.fromkeys(" ".join(item.strip().split()).casefold() for item in value)
        )
        if any(not item or len(item) > 64 for item in cleaned):
            raise ValueError("industry keywords must contain 1-64 characters")
        return cleaned


class WebMonitorCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=12, max_length=2048)
    label: str | None = Field(default=None, max_length=120)
    webhook_url: str | None = Field(default=None, min_length=12, max_length=2048)

    @field_validator("url", "webhook_url")
    @classmethod
    def validate_public_https_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        parsed = urlparse(cleaned)
        if parsed.scheme.casefold() != "https" or not parsed.hostname:
            raise ValueError("URL must use HTTPS and include a public hostname")
        if parsed.username or parsed.password or parsed.fragment:
            raise ValueError("URL credentials and fragments are not supported")
        try:
            port = parsed.port
        except ValueError as exc:
            raise ValueError("URL port is invalid") from exc
        if port not in (None, 443):
            raise ValueError("only the default HTTPS port is supported")
        hostname = parsed.hostname.casefold()
        if hostname == "localhost" or hostname.endswith(
            (".localhost", ".local", ".internal")
        ):
            raise ValueError("URL must use a public internet hostname")
        try:
            literal = ipaddress.ip_address(hostname)
        except ValueError:
            literal = None
        if literal is not None and not literal.is_global:
            raise ValueError("URL must use a public internet address")
        return cleaned

    @field_validator("label")
    @classmethod
    def normalize_label(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = " ".join(value.split())
        if not cleaned:
            return None
        if any(ord(character) < 32 for character in cleaned):
            raise ValueError("label contains control characters")
        return cleaned


class OfacExactRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    identifier_type: Literal["crypto_address", "ofac_uid", "exact_name"]
    identifier: str = Field(min_length=1, max_length=256)
    networks: list[str] = Field(default_factory=list, max_length=8)
    lists: list[Literal["SDN", "CONSOLIDATED"]] = Field(
        default_factory=lambda: ["SDN", "CONSOLIDATED"],
        min_length=1,
        max_length=2,
    )

    @field_validator("identifier")
    @classmethod
    def validate_identifier(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("identifier cannot be blank")
        if any(ord(character) < 32 for character in cleaned):
            raise ValueError("identifier contains control characters")
        return cleaned

    @field_validator("networks")
    @classmethod
    def validate_networks(cls, value: list[str]) -> list[str]:
        pattern = re.compile(r"^[a-z0-9]+:[A-Za-z0-9_-]{1,64}$")
        cleaned = list(dict.fromkeys(item.strip() for item in value))
        if any(not pattern.fullmatch(item) for item in cleaned):
            raise ValueError("networks must use CAIP-2 identifiers")
        return cleaned

    @field_validator("lists")
    @classmethod
    def unique_lists(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(value))


class OfacPreflightRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    address: str = Field(pattern=r"^0x[0-9A-Fa-f]{40}$")
    network: str = Field(default="eip155:8453", pattern=r"^eip155:\d+$")

    @field_validator("address")
    @classmethod
    def normalize_address(cls, value: str) -> str:
        return value.lower()


@dataclass(frozen=True)
class SourceSnapshot:
    source_id: str
    source_version: str
    content_sha256: str
    retrieved_at: str
    verified_at: str
    published_at: str | None
    http_last_modified: str | None
    official_digest_sha256: str | None
    official_digest_verified: bool
    content: bytes


@dataclass(frozen=True)
class PreparedResult:
    request_id: str
    product: str
    request_hash: str
    source_bundle_hash: str
    result_hash: str
    result: dict[str, Any]


def _xml_text(element: Any, name: str) -> str | None:
    found = element.find(f"{{*}}{name}")
    if found is None or found.text is None:
        return None
    value = found.text.strip()
    return value or None


def _ofac_full_name(element: Any) -> str:
    parts = [_xml_text(element, "firstName"), _xml_text(element, "lastName")]
    return " ".join(part for part in parts if part)


class EvidenceService:
    def __init__(self, db_path: Path = EVIDENCE_DB_PATH) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        self.signing_key = self._load_or_create_signing_key()
        self.analytics_hmac_key = self._load_or_create_analytics_key()

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
                CREATE TABLE IF NOT EXISTS source_snapshots (
                    source_id TEXT NOT NULL,
                    source_version TEXT NOT NULL,
                    content_sha256 TEXT NOT NULL,
                    retrieved_at TEXT NOT NULL,
                    published_at TEXT,
                    http_last_modified TEXT,
                    official_digest_sha256 TEXT,
                    official_digest_verified INTEGER NOT NULL DEFAULT 0,
                    compressed_content BLOB NOT NULL,
                    PRIMARY KEY (source_id, content_sha256)
                );
                CREATE TABLE IF NOT EXISTS source_status (
                    source_id TEXT PRIMARY KEY,
                    current_content_sha256 TEXT NOT NULL,
                    source_version TEXT NOT NULL,
                    last_checked_at TEXT NOT NULL,
                    FOREIGN KEY (source_id, current_content_sha256)
                        REFERENCES source_snapshots(source_id, content_sha256)
                );
                CREATE TABLE IF NOT EXISTS ofac_lookup (
                    source_id TEXT NOT NULL,
                    source_content_sha256 TEXT NOT NULL,
                    list_name TEXT NOT NULL,
                    identifier_type TEXT NOT NULL,
                    normalized_value TEXT NOT NULL,
                    match_json TEXT NOT NULL,
                    PRIMARY KEY (
                        source_id,
                        source_content_sha256,
                        identifier_type,
                        normalized_value,
                        match_json
                    )
                );
                CREATE INDEX IF NOT EXISTS idx_ofac_lookup_exact
                    ON ofac_lookup(
                        source_id,
                        source_content_sha256,
                        identifier_type,
                        normalized_value
                    );
                CREATE TABLE IF NOT EXISTS prepared_results (
                    request_id TEXT PRIMARY KEY,
                    product TEXT NOT NULL,
                    request_hash TEXT NOT NULL,
                    source_bundle_hash TEXT NOT NULL,
                    result_hash TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(product, request_hash, source_bundle_hash)
                );
                CREATE TABLE IF NOT EXISTS evidence_attempts (
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
                    client_hmac TEXT,
                    user_agent_hmac TEXT,
                    user_agent_family TEXT,
                    referrer_origin TEXT,
                    edge_region TEXT,
                    proxy_request_id TEXT,
                    discovery_source TEXT,
                    agent_run_id_hmac TEXT,
                    request_fingerprint_hmac TEXT,
                    http_status INTEGER,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS fulfillments (
                    request_id TEXT NOT NULL,
                    payment_signature_hash TEXT NOT NULL,
                    settlement_tx_hash TEXT,
                    fulfilled_at TEXT NOT NULL,
                    PRIMARY KEY (request_id, payment_signature_hash),
                    FOREIGN KEY (request_id) REFERENCES prepared_results(request_id)
                );
                CREATE TABLE IF NOT EXISTS payment_bindings (
                    payment_signature_hash TEXT PRIMARY KEY,
                    request_id TEXT NOT NULL,
                    route TEXT NOT NULL,
                    canonical_request_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (request_id) REFERENCES prepared_results(request_id)
                );
                """
            )
            existing_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(evidence_attempts)"
                ).fetchall()
            }
            attribution_columns = {
                "client_hmac": "TEXT",
                "user_agent_hmac": "TEXT",
                "user_agent_family": "TEXT",
                "referrer_origin": "TEXT",
                "edge_region": "TEXT",
                "proxy_request_id": "TEXT",
                "discovery_source": "TEXT",
                "agent_run_id_hmac": "TEXT",
                "request_fingerprint_hmac": "TEXT",
                "http_status": "INTEGER",
            }
            for column, column_type in attribution_columns.items():
                if column not in existing_columns:
                    connection.execute(
                        f"ALTER TABLE evidence_attempts ADD COLUMN {column} {column_type}"
                    )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_evidence_attempts_request_fingerprint
                ON evidence_attempts(request_fingerprint_hmac, timestamp_utc)
                """
            )

    def _load_or_create_signing_key(self) -> Ed25519PrivateKey:
        configured = os.getenv("AUTONOMOUS_RECEIPT_SIGNING_KEY", "").strip()
        if configured:
            try:
                raw = base64.urlsafe_b64decode(
                    configured + "=" * (-len(configured) % 4)
                )
                return Ed25519PrivateKey.from_private_bytes(raw)
            except Exception as exc:
                raise SourceConfigurationError(
                    "INVALID_RECEIPT_SIGNING_KEY",
                    "AUTONOMOUS_RECEIPT_SIGNING_KEY must be a base64url Ed25519 private key",
                ) from exc

        key_path = RUNTIME_DIR / "receipt_ed25519.key"
        if key_path.exists():
            return Ed25519PrivateKey.from_private_bytes(key_path.read_bytes())
        private_key = Ed25519PrivateKey.generate()
        raw = private_key.private_bytes(
            serialization.Encoding.Raw,
            serialization.PrivateFormat.Raw,
            serialization.NoEncryption(),
        )
        key_path.parent.mkdir(parents=True, exist_ok=True)
        key_path.write_bytes(raw)
        key_path.chmod(0o600)
        return private_key

    def _load_or_create_analytics_key(self) -> bytes:
        configured = os.getenv("AUTONOMOUS_ANALYTICS_HMAC_KEY", "").encode("utf-8")
        if configured:
            return hashlib.sha256(configured).digest()
        key_path = RUNTIME_DIR / "analytics_hmac.key"
        if key_path.exists():
            return key_path.read_bytes()
        key = secrets.token_bytes(32)
        key_path.parent.mkdir(parents=True, exist_ok=True)
        key_path.write_bytes(key)
        key_path.chmod(0o600)
        return key

    def _receipt(self, result_hash: str) -> dict[str, str]:
        signature = self.signing_key.sign(result_hash.encode("ascii"))
        public_key = self.signing_key.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
        return {
            "algorithm": "Ed25519",
            "signed_payload_sha256": f"sha256:{result_hash}",
            "public_key_base64url": base64.urlsafe_b64encode(public_key)
            .decode()
            .rstrip("="),
            "signature_base64url": base64.urlsafe_b64encode(signature)
            .decode()
            .rstrip("="),
        }

    def _snapshot_from_row(self, row: sqlite3.Row) -> SourceSnapshot:
        return SourceSnapshot(
            source_id=row["source_id"],
            source_version=row["source_version"],
            content_sha256=row["content_sha256"],
            retrieved_at=row["retrieved_at"],
            verified_at=row["last_checked_at"],
            published_at=row["published_at"],
            http_last_modified=row["http_last_modified"],
            official_digest_sha256=row["official_digest_sha256"],
            official_digest_verified=bool(row["official_digest_verified"]),
            content=zlib.decompress(row["compressed_content"]),
        )

    def current_snapshot(self, source_id: str) -> SourceSnapshot | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT s.*, st.last_checked_at
                FROM source_status st
                JOIN source_snapshots s
                  ON s.source_id = st.source_id
                 AND s.content_sha256 = st.current_content_sha256
                WHERE st.source_id = ?
                """,
                (source_id,),
            ).fetchone()
        return self._snapshot_from_row(row) if row else None

    def _store_snapshot(
        self,
        *,
        source_id: str,
        source_version: str,
        content: bytes,
        published_at: str | None,
        http_last_modified: str | None,
        official_digest_sha256: str | None,
    ) -> SourceSnapshot:
        now = utc_now()
        content_sha256 = sha256_bytes(content)
        digest_verified = bool(
            official_digest_sha256
            and hmac.compare_digest(content_sha256, official_digest_sha256)
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO source_snapshots (
                    source_id, source_version, content_sha256, retrieved_at,
                    published_at, http_last_modified, official_digest_sha256,
                    official_digest_verified, compressed_content
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source_id,
                    source_version,
                    content_sha256,
                    now,
                    published_at,
                    http_last_modified,
                    official_digest_sha256,
                    int(digest_verified),
                    zlib.compress(content, level=6),
                ),
            )
            connection.execute(
                """
                INSERT INTO source_status (
                    source_id, current_content_sha256, source_version, last_checked_at
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(source_id) DO UPDATE SET
                    current_content_sha256 = excluded.current_content_sha256,
                    source_version = excluded.source_version,
                    last_checked_at = excluded.last_checked_at
                """,
                (source_id, content_sha256, source_version, now),
            )
            connection.commit()
        snapshot = self.current_snapshot(source_id)
        if snapshot is None:
            raise SourceSchemaError(f"failed to persist source snapshot {source_id}")
        return snapshot

    def _mark_source_verified(
        self, source_id: str, source_version: str
    ) -> SourceSnapshot:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE source_status
                SET source_version = ?, last_checked_at = ?
                WHERE source_id = ?
                """,
                (source_version, utc_now(), source_id),
            )
            connection.commit()
        snapshot = self.current_snapshot(source_id)
        if snapshot is None:
            raise SourceStaleError(f"no cached snapshot exists for {source_id}")
        return snapshot

    @staticmethod
    def _digest_from_headers(headers: httpx.Headers) -> str | None:
        value = headers.get("digest", "").strip()
        match = re.search(r"sha-?256[=: ]*([0-9a-fA-F]{64})", value)
        return match.group(1).lower() if match else None

    def import_ofac_file(
        self,
        list_name: Literal["SDN", "CONSOLIDATED"],
        content: bytes,
        *,
        source_version: str | None = None,
        http_last_modified: str | None = None,
        official_digest_sha256: str | None = None,
    ) -> SourceSnapshot:
        source_id = f"ofac:{list_name.lower()}"
        published_at, records = self._parse_ofac(content, list_name)
        snapshot = self._store_snapshot(
            source_id=source_id,
            source_version=source_version or sha256_bytes(content),
            content=content,
            published_at=published_at,
            http_last_modified=http_last_modified,
            official_digest_sha256=official_digest_sha256,
        )
        with self._connect() as connection:
            connection.executemany(
                """
                INSERT OR IGNORE INTO ofac_lookup (
                    source_id, source_content_sha256, list_name,
                    identifier_type, normalized_value, match_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        source_id,
                        snapshot.content_sha256,
                        list_name,
                        identifier_type,
                        normalized_value,
                        json.dumps(match, sort_keys=True, separators=(",", ":")),
                    )
                    for identifier_type, normalized_value, match in records
                ],
            )
            connection.commit()
        return snapshot

    def _parse_ofac(
        self,
        content: bytes,
        list_name: str,
    ) -> tuple[str | None, list[tuple[str, str, dict[str, Any]]]]:
        published_at: str | None = None
        records: list[tuple[str, str, dict[str, Any]]] = []
        try:
            context = ElementTree.iterparse(io.BytesIO(content), events=("end",))
            for _, element in context:
                tag = element.tag.rsplit("}", 1)[-1]
                if tag == "Publish_Date" and element.text and published_at is None:
                    raw = element.text.strip()
                    try:
                        published_at = (
                            datetime.strptime(raw, "%m/%d/%Y")
                            .replace(tzinfo=timezone.utc)
                            .isoformat()
                        )
                    except ValueError:
                        published_at = raw
                if tag != "sdnEntry":
                    continue

                entry_uid = _xml_text(element, "uid") or ""
                primary_name = _ofac_full_name(element)
                entry_type = _xml_text(element, "sdnType")
                programs = sorted(
                    {
                        item.text.strip()
                        for item in element.findall(".//{*}program")
                        if item.text and item.text.strip()
                    }
                )
                base_match = {
                    "list": list_name,
                    "entry_uid": entry_uid,
                    "primary_name": primary_name,
                    "entry_type": entry_type,
                    "programs": programs,
                }

                if entry_uid:
                    match = {
                        **base_match,
                        "matched_field": "entry_uid",
                        "matched_value": entry_uid,
                    }
                    records.append(("ofac_uid", entry_uid.lstrip("0") or "0", match))

                names: list[tuple[str, str]] = []
                if primary_name:
                    names.append(("primary_name", primary_name))
                for alias in element.findall(".//{*}aka"):
                    alias_name = _ofac_full_name(alias)
                    if alias_name:
                        names.append(("alias", alias_name))
                for matched_field, name in names:
                    match = {
                        **base_match,
                        "matched_field": matched_field,
                        "matched_value": name,
                    }
                    records.append(("exact_name", normalize_name(name), match))

                for identifier in element.findall(".//{*}id"):
                    identifier_type = _xml_text(identifier, "idType") or ""
                    identifier_value = _xml_text(identifier, "idNumber") or ""
                    if not identifier_type.startswith("Digital Currency Address - "):
                        continue
                    match = {
                        **base_match,
                        "matched_field": identifier_type,
                        "matched_value": identifier_value,
                    }
                    records.append(
                        (
                            "crypto_address",
                            normalize_crypto_identifier(identifier_value),
                            match,
                        )
                    )
                element.clear()
        except Exception as exc:
            raise SourceSchemaError(f"OFAC XML parsing failed: {exc}") from exc
        if not records:
            raise SourceSchemaError("OFAC source contained no exact-match records")
        return published_at, records

    def refresh_ofac(
        self,
        list_name: Literal["SDN", "CONSOLIDATED"],
        *,
        force: bool = False,
    ) -> SourceSnapshot:
        source_id = f"ofac:{list_name.lower()}"
        current = self.current_snapshot(source_id)
        if (
            current
            and not force
            and age_seconds(current.verified_at) <= OFAC_FRESHNESS_SECONDS
        ):
            return current

        url = OFAC_SOURCE_URLS[list_name]
        user_agent = os.getenv(
            "AUTONOMOUS_OFAC_USER_AGENT",
            "AutonomousEvidenceAPI/0.1 (+https://localhost.invalid)",
        )
        try:
            with httpx.Client(
                timeout=httpx.Timeout(90, connect=15),
                follow_redirects=True,
                headers={"User-Agent": user_agent},
            ) as client:
                head = client.head(url)
                digest = (
                    self._digest_from_headers(head.headers)
                    if head.status_code < 400
                    else None
                )
                last_modified = (
                    head.headers.get("last-modified")
                    if head.status_code < 400
                    else None
                )
                source_version = digest or last_modified
                if not source_version:
                    probe = client.get(url, follow_redirects=False)
                    probe.raise_for_status() if probe.status_code != 302 else None
                    location = probe.headers.get("location", "")
                    source_version = urlparse(location).path or probe.headers.get(
                        "etag"
                    )
                    last_modified = last_modified or probe.headers.get("last-modified")
                if not source_version:
                    raise SourceStaleError(
                        f"OFAC did not expose a version for {list_name}"
                    )
                if current and current.source_version == source_version and not force:
                    return self._mark_source_verified(source_id, source_version)

                response = client.get(url)
                response.raise_for_status()
                content = response.content
                digest = digest or self._digest_from_headers(response.headers)
                last_modified = last_modified or response.headers.get("last-modified")
        except EvidenceError:
            raise
        except Exception as exc:
            if current and age_seconds(current.verified_at) <= OFAC_FRESHNESS_SECONDS:
                return current
            raise SourceStaleError(f"OFAC {list_name} refresh failed: {exc}") from exc

        return self.import_ofac_file(
            list_name,
            content,
            source_version=source_version,
            http_last_modified=last_modified,
            official_digest_sha256=digest,
        )

    def _sec_user_agent(self) -> str:
        user_agent = os.getenv("AUTONOMOUS_SEC_USER_AGENT", "").strip()
        if not user_agent or not ("@" in user_agent or "http" in user_agent.lower()):
            raise SourceConfigurationError(
                "SEC_USER_AGENT_NOT_CONFIGURED",
                "Set AUTONOMOUS_SEC_USER_AGENT to an identifying product/org and contact email or URL",
            )
        return user_agent

    def _fetch_sec_source(
        self,
        source_id: str,
        url: str,
        max_age_seconds: int,
    ) -> SourceSnapshot:
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname not in {
            "data.sec.gov",
            "www.sec.gov",
        }:
            raise SourceConfigurationError(
                "SOURCE_NOT_ALLOWED", "SEC source URL is not allowlisted"
            )
        current = self.current_snapshot(source_id)
        if current and age_seconds(current.verified_at) <= max_age_seconds:
            return current

        headers = {
            "User-Agent": self._sec_user_agent(),
            "Accept-Encoding": "gzip, deflate",
        }
        if current and current.http_last_modified:
            headers["If-Modified-Since"] = current.http_last_modified
        try:
            response = httpx.get(
                url, headers=headers, timeout=30, follow_redirects=True
            )
            if response.status_code == 304 and current:
                return self._mark_source_verified(source_id, current.source_version)
            response.raise_for_status()
        except Exception as exc:
            if current and age_seconds(current.verified_at) <= max_age_seconds:
                return current
            raise SourceStaleError(
                f"SEC source refresh failed for {source_id}: {exc}"
            ) from exc

        content = response.content
        source_version = (
            response.headers.get("etag")
            or response.headers.get("last-modified")
            or sha256_bytes(content)
        )
        return self._store_snapshot(
            source_id=source_id,
            source_version=source_version,
            content=content,
            published_at=None,
            http_last_modified=response.headers.get("last-modified"),
            official_digest_sha256=None,
        )

    @staticmethod
    def _submission_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
        recent = payload.get("filings", {}).get("recent", payload)
        accession_numbers = recent.get("accessionNumber")
        if not isinstance(accession_numbers, list):
            raise SourceSchemaError(
                "SEC submissions payload has no accessionNumber array"
            )
        rows: list[dict[str, Any]] = []
        for index, accession in enumerate(accession_numbers):
            row = {}
            for key, values in recent.items():
                if isinstance(values, list) and index < len(values):
                    row[key] = values[index]
            row["accessionNumber"] = accession
            rows.append(row)
        return rows

    def _resolve_sec_cik(
        self,
        *,
        cik: str | None,
        ticker: str | None,
        max_age_seconds: int,
    ) -> tuple[str, SourceSnapshot | None]:
        if cik:
            return cik, None
        if not ticker:
            raise ContractError("ISSUER_REQUIRED", "provide a CIK or ticker")
        snapshot = self._fetch_sec_source(
            "sec:company-tickers",
            SEC_TICKERS_URL,
            max_age_seconds,
        )
        try:
            payload = json.loads(snapshot.content)
        except Exception as exc:
            raise SourceSchemaError(
                f"SEC ticker map JSON parsing failed: {exc}"
            ) from exc
        entries = payload.values() if isinstance(payload, dict) else []
        match = next(
            (
                item
                for item in entries
                if isinstance(item, dict)
                and str(item.get("ticker", "")).upper() == ticker
            ),
            None,
        )
        if not match or not str(match.get("cik_str", "")).isdigit():
            raise ContractError(
                "TICKER_NOT_FOUND",
                "ticker was not found in the official SEC company ticker map",
            )
        return str(match["cik_str"]).zfill(10), snapshot

    @staticmethod
    def _sec_row_timestamp(row: dict[str, Any]) -> datetime:
        accepted = str(row.get("acceptanceDateTime") or "").strip()
        if accepted:
            try:
                if re.fullmatch(r"\d{14}", accepted):
                    return datetime.strptime(accepted, "%Y%m%d%H%M%S").replace(
                        tzinfo=timezone.utc
                    )
                return parse_utc(accepted).astimezone(timezone.utc)
            except ValueError:
                pass
        filed = str(row.get("filingDate") or "").strip()
        try:
            return datetime.fromisoformat(filed).replace(tzinfo=timezone.utc)
        except ValueError as exc:
            raise SourceSchemaError(
                "SEC filing row has no valid filing timestamp"
            ) from exc

    @classmethod
    def _sec_rows_since(
        cls,
        rows: list[dict[str, Any]],
        since: str,
        forms: list[str],
    ) -> list[dict[str, Any]]:
        baseline = parse_utc(since).astimezone(timezone.utc)
        selected = [
            row
            for row in rows
            if row.get("form") in set(forms) and cls._sec_row_timestamp(row) > baseline
        ]
        return sorted(selected, key=cls._sec_row_timestamp, reverse=True)

    @staticmethod
    def _sec_document_url(cik: str, accession: str, primary_document: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", primary_document):
            raise SourceSchemaError("SEC primary document name was not safe")
        accession_path = accession.replace("-", "")
        return f"{SEC_ARCHIVES_ROOT}/{int(cik)}/{accession_path}/{primary_document}"

    @staticmethod
    def _form_d_index_url(index_date: date) -> str:
        quarter = ((index_date.month - 1) // 3) + 1
        return (
            f"{SEC_DAILY_INDEX_ROOT}/{index_date.year}/QTR{quarter}/"
            f"master.{index_date:%Y%m%d}.idx"
        )

    @staticmethod
    def _parse_form_d_index(content: bytes) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        try:
            text = content.decode("latin-1")
        except UnicodeDecodeError as exc:
            raise SourceSchemaError(f"SEC daily index decoding failed: {exc}") from exc
        for line in text.splitlines():
            parts = line.split("|", 4)
            if len(parts) != 5:
                continue
            cik, company_name, form, filed_date, filename = parts
            if form not in {"D", "D/A"}:
                continue
            accession_match = re.search(r"(\d{10}-\d{2}-\d{6})\.txt$", filename)
            if (
                not accession_match
                or not cik.isdigit()
                or not re.fullmatch(r"\d{8}", filed_date)
                or not filename.startswith("edgar/data/")
            ):
                raise SourceSchemaError(
                    "SEC daily index contained an invalid Form D row"
                )
            rows.append(
                {
                    "cik": cik.zfill(10),
                    "company_name": company_name.strip(),
                    "form": form,
                    "filed_date": filed_date,
                    "filename": filename,
                    "accession": accession_match.group(1),
                }
            )
        return rows

    @staticmethod
    def _form_d_money(root: Any, name: str) -> Decimal | None:
        element = root.find(f".//{{*}}{name}")
        if element is None or element.text is None:
            return None
        raw = element.text.strip()
        if not raw:
            return None
        try:
            return Decimal(raw)
        except InvalidOperation:
            return None

    @staticmethod
    def _form_d_bool(root: Any, name: str) -> bool:
        element = root.find(f".//{{*}}{name}")
        return bool(
            element is not None
            and element.text
            and element.text.strip().casefold() == "true"
        )

    @staticmethod
    def _form_d_descendant_text(root: Any, name: str) -> str | None:
        element = root.find(f".//{{*}}{name}")
        if element is None or element.text is None:
            return None
        value = " ".join(element.text.strip().split())
        return value or None

    @classmethod
    def _parse_form_d_submission(
        cls,
        content: bytes,
        row: dict[str, str],
        filing_txt_url: str,
    ) -> dict[str, Any]:
        xml_match = re.search(
            rb"(<edgarSubmission\b.*?</edgarSubmission>)",
            content,
            flags=re.DOTALL | re.IGNORECASE,
        )
        if not xml_match:
            raise SourceSchemaError(
                f"SEC Form D {row['accession']} contained no edgarSubmission XML"
            )
        try:
            root = ElementTree.fromstring(xml_match.group(1))
        except Exception as exc:
            raise SourceSchemaError(
                f"SEC Form D {row['accession']} XML parsing failed: {exc}"
            ) from exc

        accepted_match = re.search(rb"<ACCEPTANCE-DATETIME>(\d{14})", content)
        accepted_at = (
            datetime.strptime(accepted_match.group(1).decode(), "%Y%m%d%H%M%S")
            .replace(tzinfo=timezone.utc)
            .isoformat()
            if accepted_match
            else datetime.strptime(row["filed_date"], "%Y%m%d")
            .replace(tzinfo=timezone.utc)
            .isoformat()
        )
        primary_filename_match = re.search(
            rb"<FILENAME>([A-Za-z0-9_.-]+\.xml)", content, flags=re.IGNORECASE
        )
        primary_filename = (
            primary_filename_match.group(1).decode("ascii")
            if primary_filename_match
            else "primary_doc.xml"
        )
        accession_path = row["accession"].replace("-", "")
        primary_document_url = (
            f"{SEC_ARCHIVES_ROOT}/{int(row['cik'])}/{accession_path}/{primary_filename}"
        )

        issuer = root.find(".//{*}primaryIssuer")
        address = issuer.find(".//{*}issuerAddress") if issuer is not None else None
        industry = cls._form_d_descendant_text(root, "industryGroupType")
        total_offering = cls._form_d_money(root, "totalOfferingAmount")
        amount_sold = cls._form_d_money(root, "totalAmountSold")
        amount_remaining = cls._form_d_money(root, "totalRemaining")
        minimum_investment = cls._form_d_money(root, "minimumInvestmentAccepted")
        first_sale_element = root.find(".//{*}dateOfFirstSale/{*}value")
        first_sale_date = (
            first_sale_element.text.strip()
            if first_sale_element is not None and first_sale_element.text
            else None
        )
        investor_count_text = cls._form_d_descendant_text(
            root, "totalNumberAlreadyInvested"
        )
        investor_count = (
            int(investor_count_text)
            if investor_count_text and investor_count_text.isdigit()
            else None
        )

        security_flags = {
            "equity": "isEquityType",
            "debt": "isDebtType",
            "option_or_warrant": "isOptionToAcquireType",
            "pooled_investment_fund": "isPooledInvestmentFundType",
            "tenant_in_common": "isTenantInCommonType",
            "mineral_property": "isMineralPropertyType",
            "other": "isOtherType",
        }
        securities = [
            label
            for label, tag in security_flags.items()
            if cls._form_d_bool(root, tag)
        ]

        related_people: list[dict[str, Any]] = []
        for person in root.findall(".//{*}relatedPersonInfo"):
            name_element = person.find(".//{*}relatedPersonName")
            name_parts = (
                [
                    _xml_text(name_element, "firstName"),
                    _xml_text(name_element, "middleName"),
                    _xml_text(name_element, "lastName"),
                ]
                if name_element is not None
                else []
            )
            roles = sorted(
                {
                    item.text.strip()
                    for item in person.findall(".//{*}relationship")
                    if item.text and item.text.strip()
                }
            )
            full_name = " ".join(part for part in name_parts if part)
            if full_name:
                related_people.append({"name": full_name, "roles": roles})

        def money_string(value: Decimal | None) -> str | None:
            return format(value, "f") if value is not None else None

        return {
            "trigger": "NEW_SEC_FORM_D_FILING",
            "accession": row["accession"],
            "filing_type": row["form"],
            "filed_at": accepted_at,
            "issuer": {
                "cik": row["cik"],
                "name": (
                    cls._form_d_descendant_text(issuer, "entityName")
                    if issuer is not None
                    else None
                )
                or row["company_name"],
                "entity_type": cls._form_d_descendant_text(root, "entityType"),
                "jurisdiction_of_incorporation": cls._form_d_descendant_text(
                    root, "jurisdictionOfInc"
                ),
                "location": {
                    "city": _xml_text(address, "city") if address is not None else None,
                    "state_or_country": (
                        _xml_text(address, "stateOrCountry")
                        if address is not None
                        else None
                    ),
                    "postal_code": (
                        _xml_text(address, "zipCode") if address is not None else None
                    ),
                },
            },
            "industry": industry,
            "funding_signal": {
                "basis": "FORM_D_REPORTED_EXEMPT_OFFERING",
                "total_offering_amount_usd": money_string(total_offering),
                "amount_sold_usd": money_string(amount_sold),
                "amount_remaining_usd": money_string(amount_remaining),
                "offering_amount_indefinite": cls._form_d_bool(root, "isIndefinite"),
                "date_of_first_sale": first_sale_date,
                "minimum_investment_usd": money_string(minimum_investment),
                "investor_count": investor_count,
                "securities": securities,
            },
            "related_people": related_people[:MAX_FORM_D_RELATED_PEOPLE],
            "related_people_truncated": len(related_people) > MAX_FORM_D_RELATED_PEOPLE,
            "official_source_urls": {
                "filing_submission": filing_txt_url,
                "primary_document": primary_document_url,
            },
        }

    @staticmethod
    def _form_d_matches(
        lead: dict[str, Any], request: FormDFundingLeadsRequest
    ) -> bool:
        state = str(lead["issuer"]["location"].get("state_or_country") or "").upper()
        if request.states and state not in set(request.states):
            return False
        industry = str(lead.get("industry") or "").casefold()
        if request.industry_keywords and not any(
            keyword in industry for keyword in request.industry_keywords
        ):
            return False
        amount_sold = lead["funding_signal"].get("amount_sold_usd")
        try:
            parsed_amount_sold = (
                Decimal(amount_sold) if amount_sold is not None else None
            )
        except InvalidOperation:
            parsed_amount_sold = None
        return bool(
            request.minimum_amount_sold_usd == 0
            or (
                parsed_amount_sold is not None
                and parsed_amount_sold >= request.minimum_amount_sold_usd
            )
        )

    @staticmethod
    def _selected_fact_deltas(
        companyfacts: dict[str, Any],
        rules: list[str],
        new_filings: dict[str, str | None],
    ) -> list[dict[str, Any]]:
        def duration_days(fact: dict[str, Any]) -> int | None:
            if not fact.get("start") or not fact.get("end"):
                return None
            try:
                start = datetime.fromisoformat(fact["start"])
                end = datetime.fromisoformat(fact["end"])
                return (end - start).days
            except ValueError:
                return None

        deltas: list[dict[str, Any]] = []
        for rule in sorted(rule for rule in rules if rule.startswith("XBRL:")):
            _, taxonomy, concept = rule.split(":", 2)
            concept_data = companyfacts.get("facts", {}).get(taxonomy, {}).get(concept)
            if not concept_data:
                deltas.append(
                    {
                        "concept": f"{taxonomy}:{concept}",
                        "status": "CONCEPT_NOT_PRESENT",
                    }
                )
                continue
            concept_delta_count = 0
            for unit, facts in sorted(concept_data.get("units", {}).items()):
                ordered = sorted(
                    [fact for fact in facts if fact.get("accn")],
                    key=lambda fact: (
                        fact.get("filed", ""),
                        fact.get("end", ""),
                        fact.get("accn", ""),
                    ),
                )
                seen_contexts: set[tuple[Any, ...]] = set()
                for current in ordered:
                    current_accession = current.get("accn")
                    if current_accession not in new_filings:
                        continue
                    report_date = new_filings[current_accession]
                    if report_date and current.get("end") != report_date:
                        continue
                    context_key = (
                        current_accession,
                        current.get("start"),
                        current.get("end"),
                        current.get("frame"),
                        current.get("form"),
                        current.get("fp"),
                        current.get("val"),
                    )
                    if context_key in seen_contexts:
                        continue
                    seen_contexts.add(context_key)

                    current_duration = duration_days(current)
                    candidates = [
                        candidate
                        for candidate in ordered
                        if candidate.get("accn") != current_accession
                        and candidate.get("form") == current.get("form")
                        and candidate.get("filed", "") < current.get("filed", "")
                    ]
                    comparable = [
                        candidate
                        for candidate in candidates
                        if candidate.get("fp") == current.get("fp")
                        and (
                            current_duration is None
                            and duration_days(candidate) is None
                            or current_duration is not None
                            and duration_days(candidate) is not None
                            and abs(duration_days(candidate) - current_duration) <= 7
                        )
                    ]
                    if not comparable:
                        comparable = [
                            candidate
                            for candidate in candidates
                            if (
                                current_duration is None
                                and duration_days(candidate) is None
                                or current_duration is not None
                                and duration_days(candidate) is not None
                                and abs(duration_days(candidate) - current_duration)
                                <= 7
                            )
                        ]
                    previous = next(
                        iter(
                            sorted(
                                comparable,
                                key=lambda candidate: (
                                    candidate.get("filed", ""),
                                    candidate.get("end", ""),
                                    candidate.get("accn", ""),
                                ),
                                reverse=True,
                            )
                        ),
                        None,
                    )
                    deltas.append(
                        {
                            "concept": f"{taxonomy}:{concept}",
                            "unit": unit,
                            "period_start": current.get("start"),
                            "period_end": current.get("end"),
                            "fiscal_period": current.get("fp"),
                            "frame": current.get("frame"),
                            "previous_value": previous.get("val") if previous else None,
                            "previous_period_start": previous.get("start")
                            if previous
                            else None,
                            "previous_period_end": previous.get("end")
                            if previous
                            else None,
                            "current_value": current.get("val"),
                            "previous_accession": previous.get("accn")
                            if previous
                            else None,
                            "source_accession": current_accession,
                        }
                    )
                    concept_delta_count += 1
            if concept_delta_count == 0:
                deltas.append(
                    {
                        "concept": f"{taxonomy}:{concept}",
                        "status": "NO_FACT_IN_SELECTED_FILINGS",
                    }
                )
        return sorted(
            deltas,
            key=lambda row: (
                row.get("concept", ""),
                row.get("unit", ""),
                row.get("source_accession", ""),
            ),
        )

    def _cached_prepared(
        self,
        product: str,
        request_hash: str,
        source_bundle_hash: str,
    ) -> PreparedResult | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM prepared_results
                WHERE product = ? AND request_hash = ? AND source_bundle_hash = ?
                """,
                (product, request_hash, source_bundle_hash),
            ).fetchone()
        if not row:
            return None
        return PreparedResult(
            request_id=row["request_id"],
            product=row["product"],
            request_hash=row["request_hash"],
            source_bundle_hash=row["source_bundle_hash"],
            result_hash=row["result_hash"],
            result=json.loads(row["result_json"]),
        )

    def _store_prepared(
        self,
        product: str,
        request_hash: str,
        source_bundle_hash: str,
        result_core: dict[str, Any],
        *,
        include_receipt: bool = True,
    ) -> PreparedResult:
        hash_payload = json.loads(json.dumps(result_core))
        if isinstance(hash_payload.get("provenance"), dict):
            hash_payload["provenance"]["result_sha256"] = None
        result_hash = sha256_json(hash_payload)
        result_core = json.loads(json.dumps(result_core))
        if isinstance(result_core.get("provenance"), dict):
            result_core["provenance"]["result_sha256"] = f"sha256:{result_hash}"
        request_id = f"{product}_{sha256_bytes(f'{request_hash}:{source_bundle_hash}'.encode())[:26]}"
        result = {
            "request_id": request_id,
            **result_core,
        }
        if include_receipt:
            result["receipt"] = self._receipt(result_hash)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO prepared_results (
                    request_id, product, request_hash, source_bundle_hash,
                    result_hash, result_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request_id,
                    product,
                    request_hash,
                    source_bundle_hash,
                    result_hash,
                    json.dumps(result, sort_keys=True, separators=(",", ":")),
                    utc_now(),
                ),
            )
            connection.commit()
        prepared = self._cached_prepared(product, request_hash, source_bundle_hash)
        if prepared is None:
            raise SourceSchemaError("failed to persist prepared result")
        return prepared

    def prepare_form_d_funding_leads(
        self, request: FormDFundingLeadsRequest
    ) -> PreparedResult:
        request_payload = request.model_dump(mode="json")
        request_hash = sha256_json(request_payload)
        baseline = parse_utc(request.since).astimezone(timezone.utc)
        today = datetime.now(timezone.utc).date()
        index_start = min(baseline.date(), today - timedelta(days=3))

        index_snapshots: list[SourceSnapshot] = []
        index_urls: list[str] = []
        index_dates: list[str] = []
        rows: list[dict[str, str]] = []
        current_date = index_start
        while current_date <= today:
            if current_date.weekday() < 5:
                index_url = self._form_d_index_url(current_date)
                try:
                    snapshot = self._fetch_sec_source(
                        f"sec:daily-master:{current_date:%Y%m%d}",
                        index_url,
                        request.max_source_age_seconds,
                    )
                except SourceStaleError as exc:
                    if current_date == today or any(
                        status in exc.detail for status in ("403", "404")
                    ):
                        current_date += timedelta(days=1)
                        continue
                    raise
                index_snapshots.append(snapshot)
                index_urls.append(index_url)
                index_dates.append(current_date.isoformat())
                rows.extend(self._parse_form_d_index(snapshot.content))
            current_date += timedelta(days=1)

        if not index_snapshots:
            raise SourceStaleError(
                "No SEC daily master index was available for the requested window"
            )

        allowed_forms = {"D", "D/A"} if request.include_amendments else {"D"}
        baseline_date = baseline.strftime("%Y%m%d")
        candidates = sorted(
            (
                row
                for row in rows
                if row["form"] in allowed_forms and row["filed_date"] >= baseline_date
            ),
            key=lambda row: (row["filed_date"], row["accession"]),
            reverse=True,
        )
        if request.cursor:
            cursor_index = next(
                (
                    index
                    for index, row in enumerate(candidates)
                    if row["accession"] == request.cursor
                ),
                None,
            )
            if cursor_index is None:
                raise ContractError(
                    "CURSOR_NOT_AVAILABLE",
                    "cursor was not found in the bounded SEC Form D window",
                )
            candidates = candidates[cursor_index + 1 :]

        source_snapshots = list(index_snapshots)
        leads: list[dict[str, Any]] = []
        parse_failure_accessions: list[str] = []
        scanned_rows: list[dict[str, str]] = []
        for row in candidates[:MAX_FORM_D_SCAN_PER_REQUEST]:
            scanned_rows.append(row)
            filing_txt_url = f"{SEC_ARCHIVES_BASE}/{row['filename']}"
            filing_snapshot = self._fetch_sec_source(
                f"sec:form-d-submission:{row['accession']}",
                filing_txt_url,
                request.max_source_age_seconds,
            )
            source_snapshots.append(filing_snapshot)
            try:
                lead = self._parse_form_d_submission(
                    filing_snapshot.content,
                    row,
                    filing_txt_url,
                )
            except SourceSchemaError:
                parse_failure_accessions.append(row["accession"])
                continue
            if parse_utc(lead["filed_at"]) <= baseline:
                continue
            if not self._form_d_matches(lead, request):
                continue
            leads.append(lead)
            if len(leads) >= request.limit:
                break

        scanned_count = len(scanned_rows)
        has_more = scanned_count < len(candidates)
        next_cursor = (
            scanned_rows[-1]["accession"] if has_more and scanned_rows else None
        )
        source_payload_hash = sha256_json(
            sorted({snapshot.content_sha256 for snapshot in source_snapshots})
        )
        source_bundle_hash = sha256_json(
            {
                "parser_version": FORM_D_PARSER_VERSION,
                "source_payload_hash": source_payload_hash,
            }
        )
        cached = self._cached_prepared(
            "form_d_funding_leads", request_hash, source_bundle_hash
        )
        if cached:
            return cached

        source_snapshot_at = max(snapshot.retrieved_at for snapshot in source_snapshots)
        result_core = {
            "decision": (
                "FORM_D_FUNDING_SIGNALS_FOUND"
                if leads
                else "NO_MATCHING_FORM_D_FUNDING_SIGNALS"
            ),
            "lead_count": len(leads),
            "leads": leads,
            "filters": {
                "states": request.states,
                "industry_keywords": request.industry_keywords,
                "minimum_amount_sold_usd": format(request.minimum_amount_sold_usd, "f"),
                "include_amendments": request.include_amendments,
            },
            "pagination": {
                "since": request.since,
                "cursor": request.cursor,
                "next_cursor": next_cursor,
                "has_more": has_more,
                "candidate_count_remaining_at_page_start": len(candidates),
                "scanned_count": scanned_count,
                "scan_limit": MAX_FORM_D_SCAN_PER_REQUEST,
            },
            "provenance": {
                "publisher": "U.S. Securities and Exchange Commission",
                "official_index_urls": index_urls,
                "available_through": max(index_dates),
                "source_snapshot_at": source_snapshot_at,
                "source_payload_sha256": f"sha256:{source_payload_hash}",
                "component_source_hashes": sorted(
                    {
                        f"sha256:{snapshot.content_sha256}"
                        for snapshot in source_snapshots
                    }
                ),
                "parser_version": FORM_D_PARSER_VERSION,
                "parse_failure_accessions": parse_failure_accessions,
                "result_sha256": None,
            },
            "limitations": [
                "Form D is a notice of an exempt offering, not proof that the total offering amount was raised.",
                "Amount sold and related-person data are issuer-reported to the SEC and may be amended.",
                "Daily-index filing dates have day precision; use accession deduplication when polling overlapping windows.",
                "This is a factual GTM signal, not investment, legal, or solicitation advice.",
            ],
        }
        return self._store_prepared(
            "form_d_funding_leads",
            request_hash,
            source_bundle_hash,
            result_core,
        )

    def prepare_web_monitor(self, request: WebMonitorCreateRequest) -> PreparedResult:
        request_payload = request.model_dump(mode="json")
        request_hash = sha256_json(request_payload)
        source_bundle_hash = sha256_bytes(b"source-change-watch/1.0")
        result_core = {
            "product": "SOURCE_CHANGE_WATCH_30_DAY",
            "url": request.url,
            "label": request.label,
            "webhook_configured": bool(request.webhook_url),
            "service_terms": {
                "duration_days": 30,
                "check_interval_seconds": 21600,
                "maximum_response_bytes": 1000000,
                "supported_scheme": "https",
                "redirects_followed": False,
            },
            "provenance": {
                "engine_version": "source-change-watch/1.0",
                "request_sha256": f"sha256:{request_hash}",
                "result_sha256": None,
            },
            "limitations": [
                "Public HTTPS text, HTML, JSON, and XML sources only.",
                "JavaScript-rendered content and authenticated pages are not supported.",
                "A successful payment creates the monitor; the first baseline check runs asynchronously.",
            ],
        }
        return self._store_prepared(
            "source_change_watch",
            request_hash,
            source_bundle_hash,
            result_core,
            include_receipt=False,
        )

    def prepare_sec(self, request: SecDeltaRequest) -> PreparedResult:
        request_payload = request.model_dump(mode="json")
        request_hash = sha256_json(request_payload)
        resolved_cik, ticker_snapshot = self._resolve_sec_cik(
            cik=request.cik,
            ticker=request.ticker,
            max_age_seconds=request.max_source_age_seconds,
        )
        submissions = self._fetch_sec_source(
            f"sec:submissions:{resolved_cik}",
            SEC_SUBMISSIONS_URL.format(cik=resolved_cik),
            request.max_source_age_seconds,
        )
        try:
            submissions_payload = json.loads(submissions.content)
        except Exception as exc:
            raise SourceSchemaError(
                f"SEC submissions JSON parsing failed: {exc}"
            ) from exc

        source_snapshots = [
            snapshot for snapshot in (ticker_snapshot, submissions) if snapshot
        ]
        rows = self._submission_rows(submissions_payload)
        accessions = {row.get("accessionNumber") for row in rows}
        if request.since_accession and request.since_accession not in accessions:
            supplemental = submissions_payload.get("filings", {}).get("files", [])
            for item in supplemental[:MAX_SEC_SUPPLEMENTAL_FILES]:
                name = item.get("name", "")
                if not re.fullmatch(r"CIK\d+-submissions-\d{3}\.json", name):
                    continue
                snapshot = self._fetch_sec_source(
                    f"sec:submissions-file:{name}",
                    f"https://data.sec.gov/submissions/{name}",
                    request.max_source_age_seconds,
                )
                source_snapshots.append(snapshot)
                try:
                    rows.extend(self._submission_rows(json.loads(snapshot.content)))
                except Exception as exc:
                    raise SourceSchemaError(
                        f"SEC supplemental parsing failed: {exc}"
                    ) from exc
                if any(
                    row.get("accessionNumber") == request.since_accession
                    for row in rows
                ):
                    break

        if request.since_accession:
            baseline_index = next(
                (
                    index
                    for index, row in enumerate(rows)
                    if row.get("accessionNumber") == request.since_accession
                ),
                None,
            )
            if baseline_index is None:
                raise ContractError(
                    "BASELINE_NOT_AVAILABLE",
                    "since_accession was not found in the bounded SEC filing history",
                )
            selected_rows = [
                row
                for row in rows[:baseline_index]
                if row.get("form") in set(request.forms)
            ]
        else:
            selected_rows = self._sec_rows_since(
                rows,
                request.since or "",
                request.forms,
            )
        if len(selected_rows) > MAX_SEC_FILINGS:
            raise ContractError(
                "RESULT_LIMIT_EXCEEDED",
                f"request would return more than {MAX_SEC_FILINGS} filings; use a newer baseline",
            )

        filings: list[dict[str, Any]] = []
        for row in selected_rows:
            accession = row.get("accessionNumber", "")
            primary_document = row.get("primaryDocument", "")
            document_url = self._sec_document_url(
                resolved_cik, accession, primary_document
            )
            document = self._fetch_sec_source(
                f"sec:document:{accession}:{primary_document}",
                document_url,
                request.max_source_age_seconds,
            )
            source_snapshots.append(document)
            items = [
                item.strip()
                for item in str(row.get("items", "")).split(",")
                if item.strip()
            ]
            matched_rules = [
                rule
                for rule in request.rules
                if rule.startswith(f"FORM:{row.get('form')}:ITEM:")
                and rule.rsplit(":", 1)[-1] in items
            ]
            filings.append(
                {
                    "accession": accession,
                    "form": row.get("form"),
                    "filed_at": row.get("acceptanceDateTime") or row.get("filingDate"),
                    "report_date": row.get("reportDate"),
                    "item_headings": [f"Item {item}" for item in items],
                    "matched_rules": matched_rules,
                    "primary_document_url": document_url,
                    "document_sha256": f"sha256:{document.content_sha256}",
                }
            )

        selected_fact_deltas: list[dict[str, Any]] = []
        xbrl_rules = [rule for rule in request.rules if rule.startswith("XBRL:")]
        if xbrl_rules and filings:
            companyfacts = self._fetch_sec_source(
                f"sec:companyfacts:{resolved_cik}",
                SEC_COMPANYFACTS_URL.format(cik=resolved_cik),
                request.max_source_age_seconds,
            )
            source_snapshots.append(companyfacts)
            try:
                companyfacts_payload = json.loads(companyfacts.content)
            except Exception as exc:
                raise SourceSchemaError(
                    f"SEC companyfacts JSON parsing failed: {exc}"
                ) from exc
            selected_fact_deltas = self._selected_fact_deltas(
                companyfacts_payload,
                xbrl_rules,
                {filing["accession"]: filing["report_date"] for filing in filings},
            )
            if len(selected_fact_deltas) > 100:
                raise ContractError(
                    "RESULT_LIMIT_EXCEEDED",
                    "selected XBRL rules produced more than 100 fact deltas",
                )

        source_payload_hash = sha256_json(
            sorted({snapshot.content_sha256 for snapshot in source_snapshots})
        )
        source_bundle_hash = sha256_json(
            {
                "parser_version": SEC_PARSER_VERSION,
                "source_payload_hash": source_payload_hash,
            }
        )
        cached = self._cached_prepared("sec", request_hash, source_bundle_hash)
        if cached:
            return cached

        source_snapshot_at = max(snapshot.retrieved_at for snapshot in source_snapshots)
        source_hashes = sorted(
            {f"sha256:{snapshot.content_sha256}" for snapshot in source_snapshots}
        )
        result_core = {
            "decision": "NEW_FILING" if filings else "NO_NEW_FILING",
            "checked_at": source_snapshot_at,
            "issuer": {
                "cik": resolved_cik,
                "name": submissions_payload.get("name"),
                "tickers_observed": sorted(submissions_payload.get("tickers") or []),
            },
            "baseline": (
                {"type": "accession", "value": request.since_accession}
                if request.since_accession
                else {"type": "timestamp", "value": request.since}
            ),
            "baseline_accession": request.since_accession,
            "filings": filings,
            "selected_fact_deltas": selected_fact_deltas,
            "provenance": {
                "publisher": "U.S. Securities and Exchange Commission",
                "source_snapshot_at": source_snapshot_at,
                "source_payload_sha256": f"sha256:{source_payload_hash}",
                "component_source_hashes": source_hashes,
                "parser_version": SEC_PARSER_VERSION,
                "result_sha256": None,
            },
            "limitations": [
                "Factual filing record only; not investment advice.",
                "No filing result does not establish that no material event occurred.",
            ],
        }
        return self._store_prepared(
            "sec", request_hash, source_bundle_hash, result_core
        )

    def prepare_sec_signal(self, request: SecSignalRequest) -> PreparedResult:
        request_payload = request.model_dump(mode="json")
        request_hash = sha256_json(request_payload)
        resolved_cik, ticker_snapshot = self._resolve_sec_cik(
            cik=request.cik,
            ticker=request.ticker,
            max_age_seconds=request.max_source_age_seconds,
        )
        submissions = self._fetch_sec_source(
            f"sec:submissions:{resolved_cik}",
            SEC_SUBMISSIONS_URL.format(cik=resolved_cik),
            request.max_source_age_seconds,
        )
        try:
            submissions_payload = json.loads(submissions.content)
        except Exception as exc:
            raise SourceSchemaError(
                f"SEC submissions JSON parsing failed: {exc}"
            ) from exc

        snapshots = [
            snapshot for snapshot in (ticker_snapshot, submissions) if snapshot
        ]
        rows = self._sec_rows_since(
            self._submission_rows(submissions_payload),
            request.since,
            request.forms,
        )
        source_payload_hash = sha256_json(
            sorted(snapshot.content_sha256 for snapshot in snapshots)
        )
        source_bundle_hash = sha256_json(
            {
                "parser_version": SEC_PARSER_VERSION,
                "source_payload_hash": source_payload_hash,
                "product": "filing-change-signal",
            }
        )
        cached = self._cached_prepared("sec_signal", request_hash, source_bundle_hash)
        if cached:
            return cached

        filings = []
        for row in rows[:MAX_SEC_FILINGS]:
            accession = str(row.get("accessionNumber") or "")
            primary_document = str(row.get("primaryDocument") or "")
            filings.append(
                {
                    "accession": accession,
                    "form": row.get("form"),
                    "filed_at": row.get("acceptanceDateTime") or row.get("filingDate"),
                    "primary_document_url": self._sec_document_url(
                        resolved_cik,
                        accession,
                        primary_document,
                    ),
                }
            )
        result_core = {
            "decision": "NEW_RELEVANT_FILING" if rows else "NO_NEW_RELEVANT_FILING",
            "issuer": {
                "cik": resolved_cik,
                "name": submissions_payload.get("name"),
                "tickers_observed": sorted(submissions_payload.get("tickers") or []),
            },
            "since": request.since,
            "next_since": submissions.retrieved_at,
            "forms_checked": request.forms,
            "filing_count": len(rows),
            "filings": filings,
            "truncated": len(rows) > MAX_SEC_FILINGS,
            "premium_evidence_path": "/v1/sec/filing-trigger-delta",
            "provenance": {
                "publisher": "U.S. Securities and Exchange Commission",
                "official_source_url": SEC_SUBMISSIONS_URL.format(cik=resolved_cik),
                "source_snapshot_at": submissions.retrieved_at,
                "parser_version": SEC_PARSER_VERSION,
                "result_sha256": None,
            },
            "limitations": [
                "Filing-presence signal only; no materiality opinion or investment advice.",
                "The premium endpoint adds document hashes, selected fact deltas, and a signed receipt.",
            ],
        }
        return self._store_prepared(
            "sec_signal",
            request_hash,
            source_bundle_hash,
            result_core,
            include_receipt=False,
        )

    def prepare_ofac(self, request: OfacExactRequest) -> PreparedResult:
        request_payload = request.model_dump(mode="json")
        request_hash = sha256_json(request_payload)
        snapshots = [self.refresh_ofac(list_name) for list_name in request.lists]
        source_payload_hash = sha256_json(
            sorted(snapshot.content_sha256 for snapshot in snapshots)
        )
        source_bundle_hash = sha256_json(
            {
                "parser_version": OFAC_PARSER_VERSION,
                "source_payload_hash": source_payload_hash,
            }
        )
        cached = self._cached_prepared("ofac", request_hash, source_bundle_hash)
        if cached:
            return cached

        if request.identifier_type == "crypto_address":
            normalized = normalize_crypto_identifier(request.identifier)
        elif request.identifier_type == "exact_name":
            normalized = normalize_name(request.identifier)
        else:
            normalized = request.identifier.lstrip("0") or "0"

        matches: list[dict[str, Any]] = []
        with self._connect() as connection:
            for snapshot in snapshots:
                rows = connection.execute(
                    """
                    SELECT match_json FROM ofac_lookup
                    WHERE source_id = ?
                      AND source_content_sha256 = ?
                      AND identifier_type = ?
                      AND normalized_value = ?
                    ORDER BY match_json
                    """,
                    (
                        snapshot.source_id,
                        snapshot.content_sha256,
                        request.identifier_type,
                        normalized,
                    ),
                ).fetchall()
                matches.extend(json.loads(row["match_json"]) for row in rows)
        unique_matches = [
            json.loads(value)
            for value in sorted(
                {json.dumps(match, sort_keys=True) for match in matches}
            )
        ]
        if request.identifier_type == "crypto_address" and request.networks:
            expected_symbols: set[str] = set()
            if any(network.startswith("eip155:") for network in request.networks):
                expected_symbols.add("ETH")
            if expected_symbols:
                unique_matches = [
                    match
                    for match in unique_matches
                    if any(
                        str(match.get("matched_field", "")).endswith(f" - {symbol}")
                        for symbol in expected_symbols
                    )
                ]
        source_versions = [
            {
                "source": "OFAC Sanctions List Service",
                "list": snapshot.source_id.rsplit(":", 1)[-1].upper(),
                "published_at": snapshot.published_at,
                "retrieved_at": snapshot.retrieved_at,
                "verified_current_at": snapshot.verified_at,
                "content_sha256": f"sha256:{snapshot.content_sha256}",
                "official_digest_sha256": (
                    f"sha256:{snapshot.official_digest_sha256}"
                    if snapshot.official_digest_sha256
                    else None
                ),
                "official_digest_verified": snapshot.official_digest_verified,
            }
            for snapshot in snapshots
        ]
        result_core = {
            "match_scope": "exact_normalized_identifier_only",
            "match_status": "EXACT_MATCH" if unique_matches else "NO_EXACT_MATCH",
            "matches": unique_matches,
            "source_versions": source_versions,
            "provenance": {
                "normalization_version": OFAC_PARSER_VERSION,
                "source_payload_sha256": f"sha256:{source_payload_hash}",
                "result_sha256": None,
            },
            "limitations": [
                "No fuzzy-name matching.",
                "No-match is not sanctions clearance or legal advice.",
                "Does not assess ownership, control, aliases not supplied, or transaction legality.",
            ],
        }
        return self._store_prepared(
            "ofac", request_hash, source_bundle_hash, result_core
        )

    def prepare_ofac_preflight(self, request: OfacPreflightRequest) -> PreparedResult:
        request_payload = request.model_dump(mode="json")
        request_hash = sha256_json(request_payload)
        premium = self.prepare_ofac(
            OfacExactRequest(
                identifier_type="crypto_address",
                identifier=request.address,
                networks=[request.network],
                lists=["SDN", "CONSOLIDATED"],
            )
        )
        cached = self._cached_prepared(
            "ofac_preflight", request_hash, premium.source_bundle_hash
        )
        if cached:
            return cached

        source_versions = premium.result.get("source_versions", [])
        checked_at_values = [
            str(item.get("verified_current_at"))
            for item in source_versions
            if item.get("verified_current_at")
        ]
        checked_at = max(checked_at_values) if checked_at_values else utc_now()
        matched = premium.result.get("match_status") == "EXACT_MATCH"
        result_core = {
            "decision": (
                "STOP_EXACT_OFAC_MATCH" if matched else "NO_EXACT_OFAC_MATCH_FOUND"
            ),
            "address": request.address,
            "network": request.network,
            "match_count": len(premium.result.get("matches", [])),
            "checked_lists": ["SDN", "CONSOLIDATED"],
            "checked_at": checked_at,
            "source_age_seconds": age_seconds(checked_at),
            "premium_evidence_path": "/v1/ofac/exact-identifier-evidence",
            "provenance": {
                "publisher": "U.S. Department of the Treasury, OFAC",
                "normalization_version": OFAC_PARSER_VERSION,
                "result_sha256": None,
            },
            "limitations": [
                "Exact address match only; no ownership, exposure, or fuzzy screening.",
                "No-match is not sanctions clearance or legal advice.",
                "The premium endpoint adds source hashes, matching records, and a signed receipt.",
            ],
        }
        return self._store_prepared(
            "ofac_preflight",
            request_hash,
            premium.source_bundle_hash,
            result_core,
            include_receipt=False,
        )

    def record_attempt(
        self,
        prepared: PreparedResult,
        *,
        route: str,
        quoted_price: str,
        network: str,
        response_status: str,
        latency_ms: int,
        payment_signature: str | None = None,
        settlement_tx_hash: str | None = None,
        payer_wallet: str | None = None,
        client_identifier: str | None = None,
        user_agent: str | None = None,
        user_agent_family: str | None = None,
        referrer_origin: str | None = None,
        edge_region: str | None = None,
        proxy_request_id: str | None = None,
        discovery_source: str | None = None,
        agent_run_id: str | None = None,
        http_status: int | None = None,
    ) -> None:
        payment_identifier = (
            sha256_bytes(payment_signature.encode("utf-8"))
            if payment_signature
            else None
        )
        payer_hmac = (
            hmac.new(
                self.analytics_hmac_key,
                payer_wallet.casefold().encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()
            if payer_wallet
            else None
        )
        client_hmac = self._analytics_hmac(client_identifier)
        user_agent_hmac = self._analytics_hmac(user_agent)
        agent_run_id_hmac = self._analytics_hmac(agent_run_id)
        request_fingerprint_hmac = self._analytics_hmac(
            "|".join(
                (
                    route,
                    prepared.request_hash,
                    client_hmac or "",
                    user_agent_hmac or "",
                )
            )
        )
        owners = {
            item.strip().casefold()
            for item in os.getenv("AUTONOMOUS_OWNER_WALLETS", "").split(",")
            if item.strip()
        }
        if os.getenv("AUTONOMOUS_X402_NETWORK", "eip155:84532") != "eip155:8453":
            owner_or_test = "TESTNET"
        elif payer_wallet and payer_wallet.casefold() in owners:
            owner_or_test = "OWNER"
        else:
            owner_or_test = "NON_OWNER_UNVERIFIED"
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO evidence_attempts (
                    request_id, timestamp_utc, route, canonical_request_hash,
                    response_hash, source_bundle_hash, quoted_price, network,
                    payment_identifier, settlement_tx_hash, payer_wallet_hmac,
                    owner_or_test_flag, response_status, latency_ms,
                    direct_cost_estimate, client_hmac, user_agent_hmac,
                    user_agent_family, referrer_origin, edge_region,
                    proxy_request_id, discovery_source, agent_run_id_hmac,
                    request_fingerprint_hmac, http_status, created_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    prepared.request_id,
                    utc_now(),
                    route,
                    prepared.request_hash,
                    prepared.result_hash,
                    prepared.source_bundle_hash,
                    quoted_price,
                    network,
                    payment_identifier,
                    settlement_tx_hash,
                    payer_hmac,
                    owner_or_test,
                    response_status,
                    latency_ms,
                    client_hmac,
                    user_agent_hmac,
                    user_agent_family,
                    referrer_origin,
                    edge_region,
                    proxy_request_id,
                    discovery_source,
                    agent_run_id_hmac,
                    request_fingerprint_hmac,
                    http_status,
                    utc_now(),
                ),
            )
            if payment_signature and response_status == "FULFILLED":
                connection.execute(
                    """
                    INSERT OR REPLACE INTO fulfillments (
                        request_id, payment_signature_hash, settlement_tx_hash, fulfilled_at
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        prepared.request_id,
                        payment_identifier,
                        settlement_tx_hash,
                        utc_now(),
                    ),
                )
            connection.commit()

    def _analytics_hmac(self, value: str | None) -> str | None:
        if not value:
            return None
        return hmac.new(
            self.analytics_hmac_key,
            value.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def bind_payment(
        self,
        payment_signature: str,
        prepared: PreparedResult,
        route: str,
    ) -> bool:
        signature_hash = sha256_bytes(payment_signature.encode("utf-8"))
        with self._connect() as connection:
            existing = connection.execute(
                """
                SELECT request_id, route, canonical_request_hash
                FROM payment_bindings
                WHERE payment_signature_hash = ?
                """,
                (signature_hash,),
            ).fetchone()
            if existing:
                return bool(
                    existing["request_id"] == prepared.request_id
                    and existing["route"] == route
                    and existing["canonical_request_hash"] == prepared.request_hash
                )
            connection.execute(
                """
                INSERT INTO payment_bindings (
                    payment_signature_hash, request_id, route,
                    canonical_request_hash, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    signature_hash,
                    prepared.request_id,
                    route,
                    prepared.request_hash,
                    utc_now(),
                ),
            )
            connection.commit()
        return True

    def replay(self, request_id: str, payment_signature: str) -> dict[str, Any] | None:
        signature_hash = sha256_bytes(payment_signature.encode("utf-8"))
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT p.result_json
                FROM fulfillments f
                JOIN prepared_results p ON p.request_id = f.request_id
                WHERE f.request_id = ? AND f.payment_signature_hash = ?
                """,
                (request_id, signature_hash),
            ).fetchone()
        return json.loads(row["result_json"]) if row else None

    def experiment_status(self, cohort_start_utc: str | None = None) -> dict[str, Any]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    response_status,
                    owner_or_test_flag,
                    COUNT(*) AS calls,
                    COUNT(DISTINCT payer_wallet_hmac) AS payer_clusters
                FROM evidence_attempts
                GROUP BY response_status, owner_or_test_flag
                ORDER BY response_status, owner_or_test_flag
                """
            ).fetchall()
            sources = connection.execute(
                """
                SELECT source_id, source_version, last_checked_at
                FROM source_status ORDER BY source_id
                """
            ).fetchall()
            cohort_rows = (
                connection.execute(
                    """
                    SELECT route, timestamp_utc, quoted_price, payer_wallet_hmac,
                           owner_or_test_flag, response_status
                    FROM evidence_attempts
                    WHERE timestamp_utc >= ?
                    ORDER BY timestamp_utc
                    """,
                    (cohort_start_utc,),
                ).fetchall()
                if cohort_start_utc
                else []
            )
            attribution_summary = connection.execute(
                """
                SELECT
                    COUNT(*) AS attempts,
                    COALESCE(SUM(CASE WHEN client_hmac IS NOT NULL THEN 1 ELSE 0 END), 0)
                        AS client_fingerprinted_attempts,
                    COALESCE(SUM(CASE WHEN user_agent_hmac IS NOT NULL THEN 1 ELSE 0 END), 0)
                        AS user_agent_fingerprinted_attempts,
                    COALESCE(SUM(CASE WHEN discovery_source IS NOT NULL THEN 1 ELSE 0 END), 0)
                        AS declared_source_attempts,
                    COALESCE(SUM(CASE WHEN response_status = 'FULFILLED'
                                          AND client_hmac IS NOT NULL
                                     THEN 1 ELSE 0 END), 0)
                        AS fingerprinted_fulfilled_attempts
                FROM evidence_attempts
                """
            ).fetchone()
            user_agent_rows = connection.execute(
                """
                SELECT user_agent_family, COUNT(*) AS attempts,
                       SUM(CASE WHEN response_status = 'FULFILLED' THEN 1 ELSE 0 END)
                           AS fulfilled_attempts
                FROM evidence_attempts
                WHERE user_agent_family IS NOT NULL
                GROUP BY user_agent_family
                ORDER BY attempts DESC, user_agent_family
                """
            ).fetchall()
            discovery_rows = connection.execute(
                """
                SELECT discovery_source, COUNT(*) AS attempts,
                       SUM(CASE WHEN response_status = 'FULFILLED' THEN 1 ELSE 0 END)
                           AS fulfilled_attempts
                FROM evidence_attempts
                WHERE discovery_source IS NOT NULL
                GROUP BY discovery_source
                ORDER BY attempts DESC, discovery_source
                LIMIT 20
                """
            ).fetchall()
        status = {
            "generated_at": utc_now(),
            "metrics": [dict(row) for row in rows],
            "sources": [dict(row) for row in sources],
            "interpretation": (
                "Testnet, owner, and unverified clusters are reported separately and do not "
                "establish independent demand."
            ),
            "attribution": {
                **dict(attribution_summary),
                "user_agent_families": [dict(row) for row in user_agent_rows],
                "declared_discovery_sources": [dict(row) for row in discovery_rows],
                "measurement_notes": [
                    "Client, user-agent, and agent-run identifiers are stored only as keyed HMACs.",
                    "Discovery source is optional and self-declared; absence does not imply direct traffic.",
                    "Attempts recorded before attribution deployment remain unclassified.",
                ],
            },
        }
        if not cohort_start_utc:
            return status

        route_metadata = {
            "/v1/monitors/source-change": {
                "tier": "long-running-job",
                "price_usd": "1.00",
                "cohort_start_utc": "2026-08-10T08:41:32Z",
                "hypothesis": (
                    "Agents will pay for a persistent monitoring job that removes "
                    "repeated polling, state management, and change delivery."
                ),
            },
            "/v1/gtm/form-d-funding-leads": {
                "tier": "gtm-signal",
                "price_usd": "0.05",
                "hypothesis": (
                    "Agents will repeatedly pay for official Form D signals that "
                    "identify newly finance-active private companies and people."
                ),
            },
            "/v1/ofac/payment-preflight": {
                "tier": "decision",
                "price_usd": "0.01",
                "hypothesis": "Agents will pay for a compact pre-payment OFAC exact-match gate.",
            },
            "/v1/sec/filing-change-signal": {
                "tier": "decision",
                "price_usd": "0.01",
                "hypothesis": "Agents will pay for a ticker-and-timestamp SEC filing-change signal.",
            },
            "/v1/ofac/exact-identifier-evidence": {
                "tier": "premium",
                "price_usd": "0.05",
                "hypothesis": "A subset of buyers will pay for signed official-source evidence.",
            },
            "/v1/sec/filing-trigger-delta": {
                "tier": "premium",
                "price_usd": "0.10",
                "hypothesis": "A subset of buyers will pay for document hashes and XBRL deltas.",
            },
        }
        funnels: dict[str, dict[str, Any]] = {}
        payer_days: dict[str, set[str]] = {}
        payer_calls: dict[str, int] = {}
        independent_revenue = Decimal(0)
        independent_paid_attempts = 0
        independent_fulfilled = 0

        for route, metadata in route_metadata.items():
            funnels[route] = {
                "route": route,
                **metadata,
                "payment_challenges": 0,
                "owner_fulfilled_calls": 0,
                "independent_fulfilled_calls": 0,
                "independent_buyer_clusters": 0,
                "repeat_independent_buyer_clusters": 0,
                "independent_revenue_usd": "0.00",
                "independent_paid_or_settlement_failures": 0,
                "independent_paid_fulfillment_rate_percent": None,
            }

        route_payers: dict[str, set[str]] = {route: set() for route in funnels}
        route_payer_days: dict[str, dict[str, set[str]]] = {
            route: {} for route in funnels
        }
        route_independent_revenue: dict[str, Decimal] = {
            route: Decimal(0) for route in funnels
        }
        route_independent_paid_attempts = {route: 0 for route in funnels}
        route_independent_fulfilled = {route: 0 for route in funnels}

        for row in cohort_rows:
            route = row["route"]
            if route not in funnels:
                continue
            response_status = row["response_status"]
            owner_flag = row["owner_or_test_flag"]
            payer = row["payer_wallet_hmac"]
            if response_status == "PAYMENT_REQUIRED":
                funnels[route]["payment_challenges"] += 1
                continue
            if response_status == "PAYMENT_OR_SETTLEMENT_FAILED":
                if owner_flag == "NON_OWNER_UNVERIFIED" and payer:
                    funnels[route]["independent_paid_or_settlement_failures"] += 1
                    independent_paid_attempts += 1
                    route_independent_paid_attempts[route] += 1
                continue
            if response_status != "FULFILLED":
                continue

            if owner_flag == "OWNER":
                funnels[route]["owner_fulfilled_calls"] += 1
                continue
            if owner_flag != "NON_OWNER_UNVERIFIED" or not payer:
                continue

            funnels[route]["independent_fulfilled_calls"] += 1
            independent_fulfilled += 1
            independent_paid_attempts += 1
            route_independent_fulfilled[route] += 1
            route_independent_paid_attempts[route] += 1
            route_payers[route].add(payer)
            day = str(row["timestamp_utc"])[:10]
            route_payer_days[route].setdefault(payer, set()).add(day)
            payer_days.setdefault(payer, set()).add(day)
            payer_calls[payer] = payer_calls.get(payer, 0) + 1
            try:
                price = Decimal(str(row["quoted_price"]).removeprefix("$"))
            except InvalidOperation:
                price = Decimal(0)
            route_independent_revenue[route] += price
            independent_revenue += price

        for route, funnel in funnels.items():
            funnel["independent_buyer_clusters"] = len(route_payers[route])
            funnel["repeat_independent_buyer_clusters"] = sum(
                1 for days in route_payer_days[route].values() if len(days) >= 2
            )
            funnel["independent_revenue_usd"] = (
                f"{route_independent_revenue[route]:.2f}"
            )
            if route_independent_paid_attempts[route]:
                funnel["independent_paid_fulfillment_rate_percent"] = round(
                    100
                    * route_independent_fulfilled[route]
                    / route_independent_paid_attempts[route],
                    2,
                )

        independent_buyers = len(payer_days)
        repeat_buyers = sum(1 for days in payer_days.values() if len(days) >= 2)
        top_buyer_share = (
            max(payer_calls.values()) / independent_fulfilled
            if independent_fulfilled
            else None
        )
        paid_fulfillment_rate = (
            Decimal(independent_fulfilled) / Decimal(independent_paid_attempts)
            if independent_paid_attempts
            else None
        )
        status["conversion_experiment"] = {
            "cohort_start_utc": cohort_start_utc,
            "independent_buyer_clusters": independent_buyers,
            "repeat_independent_buyer_clusters": repeat_buyers,
            "independent_fulfilled_calls": independent_fulfilled,
            "independent_revenue_usd": f"{independent_revenue:.2f}",
            "max_independent_buyer_call_share": (
                round(top_buyer_share, 4) if top_buyer_share is not None else None
            ),
            "independent_paid_fulfillment_rate_percent": (
                round(100 * float(paid_fulfillment_rate), 2)
                if paid_fulfillment_rate is not None
                else None
            ),
            "gates": {
                "five_independent_buyers": independent_buyers >= 5,
                "fifty_independent_fulfilled_calls": independent_fulfilled >= 50,
                "two_repeat_buyers_across_utc_days": repeat_buyers >= 2,
                "paid_fulfillment_at_least_99_percent": bool(
                    paid_fulfillment_rate is not None
                    and paid_fulfillment_rate >= Decimal("0.99")
                ),
                "no_buyer_above_50_percent_of_calls": bool(
                    top_buyer_share is not None and top_buyer_share <= 0.50
                ),
            },
            "routes": list(funnels.values()),
            "measurement_notes": [
                "Owner and testnet payments are excluded from every conversion gate.",
                "Unpaid 402 challenges include monitors and crawlers, so they are not treated as buyers or a conversion denominator.",
                "A repeat buyer must fulfill calls on at least two distinct UTC dates.",
            ],
        }
        return status

    def fulfilled_revenue_since(self, timestamp_utc: str, network: str) -> Decimal:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT quoted_price
                FROM evidence_attempts
                WHERE timestamp_utc >= ?
                  AND network = ?
                  AND response_status = 'FULFILLED'
                """,
                (timestamp_utc, network),
            ).fetchall()
        total = Decimal(0)
        for row in rows:
            try:
                total += Decimal(row["quoted_price"].removeprefix("$"))
            except (InvalidOperation, AttributeError):
                continue
        return total
