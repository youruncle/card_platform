from __future__ import annotations

from pathlib import Path
from typing import Any

from card_platform.knowledge_manifest import load_manifest


MANIFEST_DIRECTORY = Path(__file__).parents[4] / "data" / "knowledge"


def manifest_catalog() -> dict[str, Path]:
    """Return only manifests whose declared key matches a safe file in the catalog."""
    catalog: dict[str, Path] = {}
    from card_platform.domain.nba_player_catalog import CATALOG_PATH

    for path in [*sorted(MANIFEST_DIRECTORY.glob("*.json")), CATALOG_PATH]:
        manifest = load_manifest(path)
        batch_key = manifest.get("batch_key")
        if (
            isinstance(batch_key, str)
            and batch_key
            and manifest.get("catalog_enabled") is True
            and batch_key not in catalog
        ):
            catalog[batch_key] = path
    return catalog


def catalog_metadata() -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for batch_key, path in manifest_catalog().items():
        manifest = load_manifest(path)
        records.append(
            {
                "batch_key": batch_key,
                "manifest_version": manifest.get("manifest_version"),
                "source_count": len(manifest.get("sources", [])),
                "entity_count": len(manifest.get("entities", [])),
                "path": path.name,
            }
        )
    return records


def catalog_manifest(batch_key: str) -> dict[str, Any] | None:
    path = manifest_catalog().get(batch_key)
    return load_manifest(path) if path else None
