"""Write versioned controller schema artifacts under a controller root."""

import hashlib
from pathlib import Path
from typing import Literal

from syndicate.models.commands import command_schema_json, receipt_schema_json
from syndicate.models.envelope import Digest, WireModel


class SchemaExportReceipt(WireModel):
    schema_version: Literal[1] = 1
    command_schema_path: str
    command_schema_sha256: Digest
    receipt_schema_path: str
    receipt_schema_sha256: Digest


def schema_artifact_paths(root: Path) -> tuple[Path, Path]:
    if not root.is_absolute() or root.resolve() != root:
        raise ValueError("Schema root must be an absolute nonsymlink path")
    schema_root = root / "schemas"
    return (
        schema_root / "command-request-v1.json",
        schema_root / "command-receipt-v1.json",
    )


def write_schemas(root: Path) -> tuple[Path, Path]:
    paths = schema_artifact_paths(root)
    paths[0].parent.mkdir(parents=True, exist_ok=True)
    for path, payload in zip(
        paths, (command_schema_json(), receipt_schema_json()), strict=True
    ):
        if path.exists() and path.read_text(encoding="utf-8") != payload:
            raise ValueError("Versioned schema artifact differs from this controller")
        if not path.exists():
            path.write_text(payload, encoding="utf-8")
    return paths


def export_schemas(root: Path) -> SchemaExportReceipt:
    paths = write_schemas(root)
    command_payload, receipt_payload = (
        paths[0].read_bytes(),
        paths[1].read_bytes(),
    )
    return SchemaExportReceipt(
        command_schema_path=str(paths[0]),
        command_schema_sha256=hashlib.sha256(command_payload).hexdigest(),
        receipt_schema_path=str(paths[1]),
        receipt_schema_sha256=hashlib.sha256(receipt_payload).hexdigest(),
    )
