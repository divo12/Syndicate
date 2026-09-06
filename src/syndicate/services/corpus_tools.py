"""Read and write tools jailed to a corpus or harness workspace."""

from __future__ import annotations

from pathlib import Path

from nexau import Tool
from pydantic import BaseModel, ConfigDict, Field


class _PathInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    path: str = Field(min_length=1, max_length=400)


class _WriteInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    path: str = Field(min_length=1, max_length=400)
    content: str = Field(max_length=200_000)


class _GrepInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    pattern: str = Field(min_length=1, max_length=200)
    path: str = Field(default=".", max_length=400)


def _resolve(root: Path, relative: str) -> Path:
    candidate = Path(relative)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError("path escapes the allowed root")
    path = (root / candidate).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("path escapes the allowed root")
    return path


def read_tools(root: Path) -> tuple[Tool, ...]:
    def list_dir(path: str = ".") -> str:
        target = _resolve(root, path)
        if not target.is_dir():
            return f"not a directory: {path}"
        names = sorted(
            item.name + ("/" if item.is_dir() else "") for item in target.iterdir()
        )
        return "\n".join(names) or "(empty)"

    def read_file(path: str) -> str:
        target = _resolve(root, path)
        if not target.is_file():
            return f"missing file: {path}"
        return target.read_text(encoding="utf-8")[:20_000]

    def grep_files(pattern: str, path: str = ".") -> str:
        target = _resolve(root, path)
        hits: list[str] = []
        files = [target] if target.is_file() else sorted(target.rglob("*"))
        for item in files:
            if not item.is_file():
                continue
            text = item.read_text(encoding="utf-8", errors="replace")
            for number, line in enumerate(text.splitlines(), 1):
                if pattern in line:
                    rel = item.relative_to(root).as_posix()
                    hits.append(f"{rel}:{number}:{line[:200]}")
                    if len(hits) >= 50:
                        return "\n".join(hits)
        return "\n".join(hits) or f"no matches for {pattern}"

    return (
        Tool(
            name="list_dir",
            description="List files under the corpus.",
            input_schema=_PathInput.model_json_schema(),
            implementation=list_dir,
        ),
        Tool(
            name="read_file",
            description="Read one corpus file.",
            input_schema=_PathInput.model_json_schema(),
            implementation=read_file,
        ),
        Tool(
            name="grep_files",
            description="Search corpus files for a string.",
            input_schema=_GrepInput.model_json_schema(),
            implementation=grep_files,
        ),
    )


def write_tools(root: Path) -> tuple[Tool, ...]:
    def write_file(path: str, content: str) -> str:
        target = _resolve(root, path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return f"wrote {path}"

    return read_tools(root) + (
        Tool(
            name="write_file",
            description="Write one workspace file.",
            input_schema=_WriteInput.model_json_schema(),
            implementation=write_file,
        ),
    )
