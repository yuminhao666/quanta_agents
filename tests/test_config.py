from __future__ import annotations

from quanta_agents.core import config


def test_quanta_data_root_respects_explicit_path(tmp_path, monkeypatch):
    explicit = tmp_path / "custom"
    monkeypatch.setenv("GJ_QUANTA_DATA_ROOT", str(tmp_path / "stale"))

    assert config.quanta_data_root(explicit) == explicit


def test_quanta_data_root_respects_env_path_even_without_taxonomy(tmp_path, monkeypatch):
    configured = tmp_path / "migrated" / "quanta_data"
    configured.mkdir(parents=True)
    default_root = tmp_path / "old_default" / "quanta_data"
    marker = default_root / "gold" / "reference_data" / "assets" / "futures_assets.v1.json"
    marker.parent.mkdir(parents=True)
    marker.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(config, "DEFAULT_QUANTA_DATA_ROOT", default_root)
    monkeypatch.setenv("GJ_QUANTA_DATA_ROOT", str(configured))

    assert config.quanta_data_root() == configured


def test_quanta_data_root_uses_migrated_default_when_present(tmp_path, monkeypatch):
    default_root = tmp_path / "Volumes" / "数字大脑" / "quanta_data"
    default_root.mkdir(parents=True)
    legacy_root = tmp_path / "Documents" / "quanta_data"
    marker = legacy_root / "gold" / "reference_data" / "assets" / "futures_assets.v1.json"
    marker.parent.mkdir(parents=True)
    marker.write_text("{}", encoding="utf-8")

    monkeypatch.delenv("GJ_QUANTA_DATA_ROOT", raising=False)
    monkeypatch.setattr(config, "DEFAULT_QUANTA_DATA_ROOT", default_root)
    monkeypatch.setattr(config, "LEGACY_QUANTA_DATA_ROOTS", (legacy_root,))

    assert config.quanta_data_root() == default_root


def test_quanta_data_root_respects_legacy_alias_env_when_explicit_missing(tmp_path, monkeypatch):
    configured = tmp_path / "legacy_alias" / "quanta_data"
    configured.mkdir(parents=True)

    default_root = tmp_path / "missing" / "quanta_data"
    monkeypatch.delenv("GJ_QUANTA_DATA_ROOT", raising=False)
    monkeypatch.setenv("QUANTA_DATA_ROOT", str(configured))
    monkeypatch.setattr(config, "DEFAULT_QUANTA_DATA_ROOT", default_root)

    assert config.quanta_data_root() == configured


def test_quanta_data_root_falls_back_to_legacy_only_when_default_missing(tmp_path, monkeypatch):
    default_root = tmp_path / "missing" / "quanta_data"
    legacy_root = tmp_path / "Documents" / "quanta_data"
    marker = legacy_root / "gold" / "reference_data" / "assets" / "futures_assets.v1.json"
    marker.parent.mkdir(parents=True)
    marker.write_text("{}", encoding="utf-8")

    monkeypatch.delenv("GJ_QUANTA_DATA_ROOT", raising=False)
    monkeypatch.setattr(config, "DEFAULT_QUANTA_DATA_ROOT", default_root)
    monkeypatch.setattr(config, "LEGACY_QUANTA_DATA_ROOTS", (legacy_root,))

    assert config.quanta_data_root() == legacy_root
