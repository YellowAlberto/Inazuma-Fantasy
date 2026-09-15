import json
import sqlite3
from pathlib import Path

from scoring import compute_price_from_form_index

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "inazuma_fantasy.sqlite"
SEED_PATH = BASE_DIR / "seed" / "players.json"

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    avatar_sprite_url TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS players (
    id INTEGER PRIMARY KEY,
    nombre TEXT NOT NULL,
    apodo TEXT,
    juego TEXT,
    arquetipo TEXT,
    posicion TEXT,
    elemento TEXT,
    potencia INTEGER,
    control INTEGER,
    tecnica INTEGER,
    presion INTEGER,
    fisico INTEGER,
    agilidad INTEGER,
    inteligencia INTEGER,
    total INTEGER,
    grupo_edad TEXT,
    anio_escolar TEXT,
    genero TEXT,
    rol TEXT,
    sprite_url TEXT,
    price REAL,
    form_index REAL,
    tecnicas TEXT NOT NULL DEFAULT '[]',
    tecnicas_por_tipo TEXT NOT NULL DEFAULT '{}',
    es_scout INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS leagues (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    invite_code TEXT UNIQUE NOT NULL,
    budget REAL NOT NULL DEFAULT 60,
    creator_id INTEGER NOT NULL,
    current_gameweek INTEGER NOT NULL DEFAULT 0,
    seasons TEXT NOT NULL DEFAULT '',
    scout_filter TEXT NOT NULL DEFAULT 'all',
    max_members INTEGER NOT NULL DEFAULT 10,
    schedule_json TEXT NOT NULL DEFAULT '[]',
    ended INTEGER NOT NULL DEFAULT 0,
    market_resolved INTEGER NOT NULL DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (creator_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS league_members (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    league_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    team_name TEXT NOT NULL,
    formation TEXT NOT NULL DEFAULT '4-4-2',
    bonus_budget REAL NOT NULL DEFAULT 0,
    UNIQUE(league_id, user_id)
);

CREATE TABLE IF NOT EXISTS rosters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    league_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    player_id INTEGER NOT NULL,
    acquired_price REAL NOT NULL DEFAULT 0,
    clause_value REAL NOT NULL DEFAULT 0,
    UNIQUE(league_id, user_id, player_id)
);

CREATE TABLE IF NOT EXISTS lineup_selections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    league_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    player_id INTEGER NOT NULL,
    UNIQUE(league_id, user_id, player_id)
);

CREATE TABLE IF NOT EXISTS league_market (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    league_id INTEGER NOT NULL,
    player_id INTEGER NOT NULL,
    released_by_user_id INTEGER,
    UNIQUE(league_id, player_id)
);

CREATE TABLE IF NOT EXISTS bids (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    league_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    player_id INTEGER NOT NULL,
    amount REAL NOT NULL,
    UNIQUE(league_id, user_id, player_id)
);

CREATE TABLE IF NOT EXISTS league_player_value (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    league_id INTEGER NOT NULL,
    player_id INTEGER NOT NULL,
    value REAL NOT NULL,
    UNIQUE(league_id, player_id)
);

CREATE TABLE IF NOT EXISTS market_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    league_id INTEGER NOT NULL,
    gameweek_number INTEGER NOT NULL,
    player_id INTEGER NOT NULL,
    winner_user_id INTEGER,
    amount REAL
);

CREATE TABLE IF NOT EXISTS gameweeks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    league_id INTEGER NOT NULL,
    number INTEGER NOT NULL,
    played_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(league_id, number)
);

