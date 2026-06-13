"""Unit tests for parse_excel_file() in routes/import_stops.py.

Tests cover:
  - CSV parsing (basic, single-column, whitespace stripping)
  - XLSX parsing (via openpyxl + calamine engine)
  - Unsupported extension → HTTP 400
  - Row limit guard (>10_000 rows → HTTP 400)
  - Column limit guard (>100 columns → HTTP 400)
  - Zip-bomb: too many ZIP entries → HTTP 400
  - Zip-bomb: high compression ratio → HTTP 400
  - Corrupt ZIP magic bytes → HTTP 400
  - Empty CSV/XLSX → valid empty DataFrame (no error)
  - Magic-byte detection overrides file extension
"""
from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path

import pytest

# Make sure the backend package root is on sys.path so `from routes...` works.
sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import HTTPException  # noqa: E402
from routes.import_stops import parse_excel_file  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_csv(rows: list[list[str]]) -> bytes:
    """Build a CSV byte string from a list of rows (first row = headers)."""
    buf = io.StringIO()
    for row in rows:
        buf.write(",".join(str(cell) for cell in row) + "\n")
    return buf.getvalue().encode()


def make_xlsx(rows: list[list]) -> bytes:
    """Build a minimal valid XLSX using openpyxl and return the raw bytes."""
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# CSV tests
# ---------------------------------------------------------------------------

class TestCSVParsing:
    def test_basic_csv_columns_and_rows(self):
        """Parsed DataFrame has expected columns and correct row count."""
        data = make_csv([
            ["address", "name", "notes"],
            ["1 Main St", "Alice", "Leave at door"],
            ["2 Oak Ave", "Bob", "Ring bell"],
        ])
        df = parse_excel_file(data, "deliveries.csv")
        assert list(df.columns) == ["address", "name", "notes"]
        assert len(df) == 2

    def test_single_column_csv(self):
        """A CSV with only one column (header + rows) parses without error."""
        data = make_csv([["address"], ["10 Queen St"], ["20 King Rd"]])
        df = parse_excel_file(data, "stops.csv")
        assert "address" in df.columns
        assert len(df) == 2

    def test_csv_column_whitespace_stripped(self):
        """Leading/trailing whitespace in column names is stripped."""
        content = b"  address  ,  notes  \n1 Test St, ok\n"
        df = parse_excel_file(content, "data.csv")
        assert "address" in df.columns
        assert "notes" in df.columns
        # No columns with surrounding spaces should exist
        for col in df.columns:
            assert col == col.strip()

    def test_csv_data_values_preserved(self):
        """Data cell values come through unchanged."""
        data = make_csv([["address", "qty"], ["42 Elm St", "3"]])
        df = parse_excel_file(data, "data.csv")
        assert df.iloc[0]["address"] == "42 Elm St"
        assert str(df.iloc[0]["qty"]) == "3"


# ---------------------------------------------------------------------------
# XLSX tests
# ---------------------------------------------------------------------------

class TestXLSXParsing:
    def test_basic_xlsx_columns_and_rows(self):
        """XLSX parsed via calamine returns correct columns and row count."""
        xlsx = make_xlsx([
            ["address", "name", "notes"],
            ["1 Main St", "Alice", "Leave at door"],
            ["2 Oak Ave", "Bob", "Ring bell"],
        ])
        df = parse_excel_file(xlsx, "deliveries.xlsx")
        assert list(df.columns) == ["address", "name", "notes"]
        assert len(df) == 2

    def test_xlsx_data_values_preserved(self):
        """Cell values from XLSX come through correctly."""
        xlsx = make_xlsx([["address", "qty"], ["99 Park Rd", 7]])
        df = parse_excel_file(xlsx, "data.xlsx")
        assert df.iloc[0]["address"] == "99 Park Rd"
        assert df.iloc[0]["qty"] == 7

    def test_xlsx_column_whitespace_stripped(self):
        """Column names with surrounding spaces are stripped for XLSX too."""
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["  address  ", "  notes  "])
        ws.append(["1 Test St", "ok"])
        buf = io.BytesIO()
        wb.save(buf)
        xlsx_bytes = buf.getvalue()

        df = parse_excel_file(xlsx_bytes, "data.xlsx")
        for col in df.columns:
            assert col == col.strip(), f"Column '{col}' has surrounding whitespace"

    def test_empty_xlsx_returns_empty_dataframe(self):
        """An XLSX with only a header row returns an empty DataFrame (no error)."""
        xlsx = make_xlsx([["address", "name"]])
        df = parse_excel_file(xlsx, "empty.xlsx")
        assert len(df) == 0
        assert "address" in df.columns


