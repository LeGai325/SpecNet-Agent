#!/usr/bin/env python3
"""Install and verify the repository's frozen trace profile bundle."""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import zipfile
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ARCHIVE = (
    REPOSITORY_ROOT
    / "data_profiles"
    / "bundles"
    / "SpecNet-Agent-Trace-Profiles-20260801.zip"
)
DEFAULT_DATA_ROOT = REPOSITORY_ROOT / "external_agent_data"
EXPECTED_SHA256 = {
    "processed/trace_driven_v1/profile.json": (
        "a0396e1ea644f5a7c74a340ad9212ada3bd6bdfcac6996984effb71ee7fec1ae"
    ),
    "processed/trace_driven_v2/profile.json": (
        "4dbe8541f9ac8e6b901c165273e18cf169fe02f043d936b3125e622e272ceec2"
    ),
    "processed/trace_driven_v3_candidate/profile.json": (
        "926046f52a10ba4b4387fdca3755e092c6245fc922e4a1bee7d8cc472bd144e6"
    ),
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def install_profiles(
    archive: Path,
    data_root: Path,
    *,
    force: bool = False,
    expected_sha256: dict[str, str] | None = None,
) -> list[tuple[str, str]]:
    """Extract only the approved profile members and verify their hashes."""
    expected = expected_sha256 or EXPECTED_SHA256
    if not archive.is_file():
        raise FileNotFoundError(f"profile bundle not found: {archive}")

    results: list[tuple[str, str]] = []
    with zipfile.ZipFile(archive) as bundle:
        members = set(bundle.namelist())
        for relative_path, expected_hash in expected.items():
            member = f"external_agent_data/{relative_path}"
            if member not in members:
                raise ValueError(f"profile bundle is missing required member: {member}")

            destination = data_root / relative_path
            if destination.is_file():
                current_hash = sha256_file(destination)
                if current_hash == expected_hash:
                    results.append((relative_path, "already verified"))
                    continue
                if not force:
                    raise ValueError(
                        f"existing profile checksum mismatch: {destination}; "
                        "rerun with --force only after confirming replacement"
                    )

            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_name(f".{destination.name}.tmp")
            try:
                with bundle.open(member) as source, temporary.open("wb") as output:
                    shutil.copyfileobj(source, output)
                actual_hash = sha256_file(temporary)
                if actual_hash != expected_hash:
                    raise ValueError(
                        f"bundle checksum mismatch for {member}: {actual_hash}"
                    )
                os.replace(temporary, destination)
            finally:
                temporary.unlink(missing_ok=True)
            results.append((relative_path, "installed and verified"))
    return results


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Install the frozen V1/V2/V3 trace profiles bundled with the repo."
    )
    parser.add_argument("--archive", type=Path, default=DEFAULT_ARCHIVE)
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument(
        "--force",
        action="store_true",
        help="Replace an existing profile only when its checksum differs.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    results = install_profiles(
        args.archive.resolve(),
        args.data_root.resolve(),
        force=args.force,
    )
    for relative_path, status in results:
        print(f"OK {relative_path}: {status}")
    print("\nUse the installed profiles with:")
    print(f'export SPECNET_DATA_ROOT="{args.data_root.resolve()}"')


if __name__ == "__main__":
    main()
