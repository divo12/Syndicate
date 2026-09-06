"""Read finalized Neatlogs traces through its documented MCP tools only."""

import hashlib
import json
from typing import Literal, NewType
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, SecretStr

from .neatlogs_capture import RunLink

NeatlogsTraceRef = NewType("NeatlogsTraceRef", str)
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
    link: RunLink
    trace_ref: str = Field(min_length=1, max_length=200)
    expected_span_refs: tuple[str, ...] = Field(min_length=1)


class NeatlogsReadbackReceipt(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)
    link: RunLink
    trace_ref: str = Field(min_length=1, max_length=200)
    finalized: bool
    complete: bool
    semantic_digest: str
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


class _TraceSearchItem(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True, validate_by_name=True)
    trace_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
        validation_alias=AliasChoices("trace_id", "id"),
    )


class _TraceSearch(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True, validate_by_name=True)
    traces: tuple[_TraceSearchItem, ...] = Field(
        default=(),
        validation_alias=AliasChoices("traces", "results"),
    )


class _TraceNode(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)
    span_id: str = Field(min_length=1, max_length=200)
    span_type: str
    name: str
    status: str
    input: object | None = None
    output: object | None = None
    metadata: RunLink | None = None
    children: tuple["_TraceNode", ...] = ()


class _TraceContext(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)
    trace_id: str = Field(min_length=1, max_length=200)
    status: Literal["success", "error"]
    root_span: _TraceNode
    span_count: int = Field(ge=1)
    truncated: bool = False


class NeatlogsReadbackReader:
    """Transient MCP reader; it never stores trajectory payloads."""

    def __init__(
        self, api_key: SecretStr, endpoint: str = "https://ingest.neatlogs.com"
    ) -> None:
        self._api_key = api_key
        self._endpoint = endpoint.rstrip("/") + "/mcp"
        self._session_id: str | None = None

    def read(
        self, link: RunLink, trace_ref: NeatlogsTraceRef
    ) -> NeatlogsReadbackReceipt:
        raise ValueError("Expected span coverage is required")

    def fetch(self, expected: ExpectedTrace) -> NeatlogsReadbackReceipt:
        search = _TraceSearch.model_validate_json(
            self._tool(
                "search_traces", json.dumps({"query": str(expected.link.run_id)})
            )
        )
        if expected.trace_ref not in tuple(
            item.trace_id for item in search.traces if item.trace_id is not None
        ):
            return self._receipt(expected, False, False, ())
        payload = self._tool(
            "get_trace_context", json.dumps({"trace_id": expected.trace_ref})
        )
        try:
            context = _TraceContext.model_validate_json(payload)
        except ValueError:
            return self._receipt(expected, False, False, ())
        spans = self._spans(context.root_span)
        return self._receipt(
            expected,
            context.status == "success",
            self._complete(expected, context, spans),
            spans,
            payload,
        )

    def close(self) -> None:
        if self._session_id is None:
            return
        try:
            urlopen(
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
            with urlopen(
                Request(self._endpoint, data=body, headers=self._headers()), timeout=15
            ) as response:
                response.read()
                self._session_id = response.headers.get("mcp-session-id")
        except (HTTPError, URLError, OSError) as error:
            raise ValueError("Neatlogs MCP unavailable") from error
        if self._session_id is None:
            raise ValueError("Neatlogs MCP session is missing")

    def _response(self, request: Request) -> str:
        try:
            with urlopen(request, timeout=15) as response:
                envelope = _McpEnvelope.model_validate_json(
                    self._json_response(response.read().decode())
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
    ) -> bool:
        return (
            context.trace_id == expected.trace_ref
            and context.status == "success"
            and not context.truncated
            and len(spans) == context.span_count
            and expected.link == context.root_span.metadata
            and set(expected.expected_span_refs) == {span.span_id for span in spans}
        )

    def _spans(
        self, node: _TraceNode, parent: str | None = None
    ) -> tuple[ReadbackSpan, ...]:
        current = ReadbackSpan(
            span_id=node.span_id,
            parent_span_id=parent,
            node_name=node.name,
            node_type=node.span_type,
            input_text=self._text(node.input),
            output_text=self._text(node.output),
        )
        return (current,) + tuple(
            span for child in node.children for span in self._spans(child, node.span_id)
        )

    def _text(self, value: object | None) -> str | None:
        if value is None or isinstance(value, str):
            return value
        return json.dumps(value, sort_keys=True)

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
            spans=spans,
        )
