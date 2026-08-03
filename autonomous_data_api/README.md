# Official Source Evidence API

This is the implementation of the 1 August 2026 autonomous-business sprint verdict. It is a deliberately capped falsification experiment, not a validated business.

Paid candidates:

- `POST /v1/sec/filing-trigger-delta` at `$0.10` USDC per fulfilled call.
- `POST /v1/ofac/exact-identifier-evidence` at `$0.05` USDC per fulfilled call.

The PFAS and grid lead endpoints remain available for prior API-key experiments, but their x402 routes return `410 RETIRED_WEDGE` and are not promoted in the agent manifest.

## Product Boundaries

The SEC endpoint returns filing metadata, accession/document hashes, selected XBRL fact deltas, source freshness, and provenance. It never returns ratings, materiality opinions, valuation, trading advice, or execution instructions.

The OFAC endpoint performs only exact normalized lookups for a supplied crypto address, OFAC UID, or exact name. It never returns `safe`, `approved`, `cleared`, `not sanctioned`, `compliant`, or `legal to transact`; it does no fuzzy matching, ownership/control analysis, or transaction authorization.

## Payment and Fulfilment Flow

1. The outer evidence middleware validates the POST body.
2. It refreshes or verifies an allowlisted official source and precomputes a valid result.
3. Invalid, stale, broken-source, or oversized requests fail before a payment challenge.
4. x402 v2 returns a Bazaar-compatible HTTP 402 challenge.
5. A compatible buyer pays and retries automatically.
6. The already prepared result is returned with an Ed25519 receipt and source hashes.
7. A fulfilled buyer can replay the stored result with the original `Payment-Signature` at `/v1/evidence/replay/{request_id}` without a second charge.

For directory and uptime monitoring, an empty unauthenticated `POST` is a
supported probe for both paid routes. It uses the route's published Bazaar
example request and returns the normal 402 challenge. A literal `{}`, malformed
JSON, or any other schema-invalid non-empty body is still rejected before the
payment layer.

Every attempt is written to a reconciliation ledger. Testnet and configured owner-wallet traffic are explicitly excluded from independent-demand interpretation.

## Local Run

```bash
python3 -m venv .venv-api
. .venv-api/bin/activate
pip install -r autonomous_data_api/requirements.txt
export AUTONOMOUS_EVIDENCE_BACKGROUND_REFRESH=0
uvicorn autonomous_data_api.app:app --host 127.0.0.1 --port 8765 --reload
```

Useful local URLs:

- `http://127.0.0.1:8765/docs`
- `http://127.0.0.1:8765/.well-known/agent-service.json`
- `http://127.0.0.1:8765/v1/experiments/status`
- `http://127.0.0.1:8765/v1/sec/sample`
- `http://127.0.0.1:8765/v1/ofac/sample`

An unpaid testnet request should return HTTP 402:

```bash
curl -i -X POST http://127.0.0.1:8765/v1/ofac/exact-identifier-evidence \
  -H 'Content-Type: application/json' \
  -d '{"identifier_type":"ofac_uid","identifier":"36","lists":["SDN"]}'
```

## Official Sources

SEC submissions and company facts are public and require no API key. SEC automated-access guidance does require an identifying User-Agent with contact details:

```bash
export AUTONOMOUS_SEC_USER_AGENT='OfficialSourceEvidence/0.2 YourOrg contact@example.com'
```

OFAC SLS files are public and require no API key. The app checks source versions every 15 minutes and downloads/indexes a new file only when it changes. To import a previously downloaded official file for local validation:

```bash
python -m autonomous_data_api.manage_evidence import-ofac \
  --list SDN \
  --file /path/to/SDN.XML \
  --official-digest-sha256 64_hex_characters
```

Production refresh:

```bash
export AUTONOMOUS_EVIDENCE_BACKGROUND_REFRESH=1
python -m autonomous_data_api.manage_evidence refresh-ofac --list ALL
```

Source content is compressed into the ignored runtime database. Exact-match lookup records are indexed by source hash, so paid calls do not parse the source file on demand.

## Base Sepolia

The default configuration uses Base Sepolia, the public x402.org test facilitator, and a non-revenue demo recipient. Set a test recipient to validate your own flow:

```bash
export AUTONOMOUS_X402_NETWORK=eip155:84532
export AUTONOMOUS_X402_PAY_TO=0xYourTestWalletAddress
export AUTONOMOUS_X402_FACILITATOR_URL=https://x402.org/facilitator
export AUTONOMOUS_X402_SEC_PRICE='$0.10'
export AUTONOMOUS_X402_OFAC_PRICE='$0.05'
```

