# Official Source Evidence API

This is the implementation of the 1 August 2026 autonomous-business sprint verdict. It is a deliberately capped falsification experiment, not a validated business.

The pre-registered Form D hypothesis, gates, launch evidence, and directory log
are in [`FORM_D_EXPERIMENT.md`](FORM_D_EXPERIMENT.md).

Paid candidates:

- `POST /v1/procure/company-profile` at `$0.25` USDC. It purchases bounded
  retrieval and schema-constrained normalization from pinned x402 suppliers,
  with at most `$0.02` expected supplier cost per fulfilled request.
- `POST /v1/web/source-snapshot` at `$0.03` USDC per fulfilled call.
- `POST /v1/monitors/source-change` at `$1.00` USDC per 30-day monitor.
- `POST /v1/monitors/source-change-portfolio` at `$9.00` USDC per 30-day
  portfolio of 2-10 sources.
- `POST /v1/gtm/form-d-funding-leads` at `$0.05` USDC per fulfilled call.
- Dormant until explicitly activated: `POST /v1/gtm/form-d-company-dossier` at
  `$0.25` USDC, with a maximum `$0.01` paid-search input per fulfilled call.
- `POST /v1/ofac/payment-preflight` at `$0.01` USDC per fulfilled call.
- `POST /v1/sec/filing-change-signal` at `$0.01` USDC per fulfilled call.
- `POST /v1/sec/filing-trigger-delta` at `$0.10` USDC per fulfilled call.
- `POST /v1/ofac/exact-identifier-evidence` at `$0.05` USDC per fulfilled call.

The PFAS and grid lead endpoints remain available for prior API-key experiments, but their x402 routes return `410 RETIRED_WEDGE` and are not promoted in the agent manifest.

## Product Boundaries

The company-profile procurement route is a brokered execution experiment. It
uses Tavily only for bounded source discovery and BlockRun only for
schema-constrained normalization, then returns derived fields, source links,
contradictions, supplier settlement provenance, hashes, and a signed receipt.
It never returns raw supplier payloads as a pass-through. Supplier endpoint,
recipient, Base USDC asset, and maximum quote are pinned. Retrieval success with
normalization failure returns an explicit partial result instead of invented
fields. The pre-registered hypothesis and gates are in
[`PROCUREMENT_EXPERIMENT.md`](PROCUREMENT_EXPERIMENT.md).

The public-source snapshot endpoint fetches one public HTTPS HTML, JSON, XML, or
plain-text resource and returns bounded normalized text, optional literal-match
excerpts, a content hash, and a signed receipt. It does not render JavaScript,
follow redirects, access authenticated pages, or make claims about publisher
authorship or truth. Successful buyers can upgrade the same URL to the 30-day
Source Watch product without requiring a new account or credential.

The portfolio monitor reuses the same bounded source engine for 2-10 unique
public HTTPS URLs. A successful payment atomically creates a private monitor and
access token for each source. Reachability is checked after payment authorization
but before settlement, there is no metered supplier, and the route is priced
below a `$10` session ceiling.

The Form D GTM endpoint scans official SEC daily indexes in bounded pages. It
filters new filings by issuer state, industry keyword, and issuer-reported amount
sold, then returns company context, related people, official links, hashes, a
cursor, and a signed receipt. Form D is an issuer-filed notice of an exempt
offering, not proof that the total offering amount was raised. The route does not
infer funding, recommend securities, enrich personal contact details, or perform
outreach.

The funded-company dossier experiment composes one official Form D filing with
fresh web research purchased over x402. It is registered and advertised only on
Base mainnet when its dedicated supplier wallet and explicit enable flag are
both configured. Customer payment is verified before the supplier call, the
supplier selector rejects any non-USDC, non-Base, or above-$0.01 quote, and an
atomic UTC-day cap plus inbound-payment replay key bounds spend. Revenue, direct
supplier cost, and gross margin are recorded separately.

The SEC signal accepts a ticker and timestamp and returns a compact filing-change decision. The premium SEC endpoint also accepts CIK and accession inputs, and adds document hashes, selected XBRL fact deltas, source freshness, provenance, and a signed receipt. Neither returns ratings, materiality opinions, valuation, trading advice, or execution instructions.

The OFAC preflight returns a compact decision for an exact EVM address. The premium endpoint adds full matching records, source hashes, and a signed receipt for an address, OFAC UID, or exact name. Neither returns `safe`, `approved`, `cleared`, `not sanctioned`, `compliant`, or `legal to transact`; they do no fuzzy matching, ownership/control analysis, or transaction authorization.

## Payment and Fulfilment Flow

1. The outer evidence middleware validates the POST body.
2. It refreshes or verifies an allowlisted official source and precomputes a valid result.
3. Invalid, stale, broken-source, or oversized requests fail before a payment challenge.
4. x402 v2 returns a Bazaar-compatible HTTP 402 challenge.
5. A compatible buyer pays and retries automatically.
6. The already prepared result is returned. Premium evidence routes include an Ed25519 receipt and source hashes.
7. A fulfilled buyer can replay the stored result with the original `Payment-Signature` at `/v1/evidence/replay/{request_id}` without a second charge.

The composed dossier uses a separate payment-first path: x402 verifies the
customer authorization, the handler makes at most one capped supplier purchase,
and only a successful dossier response is settled. If assembly fails, the
customer is not charged; any uncertain supplier spend remains counted against
the daily supplier cap.

