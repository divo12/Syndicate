from unittest.mock import patch

import pytest
from pydantic import ValidationError

from syndicate.runtime_contracts import RuntimeIdentity, installed_runtime


def test_actual_installed_runtime_is_pinned() -> None:
    identity = installed_runtime()
    assert identity.harbor_version == "0.22.0"
    assert identity.nexau_version == "0.3.9"
    assert identity.nexau_commit == "35ee1861546db3cb280a6e17e38a74060d7c96c3"
    with pytest.raises(ValidationError):
        identity.harbor_version = "0.22.0"  # type: ignore[misc]


def test_alternate_package_version_is_rejected() -> None:
    with patch("syndicate.runtime_contracts.version", return_value="0.0.0"):
        with pytest.raises(ValidationError):
            installed_runtime()


def test_alternate_source_commit_is_rejected() -> None:
    with pytest.raises(ValidationError):
        RuntimeIdentity(nexau_commit="0" * 40)  # type: ignore[arg-type]
