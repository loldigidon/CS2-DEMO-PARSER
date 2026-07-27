from pathlib import Path

import pandas as pd

from cs2parser.input import demo_match_id, is_demo_path, materialized_demo
from cs2parser.visualization import (
    BUNDLED_RADAR_ROOT,
    _economy_item,
    _map_levels,
    _map_transform,
    _radar_candidates,
    _utility_data,
    _utility_events,
    build_dashboard,
    build_dashboard_data,
    build_dashboard_hub,
    find_parsed_matches,
)
from cs2parser.round_swing import estimate_round_players


def _write(df: pd.DataFrame, root: Path, name: str) -> None:
    df.to_parquet(root / f"{name}.parquet", index=False)


def test_duplicate_download_name_is_supported(tmp_path):
    import zstandard as zstd

    source = tmp_path / "faceit-match.dem(1).zst"
    source.write_bytes(zstd.ZstdCompressor().compress(b"demo-bytes"))

    assert is_demo_path(source)
    assert demo_match_id(source) == "faceit-match"
    with materialized_demo(source) as demo_path:
        assert demo_path.name == "faceit-match.dem"
        assert demo_path.read_bytes() == b"demo-bytes"


def test_economy_ignores_free_knives_and_bayonets():
    for item in (
        "knife", "weapon_knife", "M9 Bayonet", "Bayonet", "Karambit",
        "Shadow Daggers", "Kukri Knife", "Bowie Knife",
    ):
        assert _economy_item(item) is None
    assert _economy_item("AK-47") == ("AK-47", "primary")


def test_utility_counts_projectiles_once_and_ignores_inventory_entities():
    players = [
        {"name": "Alice", "team": "Alpha"},
        {"name": "Bob", "team": "Bravo"},
    ]
    player_index = {"sid:1": 0, "sid:2": 1}
    grenades = pd.DataFrame([
        # Inventory/base entities must not count as throws.
        {"round_num": 1, "entity_id": 10, "thrower_steamid": "1", "thrower": "Alice", "grenade_type": "CFlashbang", "tick": 100},
        {"round_num": 1, "entity_id": 10, "thrower_steamid": "1", "thrower": "Alice", "grenade_type": "CFlashbang", "tick": 101},
        # One flying projectile appears on many trajectory ticks: count once.
        {"round_num": 1, "entity_id": 11, "thrower_steamid": "1", "thrower": "Alice", "grenade_type": "CFlashbangProjectile", "tick": 110},
        {"round_num": 1, "entity_id": 11, "thrower_steamid": "1", "thrower": "Alice", "grenade_type": "CFlashbangProjectile", "tick": 111},
        {"round_num": 1, "entity_id": 11, "thrower_steamid": "1", "thrower": "Alice", "grenade_type": "CFlashbangProjectile", "tick": 112},
        # The same entity id may be reused in another round: this is another throw.
        {"round_num": 2, "entity_id": 11, "thrower_steamid": "1", "thrower": "Alice", "grenade_type": "CFlashbangProjectile", "tick": 500},
        # Molotov/incendiary projectile counts even if it never creates an inferno.
        {"round_num": 1, "entity_id": 20, "thrower_steamid": "2", "thrower": "Bob", "grenade_type": "CMolotovProjectile", "tick": 130},
        {"round_num": 1, "entity_id": 21, "thrower_steamid": "2", "thrower": "Bob", "grenade_type": "CSmokeGrenadeProjectile", "tick": 140},
    ])

    data = _utility_data(
        {
            "grenades": grenades,
            "damages": pd.DataFrame(),
            "flashes": pd.DataFrame(),
        },
        players,
        player_index,
    )
    by_player = {row["player"]: row for row in data["players"]}

    assert by_player[0]["flash"] == 2
    assert by_player[0]["total"] == 2
    assert by_player[1]["fire"] == 1
    assert by_player[1]["smoke"] == 1
    assert by_player[1]["total"] == 2


