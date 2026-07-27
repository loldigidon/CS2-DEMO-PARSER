"""Canonical data types and names used across parser tables."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

import pandas as pd


WORLD_WEAPONS = {"world", "worldspawn", "fall", "trigger_hurt", "suicide"}


def has_value(value: Any) -> bool:
    if value is None:
        return False
    try:
        if pd.isna(value):
            return False
    except (TypeError, ValueError):
        pass
    return str(value).strip() != ""


def normalize_side(value: Any) -> str | None:
    if not has_value(value):
        return None
    side = str(value).strip().lower()
    if side in {"ct", "counterterrorist", "counter-terrorist", "counter_terrorist"}:
        return "ct"
    if side in {"t", "terrorist", "terrorists", "t_side"}:
        return "t"
    return None


def normalize_bombsite(value: Any) -> str | None:
    if not has_value(value):
        return None
    site = str(value).strip().lower().replace(" ", "").replace("_", "")
    if site in {"a", "bombsitea", "sitea", "0"}:
        return "bombsite_a"
    if site in {"b", "bombsiteb", "siteb", "1"}:
        return "bombsite_b"
    if site in {"notplanted", "none", "unknown"}:
        return "not_planted"
    return None


def normalize_identifier(value: Any) -> str | None:
    """Return an exact decimal identifier string without float notation.

    The conversion is intentionally performed only after Polars values were moved
    to pandas through Arrow extension arrays. That prevents nullable UInt64 values
    from first becoming imprecise float64 values.
    """
    if not has_value(value):
        return None
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        if raw.endswith(".0") and raw[:-2].isdigit():
            return raw[:-2]
        return raw
    if isinstance(value, bool):
        return str(int(value))
    if isinstance(value, int):
        return str(value)
    try:
        dec = Decimal(str(value))
        if dec.is_nan():
            return None
        if dec == dec.to_integral_value():
            return format(dec.quantize(Decimal(1)), "f")
    except (InvalidOperation, ValueError, TypeError):
        pass
    return str(value)


def identifier_columns(df: pd.DataFrame) -> list[str]:
    return [
        col
        for col in df.columns
        if col.lower() == "steamid"
        or col.lower().endswith("_steamid")
        or col.lower().endswith("_xuid")
    ]


def normalize_identifiers(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty and len(df.columns) == 0:
        return df
    out = df.copy()
    for col in identifier_columns(out):
        out[col] = pd.Series(
            (normalize_identifier(value) for value in out[col].tolist()),
            index=out.index,
            dtype="string",
        )
    return out


def normalize_side_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for col in out.columns:
        lower = col.lower()
        if lower == "side" or lower.endswith("_side"):
            out[col] = out[col].map(normalize_side)
    return out


def normalize_table(df: pd.DataFrame) -> pd.DataFrame:
    return normalize_side_columns(normalize_identifiers(df))


def same_identifier(left: Any, right: Any) -> bool:
    a = normalize_identifier(left)
    b = normalize_identifier(right)
    return a is not None and b is not None and a == b


def is_world_or_self_kill(row: pd.Series) -> bool:
    weapon = str(row.get("weapon") or "").strip().lower()
    if weapon in WORLD_WEAPONS:
        return True
    return same_identifier(row.get("attacker_steamid"), row.get("victim_steamid"))
