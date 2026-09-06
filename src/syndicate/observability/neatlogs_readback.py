"""Read finalized Neatlogs traces through its documented MCP tools only."""

import hashlib
import json
from typing import Literal, NewType
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from .neatlogs_capture import CaptureReceipt, CaptureState, RunLink

NeatlogsSpanRef = NewType("NeatlogsSpanRef", str)


class ReadbackSpan(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)
    span_id: str = Field(min_length=1, max_length=200)
    parent_span_id: str | None = Field(default=None, min_length=1, max_length=200)
    node_name: str
    node_type: str
    input_text: str | None
    output_text: str | None


class ExpectedTrace(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)
    receipt: CaptureReceipt

    @field_validator("receipt")
    @classmethod
    def sealed_receipt(cls, value: CaptureReceipt) -> CaptureReceipt:
        if (
            value.state is not CaptureState.FLUSHED_UNVERIFIED
            or value.binding_digest is None
            or value.trace_ref is None
            or len(value.expected_span_refs) != len(set(value.expected_span_refs))
        ):
            raise ValueError("Expected span IDs must be unique")
        return value

    @property
    def link(self) -> RunLink:
        return self.receipt.link

    @property
    def trace_ref(self) -> str:
        assert self.receipt.trace_ref is not None
        return self.receipt.trace_ref

    @property
    def expected_span_refs(self) -> tuple[str, ...]:
        return self.receipt.expected_span_refs


class NeatlogsReadbackReceipt(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)
    link: RunLink
    trace_ref: str = Field(min_length=1, max_length=200)
    finalized: bool
    complete: bool
    semantic_digest: str
    binding_digest: str
    spans: tuple[ReadbackSpan, ...]


class _McpContent(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    type: Literal["text"]
    text: str


class _McpResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    content: tuple[_McpContent, ...]


class _McpEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    jsonrpc: Literal["2.0"]
    id: int
    result: _McpResult


class _McpV2Span(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)
    span_id: str = Field(min_length=1, max_length=200)
    parent_span_id: str | None = None
    name: str
    type: str
    input_value: str | None = None
    output_value: str | None = None


class _TraceContext(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)
    trace_id: str = Field(min_length=1, max_length=200)
    status: Literal["success", "error", "processing"]
    finalization_status: Literal["finalized", "pending"] | None = None
    verification_ready: bool = False
    span_payload_complete: bool = False
    span_tree_complete: bool = False
    spans: tuple[_McpV2Span, ...] = Field(default=(), max_length=1000)
    span_count: int = Field(default=0, ge=0)
    returned_span_count: int = Field(default=0, ge=0)
    root_span_count: int = Field(default=0, ge=0)
    truncated: bool = False


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args: object, **kwargs: object) -> None:
        return None


def _reaches_root(
    start: str, parents: dict[str, str | None], known_ids: set[str]
) -> bool:
    seen: set[str] = set()
    current: str | None = start
    while current is not None:
        if current in seen:
            return False
        seen.add(current)
        parent = parents.get(current)
        if parent is not None and parent not in known_ids:
            return False
        current = parent
    return True


def _valid_span_tree(context: _TraceContext, spans: tuple[ReadbackSpan, ...]) -> bool:
    ids = {span.span_id for span in spans}
    roots = [span for span in context.spans if span.parent_span_id is None]
    if len(roots) != 1 or len(ids) != len(spans):
        return False
    parents = {span.span_id: span.parent_span_id for span in context.spans}
    return all(_reaches_root(span.span_id, parents, ids) for span in spans)


