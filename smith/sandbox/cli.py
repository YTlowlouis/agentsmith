import argparse
import os
import json
from pydantic import ValidationError
import sys

from smith.models.sandbox_config import SandboxConfig


def main() -> int:
    parser = argparse.ArgumentParser(description="Sandbox cli")

    group = parser.add_mutually_exclusive_group()

    parser.add_argument(
        "config_file",
        nargs="?",
        default="sandbox_template.json",
        metavar="config-file",
    )

    group.add_argument("--mcp-stdio")
    group.add_argument("--mcp-server")

    args = parser.parse_args()

    try:
        with open(args.config_file, "r", encoding="utf-8") as f:
            config = SandboxConfig.model_validate_json(f.read())
    except FileNotFoundError:
        print(f"File: {args.config_file} does not exist", file=sys.stderr)
        return 1
    except ValidationError:
        print("Invalid Config file", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
