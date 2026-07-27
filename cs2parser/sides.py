"""Round-side reconstruction for demos where tick side labels are missing."""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

import pandas as pd

from .normalize import has_value, normalize_side


ROUND_SIDE_COLUMNS = ["round_num", "side", "team_clan_name"]


def _as_round(value: Any) -> int | None:
    try:
        if pd.isna(value):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _add(votes: dict[tuple[int, str], Counter[str]], round_num: Any, side: str, team: Any) -> None:
    rn = _as_round(round_num)
    side = normalize_side(side)
    if rn is None or side is None or not has_value(team):
        return
    votes[(rn, side)][str(team)] += 1


def build_round_sides(tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Build one CT and one T team mapping per round from every available table."""
    votes: dict[tuple[int, str], Counter[str]] = defaultdict(Counter)

    preferred = ["kills", "damages", "shots", "bomb", "footsteps", "flashes", "reloads"]
    for table_name in preferred:
        df = tables.get(table_name, pd.DataFrame())
        if df.empty or "round_num" not in df.columns:
            continue
        for _, row in df.iterrows():
            rn = row.get("round_num")
            _add(votes, rn, "ct", row.get("ct_team_clan_name"))
            _add(votes, rn, "t", row.get("t_team_clan_name"))

            for prefix in ("attacker", "victim", "assister", "player", "user", "thrower"):
                team = row.get(f"{prefix}_team_clan_name")
                side = row.get(f"{prefix}_side")
                if not has_value(side):
                    side = row.get(f"{prefix}_team_name")
                _add(votes, rn, side, team)

            team = row.get("team_clan_name")
            side = row.get("side")
            if not has_value(side):
                side = row.get("team_name")
            _add(votes, rn, side, team)

    rows: list[dict[str, Any]] = []
    for (rn, side), counts in sorted(votes.items()):
        if not counts:
            continue
        rows.append({
            "round_num": rn,
            "side": side,
            "team_clan_name": counts.most_common(1)[0][0],
        })
    return pd.DataFrame(rows, columns=ROUND_SIDE_COLUMNS)


def apply_round_sides(
    df: pd.DataFrame,
    round_sides: pd.DataFrame,
    *,
    team_column: str = "team_clan_name",
    side_column: str = "side",
) -> pd.DataFrame:
    """Fill a canonical side using `(round_num, team_clan_name)` mapping."""
    if df.empty:
        out = df.copy()
        if side_column not in out.columns:
            out[side_column] = pd.Series(dtype="string")
        return out

    out = df.copy()
    direct = out.get(side_column)
    if direct is None:
        direct = out.get("team_name", pd.Series(index=out.index, dtype="object"))
    out[side_column] = direct.map(normalize_side)

    required = {"round_num", team_column}
    if round_sides.empty or not required.issubset(out.columns):
        return out

    mapping = round_sides.rename(columns={"team_clan_name": team_column, "side": "_mapped_side"})
    out = out.merge(mapping, on=["round_num", team_column], how="left")
    out[side_column] = out[side_column].fillna(out["_mapped_side"])
    return out.drop(columns=["_mapped_side"])
