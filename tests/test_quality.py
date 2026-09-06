"""Exercise the quality command through its process boundary."""

import subprocess
import sys
from pathlib import Path

QUALITY = Path(__file__).resolve().parents[1] / "scripts" / "quality.py"


def check_complexity(path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(QUALITY), "--complexity-only", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_accepts_simple_python(tmp_path: Path) -> None:
    source = tmp_path / "simple.py"
    source.write_text("def identity(value: int) -> int:\n    return value\n")
    result = check_complexity(source)
    assert result.returncode == 0, result.stdout + result.stderr


def test_rejects_c_rank_function(tmp_path: Path) -> None:
    source = tmp_path / "complex.py"
    branches = "".join(f"    if value == {n}:\n        return {n}\n" for n in range(10))
    source.write_text("def choose(value: int) -> int:\n" + branches + "    return -1\n")
    result = check_complexity(source)
    assert result.returncode != 0
    assert "choose" in result.stdout


def test_rejects_missing_input(tmp_path: Path) -> None:
    result = check_complexity(tmp_path / "missing.py")
    assert result.returncode != 0
    assert "missing.py" in result.stderr


def test_rejects_invalid_python(tmp_path: Path) -> None:
    source = tmp_path / "broken.py"
    source.write_text("def broken(:\n")
    result = check_complexity(source)
    assert result.returncode != 0
    assert "ERROR" in result.stdout


def test_rejects_unknown_options() -> None:
    result = subprocess.run(
        [sys.executable, str(QUALITY), "--skip-checks"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "Usage:" in result.stderr


def test_accepts_cc_ten(tmp_path: Path) -> None:
    source = tmp_path / "boundary.py"
    branches = "".join(f"    if value == {n}:\n        return {n}\n" for n in range(9))
    source.write_text("def choose(value: int) -> int:\n" + branches + "    return -1\n")
    assert check_complexity(source).returncode == 0


def test_rejects_complex_methods_and_closures(tmp_path: Path) -> None:
    branches = "".join(
        f"        if value == {n}:\n            return {n}\n" for n in range(10)
    )
    for container in ("class Example:", "def outer():"):
        source = tmp_path / "nested.py"
        source.write_text(
            container + "\n    def choose(value):\n" + branches + "        return -1\n"
        )
        result = check_complexity(tmp_path)
        assert result.returncode != 0
        assert "choose" in result.stdout


def test_rejects_empty_directory(tmp_path: Path) -> None:
    result = check_complexity(tmp_path)
    assert result.returncode != 0
    assert "No Python input" in result.stderr
