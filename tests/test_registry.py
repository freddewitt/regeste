import logging

from regeste.core.registry import Registry


def test_new_creates_all_entries_pending(tmp_path):
    registry = Registry.new(tmp_path, meta={"project_name": "test"}, file_names=["a.jpg", "b.jpg"])
    assert registry.path.exists()
    # Keys are prefixed with batch_id (tmp_path.name)
    batch_id = tmp_path.name
    assert {name: e.transcription.status for name, e in registry.files.items()} == {
        f"{batch_id}_a.jpg": "pending",
        f"{batch_id}_b.jpg": "pending",
    }
    # display_name() strips the prefix
    assert registry.display_name(f"{batch_id}_a.jpg") == "a.jpg"
    assert registry.get_by_display_name("a.jpg") is not None


def test_save_and_load_roundtrip(tmp_path):
    registry = Registry.new(tmp_path, meta={"project_name": "test"}, file_names=["a.jpg"])
    batch_id = tmp_path.name
    registry.record_result(
        f"{batch_id}_a.jpg", text="hello", description="", tokens_in=10, tokens_out=5, cost=0.01, model="x"
    )
    registry.save()

    reloaded = Registry.load(tmp_path)
    assert reloaded is not None
    assert reloaded.meta["project_name"] == "test"
    assert reloaded.files[f"{batch_id}_a.jpg"].transcription.status == "ok"
    assert reloaded.files[f"{batch_id}_a.jpg"].transcription.text == "hello"
    # display_name works after reload
    assert reloaded.display_name(f"{batch_id}_a.jpg") == "a.jpg"


def test_load_folder_without_registry_returns_none(tmp_path):
    assert Registry.load(tmp_path) is None


def test_files_to_process_new_mode_takes_everything(tmp_path):
    registry = Registry.new(tmp_path, meta={}, file_names=["a.jpg", "b.jpg"])
    batch_id = tmp_path.name
    registry.record_result(
        f"{batch_id}_a.jpg", text="x", description="", tokens_in=1, tokens_out=1, cost=0.0, model="x"
    )
    assert sorted(registry.files_to_process("new")) == [f"{batch_id}_a.jpg", f"{batch_id}_b.jpg"]


def test_files_to_process_resume_mode_skips_ok_and_retries_error(tmp_path):
    registry = Registry.new(tmp_path, meta={}, file_names=["a.jpg", "b.jpg", "c.jpg"])
    batch_id = tmp_path.name
    registry.record_result(
        f"{batch_id}_a.jpg", text="x", description="", tokens_in=1, tokens_out=1, cost=0.0, model="x"
    )
    registry.record_error(f"{batch_id}_b.jpg", "boom")
    # c.jpg stays pending

    to_process = sorted(registry.files_to_process("resume"))
    assert to_process == [f"{batch_id}_b.jpg", f"{batch_id}_c.jpg"]


def test_atomic_save_leaves_no_temp_file(tmp_path):
    registry = Registry.new(tmp_path, meta={}, file_names=["a.jpg"])
    registry.save()
    temp_files = list(tmp_path.glob(".regeste.json.*.tmp"))
    assert temp_files == []


def test_verbose_diagnostic_logging(tmp_path, caplog):
    """The Logs tab's "Verbose" checkbox surfaces DEBUG logs — check the registry actually
    emits some at its key points (load/new/save/record_result/record_error).
    """
    batch_id = tmp_path.name
    with caplog.at_level(logging.DEBUG, logger="regeste.core.registry"):
        registry = Registry.new(tmp_path, meta={}, file_names=["a.jpg", "b.jpg"])
        registry.record_result(
            f"{batch_id}_a.jpg", text="x", description="", tokens_in=1, tokens_out=1, cost=0.0, model="m"
        )
        registry.record_error(f"{batch_id}_b.jpg", "boom")
        registry.save()
        Registry.load(tmp_path)

    messages = " | ".join(caplog.messages)
    assert f"{batch_id}_a.jpg" in messages and "recorded ok" in messages
    assert f"{batch_id}_b.jpg" in messages and "boom" in messages
    assert "saved" in messages
    assert "loaded" in messages