def test_build_dashboard_from_parquet(tmp_path):
    match = tmp_path / "match-1"
    match.mkdir()
    _write(pd.DataFrame([{
        "match_id": "match-1", "map_name": "de_mirage", "tickrate": 64,
        "all_events": True, "detected_event_count": 10, "parsed_event_count": 10,
        "with_positions": True, "position_sample": 16,
    }]), match, "parse_metadata")
    _write(pd.DataFrame([{
        "match_id": "match-1", "round_num": 1, "start": 0, "freeze_end": 64,
        "end": 640, "official_end": 704, "winner": "ct", "reason": "t_killed",
        "bomb_plant": None, "bomb_site": "not_planted", "total_kills": 1,
        "total_damage": 100,
    }]), match, "rounds")
    _write(pd.DataFrame([
        {"match_id": "match-1", "round_num": 1, "side": "ct", "team_clan_name": "Alpha"},
        {"match_id": "match-1", "round_num": 1, "side": "t", "team_clan_name": "Bravo"},
    ]), match, "round_sides")
    _write(pd.DataFrame([
        {"match_id": "match-1", "name": "Alice", "steamid": "1", "team_clan_name": "Alpha", "side": "all", "n_rounds": 1, "kills": 1, "deaths": 0, "assists": 0, "headshots": 1, "headshot_pct": 100.0, "opening_kills": 1, "opening_deaths": 0, "trade_kills": 0, "clutch_attempts": 0, "clutches_won": 0, "dmg": 100.0, "adr": 100.0, "kast_rounds": 1, "kast": 100.0, "impact": 1.7, "rating": 1.5},
        {"match_id": "match-1", "name": "Bob", "steamid": "2", "team_clan_name": "Bravo", "side": "all", "n_rounds": 1, "kills": 0, "deaths": 1, "assists": 0, "headshots": 0, "headshot_pct": 0.0, "opening_kills": 0, "opening_deaths": 1, "trade_kills": 0, "clutch_attempts": 0, "clutches_won": 0, "dmg": 0.0, "adr": 0.0, "kast_rounds": 0, "kast": 0.0, "impact": -0.4, "rating": 0.5},
    ]), match, "player_stats")
    _write(pd.DataFrame([{
        "match_id": "match-1", "round_num": 1, "tick": 200,
        "attacker_name": "Alice", "attacker_steamid": "1", "attacker_team_clan_name": "Alpha",
        "victim_name": "Bob", "victim_steamid": "2", "victim_team_clan_name": "Bravo",
        "weapon": "ak47", "headshot": True, "is_trade": False,
    }]), match, "kills")
    _write(pd.DataFrame([
        {"match_id": "match-1", "round_num": 1, "tick": 64, "steamid": "1", "name": "Alice", "side": "ct", "team_clan_name": "Alpha", "X": -1600.0, "Y": -1800.0, "Z": -160.0, "yaw": 0.0, "pitch": 0.0, "is_alive": True},
        {"match_id": "match-1", "round_num": 1, "tick": 64, "steamid": "2", "name": "Bob", "side": "t", "team_clan_name": "Bravo", "X": 1100.0, "Y": -60.0, "Z": -160.0, "yaw": 180.0, "pitch": 0.0, "is_alive": True},
    ]), match, "positions_sampled")
    _write(pd.DataFrame([
        {"match_id": "match-1", "round_num": 1, "tick": 64, "steamid": "1", "name": "Alice", "side": "ct", "team_clan_name": "Alpha", "balance": 300, "current_equip_value": 4200},
        {"match_id": "match-1", "round_num": 1, "tick": 64, "steamid": "2", "name": "Bob", "side": "t", "team_clan_name": "Bravo", "balance": 800, "current_equip_value": 200},
    ]), match, "ticks")
    _write(pd.DataFrame(columns=["match_id", "status", "check", "severity", "details"]), match, "validation")

    data = build_dashboard_data(match)
    assert data["teams"][0]["name"] == "Alpha"
    assert data["teams"][0]["score"] == 1
    assert data["players"][0]["name"] == "Alice"
    assert data["duels"]["pairs"] == [[0, 1, 1]]
    assert len(data["economy"]["rounds"]) == 1
    assert data["economy"]["rounds"][0]["teams"][0]["equip"] >= 0
    assert sum(len(team["players"]) for team in data["economy"]["rounds"][0]["teams"]) == 2
    assert len(data["frames"]["1"]) == 1
    # Compact position payload now keeps pitch so vertical aim is not shown as
    # a falsely confident full-length 2D direction pointer.
    assert data["frames"]["1"][0]["p"][0][5] == 0.0
    assert len(data["frames"]["1"][0]["p"][0]) == 8

    index = build_dashboard(match)
    assert index.exists()
    assert (index.parent / "data.json").exists()
    standalone = index.parent / "standalone.html"
    assert standalone.exists()
    assert (index.parent / "radar.png").exists()
    standalone_text = standalone.read_text(encoding="utf-8")
    assert "window.__CS2_RADAR__=\"data:image/png;base64," in standalone_text
    index_text = index.read_text(encoding="utf-8")
    assert "window.__CS2_DATA__=" in index_text
    assert "window.__CS2_RADAR__=\"data:image/png;base64," in index_text
    assert "Парсер и аналитика без внешних API" not in index_text
    app_text = (index.parent / "app.js").read_text(encoding="utf-8")
    for label in ("Общее", "Продвинутый", "Первых", "Размен", "Клатчей"):
        assert label in app_text
    assert "MVP МАТЧА" in app_text
    assert "function radarViewVector" in app_text
    assert "worldToRadar(" in app_text
    assert "Math.abs(Math.cos(pitchRadians))" in app_text
    assert "function levelAlphaForZ" in app_text
    assert "data-floor-mode=\"both\"" in app_text
    assert "function roundEventSeconds" in app_text
    assert "formatTime(roundEventSeconds(tick))" in app_text
    assert "function setupSortableTables" in app_text
    assert "function sortTable" in app_text
    assert "function roundWinnerColor" in app_text
    assert "if (round?.winner_team) return teamColor(round.winner_team)" in app_text
    assert "function roundOutcomeIcon" in app_text
    assert "round-icon-${roundOutcomeType(round)}" in app_text
    assert "utilityTablesByTeam(rows, utilityGeneralTable)" in app_text
    assert "utilityTablesByTeam(rows, utilityDamageTable)" in app_text
    assert "utilityTablesByTeam(rows, utilitySupportTable)" in app_text
    assert "function renderEconomy" in app_text
    assert "function economyChartSvg" in app_text
    assert "economy-player-table" in app_text
    assert "function isFreeEconomyItem" in app_text
    assert "stats-table-${esc(kind)}" in app_text
    styles_text = (index.parent / "styles.css").read_text(encoding="utf-8")
    assert ".radar-stage {\n  position: relative;\n  width: 100%;" in styles_text
    assert ".round-button .round-icon-defuse" in styles_text
    assert ".round-button .round-icon-explosion" in styles_text
    assert ".sort-button {" in styles_text
    assert ".utility-team-block:first-child" in styles_text
    assert ".economy-chart-card" in styles_text
    assert ".overview-table { min-width: 1850px; }" not in styles_text
    assert "min-width: 0;" in styles_text
    assert ".weapon-chip.primary" in styles_text
    assert "<th>Ранг</th>" not in app_text
    assert "Код прицела" not in app_text
    assert find_parsed_matches(tmp_path) == [match]



