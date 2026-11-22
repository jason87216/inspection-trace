"""Generate a synthetic CSV with a report title and unfamiliar vendor headers."""

import argparse
import csv
from datetime import datetime
from pathlib import Path

from generator.lab_report import sample_rows

HEADERS = ("Batch", "Piece", "Point", "Device", "Metric", "Reading", "UOM", "Observed", "Operator")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    options = parser.parse_args()
    options.out.parent.mkdir(parents=True, exist_ok=True)
    with options.out.open("x", encoding="utf-8", newline="") as destination:
        writer = csv.writer(destination)
        writer.writerow(["Vendor statement v1"])
        writer.writerow(HEADERS)
        for row in sample_rows():
            measured = datetime.fromisoformat(row[7]).strftime("%d/%m/%Y %H:%M:%S")
            writer.writerow((*row[:7], measured, row[8]))


if __name__ == "__main__":
    main()
