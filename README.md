# ITI Official Source Evidence API

Standalone Fly.io deployment for the experiment selected by the ITI Signal
autonomous-business research sprint.

- `POST /v1/sec/filing-trigger-delta` charges `$0.10` test USDC.
- `POST /v1/ofac/exact-identifier-evidence` charges `$0.05` test USDC.
- Base Sepolia and the public x402.org facilitator are used for the first
  deployment. No real payments are accepted by this configuration.

The full service contract, controls, source handling, and experiment gates are
documented in [`autonomous_data_api/README.md`](autonomous_data_api/README.md).

## Deployment

Fly builds `autonomous_data_api/Dockerfile`, mounts the `evidence_data` volume at
`/data`, and keeps one shared 1 GB machine running. Deployment secrets are set
in Fly and are never committed to this repository.

## Validation

```bash
PYTHONPATH=. uv run --with-requirements autonomous_data_api/requirements.txt \
  pytest autonomous_data_api/tests -q
uvx ruff check autonomous_data_api
uvx ruff format --check autonomous_data_api
docker build -t official-source-evidence-api:local \
  -f autonomous_data_api/Dockerfile .
```

The ignored `.local/testnet-wallets.json` file can be used by the bounded live
purchase check:

```bash
PYTHONPATH=. uv run --with-requirements autonomous_data_api/requirements.txt \
  python autonomous_data_api/testnet_purchase.py
```

The runner preflights and verifies the Base Sepolia network, exact price,
recipient, and resource URL before signing the payment.
