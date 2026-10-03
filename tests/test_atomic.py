from pathlib import Path

import pytest

from regeste.core.atomic import atomic_output, atomic_write_text


def test_atomic_write_text_replaces_file(tmp_path):
    target = tmp_path / "out.txt"
    target.write_text("old", encoding="utf-8")
    atomic_write_text(target, "new")
    assert target.read_text(encoding="utf-8") == "new"
    assert [p.name for p in tmp_path.iterdir()] == ["out.txt"]


def test_atomic_output_keeps_old_file_on_failure(tmp_path):
    target = tmp_path / "out.bin"
    target.write_text("good", encoding="utf-8")

    @atomic_output
    def boom(data, output_path: Path):
        output_path.write_text("partial", encoding="utf-8")
        raise RuntimeError("crash")

    with pytest.raises(RuntimeError):
        boom(1, target)
    assert target.read_text(encoding="utf-8") == "good"
    assert [p.name for p in tmp_path.iterdir()] == ["out.bin"]


def test_atomic_output_returns_real_path(tmp_path):
    target = tmp_path / "sub" / "out.txt"

    @atomic_output
    def ok(output_path: Path):
        output_path.write_text("x", encoding="utf-8")
        return output_path

    assert ok(target) == target
    assert target.read_text(encoding="utf-8") == "x"
