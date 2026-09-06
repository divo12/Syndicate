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
)

__all__ = [
    "contained",
    "dispatch",
    "execute",
    "execute_preflight",
    "failure",
    "main",
    "operator_preflight",
    "read_request",
]


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
