from __future__ import annotations

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

ENDPOINT = "https://evidence.regulavita.com/v1/ofac/exact-identifier-evidence"
EXPECTED_NETWORK = "eip155:84532"
EXPECTED_PAY_TO = "0x21a37527dee4f5eF0d84426BA39C4Df0DE32Bc6b"
EXPECTED_AMOUNT_ATOMIC = "50000"
PAYLOAD = {
    "identifier_type": "crypto_address",
    "identifier": "0x098B716B8Aaf21512996dC57EB0615e2383E2f96",
    "networks": ["eip155:1"],
    "lists": ["SDN"],
}


def decode_header(value: str) -> dict[str, Any]:
    padded = value + "=" * (-len(value) % 4)
    decoded = json.loads(base64.urlsafe_b64decode(padded))
    if not isinstance(decoded, dict):
        raise TypeError("x402 challenge is not a JSON object")
    return decoded


def load_testnet_private_key() -> str:
    configured = os.getenv("EVM_PRIVATE_KEY", "").strip()
    if configured:
        return configured
    wallet_path = Path(os.getenv("TESTNET_WALLET_FILE", ".local/testnet-wallets.json"))
    wallet_data = json.loads(wallet_path.read_text())
    if wallet_data.get("network") != "Base Sepolia":
        raise RuntimeError("Wallet file is not explicitly marked Base Sepolia")
    return str(wallet_data["buyer"]["private_key"])


def validate_challenge(challenge: dict[str, Any]) -> None:
    accepts = challenge.get("accepts")
    if not isinstance(accepts, list) or len(accepts) != 1:
        raise RuntimeError("Expected exactly one payment option")
    option = accepts[0]
    expected = {
        "network": EXPECTED_NETWORK,
        "payTo": EXPECTED_PAY_TO,
        "amount": EXPECTED_AMOUNT_ATOMIC,
    }
    for key, value in expected.items():
        if option.get(key) != value:
            raise RuntimeError(f"Refusing unexpected {key}: {option.get(key)!r}")
    if challenge.get("resource", {}).get("url") != ENDPOINT:
        raise RuntimeError("Refusing challenge for an unexpected resource URL")


async def main() -> None:
    async with httpx.AsyncClient(timeout=90) as preflight:
        challenge_response = await preflight.post(ENDPOINT, json=PAYLOAD)
    if challenge_response.status_code != 402:
        raise RuntimeError(
            f"Expected HTTP 402 preflight, got {challenge_response.status_code}"
        )
    validate_challenge(decode_header(challenge_response.headers["payment-required"]))

    account = Account.from_key(load_testnet_private_key())
    client = x402Client()
    register_exact_evm_client(client, EthAccountSigner(account))
    payment_client = x402HTTPClient(client)

    async with x402HttpxClient(client, timeout=120) as http:
        response = await http.post(ENDPOINT, json=PAYLOAD)
        await response.aread()
        if not response.is_success:
            payment_response = response.headers.get("payment-response")
            payment_required = response.headers.get("payment-required")
            decoded_payment_response = (
                decode_header(payment_response) if payment_response else None
            )
            raise RuntimeError(
                json.dumps(
                    {
                        "status": response.status_code,
                        "body": response.text,
                        "payment_required": (
                            decode_header(payment_required)
                            if payment_required
                            else None
                        ),
                        "payment_response": decoded_payment_response,
                    },
                    default=str,
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
                "buyer": account.address,
                "request_id": result.get("request_id"),
                "match_status": result.get("match_status"),
                "matched_uids": [
                    match.get("entry_uid") for match in result.get("matches", [])
                ],
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
