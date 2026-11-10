"""Loading and validating daily OHLCV data.

The files are the ones produced by scripts/fetch_data.py and read by the C++
engine: a header line followed by `date,open,high,low,close,volume` rows.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import numpy as np
import pandas as pd

from .config import default_data_dir
from .exceptions import DataError, DataNotFoundError

PRICE_COLUMNS = ("open", "high", "low", "close")
REQUIRED_COLUMNS = (*PRICE_COLUMNS, "volume")

# Symbols come from URLs and the command line and are used to build file paths,
# so they are restricted to a safe alphabet (no separators, no leading dot).
_SYMBOL_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,15}$")


def normalise_symbol(symbol: str) -> str:
    """Return the canonical (upper case) form of a symbol or raise DataError."""
    if not isinstance(symbol, str) or not _SYMBOL_PATTERN.fullmatch(symbol.strip()):
        raise DataError(f"invalid symbol: {symbol!r}")
    return symbol.strip().upper()


def validate_bars(bars: pd.DataFrame) -> list[str]:
    """Return every problem found in a bars frame (an empty list means valid).

    Hard problems (structure, non-finite or non-positive prices, high below low)
    make read_bars() raise. The open/close-outside-range check is reported here
    but tolerated by read_bars(), because adjusted vendor data can violate it by
    rounding.
    """
    issues: list[str] = []
    missing = [c for c in REQUIRED_COLUMNS if c not in bars.columns]
    if missing:
        return [f"missing columns: {', '.join(missing)}"]
    if bars.empty:
        return ["no rows"]
    if not isinstance(bars.index, pd.DatetimeIndex):
        issues.append("index is not a DatetimeIndex")
    elif not bars.index.is_monotonic_increasing:
        issues.append("dates are not sorted ascending")
    elif bars.index.has_duplicates:
        issues.append("duplicate dates")

    values = bars[list(REQUIRED_COLUMNS)].to_numpy(dtype=float)
    if not np.isfinite(values).all():
        issues.append("non-finite values")
        return issues
    if (bars[list(PRICE_COLUMNS)].to_numpy(dtype=float) <= 0).any():
        issues.append("non-positive prices")
    if (bars["volume"].to_numpy(dtype=float) < 0).any():
        issues.append("negative volume")
    if (bars["high"] < bars["low"]).any():
        issues.append("high below low")
    tolerance = 1e-6
    envelope = (
        (bars["open"] > bars["high"] * (1 + tolerance))
        | (bars["open"] < bars["low"] * (1 - tolerance))
        | (bars["close"] > bars["high"] * (1 + tolerance))
        | (bars["close"] < bars["low"] * (1 - tolerance))
    )
    if envelope.any():
        issues.append("open or close outside the high/low range")
    return issues


_FATAL_PREFIXES = (
    "missing columns",
    "no rows",
    "index is not",
    "dates are not sorted",
    "duplicate dates",
    "non-finite",
    "non-positive",
    "negative volume",
    "high below low",
)


def read_bars(path: str | Path) -> pd.DataFrame:
    """Read one symbol's CSV into a date-indexed frame of float columns."""
    path = Path(path)
    try:
        raw = pd.read_csv(path)
    except FileNotFoundError as exc:
        raise DataNotFoundError(f"data file not found: {path.name}") from exc
    except (pd.errors.ParserError, pd.errors.EmptyDataError, UnicodeDecodeError) as exc:
        raise DataError(f"cannot parse {path.name}: {exc}") from exc

    raw.columns = [str(c).strip().lower() for c in raw.columns]
    if "date" not in raw.columns:
        raise DataError(f"{path.name}: missing columns: date")
    try:
        dates = pd.to_datetime(raw["date"], format="%Y-%m-%d", errors="raise")
    except (ValueError, TypeError) as exc:
        raise DataError(f"{path.name}: unparseable dates: {exc}") from exc

    try:
        numeric = raw[list(REQUIRED_COLUMNS)].apply(pd.to_numeric, errors="raise")
    except KeyError as exc:
        raise DataError(f"{path.name}: missing columns: {exc.args[0]}") from exc
    except (ValueError, TypeError) as exc:
        raise DataError(f"{path.name}: non-numeric values: {exc}") from exc

    bars = numeric.astype(float)
    bars.index = pd.DatetimeIndex(dates, name="date")
    bars = bars.sort_index()

    fatal = [i for i in validate_bars(bars) if i.startswith(_FATAL_PREFIXES)]
    if fatal:
        raise DataError(f"{path.name}: {'; '.join(fatal)}")
    return bars


def list_symbols(data_dir: str | Path | None = None) -> list[str]:
    """Symbols that have a CSV file in the data directory, sorted."""
    root = Path(data_dir) if data_dir is not None else default_data_dir()
    if not root.is_dir():
        return []
    symbols = []
    for entry in root.iterdir():
        if entry.suffix.lower() == ".csv" and _SYMBOL_PATTERN.fullmatch(entry.stem):
            symbols.append(entry.stem.upper())
    return sorted(symbols)


def symbol_path(symbol: str, data_dir: str | Path | None = None) -> Path:
    """Path of the CSV for a symbol, matched case-insensitively."""
    canonical = normalise_symbol(symbol)
    root = Path(data_dir) if data_dir is not None else default_data_dir()
    if root.is_dir():
        for entry in root.iterdir():
            if entry.suffix.lower() == ".csv" and entry.stem.upper() == canonical:
                return entry
    # The message names the symbol only: it can end up in an HTTP response, and
    # the server's directory layout is not the caller's business.
    raise DataNotFoundError(f"no data file for symbol {canonical}")


def load_symbol(symbol: str, data_dir: str | Path | None = None) -> pd.DataFrame:
    """Load and validate the bars of one symbol."""
    return read_bars(symbol_path(symbol, data_dir))


def fingerprint(bars: pd.DataFrame) -> str:
    """Short content hash identifying exactly which data a model was trained on."""
    digest = hashlib.sha256()
    digest.update(np.ascontiguousarray(bars.index.asi8).tobytes())
    digest.update(
        np.ascontiguousarray(
            bars[list(REQUIRED_COLUMNS)].to_numpy(dtype=float)
        ).tobytes()
    )
    return digest.hexdigest()[:16]