def test_nuke_uses_two_vertical_radar_sections():
    model = _map_levels("de_nuke")
    assert model["mode"] == "split"
    assert model["default"] == "both"
    assert model["split_z"] == -500.0
    assert [level["id"] for level in model["levels"]] == ["upper", "lower"]
    radar_root = BUNDLED_RADAR_ROOT
    assert _radar_candidates("de_nuke", [radar_root], section="default")[0].name == "de_nuke_radar.dds"
    assert _radar_candidates("de_nuke", [radar_root], section="lower")[0].name == "de_nuke_lower_radar.dds"
    assert _map_levels("de_nuke2")["mode"] == "split"
    assert _map_transform("de_nuke2", pd.DataFrame())["mode"] == "overview"
    assert _radar_candidates("de_nuke2", [radar_root], section="default")[0].name == "de_nuke_radar.dds"
    assert _radar_candidates("de_nuke2", [radar_root], section="lower")[0].name == "de_nuke_lower_radar.dds"


def test_anubis_uses_bundled_radar_and_official_overview_transform():
    transform = _map_transform("de_anubis", pd.DataFrame())
    assert transform == {
        "mode": "overview",
        "pos_x": -2796.0,
        "pos_y": 3328.0,
        "scale": 5.22,
        "width": 1024,
        "height": 1024,
    }
    candidate = _radar_candidates("de_anubis", [BUNDLED_RADAR_ROOT])[0]
    assert candidate.name == "de_anubis_radar.png"
    assert candidate.is_file()