Testnet proves technical readiness only. It does not count as demand, revenue, a payer, repeat use, or retention.

## Mainnet Requirements

For public Base mainnet receipts:

```bash
export AUTONOMOUS_API_BASE_URL=https://your-api-domain.example
export AUTONOMOUS_X402_NETWORK=eip155:8453
export AUTONOMOUS_X402_PAY_TO=0xYourReceivingWallet
export AUTONOMOUS_X402_FACILITATOR_URL=https://api.cdp.coinbase.com/platform/v2/x402
export CDP_API_KEY_ID=organizations/.../apiKeys/...
export CDP_API_KEY_SECRET='-----BEGIN EC PRIVATE KEY-----...'
export AUTONOMOUS_OWNER_WALLETS=0xOwnerWallet,0xBootstrapWallet
```

The seller app never needs the receiving wallet's private key. The CDP secret authenticates facilitator verify/settle calls; it is not a treasury key.

Set persistent secrets for deterministic receipts and privacy-preserving payer analytics:

```bash
export AUTONOMOUS_RECEIPT_SIGNING_KEY=base64url_raw_32_byte_ed25519_private_key
export AUTONOMOUS_ANALYTICS_HMAC_KEY=long_random_secret
```

Without those variables, local keys are generated under `autonomous_data_api/runtime/`. A production container must use a secret manager and persistent database/object storage.

## Fly.io Experiment Scaffold

`fly.toml.example` is sized for the capped experiment: one always-on shared 1 GB machine, one 1 GB persistent volume, HTTPS, and the background source verifier in the web process. It deliberately avoids a second worker until paid demand exists.

After creating the Fly.io account and selecting a unique app name, instantiate
`fly.toml` from `fly.toml.example`, replace the placeholder app name, and then
provision the app:

```bash
fly apps create your-unique-app-name
fly volumes create evidence_data --region iad --size 1
fly secrets set \
  AUTONOMOUS_X402_PAY_TO=0xYourReceivingWallet \
  CDP_API_KEY_ID='organizations/.../apiKeys/...' \
  CDP_API_KEY_SECRET_B64='base64-encoded-secret' \
  AUTONOMOUS_SEC_USER_AGENT='OfficialSourceEvidence/0.2 YourOrg contact@example.com' \
  AUTONOMOUS_OWNER_WALLETS='0xOwnerWallet,0xBootstrapWallet' \
  AUTONOMOUS_RECEIPT_SIGNING_KEY='base64url-key' \
  AUTONOMOUS_ANALYTICS_HMAC_KEY='long-random-secret'
```

The live Fly deployment uses Base mainnet, the authenticated CDP facilitator,
an owner-controlled receiving address, and a `$10.00` UTC-day revenue cap.
Production credentials are stored only as Fly secrets.

## Automated Controls

- Strict Pydantic and Bazaar JSON schemas; unknown fields are rejected.
- Fixed SEC and OFAC source allowlists; no arbitrary URL fetching.
- Bounded forms, rules, filings, source-history files, payloads, and lists.
- Source freshness checked before HTTP 402.
- Immutable compressed source snapshots and parser versions.
- Canonical request, source-bundle, result, and component hashes.
- Ed25519 result receipts.
- Stored fulfilments and payment-proof replay.
- Owner/test classification and HMAC wallet analytics.
- Pre-payment Base-mainnet revenue cap, serialized on the single Fly machine.
- `AUTONOMOUS_EVIDENCE_ENABLED=0` kill switch for new paid requests; replay remains available.

Host-level WAF, per-IP/per-wallet/per-ASN rate limits, alerting, backups, and daily chain/facilitator reconciliation remain deployment tasks.

## Demand Gates

Continue after 30 days only if all sprint gates pass, including 5 non-owner payer clusters, 50 non-self fulfilled calls, 2 repeat clusters across separate UTC days, greater than 80% measured gross margin, at least 99% paid fulfilment, under 30 minutes of normal weekly support, no payer above 70% of calls, and no unresolved legal/source/provenance issue.

Kill at Day 21 if the correctly indexed service has zero non-owner paid wallets. Do not extend automatically at Day 31.

## Mainnet Operating Requirements

- Keep the CDP key in Fly secrets and rotate it if the downloaded copy is exposed.
- Keep the Base receiving wallet recovery material outside the app and repository.
- Reconcile fulfilled receipts against Base settlement transactions and wallet balances.
- Keep tax, VAT, buyer-location, valuation, and exchange-control records current.
- Keep the initial revenue cap in place until the small-amount operating phase is reviewed.

No SEC or OFAC account or API key is required.
