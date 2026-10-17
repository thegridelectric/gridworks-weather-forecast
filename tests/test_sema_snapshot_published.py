"""The vendored sema snapshot ships only published vocabulary on `main`.

A snapshot built with `--allow-staged` is for dev brokers only (sema spec,
snapshot "Published-only by default"); gwwf is deployed from `main`.
Every word in the snapshot's expanded seed is looked up in the sema
registry rather than trusting the snapshot's own staging marker, so a
hand-built snapshot with no marker is caught too, as is a word the
registry no longer has (a staging word can be withdrawn). The registry
comes from `SEMA_PUBLIC_REGISTRY` or the sibling sema checkout; without
either the test self-skips. Off `main` the staging words are reported,
not failed.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "src" / "gwwf" / "sema"
PRODUCTION_BRANCH = "main"


def registry_path() -> Path | None:
    override = os.environ.get("SEMA_PUBLIC_REGISTRY")
    if override:
        return Path(override)
    sibling = ROOT.parent / "sema" / "indexes" / "public_registry.yaml"
    return sibling if sibling.exists() else None


def current_branch() -> str:
    """The branch under test: GitHub Actions' ref (the PR's target on a
    pull request), else the checkout's."""
    for key in ("GITHUB_BASE_REF", "GITHUB_REF_NAME"):
        if os.environ.get(key):
            return os.environ[key]
    out = subprocess.run(
        ["git", "-C", str(ROOT), "branch", "--show-current"],
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.strip()


def status_of(entry: dict | None, version: str | None) -> str:
    """The registry status of a word (version None) or of one version.
    A registry entry with no status field is not a sema registry index
    from `dev`; say so rather than guess."""
    if entry is None:
        return "not in registry"
    target = entry if version is None else entry.get("versions", {}).get(version)
    if target is None:
        return "not in registry"
    if "status" not in target:
        raise AssertionError(
            "registry entry has no status field; is the sema checkout on dev, "
            "or SEMA_PUBLIC_REGISTRY pointing at indexes/public_registry.yaml?"
        )
    return target["status"]


def offenders_in(kind: str, worklist: dict, registry: dict) -> list[str]:
    """Non-published words of one kind (enums or types) in the expanded seed.
    A literal enum and a versionless type are looked up by name; every
    other word by each version the seed lists."""
    found: list[str] = []
    for name, spec in (worklist.get(kind) or {}).items():
        entry = registry[kind].get(name)
        versionless = "path" in spec or (
            entry is not None and entry.get("enum_type") == "literal"
        )
        if versionless:
            if (status := status_of(entry, None)) != "published":
                found.append(f"{kind[:-1]} {name} ({status})")
            continue
        for version in spec:
            if (status := status_of(entry, version)) != "published":
                found.append(f"{kind[:-1]} {name}:{version} ({status})")
    return found


def staging_words(snapshot: Path, registry: dict) -> list[str]:
    """Every word/version in the snapshot's expanded seed whose registry
    status is not published. Mirrors the sema CLI's staging-closure walk."""
    seed = yaml.safe_load((snapshot / "indexes" / "seed_expanded.yaml").read_text())
    worklist = seed.get("worklist", {})
    return sorted(
        offenders_in("enums", worklist, registry)
        + offenders_in("types", worklist, registry)
    )


def test_snapshot_is_published_only_on_main() -> None:
    path = registry_path()
    if path is None:
        pytest.skip("no sema registry: set SEMA_PUBLIC_REGISTRY or check out ../sema")
    registry = yaml.safe_load(path.read_text())

    offenders = staging_words(SNAPSHOT, registry)
    if not offenders:
        return
    report = "src/gwwf/sema holds non-published words:\n  " + "\n  ".join(offenders)
    branch = current_branch()
    if branch == PRODUCTION_BRANCH:
        pytest.fail(report)
    pytest.skip(f"on {branch!r}, not {PRODUCTION_BRANCH!r}; {report}")
