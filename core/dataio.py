"""Robust, offline file ingestion.

The original application used ``/tmp`` and a single ``read_csv`` call.  This
module makes ingestion predictable across Windows/macOS/Linux and tolerant of
the encodings and delimiters commonly produced by sensor loggers.
"""

from __future__ import annotations

import csv
import os
import tempfile
from pathlib import Path
from typing import Iterable, Optional

import pandas as pd
from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

from core.errors import ApiError

ALLOWED_EXTENSIONS = {"csv", "xlsx", "xls", "txt"}
UPLOAD_ROOT = Path(tempfile.gettempdir()) / "aqat_uploads"
UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)


def extension_of(filename: str) -> str:
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


def ensure_allowed(filename: str) -> str:
    ext = extension_of(filename or "")
    if ext not in ALLOWED_EXTENSIONS:
        raise ApiError(
            f"Unsupported file type '{ext or 'unknown'}'.",
            415,
            code="unsupported_file_type",
            hint="Upload CSV, XLSX, XLS or TXT files. Excel workbooks are read from the first non-empty sheet.",
        )
    return ext


def _detect_separator(sample: str) -> str:
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        return dialect.delimiter
    except csv.Error:
        # Count candidates in the header line as a fallback.
        header = sample.splitlines()[0] if sample else ""
        counts = {sep: header.count(sep) for sep in [",", ";", "\t", "|"]}
        best = max(counts, key=counts.get)
        return best if counts[best] else ","


def read_csv_robust(path: str | Path) -> pd.DataFrame:
    path = Path(path)
    errors = []
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
            sample = fh.read(65536)
        separator = _detect_separator(sample)
    except OSError as exc:
        raise ApiError(f"Could not open the uploaded file: {exc}", 400,
                       code="file_read_error",
                       hint="Check that the file is not locked by another program and try again.") from exc

    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            df = pd.read_csv(path, sep=separator, encoding=encoding,
                             low_memory=False, on_bad_lines="warn")
            if df.shape[1] == 1 and separator != ",":
                # Sniffer may have guessed wrong; retry with comma.
                df = pd.read_csv(path, sep=",", encoding=encoding,
                                 low_memory=False, on_bad_lines="warn")
            return df
        except UnicodeDecodeError as exc:
            errors.append(str(exc))
        except pd.errors.EmptyDataError as exc:
            raise ApiError("The file is empty.", 400, code="empty_file",
                           hint="Choose a file that contains at least a header row and one data row.") from exc
        except Exception as exc:  # noqa: BLE001 - report a friendly message
            errors.append(str(exc))
    raise ApiError(
        "The CSV could not be parsed.",
        400,
        code="csv_parse_error",
        hint="Check the delimiter, quoting and character encoding. Export the file again as UTF-8 CSV if possible.",
        details="; ".join(errors[-2:]),
    )


def read_excel_robust(path: str | Path) -> pd.DataFrame:
    path = Path(path)
    try:
        workbook = pd.ExcelFile(path)
    except Exception as exc:  # noqa: BLE001
        hint = "For .xls files install xlrd (python -m pip install xlrd), or save the workbook as .xlsx."
        raise ApiError(
            "The Excel workbook could not be opened.",
            400,
            code="excel_read_error",
            hint=hint,
            details=str(exc),
        ) from exc

    try:
        for sheet in workbook.sheet_names:
            frame = workbook.parse(sheet)
            if not frame.empty and frame.shape[1] > 0:
                frame.attrs["sheet_name"] = sheet
                if len(workbook.sheet_names) > 1:
                    frame.attrs["sheet_count"] = len(workbook.sheet_names)
                return frame
        raise ApiError("The workbook contains no readable data.", 400,
                       code="empty_workbook",
                       hint="Make sure at least one sheet contains a header and data rows.")
    finally:
        workbook.close()


def read_table(path: str | Path, filename: Optional[str] = None) -> pd.DataFrame:
    path = Path(path)
    ext = extension_of(filename or path.name)
    if ext in ("xlsx", "xls"):
        return read_excel_robust(path)
    return read_csv_robust(path)


def save_upload(file: FileStorage, prefix: str) -> tuple[Path, str]:
    """Persist an uploaded file to a private temporary directory."""
    if file is None or not file.filename:
        raise ApiError("A file was missing its filename.", 400, code="missing_filename")
    ext = ensure_allowed(file.filename)
    safe = secure_filename(file.filename) or f"upload.{ext}"
    path = UPLOAD_ROOT / f"{prefix}_{safe}"
    try:
        file.save(path)
    except Exception as exc:  # noqa: BLE001
        raise ApiError("The file could not be saved to disk.", 500,
                       code="file_save_error",
                       hint="Check free disk space and folder permissions, then retry.",
                       details=str(exc)) from exc
    return path, safe


def remove_quietly(path: str | Path) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def ingest_uploads(files: Iterable[FileStorage], prefix: str) -> tuple[list[pd.DataFrame], list[str], list[str]]:
    """Read every upload, always cleaning up temporary files.

    Returns ``(frames, file_names, warnings)``.
    """
    frames: list[pd.DataFrame] = []
    names: list[str] = []
    warnings: list[str] = []
    for file in files:
        if not file or not file.filename:
            continue
        path, safe = save_upload(file, prefix)
        try:
            frame = read_table(path, safe)
            if frame.empty:
                warnings.append(f"{safe}: no data rows found")
                continue
            frames.append(frame)
            names.append(safe)
            sheet = frame.attrs.get("sheet_name")
            if sheet and frame.attrs.get("sheet_count", 1) > 1:
                warnings.append(f"{safe}: read sheet '{sheet}' (first non-empty sheet)")
        except ApiError as exc:
            warnings.append(f"{safe}: {exc.message}")
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"{safe}: {exc}")
        finally:
            remove_quietly(path)
    return frames, names, warnings
