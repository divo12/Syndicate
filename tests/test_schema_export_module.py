from pathlib import Path

import pytest

from syndicate.controllers.schema_export import main


def test_module_exports_fixed_schemas(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = (tmp_path / ".syndicate").resolve()
    assert main(["--root", str(root)]) == 0
    assert "command_schema_sha256" in capsys.readouterr().out
    assert main(["--root", "relative"]) == 2
