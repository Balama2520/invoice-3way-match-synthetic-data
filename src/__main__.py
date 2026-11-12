"""Command-line entry point for generating and validating challenge datasets."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.generator import generate_dataset


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a reproducible invoice-matching dataset.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    generate = subparsers.add_parser("generate", help="generate data, decisions, exports and metrics")
    generate.add_argument("--output", type=Path, default=Path("data/generated"))
    generate.add_argument("--scale", type=float, default=1.0)
    generate.add_argument("--seed", type=int, default=None)
    args = parser.parse_args()

    if args.command == "generate":
        summary = generate_dataset(args.output, scale=args.scale, seed=args.seed)
        print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

