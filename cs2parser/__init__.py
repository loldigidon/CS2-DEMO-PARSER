"""Local CS2 demo parser built on Awpy; no API or AI enrichment."""
from ._version import __version__
from .demo import parse_demo, extract_tables, detect_tickrate
from .input import (
    RarExtractorNotFoundError,
    demo_match_id,
    find_demo_files,
    find_input_files,
    is_demo_path,
    is_rar_path,
    is_supported_input_path,
    materialized_demo,
    materialized_demo_collection,
)
from .events import extract_event_tables, common_event_tables, save_event_tables
from .positions import enrich_tick_sides, positions_sampled, saved_ticks
from .sides import build_round_sides, apply_round_sides
from .teams import extract_teams
from .rounds import build_rounds, fix_round_bomb_sites
from .derived import (
    classify_buys,
    opening_kills,
    trades,
    buy_outcomes,
    clutch_attempts,
    clutches,
)
from .players import player_stats
from .storage import save_all
from .validation import validate_tables, has_validation_errors
from .process import build_match_tables
from .visualization import (
    build_dashboard,
    build_dashboard_data,
    build_dashboard_hub,
    find_parsed_matches,
    serve_dashboard,
)

__all__ = [
    "__version__",
    "parse_demo",
    "demo_match_id",
    "find_demo_files",
    "find_input_files",
    "is_demo_path",
    "is_rar_path",
    "is_supported_input_path",
    "materialized_demo",
    "materialized_demo_collection",
    "RarExtractorNotFoundError",
    "extract_tables",
    "detect_tickrate",
    "extract_event_tables",
    "common_event_tables",
    "save_event_tables",
    "enrich_tick_sides",
    "positions_sampled",
    "saved_ticks",
    "build_round_sides",
    "apply_round_sides",
    "extract_teams",
    "build_rounds",
    "fix_round_bomb_sites",
    "classify_buys",
    "opening_kills",
    "trades",
    "buy_outcomes",
    "clutch_attempts",
    "clutches",
    "player_stats",
    "save_all",
    "validate_tables",
    "has_validation_errors",
    "build_match_tables",
    "build_dashboard",
    "build_dashboard_data",
    "build_dashboard_hub",
    "find_parsed_matches",
    "serve_dashboard",
]
