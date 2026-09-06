"""Pinned runtime identity, checked before any executor dispatch."""

import sys
from importlib.metadata import distribution, version
from typing import Literal

from pydantic import BaseModel, ConfigDict


class RuntimeIdentity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    harbor_version: Literal["0.22.0"] = "0.22.0"
    e2b_version: Literal["2.26.0"] = "2.26.0"
    nexau_version: Literal["0.3.9"] = "0.3.9"
    nexau_commit: Literal["35ee1861546db3cb280a6e17e38a74060d7c96c3"]


class _VcsInfo(BaseModel):
    vcs: Literal["git"]
    commit_id: str


class _SourceReceipt(BaseModel):
    vcs_info: _VcsInfo


def installed_runtime() -> RuntimeIdentity:
    """Inspect installed metadata, not an advertised config; never call a provider."""
    if sys.version_info[:2] != (3, 13):
        raise RuntimeError("Syndicate requires Python 3.13")
    source = distribution("nexau").read_text("direct_url.json")
    if source is None:
        raise RuntimeError("NexAU Git installation provenance is required")
    receipt = _SourceReceipt.model_validate_json(source)
    return RuntimeIdentity.model_validate(
        {
            "harbor_version": version("harbor"),
            "e2b_version": version("e2b"),
            "nexau_version": version("nexau"),
            "nexau_commit": receipt.vcs_info.commit_id,
        }
    )