# ---------------------------------------------------------------------------
# Unsupported extension
# ---------------------------------------------------------------------------

class TestUnsupportedExtension:
    def test_pdf_extension_raises_400(self):
        """.pdf files with non-Excel/ZIP magic bytes raise HTTP 400."""
        with pytest.raises(HTTPException) as exc_info:
            parse_excel_file(b"%PDF-1.4 garbage", "report.pdf")
        assert exc_info.value.status_code == 400

    def test_txt_extension_raises_400(self):
        """.txt files with non-Excel/ZIP magic bytes raise HTTP 400."""
        with pytest.raises(HTTPException) as exc_info:
            parse_excel_file(b"just some text data", "data.txt")
        assert exc_info.value.status_code == 400

    def test_unknown_extension_raises_400(self):
        """.xyz files with non-Excel/ZIP magic bytes raise HTTP 400."""
        with pytest.raises(HTTPException) as exc_info:
            parse_excel_file(b"\x89PNG arbitrary", "data.xyz")
        assert exc_info.value.status_code == 400


# ---------------------------------------------------------------------------
# Row / column limits
# ---------------------------------------------------------------------------

class TestRowLimit:
    def test_csv_exceeding_row_limit_raises_400(self):
        """CSV with more than MAX_IMPORT_ROWS (10_000) rows raises HTTP 400."""
        header = "address\n"
        rows = "".join(f"Stop {i}\n" for i in range(10_001))
        data = (header + rows).encode()
        with pytest.raises(HTTPException) as exc_info:
            parse_excel_file(data, "big.csv")
        assert exc_info.value.status_code == 400
        assert "too many rows" in exc_info.value.detail.lower()

    def test_csv_at_exact_row_limit_is_accepted(self):
        """CSV with exactly MAX_IMPORT_ROWS rows must NOT raise."""
        header = "address\n"
        rows = "".join(f"Stop {i}\n" for i in range(10_000))
        data = (header + rows).encode()
        df = parse_excel_file(data, "exact.csv")
        assert len(df) == 10_000


class TestColumnLimit:
    def test_csv_exceeding_column_limit_raises_400(self):
        """CSV with more than MAX_IMPORT_COLS (100) columns raises HTTP 400."""
        cols = ",".join(f"col{i}" for i in range(101))
        vals = ",".join(str(i) for i in range(101))
        data = f"{cols}\n{vals}\n".encode()
        with pytest.raises(HTTPException) as exc_info:
            parse_excel_file(data, "wide.csv")
        assert exc_info.value.status_code == 400
        assert "too many columns" in exc_info.value.detail.lower()

    def test_csv_at_exact_column_limit_is_accepted(self):
        """CSV with exactly MAX_IMPORT_COLS columns must NOT raise."""
        cols = ",".join(f"col{i}" for i in range(100))
        vals = ",".join(str(i) for i in range(100))
        data = f"{cols}\n{vals}\n".encode()
        df = parse_excel_file(data, "wide_ok.csv")
        assert len(df.columns) == 100


# ---------------------------------------------------------------------------
# Zip-bomb defenses
# ---------------------------------------------------------------------------

