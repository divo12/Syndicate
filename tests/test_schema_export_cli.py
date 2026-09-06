import hashlib
from pathlib import Path

import pytest

from syndicate.cli import main
from syndicate.services.schema_export import SchemaExportReceipt


def test_schema_export_has_fixed_paths_and_content_hashes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)

    assert main(["export-schema"]) == 0

    receipt = SchemaExportReceipt.model_validate_json(capsys.readouterr().out)
    command = Path(receipt.command_schema_path)
    response = Path(receipt.receipt_schema_path)
    assert command == tmp_path / ".syndicate/schemas/command-request-v1.json"
    assert response == tmp_path / ".syndicate/schemas/command-receipt-v1.json"
    assert (
        receipt.command_schema_sha256
        == hashlib.sha256(command.read_bytes()).hexdigest()
    )
