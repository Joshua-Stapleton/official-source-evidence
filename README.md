# ITI Official Source Evidence API

Standalone Fly.io deployment for the experiment selected by the ITI Signal
autonomous-business research sprint.

- `POST /v1/sec/filing-trigger-delta` charges `$0.10` USDC.
- `POST /v1/ofac/exact-identifier-evidence` charges `$0.05` USDC.
- The production deployment uses Base mainnet and the authenticated CDP x402
  facilitator, with a hard `$10.00` accepted-revenue cap per UTC day.

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

The ignored `.local/testnet-wallets.json` file can be used by the bounded
purchase check when the target deployment is explicitly configured for Base
Sepolia:

```bash
PYTHONPATH=. uv run --with-requirements autonomous_data_api/requirements.txt \
  python autonomous_data_api/testnet_purchase.py
```

The runner refuses mainnet and verifies the Base Sepolia network, exact price,
recipient, and resource URL before signing the payment.

The mainnet discovery bootstrap has a separate runner with an explicit monetary
arm. It refuses any network, asset, recipient, amount, buyer, or URL mismatch:

```bash
CONFIRM_MAINNET_BOOTSTRAP_USDC=0.05 PYTHONPATH=. \
  uv run --with-requirements autonomous_data_api/requirements.txt \
  python autonomous_data_api/mainnet_bootstrap_purchase.py
```
