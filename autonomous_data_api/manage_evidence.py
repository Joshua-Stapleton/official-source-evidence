from __future__ import annotations

import argparse
import json
from pathlib import Path

from autonomous_data_api.evidence import EvidenceService


def snapshot_payload(snapshot: object) -> dict[str, object]:
    return {
        "source_id": snapshot.source_id,
        "source_version": snapshot.source_version,
        "content_sha256": f"sha256:{snapshot.content_sha256}",
        "retrieved_at": snapshot.retrieved_at,
        "verified_at": snapshot.verified_at,
        "published_at": snapshot.published_at,
        "official_digest_sha256": snapshot.official_digest_sha256,
        "official_digest_verified": snapshot.official_digest_verified,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Operate deterministic evidence sources"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    refresh = subparsers.add_parser("refresh-ofac")
    refresh.add_argument(
        "--list", choices=["SDN", "CONSOLIDATED", "ALL"], default="ALL"
    )
    refresh.add_argument("--force", action="store_true")

    imported = subparsers.add_parser("import-ofac")
    imported.add_argument("--list", choices=["SDN", "CONSOLIDATED"], required=True)
    imported.add_argument("--file", type=Path, required=True)
    imported.add_argument("--source-version")
    imported.add_argument("--official-digest-sha256")

    subparsers.add_parser("status")
    args = parser.parse_args()
    service = EvidenceService()

    if args.command == "status":
        print(json.dumps(service.experiment_status(), indent=2, sort_keys=True))
        return 0

    if args.command == "import-ofac":
        snapshot = service.import_ofac_file(
            args.list,
            args.file.read_bytes(),
            source_version=args.source_version,
            official_digest_sha256=args.official_digest_sha256,
        )
        print(json.dumps(snapshot_payload(snapshot), indent=2, sort_keys=True))
        return 0

    list_names = ["SDN", "CONSOLIDATED"] if args.list == "ALL" else [args.list]
    snapshots = [
        snapshot_payload(service.refresh_ofac(list_name, force=args.force))
        for list_name in list_names
    ]
    print(json.dumps(snapshots, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
