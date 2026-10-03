import json

import keyring

from regeste.core.project import ProjectConfig, ProviderConfig


def _config(tmp_path, **provider):
    return ProjectConfig(
        project_name="p", source_dir=tmp_path, output_dir=tmp_path,
        provider=ProviderConfig(kind="claude", model="m", **provider),
    )


def test_api_key_never_written_to_meta_but_restored(tmp_path):
    meta = _config(tmp_path, api_key="sk-secret").to_meta()
    assert "sk-secret" not in json.dumps(meta)
    assert meta["provider"]["api_key"] is None
    assert ProjectConfig.from_meta(meta).provider.api_key == "sk-secret"


def test_key_is_shared_between_projects(tmp_path):
    _config(tmp_path, api_key="sk-secret").to_meta()
    other = _config(tmp_path).to_meta()
    assert ProjectConfig.from_meta(other).provider.api_key == "sk-secret"


def test_legacy_clear_text_key_is_migrated(tmp_path, memory_keyring):
    meta = _config(tmp_path).to_meta()
    meta["provider"]["api_key"] = "sk-legacy"
    assert ProjectConfig.from_meta(meta).provider.api_key == "sk-legacy"
    assert ("regeste", "claude|") in memory_keyring.store
    assert "sk-legacy" not in json.dumps(_config(tmp_path, api_key="sk-legacy").to_meta())


def test_no_keychain_means_key_not_persisted(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("no backend")

    monkeypatch.setattr(keyring, "set_password", boom)
    monkeypatch.setattr(keyring, "get_password", boom)
    meta = _config(tmp_path, api_key="sk-secret").to_meta()
    assert "sk-secret" not in json.dumps(meta)
    assert ProjectConfig.from_meta(meta).provider.api_key is None
