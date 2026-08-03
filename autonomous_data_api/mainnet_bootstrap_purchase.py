from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
from pathlib import Path
from typing import Any

import httpx
from eth_account import Account
from x402 import x402Client
from x402.http import x402HTTPClient
from x402.http.clients import x402HttpxClient
from x402.mechanisms.evm import EthAccountSigner
from x402.mechanisms.evm.exact.register import register_exact_evm_client

EXPECTED_NETWORK = "eip155:8453"
EXPECTED_PAY_TO = "0x9500075649a70411c81f99c4314f6cff55d12579"
EXPECTED_BUYER = "0x5Bd70c14C517dffC1bB3361274093A791306Ccdd"
EXPECTED_ASSET = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
PRODUCTS = {
    "ofac": {
        "endpoint": (
            "https://evidence.regulavita.com/v1/ofac/exact-identifier-evidence"
        ),
        "amount_atomic": "50000",
        "arm_value": "0.05",
        "payload": {
            "identifier_type": "crypto_address",
            "identifier": "0x0000000000000000000000000000000000000000",
            "networks": ["eip155:1", "eip155:8453"],
            "lists": ["SDN", "CONSOLIDATED"],
        },
    },
    "sec": {
        "endpoint": "https://evidence.regulavita.com/v1/sec/filing-trigger-delta",
        "amount_atomic": "100000",
        "arm_value": "0.10",
        "payload": {
            "cik": "0000320193",
            "since_accession": "0000320193-26-000018",
            "forms": ["8-K", "10-Q", "10-K"],
            "rules": ["FORM:8-K:ITEM:2.02", "XBRL:us-gaap:Revenues"],
            "max_source_age_seconds": 600,
        },
    },
}


def decode_header(value: str) -> dict[str, Any]:
    padded = value + "=" * (-len(value) % 4)
    decoded = json.loads(base64.urlsafe_b64decode(padded))
    if not isinstance(decoded, dict):
        raise TypeError("x402 challenge is not a JSON object")
    return decoded


def load_private_key() -> str:
    wallet_path = Path(
        os.getenv(
            "MAINNET_BOOTSTRAP_WALLET_FILE",
            ".local/mainnet-bootstrap-wallet.json",
        )
    )
    wallet_data = json.loads(wallet_path.read_text())
    if wallet_data.get("network") != "Base Mainnet":
        raise RuntimeError("Wallet file is not explicitly marked Base Mainnet")
    if wallet_data.get("chain_id") != 8453:
        raise RuntimeError("Wallet file has an unexpected chain ID")
    return str(wallet_data["buyer"]["private_key"])


def validate_challenge(challenge: dict[str, Any], product: dict[str, Any]) -> None:
    accepts = challenge.get("accepts")
    if not isinstance(accepts, list) or len(accepts) != 1:
        raise RuntimeError("Expected exactly one payment option")
    option = accepts[0]
    expected = {
        "network": EXPECTED_NETWORK,
        "payTo": EXPECTED_PAY_TO,
        "asset": EXPECTED_ASSET,
        "amount": product["amount_atomic"],
    }
    for key, value in expected.items():
        if str(option.get(key)).casefold() != value.casefold():
            raise RuntimeError(f"Refusing unexpected {key}: {option.get(key)!r}")
    if challenge.get("resource", {}).get("url") != product["endpoint"]:
        raise RuntimeError("Refusing challenge for an unexpected resource URL")


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Make one strictly capped owner-funded production purchase."
    )
    parser.add_argument("product", choices=sorted(PRODUCTS))
    args = parser.parse_args()
    product = PRODUCTS[args.product]

    if os.getenv("CONFIRM_MAINNET_BOOTSTRAP_USDC") != product["arm_value"]:
        raise RuntimeError(
            "Set CONFIRM_MAINNET_BOOTSTRAP_USDC="
            f"{product['arm_value']} to authorize one {args.product} payment"
        )

    async with httpx.AsyncClient(timeout=90) as preflight:
        challenge_response = await preflight.post(
            product["endpoint"], json=product["payload"]
        )
    if challenge_response.status_code != 402:
        raise RuntimeError(
            f"Expected HTTP 402 preflight, got {challenge_response.status_code}"
        )
    validate_challenge(
        decode_header(challenge_response.headers["payment-required"]), product
    )

    account = Account.from_key(load_private_key())
    if account.address.casefold() != EXPECTED_BUYER.casefold():
        raise RuntimeError("Bootstrap wallet does not match the owner-tagged buyer")

    client = x402Client()
    register_exact_evm_client(client, EthAccountSigner(account))
    payment_client = x402HTTPClient(client)
    async with x402HttpxClient(client, timeout=120) as http:
        response = await http.post(product["endpoint"], json=product["payload"])
        await response.aread()
        if not response.is_success:
            raise RuntimeError(
                json.dumps(
                    {
                        "status": response.status_code,
                        "body": response.text,
                    }
                )
            )
        settlement = payment_client.get_payment_settle_response(
            lambda name: response.headers.get(name)
        )

    result = response.json()
    print(
        json.dumps(
            {
                "http_status": response.status_code,
                "product": args.product,
                "buyer": account.address,
                "request_id": result.get("request_id"),
                "match_status": result.get("match_status"),
                "result_sha256": result.get("provenance", {}).get("result_sha256"),
                "settlement": (
                    settlement.model_dump()
                    if hasattr(settlement, "model_dump")
                    else settlement
                ),
            },
            indent=2,
            default=str,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