CREATE TABLE IF NOT EXISTS gameweek_scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    gameweek_id INTEGER NOT NULL,
    league_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    player_id INTEGER NOT NULL,
    points INTEGER NOT NULL,
    events TEXT NOT NULL DEFAULT '',
    goals INTEGER NOT NULL DEFAULT 0,
    assists INTEGER NOT NULL DEFAULT 0,
    saves INTEGER NOT NULL DEFAULT 0,
    steals INTEGER NOT NULL DEFAULT 0,
    interceptions INTEGER NOT NULL DEFAULT 0,
    clearances INTEGER NOT NULL DEFAULT 0,
    key_passes INTEGER NOT NULL DEFAULT 0,
    losses INTEGER NOT NULL DEFAULT 0,
    clean_sheet INTEGER NOT NULL DEFAULT 0,
    blocks INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS gameweek_totals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    gameweek_id INTEGER NOT NULL,
    league_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    total_points INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS fixtures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    league_id INTEGER NOT NULL,
    gameweek_id INTEGER NOT NULL,
    home_user_id INTEGER NOT NULL,
    away_user_id INTEGER,
    home_label TEXT NOT NULL,
    away_label TEXT NOT NULL,
    home_goals INTEGER NOT NULL DEFAULT 0,
    away_goals INTEGER NOT NULL DEFAULT 0,
    home_points INTEGER NOT NULL DEFAULT 0,
    away_points INTEGER NOT NULL DEFAULT 0,
    summary TEXT NOT NULL DEFAULT '',
    events_json TEXT NOT NULL DEFAULT '[]',
    home_lineup_json TEXT NOT NULL DEFAULT '[]',
    away_lineup_json TEXT NOT NULL DEFAULT '[]',
    home_formation TEXT NOT NULL DEFAULT '4-4-2',
    away_formation TEXT NOT NULL DEFAULT '4-4-2'
);
"""


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # WAL lets readers keep working while a write is in progress, instead of
    # the whole database locking up for every request. Cheap, real win for
    # concurrency regardless of which host this runs on.
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def _run_migrations(conn):
    """Lightweight, idempotent migrations so existing local databases (from
    earlier versions of this app) get upgraded automatically instead of
    needing to be deleted and re-seeded."""
    user_cols = [row["name"] for row in conn.execute("PRAGMA table_info(users)").fetchall()]
    if "avatar_sprite_url" not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN avatar_sprite_url TEXT")

    league_cols = [row["name"] for row in conn.execute("PRAGMA table_info(leagues)").fetchall()]
    if "seasons" not in league_cols:
        conn.execute("ALTER TABLE leagues ADD COLUMN seasons TEXT NOT NULL DEFAULT ''")
    if "market_resolved" not in league_cols:
        conn.execute("ALTER TABLE leagues ADD COLUMN market_resolved INTEGER NOT NULL DEFAULT 0")
    if "scout_filter" not in league_cols:
        conn.execute("ALTER TABLE leagues ADD COLUMN scout_filter TEXT NOT NULL DEFAULT 'all'")
    if "max_members" not in league_cols:
        conn.execute("ALTER TABLE leagues ADD COLUMN max_members INTEGER NOT NULL DEFAULT 10")
    if "schedule_json" not in league_cols:
        conn.execute("ALTER TABLE leagues ADD COLUMN schedule_json TEXT NOT NULL DEFAULT '[]'")
    if "ended" not in league_cols:
        conn.execute("ALTER TABLE leagues ADD COLUMN ended INTEGER NOT NULL DEFAULT 0")

    member_cols = [row["name"] for row in conn.execute("PRAGMA table_info(league_members)").fetchall()]
    if "formation" not in member_cols:
        conn.execute("ALTER TABLE league_members ADD COLUMN formation TEXT NOT NULL DEFAULT '4-4-2'")

    roster_cols = [row["name"] for row in conn.execute("PRAGMA table_info(rosters)").fetchall()]
    if "acquired_price" not in roster_cols:
        conn.execute("ALTER TABLE rosters ADD COLUMN acquired_price REAL NOT NULL DEFAULT 0")
        # Players already in a roster from before the bidding system existed
        # were "bought" at their listed price -- keep that as their paid cost
        # so budgets stay consistent, except we can't tell which were free
        # starters vs market buys, so we treat them all as already paid; this
        # only affects pre-existing local databases from earlier versions.
        conn.execute(
            """
            UPDATE rosters SET acquired_price = (
                SELECT price FROM players WHERE players.id = rosters.player_id
            ) WHERE acquired_price = 0
            """
        )

    # One-time backfill: give every existing league member a starting lineup
    # selection equal to their current roster, if they don't have one yet,
    # so old leagues keep working under the new squad/lineup split.
    has_any_lineup = conn.execute("SELECT COUNT(*) as c FROM lineup_selections").fetchone()["c"]
    if has_any_lineup == 0:
        existing_rosters = conn.execute("SELECT COUNT(*) as c FROM rosters").fetchone()["c"]
        if existing_rosters > 0:
            conn.execute(
                "INSERT OR IGNORE INTO lineup_selections (league_id, user_id, player_id) "
                "SELECT league_id, user_id, player_id FROM rosters"
            )

    score_cols = [row["name"] for row in conn.execute("PRAGMA table_info(gameweek_scores)").fetchall()]
    if "events" not in score_cols:
        conn.execute("ALTER TABLE gameweek_scores ADD COLUMN events TEXT NOT NULL DEFAULT ''")
    for stat_col in (
        "goals", "assists", "saves", "steals", "interceptions",
        "clearances", "key_passes", "losses", "clean_sheet", "blocks",
    ):
        if stat_col not in score_cols:
            conn.execute(f"ALTER TABLE gameweek_scores ADD COLUMN {stat_col} INTEGER NOT NULL DEFAULT 0")

    market_cols = [row["name"] for row in conn.execute("PRAGMA table_info(league_market)").fetchall()]
    if "released_by_user_id" not in market_cols:
        conn.execute("ALTER TABLE league_market ADD COLUMN released_by_user_id INTEGER")

    fixture_cols = [row["name"] for row in conn.execute("PRAGMA table_info(fixtures)").fetchall()]
    if "events_json" not in fixture_cols:
        conn.execute("ALTER TABLE fixtures ADD COLUMN events_json TEXT NOT NULL DEFAULT '[]'")
    if "home_lineup_json" not in fixture_cols:
        conn.execute("ALTER TABLE fixtures ADD COLUMN home_lineup_json TEXT NOT NULL DEFAULT '[]'")
        conn.execute("ALTER TABLE fixtures ADD COLUMN away_lineup_json TEXT NOT NULL DEFAULT '[]'")
        conn.execute("ALTER TABLE fixtures ADD COLUMN home_formation TEXT NOT NULL DEFAULT '4-4-2'")
        conn.execute("ALTER TABLE fixtures ADD COLUMN away_formation TEXT NOT NULL DEFAULT '4-4-2'")

    member_cols = [row["name"] for row in conn.execute("PRAGMA table_info(league_members)").fetchall()]
    if "bonus_budget" not in member_cols:
        conn.execute("ALTER TABLE league_members ADD COLUMN bonus_budget REAL NOT NULL DEFAULT 0")

    roster_cols = [row["name"] for row in conn.execute("PRAGMA table_info(rosters)").fetchall()]
    if "clause_value" not in roster_cols:
        conn.execute("ALTER TABLE rosters ADD COLUMN clause_value REAL NOT NULL DEFAULT 0")
        # Give existing squads a sensible starting clause: 1.5x what they're
        # worth (their acquired price, or the player's base price for free
        # starters), so buyout clauses aren't just zero after upgrading.
        conn.execute(
            """
            UPDATE rosters
            SET clause_value = ROUND(
                1.5 * (
                    CASE WHEN acquired_price > 0 THEN acquired_price
                         ELSE (SELECT price FROM players WHERE players.id = rosters.player_id)
                    END
                )
            )
            WHERE clause_value = 0
            """
        )

    player_cols = [row["name"] for row in conn.execute("PRAGMA table_info(players)").fetchall()]
    if "tecnicas" not in player_cols:
        conn.execute("ALTER TABLE players ADD COLUMN tecnicas TEXT NOT NULL DEFAULT '[]'")
    if "tecnicas_por_tipo" not in player_cols:
        conn.execute("ALTER TABLE players ADD COLUMN tecnicas_por_tipo TEXT NOT NULL DEFAULT '{}'")
    if "es_scout" not in player_cols:
        conn.execute("ALTER TABLE players ADD COLUMN es_scout INTEGER NOT NULL DEFAULT 0")

    _backfill_sprites_from_seed(conn)
    _backfill_techniques_from_seed(conn)
    _backfill_technique_categories_from_seed(conn)
    _backfill_scout_flags_from_seed(conn)
    _backfill_victory_road_club_scout_fix(conn)

    # Fix sprite paths from an earlier version that pointed at /sprites/...
    # instead of Flask's actual static route /static/sprites/...
    conn.execute(
        "UPDATE players SET sprite_url = '/static' || sprite_url WHERE sprite_url LIKE '/sprites/%'"
    )

    _rescale_to_points_system(conn)
    conn.commit()


def _backfill_sprites_from_seed(conn):
    """Older installs may have NULL sprite_url for players whose names
    contain an apostrophe (e.g. O'Shea) because of a slugify bug that's
    since been fixed. Re-applies the corrected sprite_url from the bundled
    seed data for any player still missing one, without needing a full
    reseed of the database."""
    missing = conn.execute("SELECT id FROM players WHERE sprite_url IS NULL OR sprite_url = ''").fetchall()
    if not missing:
        return
    missing_ids = {row["id"] for row in missing}

    try:
        with open(SEED_PATH, encoding="utf-8") as f:
            seed_players = json.load(f)
    except FileNotFoundError:
        return

    for p in seed_players:
        if p["id"] in missing_ids and p.get("sprite_url"):
            conn.execute("UPDATE players SET sprite_url = ? WHERE id = ?", (p["sprite_url"], p["id"]))


def _backfill_techniques_from_seed(conn):
    """Older installs seeded before real hissatsu técnica data was added
    have every player's `tecnicas` at its default '[]'. Backfills the real
    move list from the bundled seed data without needing a full reseed."""
    missing = conn.execute("SELECT id FROM players WHERE tecnicas IS NULL OR tecnicas = '[]'").fetchall()
    if not missing:
        return
    missing_ids = {row["id"] for row in missing}

    try:
        with open(SEED_PATH, encoding="utf-8") as f:
            seed_players = json.load(f)
    except FileNotFoundError:
        return

    for p in seed_players:
        if p["id"] in missing_ids and p.get("tecnicas"):
            conn.execute(
                "UPDATE players SET tecnicas = ? WHERE id = ?",
                (json.dumps(p["tecnicas"], ensure_ascii=False), p["id"]),
            )


def _backfill_technique_categories_from_seed(conn):
    """Older installs seeded before técnicas were split into Tiro/Portería/
    Defensa/Regate buckets have `tecnicas_por_tipo` at its default '{}'.
    Backfills the categorized move lists from the bundled seed data,
    independent of whether `tecnicas` itself was already filled in."""
    missing = conn.execute(
        "SELECT id FROM players WHERE tecnicas_por_tipo IS NULL OR tecnicas_por_tipo = '{}'"
    ).fetchall()
    if not missing:
        return
    missing_ids = {row["id"] for row in missing}

    try:
        with open(SEED_PATH, encoding="utf-8") as f:
            seed_players = json.load(f)
    except FileNotFoundError:
        return

    for p in seed_players:
        if p["id"] in missing_ids and p.get("tecnicas_por_tipo"):
            conn.execute(
                "UPDATE players SET tecnicas_por_tipo = ? WHERE id = ?",
                (json.dumps(p["tecnicas_por_tipo"], ensure_ascii=False), p["id"]),
            )


def _backfill_scout_flags_from_seed(conn):
    """Older installs seeded before scout flags were added have every
    player's `es_scout` at its default 0. Detects this (literally no
    player anywhere is flagged as a scout, which would never happen once
    this migration has already run) and backfills from the bundled seed
    data — without needing a full reseed of the database."""
    any_scout = conn.execute("SELECT 1 FROM players WHERE es_scout = 1 LIMIT 1").fetchone()
    if any_scout:
        return

    try:
        with open(SEED_PATH, encoding="utf-8") as f:
            seed_players = json.load(f)
    except FileNotFoundError:
        return

    for p in seed_players:
        if p.get("es_scout"):
            conn.execute("UPDATE players SET es_scout = 1 WHERE id = ?", (p["id"],))


def _backfill_victory_road_club_scout_fix(conn):
    """One-time correction: many 'Inazuma Eleven: Victory Road' characters
    belong to school clubs (Judo Club, Dance Club, Baseball Club Team...)
    which the official database's 'Unaffiliated' filter does NOT count as
    scouts, even though they're just as minor/background as real scouts.
    Detects whether this fix has already run (via a known example
    character) and, if not, re-syncs es_scout from the bundled seed data
    for every Victory Road player."""
    already_fixed = conn.execute(
        "SELECT es_scout FROM players WHERE nombre = 'Wagner Waltz' AND juego = 'Inazuma Eleven: Victory Road'"
    ).fetchone()
    if already_fixed and already_fixed["es_scout"]:
        return

    try:
        with open(SEED_PATH, encoding="utf-8") as f:
            seed_players = json.load(f)
    except FileNotFoundError:
        return

    for p in seed_players:
        if p.get("juego") == "Inazuma Eleven: Victory Road" and p.get("es_scout"):
            conn.execute("UPDATE players SET es_scout = 1 WHERE id = ?", (p["id"],))


def _rescale_to_points_system(conn):
    """Older versions priced players on a 0.5-10.0 'millions' scale. This
    rescales everything to a wider, unit-less point system (roughly 3-150)
    so bids can be more granular, and recomputes player prices from their
    stored form_index with a little random jitter so values look more
    natural instead of suspiciously round. Runs only once, detected by the
    old scale's low max price."""
    max_price = conn.execute("SELECT MAX(price) as m FROM players").fetchone()["m"]
    if max_price is None or max_price >= 20:
        return  # already on the new scale (or no players yet)

    scale = 15  # roughly maps the old 0.5-10.0 range onto the new 8-150 one

    players = conn.execute("SELECT id, form_index FROM players").fetchall()
    for p in players:
        new_price = compute_price_from_form_index(p["form_index"])
        conn.execute("UPDATE players SET price = ? WHERE id = ?", (new_price, p["id"]))

    conn.execute(f"UPDATE leagues SET budget = ROUND(budget * {scale})")
    conn.execute(f"UPDATE rosters SET acquired_price = ROUND(acquired_price * {scale})")
    conn.execute(f"UPDATE bids SET amount = ROUND(amount * {scale})")
    conn.execute(f"UPDATE league_player_value SET value = ROUND(value * {scale})")
    conn.execute(f"UPDATE market_results SET amount = ROUND(amount * {scale}) WHERE amount IS NOT NULL")


