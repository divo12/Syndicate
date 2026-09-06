"""Fixed controller schema export for Trigger build and test setup."""

import argparse
import sys
from pathlib import Path

from syndicate.cli_envelope import export_schemas


def main(arguments: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    values = parser.parse_args(arguments)
    root = Path(values.root)
    try:
        receipt = export_schemas(root)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2
    print(receipt.model_dump_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
