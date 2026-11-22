"""Generate a fixed-layout PDF from the bundled synthetic C40 sample."""

import argparse
import csv
from datetime import datetime
from io import BytesIO
from pathlib import Path

from inspection_trace.ingestion.lab_pdf import FOOTER, HEADERS, TITLE
from inspection_trace.ingestion.normalization import parse_clock, parse_roc_day


def render_lab_pdf(records: list[tuple[str, ...]]) -> bytes:
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    def literal(value: str) -> str:
        return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    writer = PdfWriter()
    for index in range(0, len(records), 40):
        page = writer.add_blank_page(width=842, height=595)
        font = DictionaryObject({
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Courier"),
        })
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): font}),
        })
        lines = [
            TITLE, " | ".join(HEADERS),
            *(" | ".join(values) for values in records[index:index + 40]), FOOTER,
        ]
        stream = DecodedStreamObject()
        commands = ["BT /F1 9 Tf 36 555 Td 12 TL"]
        commands.extend(f"({literal(line)}) Tj T*" for line in lines)
        stream.set_data(("\n".join([*commands, "ET"])).encode("ascii"))
        page[NameObject("/Contents")] = stream
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def sample_rows() -> list[tuple[str, ...]]:
    with Path("samples/files/MES_OLD_C40_1151103.csv").open(encoding="cp950", newline="") as source:
        return [
            (
                row["LOTNO"], row["PNL"], row["PT"], row["EQP"], row["ITEM"],
                row["VAL"], row["UOM"],
                datetime.combine(parse_roc_day(row["DT"]), parse_clock(row["TM"])).isoformat(),
                row["OP"],
            )
            for row in csv.DictReader(source)
        ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    options = parser.parse_args()
    data = render_lab_pdf(sample_rows())
    options.out.parent.mkdir(parents=True, exist_ok=True)
    with options.out.open("xb") as destination:
        destination.write(data)


if __name__ == "__main__":
    main()