def test_v1_migration(tmp_path):
    """A v1 registry (flat fields, no schema_version) is migrated transparently to v2."""
    import json

    v1_data = {
        "meta": {"project_name": "legacy", "transcription_mode": "literal"},
        "files": {
            "doc.jpg": {
                "status": "ok",
                "text": "transcribed text",
                "description": "a document",
                "language": "fr",
                "tokens_in": 100,
                "tokens_out": 50,
                "cost": 0.05,
                "model": "claude-x",
                "date": "2026-01-01T00:00:00+00:00",
                "error_message": None,
            },
            "pending.jpg": {
                "status": "pending",
                "text": "",
                "description": "",
                "language": "",
                "tokens_in": 0,
                "tokens_out": 0,
                "cost": 0.0,
                "model": None,
                "date": None,
                "error_message": None,
            },
        },
    }
    (tmp_path / "regeste.json").write_text(json.dumps(v1_data), encoding="utf-8")

    registry = Registry.load(tmp_path)
    assert registry is not None
    # schema_version bumped to 2
    assert registry.meta["schema_version"] == 2

    # Migrated entry has nested structure
    doc = registry.files["doc.jpg"]
    assert doc.transcription.status == "ok"
    assert doc.transcription.text == "transcribed text"
    assert doc.transcription.mode == "literal"  # inherited from meta
    assert doc.transcription.tokens_in == 100
    assert doc.transcription.cost == 0.05
    assert doc.description == "a document"
    assert doc.language == "fr"
    assert doc.source.batch_id == tmp_path.name
    assert doc.source.imported is False

    pending = registry.files["pending.jpg"]
    assert pending.transcription.status == "pending"

    # Re-loading the saved v2 works without re-migrating
    reloaded = Registry.load(tmp_path)
    assert reloaded.meta["schema_version"] == 2
    assert reloaded.files["doc.jpg"].transcription.text == "transcribed text"


def test_two_batch_ids(tmp_path):
    """Two distinct batch_ids in a single registry coexist without collision."""
    registry = Registry.new(tmp_path, meta={}, file_names=["batch1_doc.jpg"])
    batch_id = tmp_path.name

    # Simulate a second batch
    from regeste.core.registry import FileEntry, SourceInfo, TranscriptionInfo

    registry.files["batch2_doc.jpg"] = FileEntry(
        source=SourceInfo(batch_id="batch2", physical_path="/other/path/batch2_doc.jpg", imported=True, key_prefixed=False),
    )
    registry.record_result(
        f"{batch_id}_batch1_doc.jpg", text="from batch1", description="", tokens_in=10, tokens_out=5,
        cost=0.01, model="m", transcription_mode="literal",
    )
    registry.record_result(
        "batch2_doc.jpg", text="from batch2", description="", tokens_in=20, tokens_out=10,
        cost=0.02, model="m", transcription_mode="hypotheses",
    )
    registry.save()

    reloaded = Registry.load(tmp_path)
    assert reloaded is not None
    b1 = reloaded.files[f"{batch_id}_batch1_doc.jpg"]
    b2 = reloaded.files["batch2_doc.jpg"]

    assert b1.source.batch_id == tmp_path.name  # default for new()
    assert b2.source.batch_id == "batch2"
    assert b1.transcription.mode == "literal"
    assert b2.transcription.mode == "hypotheses"
    assert b1.transcription.text == "from batch1"
    assert b2.transcription.text == "from batch2"
    # display_name for prefixed key
    assert reloaded.display_name(f"{batch_id}_batch1_doc.jpg") == "batch1_doc.jpg"
    # display_name for unprefixed key (backward compat)
    assert reloaded.display_name("batch2_doc.jpg") == "batch2_doc.jpg"
