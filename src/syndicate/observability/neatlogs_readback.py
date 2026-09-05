"""Read-only Neatlogs v3 trace readback; never persists remote payloads."""

import hashlib
import json
from typing import NewType, cast
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
    input_text: str | None
    output_text: str | None


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
                payload = response.read(1_000_001)
        except (HTTPError, URLError, OSError) as error:
            raise ValueError("Neatlogs readback unavailable") from error
        if len(payload) > 1_000_000:
            raise ValueError("Neatlogs readback exceeds byte limit")
        parsed = json.loads(payload)
        if not isinstance(parsed, dict):
            raise ValueError("Neatlogs readback is not an object")
        trace_id = parsed.get("_id")
        status = parsed.get("status")
        spans = parsed.get("spans")
        if (
            not isinstance(trace_id, str)
            or trace_id != trace_ref
            or not isinstance(spans, list)
        ):
            raise ValueError("Neatlogs readback identity is invalid")
        typed_spans = tuple(self._span(span) for span in spans)
        persisted_link = self._persisted_link(spans)
        if persisted_link != link:
            raise ValueError("Neatlogs persisted RunLink does not match request")
        complete = status == "success"
        return NeatlogsReadbackReceipt(
            link=link,
            trace_ref=trace_id,
            finalized=status == "success",
            complete=complete,
            semantic_digest="sha256:" + hashlib.sha256(payload).hexdigest(),
            spans=typed_spans,
        )

    def _span(self, value: object) -> ReadbackSpan:
        if not isinstance(value, dict):
            raise ValueError("Neatlogs span is invalid")
        span = cast(dict[str, object], value)
        data = span.get("data")
        if not isinstance(data, dict):
            raise ValueError("Neatlogs span data is invalid")
        fields = cast(dict[str, object], data)
        return ReadbackSpan(
            span_id=self._text(span, "span_id"),
            parent_span_id=self._optional_text(span, "parent_span_id"),
            node_name=self._text(span, "node_name"),
            node_type=self._text(span, "node_type"),
            input_text=self._optional_text(fields, "input_value"),
            output_text=self._optional_text(fields, "output_value"),
        )

    def _text(self, values: dict[str, object], key: str) -> str:
        value = values.get(key)
        if not isinstance(value, str):
            raise ValueError("Neatlogs text field is invalid")
        return value

    def _persisted_link(self, spans: object) -> RunLink:
        if not isinstance(spans, list) or not spans:
            raise ValueError("Neatlogs persisted RunLink is missing")
        first = spans[0]
        if not isinstance(first, dict):
            raise ValueError("Neatlogs persisted RunLink is malformed")
        metadata = cast(dict[str, object], first).get("span_metadata")
        if not isinstance(metadata, dict):
            raise ValueError("Neatlogs persisted RunLink is missing")
        fields = cast(dict[str, object], metadata)
        return RunLink.model_validate(
            {
                "operation_id": self._text(fields, "operation_id"),
                "attempt_id": self._text(fields, "attempt_id"),
                "run_id": self._text(fields, "run_id"),
                "task_id": self._text(fields, "task_id"),
            }
        )

    def _optional_text(self, values: dict[str, object], key: str) -> str | None:
        value = values.get(key)
        if value is not None and not isinstance(value, str):
            raise ValueError("Neatlogs text field is invalid")
        return value
