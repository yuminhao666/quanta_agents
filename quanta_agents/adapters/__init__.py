from __future__ import annotations

from pathlib import Path
from typing import Any

from quanta_agents.core.config import quanta_data_root
from quanta_agents.core.io import read_json, relative_to_root
from quanta_agents.repositories import CatalogRepository, object_catalog_enabled


def register_payload_if_enabled(
    payload: dict[str, Any],
    *,
    kind: str,
    root: str | Path | None = None,
    source_path: str | Path | None = None,
) -> dict[str, Any]:
    if not object_catalog_enabled():
        return {"status": "skipped", "reason": "QUANTA_OBJECT_CATALOG_ENABLED is not true"}
    root_path = quanta_data_root(root)
    repository = CatalogRepository(root_path)
    if kind == "news_logic":
        from quanta_agents.adapters.news_logic_adapter import register_news_logic_payload

        return register_news_logic_payload(repository, payload)
    if kind == "research_signal":
        from quanta_agents.adapters.research_signal_adapter import register_research_signal_payload

        return register_research_signal_payload(repository, payload)
    if kind == "theme_anchor":
        from quanta_agents.adapters.theme_anchor_adapter import register_theme_anchor_payload

        return register_theme_anchor_payload(repository, payload)
    if kind == "news_event_batch":
        from quanta_agents.adapters.news_event_catalog_adapter import register_news_event_batch_result

        manifest_uri = relative_to_root(Path(source_path), root_path) if source_path else None
        return register_news_event_batch_result(repository, payload, manifest_uri=manifest_uri)
    if kind == "run_manifest":
        from quanta_agents.adapters.run_manifest_adapter import register_run_manifest

        manifest_uri = relative_to_root(Path(source_path), root_path) if source_path else None
        return register_run_manifest(repository, payload, manifest_uri=manifest_uri)
    raise ValueError(f"unsupported catalog adapter kind: {kind}")


def register_file_if_enabled(
    path: str | Path,
    *,
    kind: str,
    root: str | Path | None = None,
) -> dict[str, Any]:
    file_path = Path(path).expanduser()
    payload = read_json(file_path)
    if not isinstance(payload, dict):
        raise ValueError(f"catalog adapter expects a JSON object: {path}")
    return register_payload_if_enabled(payload, kind=kind, root=root, source_path=file_path)
