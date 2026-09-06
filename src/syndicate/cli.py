"""Operator CLI entry; request handling lives in the controller layer."""

import sys

from syndicate.controllers.preflight import (
    contained,
    dispatch,
    execute,
    execute_preflight,
    failure,
    main,
    operator_preflight,
    read_request,
    request_path,
    write_request,
)
from syndicate.models.commands import parse_command
from syndicate.services.schema_export import export_schemas

__all__ = [
    "contained",
    "dispatch",
    "execute",
    "execute_preflight",
    "export_schemas",
    "failure",
    "main",
    "operator_preflight",
    "parse_command",
    "read_request",
    "request_path",
    "write_request",
]


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
