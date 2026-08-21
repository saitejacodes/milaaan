"""Strict CSV readers. The engine never reads the ground-truth manifest."""

from __future__ import annotations

import csv
from pathlib import Path


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def read_source_files(data_dir: Path) -> tuple[list[dict[str, str]], list[dict[str, str]], list[dict[str, str]]]:
    return (
        read_csv(data_dir / "orders.csv"),
        read_csv(data_dir / "gateway_recon.csv"),
        read_csv(data_dir / "bank.csv"),
    )