class TestZipBombDefense:
    def test_too_many_zip_entries_raises_400(self):
        """XLSX (ZIP) with more than 500 entries raises HTTP 400."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
            for i in range(501):
                zf.writestr(f"entry_{i:04d}.xml", b"x")
        zip_bytes = buf.getvalue()
        # Confirm it carries the ZIP/XLSX magic bytes (PK\x03\x04)
        assert zip_bytes[:4] == b"PK\x03\x04"
        with pytest.raises(HTTPException) as exc_info:
            parse_excel_file(zip_bytes, "bomb.xlsx")
        assert exc_info.value.status_code == 400

    def test_high_compression_ratio_raises_400(self):
        """Entry with compression ratio > 100x triggers the ratio guard."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            # 10 MB of null bytes compresses to near-zero; ratio >> 100
            zf.writestr("xl/worksheets/sheet1.xml", b"\x00" * (10 * 1024 * 1024))
        zip_bytes = buf.getvalue()
        assert zip_bytes[:4] == b"PK\x03\x04"
        with pytest.raises(HTTPException) as exc_info:
            parse_excel_file(zip_bytes, "ratio_bomb.xlsx")
        assert exc_info.value.status_code == 400

    def test_corrupt_zip_magic_raises_400(self):
        """Bytes starting with PK magic but otherwise corrupt raise HTTP 400."""
        garbage = b"PK\x03\x04" + b"\xff" * 100
        with pytest.raises(HTTPException) as exc_info:
            parse_excel_file(garbage, "corrupt.xlsx")
        assert exc_info.value.status_code == 400

    def test_exactly_500_entries_is_accepted(self):
        """Exactly 500 ZIP entries is at the limit and must not raise.

        The limit check is `> 500`, so 500 is allowed.
        After the ZIP safety gate the bytes won't parse as a valid XLSX
        (no xl/workbook.xml etc.), so we only care that it doesn't raise
        the entry-count 400 — a different parsing error is fine.
        """
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
            for i in range(500):
                zf.writestr(f"entry_{i:04d}.xml", b"x")
        zip_bytes = buf.getvalue()
        # Should NOT raise HTTPException for entry count.
        # May raise for invalid XLSX content — that's acceptable here.
        try:
            parse_excel_file(zip_bytes, "edge.xlsx")
        except HTTPException as exc:
            assert "too many archive entries" not in exc.detail


# ---------------------------------------------------------------------------
# Empty file
# ---------------------------------------------------------------------------

class TestEmptyFile:
    def test_empty_csv_raises_400(self):
        """A completely empty CSV (0 bytes) raises HTTP 400.

        pandas.read_csv() raises 'No columns to parse from file' on empty
        input, which parse_excel_file wraps as HTTP 400. This is the actual
        runtime behavior — there is nothing meaningful to import."""
        with pytest.raises(HTTPException) as exc_info:
            parse_excel_file(b"", "data.csv")
        assert exc_info.value.status_code == 400

    def test_header_only_csv_returns_empty_dataframe(self):
        """A CSV with only a header row returns an empty DataFrame."""
        data = b"address,name,notes\n"
        df = parse_excel_file(data, "data.csv")
        assert len(df) == 0
        assert "address" in df.columns


# ---------------------------------------------------------------------------
# Magic-byte detection overrides extension
# ---------------------------------------------------------------------------

class TestMagicByteDetection:
    def test_xlsx_bytes_with_xls_extension_parses_correctly(self):
        """Valid XLSX content with a .xls extension must parse via calamine.

        The magic bytes (PK\x03\x04) tell parse_excel_file it's a ZIP/OOXML
        container regardless of what the filename says."""
        xlsx = make_xlsx([
            ["address", "qty"],
            ["1 Magic St", 5],
        ])
        # Sanity-check: it really does carry ZIP magic
        assert xlsx[:4] == b"PK\x03\x04"
        df = parse_excel_file(xlsx, "data.xls")  # Wrong extension
        assert list(df.columns) == ["address", "qty"]
        assert len(df) == 1
        assert df.iloc[0]["address"] == "1 Magic St"

    def test_xlsx_bytes_with_uppercase_xls_extension(self):
        """Case-variant extensions (.XLS, .XLSX) are handled via magic bytes."""
        xlsx = make_xlsx([["col1"], ["value1"]])
        df = parse_excel_file(xlsx, "FILE.XLSX")
        assert "col1" in df.columns
        assert len(df) == 1

    def test_xlsx_bytes_filename_csv_extension_are_parsed_as_xlsx(self):
        """XLSX bytes with a .csv extension are detected via magic bytes and
        routed to the Excel parser (not the CSV reader)."""
        xlsx = make_xlsx([["address"], ["1 Test St"]])
        df = parse_excel_file(xlsx, "sneaky.csv")
        assert "address" in df.columns
        assert len(df) == 1
