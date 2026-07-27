"""Round summaries and trustworthy bomb/damage aggregates."""
from __future__ import annotations

import pandas as pd

from .normalize import (
    WORLD_WEAPONS,
    has_value,
    normalize_bombsite,
    normalize_side,
    same_identifier,
)


def _enemy_mask(df: pd.DataFrame, attacker_prefix: str = "attacker", victim_prefix: str = "victim") -> pd.Series:
    if df.empty:
        return pd.Series(dtype=bool)
    a_team = df.get(f"{attacker_prefix}_team_clan_name", pd.Series(index=df.index, dtype="object"))
    v_team = df.get(f"{victim_prefix}_team_clan_name", pd.Series(index=df.index, dtype="object"))
    a_side = df.get(f"{attacker_prefix}_side", pd.Series(index=df.index, dtype="object")).map(normalize_side)
    v_side = df.get(f"{victim_prefix}_side", pd.Series(index=df.index, dtype="object")).map(normalize_side)
    known_team = a_team.notna() & v_team.notna()
    known_side = a_side.notna() & v_side.notna()
    return (known_team & a_team.astype(str).ne(v_team.astype(str))) | (~known_team & known_side & a_side.ne(v_side))


def _valid_combat_kills(kills: pd.DataFrame) -> pd.DataFrame:
    if kills.empty:
        return kills.copy()
    weapon = kills.get("weapon", pd.Series(index=kills.index, dtype="object")).astype("string").str.lower()
    world = weapon.isin(WORLD_WEAPONS)
    attacker = kills.get("attacker_steamid", pd.Series(index=kills.index, dtype="object"))
    victim = kills.get("victim_steamid", pd.Series(index=kills.index, dtype="object"))
    self_kill = pd.Series(
        [same_identifier(a, v) for a, v in zip(attacker, victim, strict=False)],
        index=kills.index,
    )
    return kills[~world & ~self_kill & _enemy_mask(kills)].copy()


def fix_round_bomb_sites(rounds: pd.DataFrame, bomb: pd.DataFrame) -> pd.DataFrame:
    """Use actual active plant events instead of unreliable `rounds.bomb_site`."""
    if rounds.empty:
        return rounds.copy()
    out = rounds.copy()
    out["bomb_plant"] = pd.NA
    out["bomb_site"] = "not_planted"
    out["postround_plant_count"] = 0

    if bomb.empty or not {"round_num", "tick", "event"}.issubset(bomb.columns):
        return out

    for idx, rr in out.iterrows():
        rn = rr.get("round_num")
        rb = bomb[(bomb["round_num"] == rn) & (bomb["event"].astype(str).str.lower() == "plant")].copy()
        if rb.empty:
            continue
        ticks = pd.to_numeric(rb["tick"], errors="coerce")
        freeze = pd.to_numeric(pd.Series([rr.get("freeze_end")]), errors="coerce").iloc[0]
        end = pd.to_numeric(pd.Series([rr.get("end")]), errors="coerce").iloc[0]
        official = pd.to_numeric(pd.Series([rr.get("official_end")]), errors="coerce").iloc[0]
        active = rb[(ticks >= freeze) & (ticks <= end)] if pd.notna(freeze) and pd.notna(end) else rb.iloc[0:0]
        post = rb[(ticks > end) & (ticks <= official)] if pd.notna(end) and pd.notna(official) else rb.iloc[0:0]
        out.at[idx, "postround_plant_count"] = len(post)
        if active.empty:
            continue
        plant = active.sort_values("tick").iloc[0]
        out.at[idx, "bomb_plant"] = int(plant["tick"])
        out.at[idx, "bomb_site"] = normalize_bombsite(plant.get("bombsite")) or "unknown"
    out["bomb_plant"] = pd.to_numeric(out["bomb_plant"], errors="coerce").astype("Int64")
    out["postround_plant_count"] = pd.to_numeric(
        out["postround_plant_count"], errors="coerce"
    ).fillna(0).astype("int64")
    return out


