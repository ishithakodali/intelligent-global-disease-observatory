from __future__ import annotations

import csv
from pathlib import Path


def _extract_name(row: dict[str, str]) -> str:
    candidate_keys = [
        "name",
        "disease",
        "disease_name",
        "disorder",
        "disorder_name",
        "MalaCard Name",
        "MalaCards Name",
        "Disease Name",
    ]

    for key in candidate_keys:
        for row_key, value in row.items():
            if row_key.strip().lower() == key.strip().lower() and value:
                return str(value).strip()

    for value in row.values():
        if value and str(value).strip():
            return str(value).strip()

    return ""


def load_malacards_names_from_file(file_path: str) -> list[str]:
    path = Path(file_path)
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"File not found: {file_path}")

    delimiter = "\t" if path.suffix.lower() in {".tsv", ".txt"} else ","

    names: list[str] = []
    with path.open("r", encoding="utf-8", errors="ignore", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=delimiter)
        if not reader.fieldnames:
            raise ValueError("Input file has no headers; expected CSV/TSV with column names")

        for row in reader:
            name = _extract_name(row)
            if name:
                names.append(name)

    return names