The procurement route uses the same payment-first boundary with an independent
supplier-spend ledger and cap. It binds the inbound proof to the canonical
request before supplier execution, returns a stored completed result on an
identical retry without buying again, and rejects proof reuse with another
request.

For directory and uptime monitoring, an empty unauthenticated `POST` is a
supported probe for all paid routes. It uses the route's published Bazaar
example request and returns the normal 402 challenge. A literal `{}`, malformed
JSON, or any other schema-invalid non-empty body is still rejected before the
payment layer.

Every attempt is written to a reconciliation ledger. Testnet and configured owner-wallet traffic are explicitly excluded from independent-demand interpretation.

Agents may optionally send `X-Agent-Discovery-Source` (for example,
`coinbase-bazaar`, `x402-list`, or `direct`) and `X-Agent-Run-Id`. Client IP,
full User-Agent, and agent-run values are stored only as keyed HMACs. The ledger
keeps only a broad User-Agent family and the origin portion of a valid referrer;
it never stores a raw client IP or a referrer path/query. Attribution headers are
self-declared evidence, not verified identity.

## Local Run

```bash
python3 -m venv .venv-api
. .venv-api/bin/activate
pip install -r autonomous_data_api/requirements.txt
export AUTONOMOUS_EVIDENCE_BACKGROUND_REFRESH=0
uvicorn autonomous_data_api.app:app --host 127.0.0.1 --port 8765 --reload
```

Useful local URLs:

- `http://127.0.0.1:8765/v1/procure/company-profile/sample`
- `http://127.0.0.1:8765/docs`
- `http://127.0.0.1:8765/.well-known/agent-service.json`
- `http://127.0.0.1:8765/v1/experiments/status`
- `http://127.0.0.1:8765/v1/web/source-snapshot/sample`
- `http://127.0.0.1:8765/v1/monitors/source-change-portfolio/sample`
- `http://127.0.0.1:8765/v1/gtm/form-d-funding-leads/sample`
- `http://127.0.0.1:8765/v1/sec/sample`
- `http://127.0.0.1:8765/v1/ofac/sample`

An unpaid testnet request should return HTTP 402:

```bash
curl -i -X POST http://127.0.0.1:8765/v1/ofac/exact-identifier-evidence \
  -H 'Content-Type: application/json' \
  -d '{"identifier_type":"ofac_uid","identifier":"36","lists":["SDN"]}'
```

## Official Sources

SEC daily indexes, filing submissions, company submissions, and company facts
are public and require no API key. SEC automated-access guidance does require an
identifying User-Agent with contact details:

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
export AUTONOMOUS_X402_SEC_SIGNAL_PRICE='$0.01'
export AUTONOMOUS_X402_OFAC_PRICE='$0.05'
export AUTONOMOUS_X402_OFAC_PREFLIGHT_PRICE='$0.01'
export AUTONOMOUS_X402_FORM_D_PRICE='$0.05'
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

To activate the composed dossier after its spend budget is approved, use a
dedicated low-balance supplier wallet and a small UTC-day cap:

```bash
export AUTONOMOUS_GTM_DOSSIER_ENABLED=1
export AUTONOMOUS_X402_FORM_D_DOSSIER_PRICE='$0.25'
export AUTONOMOUS_GTM_DOSSIER_SUPPLIER_DAILY_CAP_USD='0.05'
export AUTONOMOUS_SUPPLIER_WALLET_PRIVATE_KEY='0xDedicatedSupplierWalletKey'
```

The procurement experiment reuses that dedicated low-balance supplier wallet
with a separate daily cap:

```bash
export AUTONOMOUS_PROCUREMENT_ENABLED=1
export AUTONOMOUS_X402_COMPANY_PROFILE_PRICE='$0.25'
export AUTONOMOUS_PROCUREMENT_SUPPLIER_DAILY_CAP_USD='0.20'
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
- Form D lookbacks are capped at 14 days and each page scans at most 25 filings.
- Source freshness checked before HTTP 402.
- Immutable compressed source snapshots and parser versions.
- Canonical request, source-bundle, result, and component hashes.
- Ed25519 result receipts.
- Stored fulfilments and payment-proof replay.
- Owner/test classification plus HMAC wallet, client, User-Agent, and run analytics.
- Pre-payment Base-mainnet revenue cap, serialized on the single Fly machine.
- `AUTONOMOUS_EVIDENCE_ENABLED=0` kill switch for new paid requests; replay remains available.

Host-level WAF, per-IP/per-wallet/per-ASN rate limits, alerting, backups, and daily chain/facilitator reconciliation remain deployment tasks.

## Demand Gates

Continue after 30 days only if all sprint gates pass, including 5 non-owner
payer clusters, 50 non-self fulfilled calls, 2 repeat clusters across separate
UTC days, greater than 80% measured gross margin, at least 99% paid fulfilment,
under 30 minutes of normal weekly support, no payer above 50% of calls, and no
unresolved legal/source/provenance issue.

Kill at Day 21 if the correctly indexed service has zero non-owner paid wallets. Do not extend automatically at Day 31.

## Mainnet Operating Requirements

- Keep the CDP key in Fly secrets and rotate it if the downloaded copy is exposed.
- Keep the Base receiving wallet recovery material outside the app and repository.
- Reconcile fulfilled receipts against Base settlement transactions and wallet balances.
- Keep tax, VAT, buyer-location, valuation, and exchange-control records current.
- Keep the initial revenue cap in place until the small-amount operating phase is reviewed.

No SEC or OFAC account or API key is required.
