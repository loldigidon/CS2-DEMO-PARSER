"""Извлечение названий команд и их составов."""
from __future__ import annotations

import pandas as pd


def extract_teams(ticks: pd.DataFrame) -> pd.DataFrame:
    """Возвращает ростеры: команда (team_clan_name) -> игроки.

    Важно: team_name — это сторона (CT/TERRORIST) и меняется по ходу матча.
    Название команды — это team_clan_name. Игроков связываем по steamid.
    """
    if ticks.empty:
        return pd.DataFrame(columns=["team_clan_name", "steamid", "name"])

    cols = [c for c in ["team_clan_name", "steamid", "name"] if c in ticks.columns]
    if "steamid" not in cols or "name" not in cols:
        return pd.DataFrame(columns=["team_clan_name", "steamid", "name"])

    roster = (
        ticks[cols]
        .dropna(subset=["steamid"])
        .drop_duplicates()
    )
    # Для каждого steamid берём наиболее частый ник и клан
    roster = (
        roster.groupby("steamid")
        .agg(lambda s: s.value_counts().index[0] if len(s) else None)
        .reset_index()
    )
    return roster


def team_names(ticks: pd.DataFrame) -> list[str]:
    """Список уникальных названий команд."""
    if ticks.empty or "team_clan_name" not in ticks.columns:
        return []
    return [t for t in ticks["team_clan_name"].dropna().unique().tolist() if t]