def test_dashboard_hub_links_every_generated_match(tmp_path):
    dashboards = []
    for match_id, map_name, scores in (
        ("match-a", "de_anubis", (13, 7)),
        ("match-b", "de_mirage", (11, 13)),
    ):
        dashboard = tmp_path / match_id / "visualization" / "index.html"
        dashboard.parent.mkdir(parents=True)
        dashboard.write_text("<h1>match</h1>", encoding="utf-8")
        (dashboard.parent / "data.json").write_text(
            (
                '{"match":{"map":"%s","round_count":%d,"radar_found":true},'
                '"teams":[{"name":"Alpha","score":%d},{"name":"Bravo","score":%d}]}'
            ) % (map_name, sum(scores), scores[0], scores[1]),
            encoding="utf-8",
        )
        dashboards.append(dashboard)

    hub = build_dashboard_hub(dashboards)
    text = hub.read_text(encoding="utf-8")

    assert hub == tmp_path / "index.html"
    assert "match-a/visualization/index.html" in text
    assert "match-b/visualization/index.html" in text
    assert "de_anubis" in text
    assert "de_mirage" in text


def test_unused_inventory_preserves_stacked_flashbangs_and_ignores_decoys():
    players = [
        {"name": "Alice", "team": "Alpha"},
        {"name": "Bob", "team": "Bravo"},
    ]
    player_index = {"sid:1": 0, "sid:2": 1}
    ticks = pd.DataFrame([
        {
            "round_num": 1, "tick": 99, "steamid": "2", "name": "Bob",
            "inventory": [
                "Flashbang", "Flashbang", "High Explosive Grenade",
                "Decoy Grenade",
            ],
        },
    ])
    kills = pd.DataFrame([
        {
            "round_num": 1, "tick": 100,
            "attacker_name": "Alice", "attacker_steamid": "1",
            "victim_name": "Bob", "victim_steamid": "2",
            "weapon": "ak47",
        },
    ])

    data = _utility_data(
        {
            "ticks": ticks,
            "kills": kills,
            "grenades": pd.DataFrame(),
            "damages": pd.DataFrame(),
            "flashes": pd.DataFrame(),
        },
        players,
        player_index,
    )
    bob = next(row for row in data["players"] if row["player"] == 1)

    assert bob["unused_flash"] == 2
    assert bob["unused_he"] == 1
    assert bob["unused_decoy"] == 0
    assert bob["unused_total"] == 3


