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

ENDPOINT = (
    "https://iti-official-source-evidence.fly.dev/v1/ofac/exact-identifier-evidence"
)
EXPECTED_NETWORK = "eip155:8453"
EXPECTED_PAY_TO = "0x9500075649a70411c81f99c4314f6cff55d12579"
EXPECTED_BUYER = "0x5Bd70c14C517dffC1bB3361274093A791306Ccdd"
EXPECTED_ASSET = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
EXPECTED_AMOUNT_ATOMIC = "50000"
ARM_VALUE = "0.05"
PAYLOAD = {
    "identifier_type": "ofac_uid",
    "identifier": "36",
    "lists": ["SDN"],
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


def validate_challenge(challenge: dict[str, Any]) -> None:
    accepts = challenge.get("accepts")
    if not isinstance(accepts, list) or len(accepts) != 1:
        raise RuntimeError("Expected exactly one payment option")
    option = accepts[0]
    expected = {
        "network": EXPECTED_NETWORK,
        "payTo": EXPECTED_PAY_TO,
        "asset": EXPECTED_ASSET,
        "amount": EXPECTED_AMOUNT_ATOMIC,
    }
    for key, value in expected.items():
        if str(option.get(key)).casefold() != value.casefold():
            raise RuntimeError(f"Refusing unexpected {key}: {option.get(key)!r}")
    if challenge.get("resource", {}).get("url") != ENDPOINT:
        raise RuntimeError("Refusing challenge for an unexpected resource URL")


async def main() -> None:
    if os.getenv("CONFIRM_MAINNET_BOOTSTRAP_USDC") != ARM_VALUE:
        raise RuntimeError(
            f"Set CONFIRM_MAINNET_BOOTSTRAP_USDC={ARM_VALUE} to authorize one payment"
        )

    async with httpx.AsyncClient(timeout=90) as preflight:
        challenge_response = await preflight.post(ENDPOINT, json=PAYLOAD)
    if challenge_response.status_code != 402:
        raise RuntimeError(
            f"Expected HTTP 402 preflight, got {challenge_response.status_code}"
        )
    validate_challenge(decode_header(challenge_response.headers["payment-required"]))

    account = Account.from_key(load_private_key())
    if account.address.casefold() != EXPECTED_BUYER.casefold():
        raise RuntimeError("Bootstrap wallet does not match the owner-tagged buyer")

    client = x402Client()
    register_exact_evm_client(client, EthAccountSigner(account))
    payment_client = x402HTTPClient(client)
    async with x402HttpxClient(client, timeout=120) as http:
        response = await http.post(ENDPOINT, json=PAYLOAD)
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
