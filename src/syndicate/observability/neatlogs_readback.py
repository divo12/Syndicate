"""Read-only Neatlogs v3 trace readback; never persists remote payloads."""

import hashlib
import json
from typing import NewType
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from .neatlogs_capture import RunLink

NeatlogsTraceRef = NewType("NeatlogsTraceRef", str)
NeatlogsSpanRef = NewType("NeatlogsSpanRef", str)


class ReadbackSpan(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    span_id: str = Field(pattern=r"^[0-9a-f]{16}$")
    parent_span_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{16}$")
    node_name: str
    node_type: str


class NeatlogsReadbackReceipt(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    link: RunLink
    trace_ref: str = Field(pattern=r"^[0-9a-f]{32}$")
    finalized: bool
    complete: bool
    semantic_digest: str
    spans: tuple[ReadbackSpan, ...]


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args: object, **kwargs: object) -> None:
        return None


class NeatlogsReadbackReader:
    def __init__(
        self, api_key: SecretStr, endpoint: str = "https://ingest.neatlogs.com"
    ) -> None:
        self._api_key = api_key
        self._endpoint = endpoint.rstrip("/")

    def read(
        self, link: RunLink, trace_ref: NeatlogsTraceRef
    ) -> NeatlogsReadbackReceipt:
        request = Request(
            f"{self._endpoint}/api/traces/v3/{trace_ref}",
            headers={"x-api-key": self._api_key.get_secret_value()},
        )
        try:
            with build_opener(_NoRedirect()).open(request, timeout=10) as response:
                payload = response.read()
        except (HTTPError, URLError, OSError) as error:
            raise ValueError("Neatlogs readback unavailable") from error
        parsed = json.loads(payload)
        if not isinstance(parsed, dict):
            raise ValueError("Neatlogs readback is not an object")
        trace_id = parsed.get("_id")
        status = parsed.get("status")
        spans = parsed.get("spans")
        count = parsed.get("spanCount")
        if (
            not isinstance(trace_id, str)
            or trace_id != trace_ref
            or not isinstance(spans, list)
        ):
            raise ValueError("Neatlogs readback identity is invalid")
        typed_spans = tuple(ReadbackSpan.model_validate(span) for span in spans)
        complete = status == "finalized" and count == len(typed_spans)
        return NeatlogsReadbackReceipt(
            link=link,
            trace_ref=trace_id,
            finalized=status == "finalized",
            complete=complete,
            semantic_digest="sha256:" + hashlib.sha256(payload).hexdigest(),
            spans=typed_spans,
        )