def test_successful_flash_entity_ids_are_scoped_by_round():
    players = [
        {"name": "Alice", "team": "Alpha"},
        {"name": "Bob", "team": "Bravo"},
    ]
    player_index = {"sid:1": 0, "sid:2": 1}
    flashes = pd.DataFrame([
        {
            "round_num": 1, "tick": 100, "entityid": 447,
            "attacker_name": "Alice", "attacker_steamid": "1",
            "attacker_team_clan_name": "Alpha",
            "user_name": "Bob", "user_steamid": "2",
            "user_team_clan_name": "Bravo", "blind_duration": 2.0,
        },
        {
            "round_num": 2, "tick": 500, "entityid": 447,
            "attacker_name": "Alice", "attacker_steamid": "1",
            "attacker_team_clan_name": "Alpha",
            "user_name": "Bob", "user_steamid": "2",
            "user_team_clan_name": "Bravo", "blind_duration": 2.0,
        },
    ])
    data = _utility_data(
        {"flashes": flashes, "kills": pd.DataFrame(), "grenades": pd.DataFrame(), "damages": pd.DataFrame()},
        players,
        player_index,
    )
    alice = next(row for row in data["players"] if row["player"] == 0)
    assert alice["successful_flash"] == 2
    assert alice["successful_grenades"] == 2


def test_direct_grenade_impacts_count_only_in_outgoing_utility_total():
    players = [
        {"name": "Alice", "team": "Alpha"},
        {"name": "Bob", "team": "Bravo"},
    ]
    player_index = {"sid:1": 0, "sid:2": 1}
    damages = pd.DataFrame([
        {
            "round_num": 1, "tick": 100, "weapon": "hegrenade",
            "attacker_name": "Alice", "attacker_steamid": "1", "attacker_team_clan_name": "Alpha",
            "victim_name": "Bob", "victim_steamid": "2", "victim_team_clan_name": "Bravo",
            "dmg_health_real": 30,
        },
        {
            "round_num": 1, "tick": 110, "weapon": "inferno",
            "attacker_name": "Alice", "attacker_steamid": "1", "attacker_team_clan_name": "Alpha",
            "victim_name": "Bob", "victim_steamid": "2", "victim_team_clan_name": "Bravo",
            "dmg_health_real": 5,
        },
        {
            "round_num": 1, "tick": 120, "weapon": "smokegrenade",
            "attacker_name": "Alice", "attacker_steamid": "1", "attacker_team_clan_name": "Alpha",
            "victim_name": "Bob", "victim_steamid": "2", "victim_team_clan_name": "Bravo",
            "dmg_health_real": 2,
        },
        {
            "round_num": 1, "tick": 130, "weapon": "molotov",
            "attacker_name": "Alice", "attacker_steamid": "1", "attacker_team_clan_name": "Alpha",
            "victim_name": "Bob", "victim_steamid": "2", "victim_team_clan_name": "Bravo",
            "dmg_health_real": 1,
        },
    ])
    data = _utility_data(
        {"damages": damages, "kills": pd.DataFrame(), "grenades": pd.DataFrame(), "flashes": pd.DataFrame()},
        players,
        player_index,
    )
    alice = next(row for row in data["players"] if row["player"] == 0)
    bob = next(row for row in data["players"] if row["player"] == 1)
    assert alice["he_damage"] == 30
    assert alice["fire_damage"] == 5
    assert alice["impact_damage"] == 3
    assert alice["damage"] == 38
    assert bob["damage_received"] == 35



