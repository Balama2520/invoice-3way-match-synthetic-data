"""Command-line entry point for generating and validating challenge datasets."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.generator import generate_dataset
from src.intake import extract_invoice_source


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a reproducible invoice-matching dataset.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    generate = subparsers.add_parser("generate", help="generate data, decisions, exports and metrics")
    generate.add_argument("--output", type=Path, default=Path("data/generated"))
    generate.add_argument("--scale", type=float, default=1.0)
    generate.add_argument("--seed", type=int, default=None)
    extract = subparsers.add_parser(
        "extract",
        help="extract fields from a supported invoice text file, directory, or ZIP",
    )
    extract.add_argument("--input", type=Path, required=True)
    extract.add_argument("--output", type=Path)
    args = parser.parse_args()

    if args.command == "generate":
        summary = generate_dataset(args.output, scale=args.scale, seed=args.seed)
        print(json.dumps(summary, indent=2, sort_keys=True))
    elif args.command == "extract":
        extracted = extract_invoice_source(args.input)
        serialized = json.dumps(extracted, indent=2, sort_keys=True) + "\n"
        if args.output is None:
            print(serialized, end="")
        else:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(serialized, encoding="utf-8")
            print(f"Extracted {len(extracted)} invoice(s) to {args.output}.")


if __name__ == "__main__":
    main()