class NeatlogsReadbackReader:
    """Transient MCP reader; it never stores trajectory payloads."""

    def __init__(
        self, api_key: SecretStr, endpoint: str = "https://ingest.neatlogs.com"
    ) -> None:
        self._api_key = api_key
        self._endpoint = endpoint.rstrip("/") + "/mcp"
        self._session_id: str | None = None
        self._opener = build_opener(_NoRedirect())

    def fetch(self, expected: ExpectedTrace) -> NeatlogsReadbackReceipt:
        payload = self._tool(
            "get_trace_context", json.dumps({"trace_id": expected.trace_ref})
        )
        try:
            context = _TraceContext.model_validate_json(payload)
        except ValueError:
            return self._receipt(expected, False, False, ())
        spans = tuple(
            ReadbackSpan(
                span_id=span.span_id,
                parent_span_id=span.parent_span_id,
                node_name=span.name,
                node_type=span.type,
                input_text=span.input_value,
                output_text=span.output_value,
            )
            for span in context.spans
        )
        finalized = self._finalized(context)
        return self._receipt(
            expected,
            finalized,
            self._complete(expected, context, spans, finalized),
            spans,
            payload,
        )

    def close(self) -> None:
        if self._session_id is None:
            return
        try:
            self._opener.open(
                Request(self._endpoint, headers=self._headers(), method="DELETE"),
                timeout=15,
            ).close()
        except (HTTPError, URLError, OSError):
            pass
        self._session_id = None

    def _tool(self, name: str, arguments: str) -> str:
        if self._session_id is None:
            self._initialize()
        body = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": name, "arguments": json.loads(arguments)},
            }
        ).encode()
        return self._response(
            Request(self._endpoint, data=body, headers=self._headers())
        )

    def _initialize(self) -> None:
        body = (
            b'{"jsonrpc":"2.0","id":1,"method":"initialize","params":'
            b'{"protocolVersion":"2024-11-05","capabilities":{},'
            b'"clientInfo":{"name":"syndicate","version":"0.1.0"}}}'
        )
        try:
            with self._opener.open(
                Request(self._endpoint, data=body, headers=self._headers()), timeout=15
            ) as response:
                response.read(1_000_001)
                self._session_id = response.headers.get("mcp-session-id")
        except (HTTPError, URLError, OSError) as error:
            raise ValueError("Neatlogs MCP unavailable") from error
        if self._session_id is None:
            raise ValueError("Neatlogs MCP session is missing")

    def _response(self, request: Request) -> str:
        try:
            with self._opener.open(request, timeout=15) as response:
                body = response.read(1_000_001)
                if len(body) > 1_000_000:
                    raise ValueError("Neatlogs MCP response exceeds byte limit")
                envelope = _McpEnvelope.model_validate_json(
                    self._json_response(body.decode())
                )
        except (HTTPError, URLError, OSError, ValueError) as error:
            raise ValueError("Neatlogs MCP readback unavailable") from error
        if len(envelope.result.content) != 1:
            raise ValueError("Neatlogs MCP response is incomplete")
        return envelope.result.content[0].text

    def _json_response(self, body: str) -> str:
        for line in body.splitlines():
            if line.startswith("data: "):
                return line.removeprefix("data: ")
        return body

    def _headers(self) -> dict[str, str]:
        headers = {
            "Authorization": "Bearer " + self._api_key.get_secret_value(),
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self._session_id is not None:
            headers["mcp-session-id"] = self._session_id
        return headers

    def _complete(
        self,
        expected: ExpectedTrace,
        context: _TraceContext,
        spans: tuple[ReadbackSpan, ...],
        finalized: bool,
    ) -> bool:
        return all(
            (
                _valid_span_tree(context, spans),
                context.trace_id == expected.trace_ref,
                finalized,
                not context.truncated,
                len(spans) == context.span_count,
                context.returned_span_count == context.span_count,
                context.root_span_count == 1,
                set(expected.expected_span_refs) == {span.span_id for span in spans},
            )
        )

    def _finalized(self, context: _TraceContext) -> bool:
        return (
            context.status == "success"
            and context.finalization_status == "finalized"
            and context.verification_ready
            and context.span_payload_complete
            and context.span_tree_complete
        )

    def _receipt(
        self,
        expected: ExpectedTrace,
        finalized: bool,
        complete: bool,
        spans: tuple[ReadbackSpan, ...],
        payload: str | None = None,
    ) -> NeatlogsReadbackReceipt:
        digest = payload if payload is not None else expected.trace_ref
        return NeatlogsReadbackReceipt(
            link=expected.link,
            trace_ref=expected.trace_ref,
            finalized=finalized,
            complete=complete,
            semantic_digest="sha256:" + hashlib.sha256(digest.encode()).hexdigest(),
            binding_digest=expected.receipt.binding_digest or "",
            spans=spans,
        )