def build_rounds(tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return round data with enemy combat and damage aggregates."""
    rounds = tables.get("rounds", pd.DataFrame()).copy()
    if rounds.empty:
        return rounds

    kills = tables.get("kills", pd.DataFrame())
    damages = tables.get("damages", pd.DataFrame())
    combat = _valid_combat_kills(kills)

    if not combat.empty and "round_num" in combat.columns:
        k = combat.groupby("round_num").size().rename("total_kills")
        rounds = rounds.drop(columns=["total_kills"], errors="ignore").merge(
            k, left_on="round_num", right_index=True, how="left"
        )

    dmg_col = "dmg_health_real" if "dmg_health_real" in damages.columns else (
        "dmg_health" if "dmg_health" in damages.columns else None
    )
    if not damages.empty and dmg_col and "round_num" in damages.columns:
        numeric = pd.to_numeric(damages[dmg_col], errors="coerce").fillna(0)
        enemy = _enemy_mask(damages)
        attacker = damages.get("attacker_steamid", pd.Series(index=damages.index, dtype="object"))
        victim = damages.get("victim_steamid", pd.Series(index=damages.index, dtype="object"))
        self_mask = pd.Series(
            [same_identifier(a, v) for a, v in zip(attacker, victim, strict=False)],
            index=damages.index,
        )
        d = damages.assign(_damage=numeric)
        enemy_dmg = d[enemy].groupby("round_num")["_damage"].sum().rename("total_damage")
        friendly_dmg = d[~enemy & ~self_mask].groupby("round_num")["_damage"].sum().rename("friendly_damage")
        self_dmg = d[self_mask].groupby("round_num")["_damage"].sum().rename("self_damage")
        rounds = rounds.drop(columns=["total_damage", "friendly_damage", "self_damage"], errors="ignore")
        rounds = rounds.merge(enemy_dmg, left_on="round_num", right_index=True, how="left")
        rounds = rounds.merge(friendly_dmg, left_on="round_num", right_index=True, how="left")
        rounds = rounds.merge(self_dmg, left_on="round_num", right_index=True, how="left")

    for col in ("total_kills", "total_damage", "friendly_damage", "self_damage"):
        if col not in rounds.columns:
            rounds[col] = 0
        rounds[col] = pd.to_numeric(rounds[col], errors="coerce").fillna(0).astype("int64")

    return fix_round_bomb_sites(rounds, tables.get("bomb", pd.DataFrame()))


def round_summary(tables: dict[str, pd.DataFrame], n: int) -> dict:
    rounds = tables["rounds"]
    kills = tables.get("kills", pd.DataFrame())
    damages = tables.get("damages", pd.DataFrame())
    ticks = tables.get("ticks", pd.DataFrame())

    r = rounds[rounds.round_num == n].iloc[0]
    out = {
        "round": int(n),
        "winner": r.get("winner"),
        "reason": r.get("reason"),
        "bomb_site": r.get("bomb_site"),
    }
    if not kills.empty and "round_num" in kills.columns:
        rk = kills[kills.round_num == n]
        keep = [c for c in ["attacker_name", "victim_name", "weapon", "headshot"] if c in rk.columns]
        out["kills"] = rk[keep].to_dict("records")
    if not damages.empty and "round_num" in damages.columns:
        dmg_col = "dmg_health_real" if "dmg_health_real" in damages.columns else "dmg_health"
        rd = damages[damages.round_num == n]
        if "attacker_name" in rd.columns and dmg_col in rd.columns:
            out["dmg_by_player"] = rd.groupby("attacker_name")[dmg_col].sum().to_dict()
    if not ticks.empty and {"round_num", "tick"}.issubset(ticks.columns) and has_value(r.get("freeze_end")):
        econ = ticks[(ticks.round_num == n) & (ticks.tick == r.freeze_end)]
        if "team_clan_name" in econ.columns and "balance" in econ.columns:
            out["team_money"] = econ.groupby("team_clan_name")["balance"].sum().to_dict()
    return out
