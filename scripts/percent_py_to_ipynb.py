#!/usr/bin/env python3
"""Convert a percent-cell Python file to a minimal Jupyter notebook."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def parse_percent_cells(text: str) -> list[dict]:
    cells: list[dict] = []
    current_type = "code"
    current_lines: list[str] = []
    saw_marker = False

    def flush() -> None:
        nonlocal current_lines
        if not saw_marker and not current_lines:
            return
        source = current_lines
        if current_type == "markdown":
            source = [strip_markdown_comment(line) for line in current_lines]
        cells.append(
            {
                "cell_type": current_type,
                "metadata": {},
                "source": source,
                **({"outputs": [], "execution_count": None} if current_type == "code" else {}),
            }
        )
        current_lines = []

    for raw_line in text.splitlines(keepends=True):
        line = raw_line.rstrip("\n")
        if line.startswith("# %%"):
            flush()
            saw_marker = True
            current_type = "markdown" if "[markdown]" in line else "code"
            current_lines = []
            continue
        current_lines.append(raw_line)
    flush()
    return cells


def strip_markdown_comment(line: str) -> str:
    if line.startswith("# "):
        return line[2:]
    if line == "#\n" or line == "#":
        return "\n" if line.endswith("\n") else ""
    return line


def build_notebook(cells: list[dict]) -> dict:
    return {
        "nbformat": 4,
        "nbformat_minor": 0,
        "metadata": {
            "colab": {"provenance": []},
            "kernelspec": {"name": "python3", "display_name": "Python 3"},
            "language_info": {"name": "python"},
        },
        "cells": cells,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Percent-cell .py input path.")
    parser.add_argument("output", type=Path, help=".ipynb output path.")
    args = parser.parse_args()

    cells = parse_percent_cells(args.input.read_text(encoding="utf-8"))
    notebook = build_notebook(cells)
    args.output.write_text(json.dumps(notebook, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {args.output} ({len(cells)} cells)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
