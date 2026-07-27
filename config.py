"""Parser configuration."""
from __future__ import annotations

from dataclasses import dataclass


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


CORE_TICK_PROPS = [
    "steamid",
    "name",
    "health",
    "is_alive",
    "balance",
    "current_equip_value",
    "team_name",
    "team_clan_name",
    # Cumulative scoreboard counters.  These are cheap scalar properties and
    # let us reproduce the final CS2 scoreboard (especially MVPs) exactly.
    "kills_total",
    "deaths_total",
    "assists_total",
    "headshot_kills_total",
    "damage_total",
    "utility_damage_total",
    "enemies_flashed_total",
    "score",
    "CCSPlayerController.m_iMVPs",
]

# Event rows are sparse, so rich inventory/context fields are safe here.  They
# must not be requested for every player on every tick: a list-valued inventory
# column across a long demo can consume many gigabytes and make parsing appear
# to hang.  Keeping tick and event properties separate is the main performance
# improvement in the v2 parser.
EVENT_PLAYER_PROPS = [
    "steamid",
    "name",
    "health",
    "is_alive",
    "balance",
    "current_equip_value",
    "inventory",
    "team_name",
    "team_clan_name",
    "X",
    "Y",
    "Z",
    "pitch",
    "yaw",
    "flash_duration",
    "is_scoped",
    "last_place_name",
]

POSITION_TICK_PROPS_MINIMAL = ["X", "Y", "Z", "pitch", "yaw"]
POSITION_TICK_PROPS_STANDARD = [
    "X",
    "Y",
    "Z",
    "pitch",
    "yaw",
    "flash_duration",
    "is_scoped",
]
DEFAULT_PLAYER_PROPS = _unique(CORE_TICK_PROPS + POSITION_TICK_PROPS_STANDARD)


@dataclass
class ParserConfig:
    # None means: infer from demo ticks (`game_time`) and set it on awpy.Demo.
    tickrate: int | None = None
    tick_sample: int = 1  # legacy compatibility, not used for derived logic
    player_props: list[str] | None = None
    events: list[str] | None = None
    all_events: bool = True
    # Parsing every raw event detected by the engine is optional because it
    # requires an additional full demo pass.  The default keeps all rich
    # events requested by Awpy plus the events needed by the dashboard.
    exhaustive_events: bool = False
    verbose: bool = False

    # Radar playback is a first-class dashboard feature, so spatial samples
    # are enabled for both CLI and library usage by default.  Callers that only
    # need statistics can still opt out explicitly with ``with_positions=False``
    # or the CLI flag ``--no-positions``.
    with_positions: bool = True
    position_sample: int = 16
    position_props_mode: str = "minimal"

    skip_validation_stats: bool = False
    skip_shots: bool = False
    skip_footsteps: bool = False
    allow_partial: bool = False

    # Equipment thresholds are team totals at freeze end.
    eco_max: int = 7000
    force_max: int = 18000
    trade_seconds: float = 5.0

    def position_tick_props(self) -> list[str]:
        if self.position_props_mode == "standard":
            return list(POSITION_TICK_PROPS_STANDARD)
        return list(POSITION_TICK_PROPS_MINIMAL)

    def tick_player_props(self) -> list[str]:
        if self.player_props is not None:
            return list(self.player_props)
        props = list(CORE_TICK_PROPS)
        if self.with_positions:
            props.extend(self.position_tick_props())
        return _unique(props)

    def event_player_props(self) -> list[str]:
        """Rich properties attached only to sparse game-event rows."""
        return list(EVENT_PLAYER_PROPS)