def test_utility_events_build_trajectory_and_stop_stationary_tail():
    player_index = {"sid:1": 0}
    grenades = pd.DataFrame([
        {"round_num": 1, "entity_id": 77, "thrower_steamid": "1", "thrower": "Alice", "grenade_type": "CSmokeGrenadeProjectile", "tick": 100, "X": 0.0, "Y": 0.0, "Z": 10.0},
        {"round_num": 1, "entity_id": 77, "thrower_steamid": "1", "thrower": "Alice", "grenade_type": "CSmokeGrenadeProjectile", "tick": 101, "X": 10.0, "Y": 5.0, "Z": 15.0},
        {"round_num": 1, "entity_id": 77, "thrower_steamid": "1", "thrower": "Alice", "grenade_type": "CSmokeGrenadeProjectile", "tick": 102, "X": 20.0, "Y": 10.0, "Z": 5.0},
        {"round_num": 1, "entity_id": 77, "thrower_steamid": "1", "thrower": "Alice", "grenade_type": "CSmokeGrenadeProjectile", "tick": 103, "X": 20.0, "Y": 10.0, "Z": 5.0},
        {"round_num": 1, "entity_id": 77, "thrower_steamid": "1", "thrower": "Alice", "grenade_type": "CSmokeGrenadeProjectile", "tick": 104, "X": 20.0, "Y": 10.0, "Z": 5.0},
    ])
    smokes = pd.DataFrame([{
        "round_num": 1, "entity_id": 77, "start_tick": 105, "end_tick": 500,
        "X": 21.0, "Y": 11.0, "Z": 4.0,
    }])

    events = _utility_events(
        {"grenades": grenades, "smokes": smokes, "infernos": pd.DataFrame(), "flashes": pd.DataFrame(), "damages": pd.DataFrame()},
        player_index,
        64,
    )
    event = events["1"][0]

    assert event["kind"] == "smoke"
    assert event["player"] == 0
    assert event["start"] == 100
    assert event["land"] == 102
    assert event["effect_start"] == 105
    assert event["end"] == 500
    assert event["path"][-1] == [102, 20.0, 10.0, 5.0]
    assert (event["x"], event["y"], event["z"]) == (21.0, 11.0, 4.0)


def test_round_swing_exposes_per_round_player_metrics_and_team_targets():
    players = [
        {"name": "Alice", "team": "Alpha"},
        {"name": "Anya", "team": "Alpha"},
        {"name": "Bob", "team": "Bravo"},
        {"name": "Ben", "team": "Bravo"},
    ]
    player_index = {"sid:1": 0, "sid:2": 1, "sid:3": 2, "sid:4": 3}
    tables = {
        "rounds": pd.DataFrame([{"round_num": 1, "winner": "ct"}]),
        "round_sides": pd.DataFrame([
            {"round_num": 1, "side": "ct", "team_clan_name": "Alpha"},
            {"round_num": 1, "side": "t", "team_clan_name": "Bravo"},
        ]),
        "buys": pd.DataFrame([
            {"round_num": 1, "team_clan_name": "Alpha", "equip": 5000},
            {"round_num": 1, "team_clan_name": "Bravo", "equip": 5000},
        ]),
        "damages": pd.DataFrame([{
            "round_num": 1, "tick": 100, "attacker_steamid": "1", "attacker_name": "Alice",
            "attacker_team_clan_name": "Alpha", "victim_steamid": "3", "victim_name": "Bob",
            "victim_team_clan_name": "Bravo", "dmg_health_real": 40,
        }]),
        "kills": pd.DataFrame([{
            "round_num": 1, "tick": 110, "attacker_steamid": "1", "attacker_name": "Alice",
            "attacker_team_clan_name": "Alpha", "victim_steamid": "3", "victim_name": "Bob",
            "victim_team_clan_name": "Bravo", "assister_steamid": "2", "assister_name": "Anya",
            "assistedflash": True,
        }]),
        "bomb": pd.DataFrame(),
    }

    result = estimate_round_players(tables, players, player_index, "de_dust2")["1"]
    rows = {row["player"]: row for row in result["players"]}

    assert rows[0]["damage"] == 40
    assert rows[0]["kills"] == 1
    assert rows[1]["assists"] == 1
    assert rows[2]["deaths"] == 1
    assert round(sum(row["swing"] for row in result["players"] if row["team"] == "Alpha"), 1) == round(result["team_swing"]["Alpha"], 1)
    assert round(sum(row["swing"] for row in result["players"] if row["team"] == "Bravo"), 1) == round(result["team_swing"]["Bravo"], 1)
    assert result["team_swing"]["Alpha"] > 0
    assert result["team_swing"]["Bravo"] < 0
