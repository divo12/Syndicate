"""Run the same local and CI gates; exit nonzero on any failure."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def complexity(paths: list[str]) -> int:
    for name in paths:
        path = Path(name)
        files = sorted(path.rglob("*.py")) if path.is_dir() else [path]
        if not files or any(not file.is_file() for file in files):
            print(f"No Python input: {name}", file=sys.stderr)
            return 1
    result = subprocess.run(
        [sys.executable, "-m", "radon", "cc", "--min", "C", "--show-closures", *paths],
        capture_output=True,
        text=True,
        check=False,
    )
    print(result.stdout, end="")
    print(result.stderr, end="", file=sys.stderr)
    return int(
        bool(result.returncode or result.stdout.strip() or result.stderr.strip())
    )


def main(arguments: list[str]) -> int:
    if arguments:
        if arguments[0] == "--complexity-only" and len(arguments) > 1:
            return complexity(arguments[1:])
        print("Usage: quality.py [--complexity-only PATH ...]", file=sys.stderr)
        return 2
    commands = (
        ("ruff", "format", "--check", "src", "scripts", "tests"),
        ("ruff", "check", "src", "scripts", "tests"),
        ("mypy",),
        ("pytest",),
    )
    for command in commands:
        print("+ " + " ".join(command), flush=True)
        result = subprocess.run([sys.executable, "-m", *command], cwd=ROOT, check=False)
        if result.returncode:
            return result.returncode
    return complexity([str(ROOT / name) for name in ("src", "scripts", "tests")])


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
