# Isolated Compute, Agent Procurement, Evidence, Source Watch, and GTM Signals

One-call isolated Python execution, brokered machine-service procurement, long-running monitoring jobs, and
pay-per-call official-source signals and evidence for autonomous agents. No
account, API key, or sales call.

**Live service:** [evidence.regulavita.com](https://evidence.regulavita.com/)
| [OpenAPI](https://evidence.regulavita.com/openapi.json)
| [Agent manifest](https://evidence.regulavita.com/.well-known/agent-service.json)
| [Remote MCP](https://evidence.regulavita.com/server.json)
| [x402 compatibility](https://evidence.regulavita.com/.well-known/x402)
| [Coinbase Bazaar listing](https://api.cdp.coinbase.com/platform/v2/x402/discovery/merchant?payTo=0x9500075649a70411c81f99c4314f6cff55d12579&limit=100)

The Source Watch demand hypothesis, attribution rules, and stop/scale gates are
pre-registered in [SOURCE_WATCH_EXPERIMENT.md](autonomous_data_api/SOURCE_WATCH_EXPERIMENT.md).
The first independent buyer's on-chain cohort, interpretation, and resulting
actions are recorded in
[INDEPENDENT_BUYER_ANALYSIS.md](autonomous_data_api/INDEPENDENT_BUYER_ANALYSIS.md).

## Agent-Payable Products

| Endpoint | Result | Price |
| --- | --- | ---: |
| `POST /v1/compute/python-run` | Run bounded Python 3.11 code in an ephemeral isolated sandbox; one payment covers create, execute, and terminate and returns output, supplier settlements, hashes, and a signed receipt | $0.03 USDC |
| `POST /v1/procure/company-profile` | Procure bounded company retrieval and schema-constrained normalization from pinned x402 suppliers, with derived fields, source links, contradictions, supplier settlements, partial-failure state, hashes, and a signed receipt | $0.25 USDC |
| `POST /v1/web/source-snapshot` | One-shot normalized extraction from a public HTTPS HTML, JSON, XML, or text source, with optional literal excerpts, a content hash, and a signed receipt | $0.03 USDC |
| `POST /v1/monitors/source-change` | Monitor one public HTTPS text, HTML, JSON, or XML source every six hours for 30 days, with private polling and optional signed webhooks | $1.00 USDC |
| `POST /v1/gtm/form-d-funding-leads` | New SEC Form D private-offering signals filtered by issuer state, industry keyword, and reported amount sold, with related people, official links, and a cursor | $0.05 USDC |
| `POST /v1/ofac/payment-preflight` | Compact stop/no-exact-match decision for an EVM destination address against current official OFAC data | $0.01 USDC |
| `POST /v1/sec/filing-change-signal` | New SEC 8-K, 10-Q, or 10-K filings for a ticker since a timestamp, with links and a next-check cursor | $0.01 USDC |
| `POST /v1/ofac/exact-identifier-evidence` | Exact OFAC SDN or Consolidated lookup for a crypto address, OFAC UID, or exact name, with versioned source proof | $0.05 USDC |
| `POST /v1/sec/filing-trigger-delta` | New SEC filings for a ticker and timestamp or CIK and accession, plus deterministic XBRL deltas and a signed receipt | $0.10 USDC |

All routes use x402 v2 on Base mainnet. An x402-compatible client receives a
standard HTTP 402 challenge, pays USDC, retries automatically, and receives the
JSON result. The production service has a hard `$10.00` accepted-revenue cap
per UTC day.

The procurement experiment tests a brokered action, not another raw-data
wrapper. A free quote at `POST /v1/procure/company-profile/quote` exposes the
fixed customer price, supplier plan, maximum supplier cost, and partial-result
policy before payment. The hypothesis and stop/scale gates are pre-registered
in [PROCUREMENT_EXPERIMENT.md](autonomous_data_api/PROCUREMENT_EXPERIMENT.md).

The isolated Python experiment compresses a three-payment sandbox lifecycle into
one buyer call. A free quote exposes the fixed `$0.03` customer price and maximum
`$0.015` supplier cost before payment. The bounded hypothesis and gates are in
[PYTHON_RUN_EXPERIMENT.md](autonomous_data_api/PYTHON_RUN_EXPERIMENT.md).

Source Watch is a long-running job rather than a lookup. It accepts only public
HTTPS sources, does not follow redirects, caps each response at 1 MB, stores
normalized text rather than raw pages, and expires automatically after 30 days.
The paid response returns a bearer token for private status polling and, when
requested, a separate HMAC secret for verifying change webhooks.

Monitoring tools may send an empty unauthenticated `POST` to any paid route.
The service treats that as the published example request and returns the normal
HTTP 402 challenge without charging. Non-empty malformed or schema-invalid
requests are still rejected before payment.

## Discover and Inspect

The stateless Remote MCP at `https://evidence.regulavita.com/mcp/` exposes free
status, quotes, and capability requests. `get_example_payment` creates a live
x402 challenge from a known-valid published example without moving funds; this
lets generic catalog agents reach the payment decision without inventing test
inputs. Custom-input tools remain strict and return a machine-readable recovery
path when validation fails.

Search the Coinbase Bazaar from an Agentic Wallet CLI:

```bash
npx awal@latest x402 bazaar search "OFAC exact identifier evidence"
npx awal@latest x402 bazaar search "SEC Form D funding sales trigger"
npx awal@latest x402 bazaar search "public source snapshot normalized text signed receipt"
```

Inspect the free contracts before paying:

```bash
curl https://evidence.regulavita.com/v1/ofac/sample
curl https://evidence.regulavita.com/v1/compute/python-run/sample
curl https://evidence.regulavita.com/v1/procure/company-profile/sample
curl https://evidence.regulavita.com/v1/sec/sample
curl https://evidence.regulavita.com/v1/gtm/form-d-funding-leads/sample
curl https://evidence.regulavita.com/v1/web/source-snapshot/sample
curl https://evidence.regulavita.com/v1/monitors/source-change/sample
curl https://evidence.regulavita.com/llms.txt
```

An unpaid valid request demonstrates the machine-readable payment challenge:

```bash
curl -i -X POST \
  https://evidence.regulavita.com/v1/gtm/form-d-funding-leads \
  -H 'Content-Type: application/json' \
  -d '{"since":"2026-08-03T00:00:00Z","states":["CA","NY"],"minimum_amount_sold_usd":"1000000","limit":10}'
```

## Decision And Evidence Layers

The two `$0.01` routes return compact, unsigned decisions for frequent agent
workflows. The Form D GTM feed and premium evidence routes add:

- Official publisher and source-version metadata.
- Canonical request, source-bundle, component, and result SHA-256 hashes.
- An Ed25519-signed evidence receipt.
- Explicit data freshness and parser version.
- Paid-result replay bound to the original payment proof.

The OFAC route is exact-match evidence only. It is not fuzzy screening,
ownership/control analysis, sanctions clearance, transaction authorization, or
legal advice. The SEC route returns factual filing records and deterministic
deltas, never materiality opinions, valuation, investment advice, or execution
instructions.

The Form D route is a factual sales trigger. Form D is an issuer-filed notice of
an exempt offering; its total offering amount is not proof of capital raised.
The API exposes the separately reported amount sold and preserves official SEC
links so consuming agents can state that distinction accurately.

## Operator Documentation

The implementation, source controls, payment flow, deployment configuration,
operating limits, and experiment gates are documented in
[`autonomous_data_api/README.md`](autonomous_data_api/README.md).

Validate a checkout with:

```bash
PYTHONPATH=. uv run --with-requirements autonomous_data_api/requirements.txt \
  pytest autonomous_data_api/tests -q
uvx ruff check autonomous_data_api
uvx ruff format --check autonomous_data_api
docker build -t official-source-evidence-api:local \
  -f autonomous_data_api/Dockerfile .
```

This remains a capped demand experiment. Owner-funded calls prove technical
settlement and discovery, not independent demand, revenue quality, or a
validated business.
