"""Command-line entry point. Sub-commands are added milestone by milestone."""

import argparse
from collections.abc import Sequence

from oabench import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="oabench",
        description="Dataset-search benchmark on an open-data catalog (work in progress).",
    )
    parser.add_argument("--version", action="version", version=f"oabench {__version__}")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    build_parser().parse_args(argv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