def init_db():
    conn = get_connection()
    conn.executescript(SCHEMA)
    conn.commit()
    _run_migrations(conn)

    count = conn.execute("SELECT COUNT(*) as c FROM players").fetchone()["c"]
    if count == 0:
        with open(SEED_PATH, encoding="utf-8") as f:
            players = json.load(f)
        for p in players:
            p["tecnicas"] = json.dumps(p.get("tecnicas") or [], ensure_ascii=False)
            p["tecnicas_por_tipo"] = json.dumps(p.get("tecnicas_por_tipo") or {}, ensure_ascii=False)
            p["es_scout"] = 1 if p.get("es_scout") else 0
        conn.executemany(
            """
            INSERT INTO players (id, nombre, apodo, juego, arquetipo, posicion, elemento,
                potencia, control, tecnica, presion, fisico, agilidad, inteligencia, total,
                grupo_edad, anio_escolar, genero, rol, sprite_url, price, form_index, tecnicas, tecnicas_por_tipo, es_scout)
            VALUES (:id, :nombre, :apodo, :juego, :arquetipo, :posicion, :elemento,
                :potencia, :control, :tecnica, :presion, :fisico, :agilidad, :inteligencia, :total,
                :grupo_edad, :anio_escolar, :genero, :rol, :sprite_url, :price, :form_index, :tecnicas, :tecnicas_por_tipo, :es_scout)
            """,
            players,
        )
        conn.commit()
        print(f"Seeded {len(players)} players into the database.")
    conn.close()
