import logging

from regeste.core.registry import Registry


def test_new_creates_all_entries_pending(tmp_path):
    registry = Registry.new(tmp_path, meta={"project_name": "test"}, file_names=["a.jpg", "b.jpg"])
    assert registry.path.exists()
    assert {name: e.status for name, e in registry.files.items()} == {
        "a.jpg": "pending",
        "b.jpg": "pending",
    }


def test_save_and_load_roundtrip(tmp_path):
    registry = Registry.new(tmp_path, meta={"project_name": "test"}, file_names=["a.jpg"])
    registry.record_result(
        "a.jpg", text="hello", description="", tokens_in=10, tokens_out=5, cost=0.01, model="x"
    )
    registry.save()

    reloaded = Registry.load(tmp_path)
    assert reloaded is not None
    assert reloaded.meta == {"project_name": "test"}
    assert reloaded.files["a.jpg"].status == "ok"
    assert reloaded.files["a.jpg"].text == "hello"


def test_load_folder_without_registry_returns_none(tmp_path):
    assert Registry.load(tmp_path) is None


def test_files_to_process_new_mode_takes_everything(tmp_path):
    registry = Registry.new(tmp_path, meta={}, file_names=["a.jpg", "b.jpg"])
    registry.record_result(
        "a.jpg", text="x", description="", tokens_in=1, tokens_out=1, cost=0.0, model="x"
    )
    assert sorted(registry.files_to_process("new")) == ["a.jpg", "b.jpg"]


def test_files_to_process_resume_mode_skips_ok_and_retries_error(tmp_path):
    registry = Registry.new(tmp_path, meta={}, file_names=["a.jpg", "b.jpg", "c.jpg"])
    registry.record_result(
        "a.jpg", text="x", description="", tokens_in=1, tokens_out=1, cost=0.0, model="x"
    )
    registry.record_error("b.jpg", "boom")
    # c.jpg stays pending

    to_process = sorted(registry.files_to_process("resume"))
    assert to_process == ["b.jpg", "c.jpg"]


def test_atomic_save_leaves_no_temp_file(tmp_path):
    registry = Registry.new(tmp_path, meta={}, file_names=["a.jpg"])
    registry.save()
    temp_files = list(tmp_path.glob(".regeste.json.*.tmp"))
    assert temp_files == []


def test_verbose_diagnostic_logging(tmp_path, caplog):
    """The Logs tab's "Verbose" checkbox surfaces DEBUG logs — check the registry actually
    emits some at its key points (load/new/save/record_result/record_error).
    """
    with caplog.at_level(logging.DEBUG, logger="regeste.core.registry"):
        registry = Registry.new(tmp_path, meta={}, file_names=["a.jpg", "b.jpg"])
        registry.record_result(
            "a.jpg", text="x", description="", tokens_in=1, tokens_out=1, cost=0.0, model="m"
        )
        registry.record_error("b.jpg", "boom")
        registry.save()
        Registry.load(tmp_path)

    messages = " | ".join(caplog.messages)
    assert "a.jpg" in messages and "recorded ok" in messages
    assert "b.jpg" in messages and "boom" in messages
    assert "saved" in messages
    assert "loaded" in messages
