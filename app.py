import functools
import hashlib
import hmac
import json
import os
import random
import subprocess
from datetime import datetime

try:
    from zoneinfo import ZoneInfo
    MADRID_TZ = ZoneInfo("Europe/Madrid")
except Exception:  # pragma: no cover - extremely unlikely on a modern Python
    MADRID_TZ = None

import requests
from flask import Flask, g, jsonify, redirect, render_template, request, session, url_for, flash
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.routing import BaseConverter, ValidationError

from db import get_connection, init_db, unique_league_slug
from scoring import (
    ALL_SEASONS,
    DEFAULT_FORMATION,
    FORMATIONS,
    NPC_TEAM_NAMES,
    POSITION_LABELS,
    SEASON_GROUPS,
    adjust_player_value,
    count_missing_slots,
    formation_requirements,
    generate_balanced_roster,
    generate_league_pool,
    generate_weekly_market,
    missing_positions_message,
    season_group_label,
    season_label_es,
    simulate_fixture,
    simulate_market_player_points,
    validate_lineup,
)

app = Flask(__name__)
app.secret_key = os.environ.get(
    "SECRET_KEY",
    "inazuma-fantasy-dev-secret-change-me",  # only used for local testing; never used in production
)


class LeagueSlugConverter(BaseConverter):
    """URL converter for league routes: the URL carries the league's slug
    (e.g. 'inazuma-legends') instead of its raw numeric id, but every view
    function still receives a plain int league_id exactly as before —
    to_python() resolves slug -> id on the way in, to_url() resolves
    id -> slug on the way out (so every existing url_for(..., league_id=X)
    call and every {{ url_for(...) }} in the templates keeps working
    unchanged)."""

    def to_python(self, value):
        db = get_db()
        row = db.execute("SELECT id FROM leagues WHERE slug = ?", (value,)).fetchone()
        if row:
            return row["id"]
        # Backward compatibility: a link shared/bookmarked before this
        # change still has the raw numeric id in it — keep it working.
        if value.isdigit():
            row = db.execute("SELECT id FROM leagues WHERE id = ?", (int(value),)).fetchone()
            if row:
                return row["id"]
        raise ValidationError()

    def to_url(self, value):
        db = get_db()
        row = db.execute("SELECT slug FROM leagues WHERE id = ?", (value,)).fetchone()
        slug = row["slug"] if row and row["slug"] else str(value)
        return super().to_url(slug)


app.url_map.converters["league"] = LeagueSlugConverter


@app.template_filter("display_season")
def display_season_filter(juego):
    """Shows a player's game/season in Spanish, e.g. in the market listing."""
    return season_label_es(season_group_label(juego))


# Sentinel "user id" for CPU market offers. Real users always get a positive
# AUTOINCREMENT id, so -1 can never collide with one.
CPU_USER_ID = -1
CPU_BID_CHANCE = 0.65
# A second sentinel for the standalone weekly simulation given to unclaimed
# market players, so they build up a "points hechos" track record too.
MARKET_SIM_USER_ID = -2
MAX_LEAGUE_MEMBERS = 10


def _load_rotulos_manifest():
    """Maps a técnica's Spanish name to its 'rótulo' label graphic
    (static/rotulos/<skill_id>.webp), when we have one on file. Loaded once
    at import time from the bundled lookup table."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seed", "rotulos_manifest.json")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


ROTULOS_MANIFEST = _load_rotulos_manifest()
MAX_LEAGUE_CYCLES = 3  # a league runs for 3 full round-robin cycles (everyone plays everyone 3 times) before it ends and crowns a champion
AVATAR_RESULT_LIMIT = 60


def _search_avatar_candidates(db, season, query, limit):
    """Shared filter logic for the avatar picker: used by both the normal
    page load and the live-search JSON endpoint. Returns (total_count, rows)."""
    where = ["sprite_url IS NOT NULL"]
    params = []
    if season and season in SEASON_GROUPS:
        juegos = SEASON_GROUPS[season]
        where.append(f"juego IN ({','.join('?' for _ in juegos)})")
        params.extend(juegos)
    if query:
        where.append("nombre LIKE ?")
        params.append(f"%{query}%")
    where_sql = " AND ".join(where)

    count = db.execute(f"SELECT COUNT(*) as c FROM players WHERE {where_sql}", params).fetchone()["c"]
    rows = rows_to_list(
        db.execute(
            f"SELECT id, nombre, posicion, sprite_url, juego FROM players WHERE {where_sql} ORDER BY nombre LIMIT ?",
            params + [limit],
        ).fetchall()
    )
    return count, rows
POINTS_PER_MILLION_EUROS = 10  # 1 point = 100.000€, so 10 points = 1.000.000€


def euros_to_points(euros_millions):
    """Converts a euros-in-millions form input (what the person types) into
    the internal point value used for all budget/price/clause math."""
    return euros_millions * POINTS_PER_MILLION_EUROS


def points_to_euros_millions(points):
    """Converts an internal point value into millions of euros, for display."""
    return (points or 0) / POINTS_PER_MILLION_EUROS


def format_euros(points):
    """Formats an internal point value as a euro amount for display, e.g.
    15.2 -> '15,2M €', 80 -> '80M €', 0.3 -> '300k €'."""
    millions = points_to_euros_millions(points)
    if abs(millions) < 1:
        thousands = round(millions * 1000)
        return f"{thousands}k €"
    if millions == int(millions):
        return f"{int(millions)}M €"
    return f"{millions:.1f}".replace(".", ",") + "M €"


@app.template_filter("euros")
def euros_filter(points):
    return format_euros(points)


@app.template_filter("euromillions")
def euromillions_filter(points):
    """Raw millions-of-euros number (no 'M €' suffix), for use as a numeric
    input's value/min/placeholder attribute rather than display text."""
    millions = points_to_euros_millions(points)
    return round(millions, 2) if millions != int(millions) else int(millions)
BUDGET_BONUS_POINTS_PER_UNIT = 15  # every N fantasy points scored in a gameweek = +1 budget to spend
CLAUSE_MULTIPLIER = 1.5  # a player's default release clause = 1.5x what they're worth
CLAUSE_MIN_INCREASE = 1
CLAUSE_LEAGUE_AGE_DAYS = 7  # clauses can't be paid off until a league is at least this old

LEADERBOARD_CATEGORIES = [
    ("points", "🏆 Más puntos totales"),
    ("goals", "⚽ Goleadores"),
    ("assists", "🅰️ Asistencias"),
    ("saves", "🧤 Paradas"),
    ("steals", "🛡️ Robos de balón"),
    ("interceptions", "🎯 Intercepciones"),
    ("clearances", "🧹 Despejes"),
    ("blocks", "🚧 Bloqueos"),
    ("key_passes", "🔑 Pases clave"),
]


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------
def get_db():
    if "db" not in g:
        g.db = get_connection()
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def row_to_dict(row):
    return dict(row) if row is not None else None


def rows_to_list(rows):
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------
def login_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        user_id = session.get("user_id")
        if not user_id:
            return redirect(url_for("login"))

        # Guards against a stale session cookie pointing at a user_id that no
        # longer exists (e.g. the local .sqlite file was deleted/reset while
        # a browser was still "logged in"). Without this check, any insert
        # referencing that user_id would blow up with a FOREIGN KEY error.
        db = get_db()
        exists = db.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone()
        if not exists:
            session.clear()
            flash("Tu sesión ya no era válida (puede que se reiniciara la base de datos). Vuelve a iniciar sesión.", "error")
            return redirect(url_for("login"))

        return view(*args, **kwargs)

    return wrapped


# Set this to your own username via the ADMIN_USERNAME environment variable
# (same place as SECRET_KEY/SCHEDULER_TOKEN) to unlock the admin pages below.
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "")


def admin_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        user_id = session.get("user_id")
        if not user_id:
            return redirect(url_for("login"))
        username = session.get("username", "")
        if not ADMIN_USERNAME or username.lower() != ADMIN_USERNAME.lower():
            flash("No tienes acceso a esta página.", "error")
            return redirect(url_for("leagues"))
        return view(*args, **kwargs)

    return wrapped


@app.context_processor
def inject_user():
    is_admin = bool(ADMIN_USERNAME) and session.get("username", "").lower() == ADMIN_USERNAME.lower()
    avatar_url = None
    if session.get("user_id"):
        db = get_db()
        row = db.execute("SELECT avatar_sprite_url FROM users WHERE id = ?", (session["user_id"],)).fetchone()
        avatar_url = row["avatar_sprite_url"] if row else None
    return {
        "current_user": {
            "id": session.get("user_id"),
            "username": session.get("username"),
            "is_admin": is_admin,
            "avatar_url": avatar_url,
        }
    }


def get_league_or_404(league_id):
    db = get_db()
    return row_to_dict(db.execute("SELECT * FROM leagues WHERE id = ?", (league_id,)).fetchone())


def get_membership(league_id, user_id):
    db = get_db()
    return row_to_dict(
        db.execute(
            "SELECT * FROM league_members WHERE league_id = ? AND user_id = ?",
            (league_id, user_id),
        ).fetchone()
    )


def require_membership(league_id):
    """Returns (league, membership) or (None, None) if not found/not a member."""
    league = get_league_or_404(league_id)
    if not league:
        return None, None
    membership = get_membership(league_id, session["user_id"])
    if not membership:
        return None, None
    return league, membership


def league_seasons_list(league):
    """Returns the list of `juego` values a league is restricted to, or None
    if the league has no restriction (all seasons allowed)."""
    raw = (league["seasons"] or "").strip()
    if not raw:
        return None
    return [s for s in raw.split("|") if s]


def owned_player_ids(db, league_id):
    """All player ids currently owned by ANY member of a league."""
    rows = db.execute("SELECT DISTINCT player_id FROM rosters WHERE league_id = ?", (league_id,)).fetchall()
    return {r["player_id"] for r in rows}


def current_market_ids(db, league_id):
    rows = db.execute("SELECT player_id FROM league_market WHERE league_id = ?", (league_id,)).fetchall()
    return {r["player_id"] for r in rows}


def default_clause_value(acquired_price, base_price):
    """A newly-acquired player's starting release clause: 1.5x whatever
    they're worth (what was actually paid, or their base price if they
    came free), rounded to a whole point."""
    reference = acquired_price if acquired_price and acquired_price > 0 else base_price
    return max(1, round(CLAUSE_MULTIPLIER * reference))


def league_is_old_enough_for_clauses(league):
    """Clauses can only be paid off once a league has been running for at
    least CLAUSE_LEAGUE_AGE_DAYS, so brand new leagues get a settling-in
    period before anyone can be sniped."""
    created_at = league["created_at"]
    if not created_at:
        return True
    try:
        created = datetime.strptime(created_at.split(".")[0], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return True
    return (datetime.utcnow() - created).days >= CLAUSE_LEAGUE_AGE_DAYS


def squad_spent(db, league_id, user_id):
    row = db.execute(
        "SELECT COALESCE(SUM(acquired_price), 0) as spent FROM rosters WHERE league_id = ? AND user_id = ?",
        (league_id, user_id),
    ).fetchone()
    return row["spent"]


def pending_bids_total(db, league_id, user_id, exclude_player_id=None):
    if exclude_player_id is not None:
        row = db.execute(
            "SELECT COALESCE(SUM(amount), 0) as total FROM bids WHERE league_id = ? AND user_id = ? AND player_id != ?",
            (league_id, user_id, exclude_player_id),
        ).fetchone()
    else:
        row = db.execute(
            "SELECT COALESCE(SUM(amount), 0) as total FROM bids WHERE league_id = ? AND user_id = ?",
            (league_id, user_id),
        ).fetchone()
    return row["total"]


def maybe_add_cpu_offer(db, league_id, player_id):
    """When a player hits the market, there's a chance a 'CPU' buyer from
    outside the league puts in a sealed offer too — scaled by the player's
    base value and how well they've recently performed. Returns the offer
    amount if one was made, otherwise None."""
    if random.random() > CPU_BID_CHANCE:
        return None

    value_row = db.execute(
        "SELECT value FROM league_player_value WHERE league_id = ? AND player_id = ?", (league_id, player_id)
    ).fetchone()
    price_row = db.execute("SELECT price FROM players WHERE id = ?", (player_id,)).fetchone()
    base_value = value_row["value"] if value_row else price_row["price"]

    recent_rows = db.execute(
        "SELECT gs.points FROM gameweek_scores gs JOIN gameweeks gw ON gw.id = gs.gameweek_id "
        "WHERE gs.league_id = ? AND gs.player_id = ? ORDER BY gw.number DESC LIMIT 3",
        (league_id, player_id),
    ).fetchall()
    if recent_rows:
        recent_avg = sum(r["points"] for r in recent_rows) / len(recent_rows)
        perf_multiplier = max(0.8, min(1.4, 0.8 + (recent_avg / 15) * 0.6))
    else:
        perf_multiplier = 1.0

    offer = round(base_value * perf_multiplier * random.uniform(0.9, 1.15))
    offer = max(offer, round(base_value))  # never insults the seller below the player's own value

    db.execute(
        """
        INSERT INTO bids (league_id, user_id, player_id, amount) VALUES (?, ?, ?, ?)
        ON CONFLICT(league_id, user_id, player_id) DO UPDATE SET amount = excluded.amount
        """,
        (league_id, CPU_USER_ID, player_id, offer),
    )
    return offer


PITCH_LINE_X = {
    "home": {"GK": 6, "DF": 22, "MF": 38, "FW": 48},
    "away": {"GK": 94, "DF": 78, "MF": 62, "FW": 52},
}


def compute_match_pitch_positions(lineup_data, side):
    """Lays out a saved lineup (list of {id, nombre, posicion, sprite_url})
    onto x/y percentage positions for the match replay pitch — one column
    per position line (GK/DF/MF/FW), players spread evenly down that line,
    mirrored so home attacks rightward and away attacks leftward."""
    by_pos = {}
    for p in lineup_data:
        by_pos.setdefault(p["posicion"], []).append(p)

    positions = []
    for pos in ("GK", "DF", "MF", "FW"):
        players = by_pos.get(pos, [])
        n = len(players)
        if n == 0:
            continue
        x = PITCH_LINE_X[side][pos]
        for i, p in enumerate(players):
            y = (i + 1) / (n + 1) * 100
            positions.append(
                {
                    "id": p["id"],
                    "nombre": p["nombre"],
                    "posicion": pos,
                    "sprite_url": p["sprite_url"],
                    "x": round(x, 1),
                    "y": round(y, 1),
                }
            )
    return positions


def ensure_league_pool(db, league):
    """Every league has a fixed pool of players (generated once, at creation
    time) that its weekly market rotates through. Generates it now if it
    doesn't exist yet (brand new league, or an older league upgraded by a
    migration that somehow missed it) and returns the pool's player ids."""
    pool_ids = [
        r["player_id"]
        for r in db.execute("SELECT player_id FROM league_player_pool WHERE league_id = ?", (league["id"],)).fetchall()
    ]
    if pool_ids:
        return pool_ids

    seasons = league_seasons_list(league)
    pool_ids = generate_league_pool(db, seasons=seasons, scout_filter=league["scout_filter"])
    for pid in pool_ids:
        db.execute(
            "INSERT OR IGNORE INTO league_player_pool (league_id, player_id) VALUES (?, ?)",
            (league["id"], pid),
        )
    return pool_ids


def refresh_league_market(db, league):
    """Clears and regenerates a league's weekly market, excluding anyone
    already owned by a member, and seeds/keeps market values. Only ever
    draws from the league's own fixed player pool (see ensure_league_pool),
    so the market is a closed, rotating set rather than infinite variety."""
    db.execute("DELETE FROM league_market WHERE league_id = ?", (league["id"],))
    exclude = owned_player_ids(db, league["id"])
    seasons = league_seasons_list(league)
    pool_ids = ensure_league_pool(db, league)
    new_ids = generate_weekly_market(
        db, seasons=seasons, exclude_ids=exclude, scout_filter=league["scout_filter"], pool_ids=pool_ids
    )
    for pid in new_ids:
        db.execute(
            "INSERT OR IGNORE INTO league_market (league_id, player_id) VALUES (?, ?)",
            (league["id"], pid),
        )
        has_value = db.execute(
            "SELECT id FROM league_player_value WHERE league_id = ? AND player_id = ?",
            (league["id"], pid),
        ).fetchone()
        if not has_value:
            base_price = db.execute("SELECT price FROM players WHERE id = ?", (pid,)).fetchone()["price"]
            db.execute(
                "INSERT INTO league_player_value (league_id, player_id, value) VALUES (?, ?, ?)",
                (league["id"], pid, base_price),
            )
    # NOTE: market players intentionally do NOT get simulated points here.
    # Their "puntos hechos" should only start moving once the league has
    # actually played a real jornada (see play_gameweek_for_league), so a
    # brand new league's market correctly shows 0 for everyone until then.


def simulate_points_for_market_pool(db, league_id, gw_id):
    """Gives every player currently sitting unclaimed in the market a
    simulated performance, so their "puntos hechos" keeps moving as the
    league's real jornadas get played -- even though these players aren't
    owned by anyone and so never take part in a simulated fixture directly.

    Called once per real jornada played (from play_gameweek_for_league),
    right after that jornada's gameweek row is created, so market points
    only start accumulating once jornadas actually start being played --
    a brand new league's market correctly shows 0 "puntos hechos" until its
    first jornada. The running total lives in market_simulated_points,
    independent of the real gameweeks table; the same points are also filed
    under the just-played gameweek (via gw_id) so older per-gameweek views
    keep working."""
    pool_players = rows_to_list(
        db.execute(
            "SELECT p.* FROM league_market lm JOIN players p ON p.id = lm.player_id WHERE lm.league_id = ?",
            (league_id,),
        ).fetchall()
    )
    for p in pool_players:
        pts = simulate_market_player_points(p)
        db.execute(
            """
            INSERT INTO market_simulated_points (league_id, player_id, points) VALUES (?, ?, ?)
            ON CONFLICT(league_id, player_id) DO UPDATE SET points = points + excluded.points
            """,
            (league_id, p["id"], pts),
        )
        if gw_id is not None:
            db.execute(
                "INSERT INTO gameweek_scores (gameweek_id, league_id, user_id, player_id, points, events) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (gw_id, league_id, MARKET_SIM_USER_ID, p["id"], pts, "🎲 Rendimiento estimado en el mercado"),
            )
        value_row = db.execute(
            "SELECT value FROM league_player_value WHERE league_id = ? AND player_id = ?",
            (league_id, p["id"]),
        ).fetchone()
        current_value = value_row["value"] if value_row else p["price"]
        new_value = adjust_player_value(current_value, pts, p["price"])
        db.execute(
            """
            INSERT INTO league_player_value (league_id, player_id, value) VALUES (?, ?, ?)
            ON CONFLICT(league_id, player_id) DO UPDATE SET value = excluded.value
            """,
            (league_id, p["id"], new_value),
        )


def rotate_market_for_league(db, league_id):
    """Resolves whatever bids are currently in the league's market pool
    (awarding each player to the highest bidder) and immediately opens a
    fresh pool. This is the daily market rotation — it doesn't touch
    gameweeks/fixtures at all, so it can run any day of the week,
    independently of whether a match day is also being played.

    Returns a list of dicts (one per player that actually got sold, i.e.
    had at least one bid) with keys: player (name), team (buyer's team
    name), amount — so callers can announce who signed whom."""
    league = row_to_dict(db.execute("SELECT * FROM leagues WHERE id = ?", (league_id,)).fetchone())
    if not league:
        return []

    next_number = league["current_gameweek"] + 1
    market_ids = [
        r["player_id"]
        for r in db.execute("SELECT player_id FROM league_market WHERE league_id = ?", (league_id,)).fetchall()
    ]

    sold = []

    for pid in market_ids:
        bids = db.execute(
            "SELECT user_id, amount FROM bids WHERE league_id = ? AND player_id = ?", (league_id, pid)
        ).fetchall()
        if bids:
            top_amount = max(b["amount"] for b in bids)
            winners = [b for b in bids if b["amount"] == top_amount]
            winner = random.choice(winners)
            player_row = db.execute("SELECT nombre, price FROM players WHERE id = ?", (pid,)).fetchone()
            base_price = player_row["price"]
            db.execute(
                "INSERT OR IGNORE INTO rosters (league_id, user_id, player_id, acquired_price, clause_value) "
                "VALUES (?, ?, ?, ?, ?)",
                (league_id, winner["user_id"], pid, top_amount, default_clause_value(top_amount, base_price)),
            )
            db.execute(
                "INSERT INTO market_results (league_id, gameweek_number, player_id, winner_user_id, amount) "
                "VALUES (?, ?, ?, ?, ?)",
                (league_id, next_number, pid, winner["user_id"], top_amount),
            )
            team_row = db.execute(
                "SELECT team_name FROM league_members WHERE league_id = ? AND user_id = ?",
                (league_id, winner["user_id"]),
            ).fetchone()
            sold.append(
                {
                    "player": player_row["nombre"],
                    "team": team_row["team_name"] if team_row else "—",
                    "amount": top_amount,
                }
            )
        else:
            db.execute(
                "INSERT INTO market_results (league_id, gameweek_number, player_id, winner_user_id, amount) "
                "VALUES (?, ?, ?, NULL, NULL)",
                (league_id, next_number, pid),
            )

    db.execute("DELETE FROM bids WHERE league_id = ?", (league_id,))
    db.execute("DELETE FROM league_market WHERE league_id = ?", (league_id,))

    league = row_to_dict(db.execute("SELECT * FROM leagues WHERE id = ?", (league_id,)).fetchone())
    refresh_league_market(db, league)
    db.commit()

    sold.sort(key=lambda s: s["amount"], reverse=True)
    return sold


def play_gameweek_for_league(db, league_id):
    """Plays one gameweek for a league: pairs managers 1v1 (or vs an NPC if
    odd) following the league's fixed round-robin calendar, simulates each
    match, persists scores/values, and hands out budget bonuses. Returns
    (ok, message). Does NOT touch the market at all — market rotation is a
    fully separate, independent action now."""
    league = row_to_dict(db.execute("SELECT * FROM leagues WHERE id = ?", (league_id,)).fetchone())
    if not league:
        return False, "Liga no encontrada."

    if league["ended"]:
        champion = compute_standings(db, league_id)
        champion_name = champion[0]["team_name"] if champion else "—"
        return False, f"Esta liga ya ha terminado. Campeón: {champion_name}."

    members = rows_to_list(db.execute("SELECT * FROM league_members WHERE league_id = ?", (league_id,)).fetchall())
    if not members:
        return False, "Esta liga todavía no tiene entrenadores."

    incomplete = []
    for m in members:
        lineup = rows_to_list(
            db.execute(
                "SELECT p.* FROM lineup_selections ls JOIN players p ON p.id = ls.player_id "
                "WHERE ls.league_id = ? AND ls.user_id = ?",
                (league_id, m["user_id"]),
            ).fetchall()
        )
        missing = count_missing_slots(lineup, m["formation"] or DEFAULT_FORMATION)
        if missing:
            msg = missing_positions_message(lineup, m["formation"] or DEFAULT_FORMATION) or "alineación incompleta"
            incomplete.append(f"{m['team_name']} ({msg})")

    if incomplete:
        pass  # no longer blocks play — incomplete teams just take a scoring penalty (see below)

    next_number = league["current_gameweek"] + 1

    # Load (or lazily generate) the round-robin calendar BEFORE creating the
    # gameweek, so we can check the 3-cycle limit first and avoid creating
    # an empty gameweek nobody asked for.
    schedule = json.loads(league["schedule_json"] or "[]")
    if not schedule or not schedule[0]:
        schedule = generate_round_robin_schedule(len(members))
        db.execute("UPDATE leagues SET schedule_json = ? WHERE id = ?", (json.dumps(schedule), league_id))

    total_rounds = len(schedule)
    max_gameweeks = total_rounds * MAX_LEAGUE_CYCLES

    if next_number > max_gameweeks:
        db.execute("UPDATE leagues SET ended = 1 WHERE id = ?", (league_id,))
        db.commit()
        champion = compute_standings(db, league_id)
        champion_name = champion[0]["team_name"] if champion else "—"
        return False, f"Esta liga ha alcanzado el límite de {max_gameweeks} jornadas ({MAX_LEAGUE_CYCLES} vueltas completas). Campeón: {champion_name}."

    cur = db.execute("INSERT INTO gameweeks (league_id, number) VALUES (?, ?)", (league_id, next_number))
    gameweek_id = cur.lastrowid

    def get_lineup(user_id):
        return rows_to_list(
            db.execute(
                "SELECT p.* FROM lineup_selections ls JOIN players p ON p.id = ls.player_id "
                "WHERE ls.league_id = ? AND ls.user_id = ?",
                (league_id, user_id),
            ).fetchall()
        )

    def persist_side(lineup, user_id, player_results, missing_penalty=0):
        """Saves individual scores/value nudges for one side of a simulated
        fixture, and (for real managers) tops up their budget bonus at a
        rate of +1 point of budget for every 15 fantasy points scored.
        `missing_penalty` is a flat point deduction (already negative or
        zero) for any required lineup slots the manager left empty."""
        total_points = missing_penalty
        for p in lineup:
            r = player_results[p["id"]]
            pts, breakdown = r["points"], r["breakdown"]
            total_points += pts

            if user_id is not None:
                db.execute(
                    "INSERT INTO gameweek_scores "
                    "(gameweek_id, league_id, user_id, player_id, points, events, "
                    " goals, assists, saves, steals, interceptions, clearances, key_passes, losses, clean_sheet, blocks) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        gameweek_id, league_id, user_id, p["id"], pts, "|".join(breakdown),
                        r["goals"], r["assists"], r["saves"], r["steals"], r["interceptions"],
                        r["clearances"], r["key_passes"], r["losses"], r["clean_sheet"], r["blocks"],
                    ),
                )

            value_row = db.execute(
                "SELECT value FROM league_player_value WHERE league_id = ? AND player_id = ?",
                (league_id, p["id"]),
            ).fetchone()
            current_value = value_row["value"] if value_row else p["price"]
            new_value = adjust_player_value(current_value, pts, p["price"])
            db.execute(
                """
                INSERT INTO league_player_value (league_id, player_id, value) VALUES (?, ?, ?)
                ON CONFLICT(league_id, player_id) DO UPDATE SET value = excluded.value
                """,
                (league_id, p["id"], new_value),
            )

        if user_id is not None:
            db.execute(
                "INSERT INTO gameweek_totals (gameweek_id, league_id, user_id, total_points) VALUES (?, ?, ?, ?)",
                (gameweek_id, league_id, user_id, total_points),
            )
            budget_bonus = max(0, total_points // BUDGET_BONUS_POINTS_PER_UNIT)
            if budget_bonus:
                db.execute(
                    "UPDATE league_members SET bonus_budget = bonus_budget + ? WHERE league_id = ? AND user_id = ?",
                    (budget_bonus, league_id, user_id),
                )

        return total_points

    # Pair up managers for this gameweek following the league's fixed
    # round-robin calendar (slots assigned in join order), instead of
    # random pairing — this guarantees everyone faces everyone else
    # exactly once per cycle, same as a real league table.
    members_ordered = sorted(members, key=lambda m: m["id"])
    slot_to_member = {i + 1: m for i, m in enumerate(members_ordered)}

    round_index = (next_number - 1) % total_rounds if total_rounds else 0
    round_pairs = schedule[round_index]

    pairs = []
    for slot_a, slot_b in round_pairs:
        member_a = slot_to_member.get(slot_a) if slot_a else None
        member_b = slot_to_member.get(slot_b) if slot_b else None
        if member_a is None and member_b is None:
            continue  # neither slot is filled by a real entrenador this cycle
        if member_a is None:
            pairs.append((member_b, None))
        elif member_b is None:
            pairs.append((member_a, None))
        else:
            pairs.append((member_a, member_b))

    already_owned = owned_player_ids(db, league_id)
    seasons = league_seasons_list(league)
    league_pool_ids = ensure_league_pool(db, league)

    for home_m, away_m in pairs:
        home_lineup = get_lineup(home_m["user_id"])
        home_label = home_m["team_name"]

        if away_m is not None:
            away_lineup = get_lineup(away_m["user_id"])
            away_label = away_m["team_name"]
            away_user_id = away_m["user_id"]
        else:
            # Improvise a balanced NPC squad drawn from the league's own
            # fixed player pool (same closed set the market rotates
            # through), excluding anyone already owned, so CPU opponents
            # don't interfere with the market or introduce players from
            # outside the pool.
            npc_ids = generate_balanced_roster(
                db,
                league["budget"],
                seasons=seasons,
                exclude_ids=already_owned,
                scout_filter=league["scout_filter"],
                pool_ids=league_pool_ids,
            )
            if npc_ids:
                placeholders = ",".join("?" for _ in npc_ids)
                away_lineup = rows_to_list(
                    db.execute(f"SELECT * FROM players WHERE id IN ({placeholders})", npc_ids).fetchall()
                )
            else:
                away_lineup = []
            away_label = random.choice(NPC_TEAM_NAMES)
            away_user_id = None

        result = simulate_fixture(home_lineup, home_label, away_lineup, away_label)

        home_formation = home_m["formation"] or DEFAULT_FORMATION
        away_formation = (away_m["formation"] if away_m is not None else None) or DEFAULT_FORMATION

        home_missing = count_missing_slots(home_lineup, home_formation)
        home_penalty = -3 * home_missing
        home_total = persist_side(home_lineup, home_m["user_id"], result["home_players"], home_penalty)

        if away_m is not None:
            away_missing = count_missing_slots(away_lineup, away_formation)
            away_penalty = -3 * away_missing
        else:
            away_missing = 0
            away_penalty = 0
        away_total = persist_side(away_lineup, away_user_id, result["away_players"], away_penalty)

        timeline = result["timeline"]
        if home_missing:
            timeline = timeline + [f"⚠️ {home_label} jugó con {home_missing} hueco(s) vacío(s) en la alineación ({home_penalty} pts)"]
        if away_missing:
            timeline = timeline + [f"⚠️ {away_label} jugó con {away_missing} hueco(s) vacío(s) en la alineación ({away_penalty} pts)"]

        home_lineup_data = [
            {"id": p["id"], "nombre": p["nombre"], "posicion": p["posicion"], "sprite_url": p["sprite_url"]}
            for p in home_lineup
        ]
        away_lineup_data = [
            {"id": p["id"], "nombre": p["nombre"], "posicion": p["posicion"], "sprite_url": p["sprite_url"]}
            for p in away_lineup
        ]

        db.execute(
            """
            INSERT INTO fixtures
                (league_id, gameweek_id, home_user_id, away_user_id, home_label, away_label,
                 home_goals, away_goals, home_points, away_points, summary, events_json,
                 home_lineup_json, away_lineup_json, home_formation, away_formation)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                league_id, gameweek_id, home_m["user_id"], away_user_id, home_label, away_label,
                result["home_goals"], result["away_goals"], home_total, away_total,
                "\n".join(timeline), json.dumps(result["events"]),
                json.dumps(home_lineup_data), json.dumps(away_lineup_data), home_formation, away_formation,
            ),
        )

    db.execute("UPDATE leagues SET current_gameweek = ? WHERE id = ?", (next_number, league_id))
    if next_number >= max_gameweeks:
        db.execute("UPDATE leagues SET ended = 1 WHERE id = ?", (league_id,))

    # Now that a real jornada has actually been played, give whoever is
    # currently sitting unclaimed in the market a simulated performance for
    # it too, so their "puntos hechos" grows in step with real jornadas
    # instead of existing before any jornada has been played.
    simulate_points_for_market_pool(db, league_id, gameweek_id)

    db.commit()
    return True, next_number


def assign_starting_roster(db, league, user_id):
    """Gives a brand new member of a league a FREE, budget-balanced starting
    11 (doesn't cost any of their transfer budget), avoiding players already
    owned by other members or currently up for bidding."""
    seasons = league_seasons_list(league)
    exclude = owned_player_ids(db, league["id"]) | current_market_ids(db, league["id"])
    player_ids = generate_balanced_roster(db, league["budget"], seasons=seasons, exclude_ids=exclude, scout_filter=league["scout_filter"])
    for pid in player_ids:
        base_price = db.execute("SELECT price FROM players WHERE id = ?", (pid,)).fetchone()["price"]
        db.execute(
            "INSERT OR IGNORE INTO rosters (league_id, user_id, player_id, acquired_price, clause_value) "
            "VALUES (?, ?, ?, 0, ?)",
            (league["id"], user_id, pid, default_clause_value(0, base_price)),
        )
        db.execute(
            "INSERT OR IGNORE INTO lineup_selections (league_id, user_id, player_id) VALUES (?, ?, ?)",
            (league["id"], user_id, pid),
        )


# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("home.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if len(username) < 3 or len(password) < 4:
            flash("Usuario (mín. 3 caracteres) y contraseña (mín. 4 caracteres) requeridos.", "error")
            return render_template("register.html")

        db = get_db()
        existing = db.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
        if existing:
            flash("Ese nombre de usuario ya existe.", "error")
            return render_template("register.html")

        password_hash = generate_password_hash(password)
        cur = db.execute(
            "INSERT INTO users (username, password_hash) VALUES (?, ?)", (username, password_hash)
        )
        db.commit()
        session["user_id"] = cur.lastrowid
        session["username"] = username
        return redirect(url_for("leagues"))

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        db = get_db()
        user = db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        if not user or not check_password_hash(user["password_hash"], password):
            flash("Usuario o contraseña incorrectos.", "error")
            return render_template("login.html")

        session["user_id"] = user["id"]
        session["username"] = user["username"]
        return redirect(url_for("leagues"))

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/profile")
@login_required
def profile():
    db = get_db()
    user = row_to_dict(db.execute("SELECT * FROM users WHERE id = ?", (session["user_id"],)).fetchone())

    picker_open = request.args.get("picker") == "1"
    query = request.args.get("q", "").strip()
    season = request.args.get("season", "")

    search_results = []
    result_count = 0
    if picker_open and (query or season):
        result_count, search_results = _search_avatar_candidates(db, season, query, AVATAR_RESULT_LIMIT)

    return render_template(
        "profile.html",
        user=user,
        query=query,
        season=season,
        picker_open=picker_open,
        search_results=search_results,
        result_count=result_count,
        result_limit=AVATAR_RESULT_LIMIT,
        all_seasons=list(SEASON_GROUPS.keys()),
        season_labels_es={s: season_label_es(s) for s in SEASON_GROUPS},
        position_labels=POSITION_LABELS,
    )


@app.route("/profile/avatar-search")
@login_required
def avatar_search():
    """JSON endpoint powering the live (as-you-type) avatar search box."""
    db = get_db()
    query = request.args.get("q", "").strip()
    season = request.args.get("season", "")

    if not query and not season:
        return {"count": 0, "results": []}

    count, results = _search_avatar_candidates(db, season, query, AVATAR_RESULT_LIMIT)
    return {
        "count": count,
        "limit": AVATAR_RESULT_LIMIT,
        "results": [
            {
                "id": p["id"],
                "nombre": p["nombre"],
                "posicion": p["posicion"],
                "posicion_label": POSITION_LABELS.get(p["posicion"], p["posicion"]),
                "sprite_url": p["sprite_url"],
            }
            for p in results
        ],
    }


@app.route("/profile/set-avatar", methods=["POST"])
@login_required
def set_avatar():
    db = get_db()
    player_id = request.form.get("player_id", type=int)
    query = request.form.get("q", "")
    season = request.form.get("season", "")

    if not player_id:
        flash("Selecciona un jugador válido.", "error")
        return redirect(url_for("profile", picker=1, q=query, season=season))

    player = db.execute("SELECT nombre, sprite_url FROM players WHERE id = ?", (player_id,)).fetchone()
    if not player or not player["sprite_url"]:
        flash("Ese jugador no tiene sprite disponible.", "error")
        return redirect(url_for("profile", picker=1, q=query, season=season))

    db.execute("UPDATE users SET avatar_sprite_url = ? WHERE id = ?", (player["sprite_url"], session["user_id"]))
    db.commit()
    flash(f"¡Tu icono ahora es {player['nombre']}!", "success")
    return redirect(url_for("profile"))


@app.route("/profile/clear-avatar", methods=["POST"])
@login_required
def clear_avatar():
    db = get_db()
    db.execute("UPDATE users SET avatar_sprite_url = NULL WHERE id = ?", (session["user_id"],))
    db.commit()
    flash("Icono restablecido al de por defecto.", "success")
    return redirect(url_for("profile"))


# ---------------------------------------------------------------------------
# Leagues
# ---------------------------------------------------------------------------
def generate_round_robin_schedule(n):
    """Standard 'circle method' round-robin schedule for n slots: every slot
    faces every other slot exactly once across the cycle. If n is odd, a
    'bye' (None) slot is added so it stays fair — in this app a bye just
    means that week's opponent is an improvised NPC instead of a real slot.
    Returns a list of rounds; each round is a list of (slot_a, slot_b)
    pairs (1-indexed slot numbers, or None for a bye)."""
    if n < 1:
        return [[]]
    teams = list(range(1, n + 1))
    if n % 2 == 1:
        teams.append(None)
    total = len(teams)
    rounds = []
    for _ in range(total - 1):
        pairs = [(teams[i], teams[total - 1 - i]) for i in range(total // 2)]
        rounds.append(pairs)
        teams = [teams[0]] + [teams[-1]] + teams[1:-1]
    return rounds


def regenerate_league_schedule(db, league_id, max_members):
    """(Re)builds a league's round-robin calendar for its current max
    number of entrenadores and saves it. Call this whenever the league is
    created or its max_members changes."""
    schedule = generate_round_robin_schedule(max_members)
    db.execute(
        "UPDATE leagues SET schedule_json = ? WHERE id = ?",
        (json.dumps(schedule), league_id),
    )
    return schedule


def generate_invite_code(db):
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    while True:
        code = "".join(random.choices(alphabet, k=6))
        exists = db.execute("SELECT id FROM leagues WHERE invite_code = ?", (code,)).fetchone()
        if not exists:
            return code


@app.route("/admin/leagues")
@admin_required
def admin_leagues():
    db = get_db()
    rows = rows_to_list(
        db.execute(
            """
            SELECT l.id, l.name, l.invite_code, l.created_at, l.current_gameweek, l.budget,
                u.username AS creator_username,
                (SELECT COUNT(*) FROM league_members WHERE league_id = l.id) AS member_count
            FROM leagues l
            JOIN users u ON u.id = l.creator_id
            ORDER BY l.created_at DESC
            """
        ).fetchall()
    )
    total_users = db.execute("SELECT COUNT(*) as c FROM users").fetchone()["c"]
    return render_template(
        "admin_leagues.html",
        leagues=rows,
        total_leagues=len(rows),
        total_users=total_users,
        max_league_members=MAX_LEAGUE_MEMBERS,
    )


@app.route("/admin/leagues/<league:league_id>")
@admin_required
def admin_league_detail(league_id):
    db = get_db()
    league = row_to_dict(db.execute("SELECT * FROM leagues WHERE id = ?", (league_id,)).fetchone())
    if not league:
        flash("Esa liga no existe.", "error")
        return redirect(url_for("admin_leagues"))

    members = rows_to_list(
        db.execute(
            """
            SELECT lm.user_id, lm.team_name, u.username,
                (SELECT COUNT(*) FROM rosters r WHERE r.league_id = lm.league_id AND r.user_id = lm.user_id) AS squad_size
            FROM league_members lm
            JOIN users u ON u.id = lm.user_id
            WHERE lm.league_id = ?
            ORDER BY lm.team_name
            """,
            (league_id,),
        ).fetchall()
    )
    return render_template("admin_league_detail.html", league=league, members=members)


@app.route("/admin/leagues/<league:league_id>/max-members", methods=["POST"])
@admin_required
def admin_set_max_members(league_id):
    db = get_db()
    league = db.execute("SELECT id FROM leagues WHERE id = ?", (league_id,)).fetchone()
    if not league:
        flash("Esa liga no existe.", "error")
        return redirect(url_for("admin_leagues"))

    new_max = request.form.get("max_members", type=int)
    current_members = db.execute(
        "SELECT COUNT(*) as c FROM league_members WHERE league_id = ?", (league_id,)
    ).fetchone()["c"]

    if not new_max or new_max < 1:
        flash("Introduce un número válido (mínimo 1).", "error")
    elif new_max < current_members:
        flash(f"Esta liga ya tiene {current_members} entrenadores, no puedes poner un máximo por debajo de eso.", "error")
    else:
        db.execute("UPDATE leagues SET max_members = ? WHERE id = ?", (new_max, league_id))
        db.commit()
        flash(f"Máximo de entrenadores actualizado a {new_max}.", "success")

    return redirect(url_for("admin_league_detail", league_id=league_id))


@app.route("/admin/leagues/<league:league_id>/members/<int:user_id>")
@admin_required
def admin_member_roster(league_id, user_id):
    db = get_db()
    league = row_to_dict(db.execute("SELECT * FROM leagues WHERE id = ?", (league_id,)).fetchone())
    member = row_to_dict(
        db.execute(
            "SELECT lm.*, u.username FROM league_members lm JOIN users u ON u.id = lm.user_id WHERE lm.league_id = ? AND lm.user_id = ?",
            (league_id, user_id),
        ).fetchone()
    )
    if not league or not member:
        flash("Esa liga o ese entrenador no existen.", "error")
        return redirect(url_for("admin_leagues"))

    roster = rows_to_list(
        db.execute(
            """
            SELECT p.id, p.nombre, p.posicion, p.sprite_url, p.price, r.acquired_price, r.clause_value
            FROM rosters r JOIN players p ON p.id = r.player_id
            WHERE r.league_id = ? AND r.user_id = ?
            ORDER BY CASE p.posicion WHEN 'GK' THEN 0 WHEN 'DF' THEN 1 WHEN 'MF' THEN 2 ELSE 3 END, p.nombre
            """,
            (league_id, user_id),
        ).fetchall()
    )

    query = request.args.get("q", "").strip()
    search_results = []
    if query:
        owned_ids = {r["id"] for r in roster}
        rows = rows_to_list(
            db.execute(
                "SELECT id, nombre, posicion, sprite_url, price FROM players WHERE nombre LIKE ? ORDER BY nombre LIMIT 30",
                (f"%{query}%",),
            ).fetchall()
        )
        search_results = [r for r in rows if r["id"] not in owned_ids]

    return render_template(
        "admin_member_roster.html",
        league=league,
        member=member,
        roster=roster,
        query=query,
        search_results=search_results,
        position_labels=POSITION_LABELS,
    )


@app.route("/admin/leagues/<league:league_id>/members/<int:user_id>/add", methods=["POST"])
@admin_required
def admin_add_player(league_id, user_id):
    db = get_db()
    player_id = request.form.get("player_id", type=int)
    query = request.form.get("q", "")
    if not player_id:
        flash("Selecciona un jugador válido.", "error")
        return redirect(url_for("admin_member_roster", league_id=league_id, user_id=user_id, q=query))

    already_here = db.execute(
        "SELECT 1 FROM rosters WHERE league_id = ? AND user_id = ? AND player_id = ?",
        (league_id, user_id, player_id),
    ).fetchone()
    if already_here:
        flash("Ese jugador ya está en este equipo.", "error")
        return redirect(url_for("admin_member_roster", league_id=league_id, user_id=user_id, q=query))

    other_owner = db.execute(
        """
        SELECT lm.team_name FROM rosters r JOIN league_members lm ON lm.league_id = r.league_id AND lm.user_id = r.user_id
        WHERE r.league_id = ? AND r.player_id = ?
        """,
        (league_id, player_id),
    ).fetchone()
    if other_owner:
        flash(f"Ese jugador ya pertenece a otro equipo de esta liga ({other_owner['team_name']}). Quítalo de ahí primero.", "error")
        return redirect(url_for("admin_member_roster", league_id=league_id, user_id=user_id, q=query))

    player = db.execute("SELECT nombre, price FROM players WHERE id = ?", (player_id,)).fetchone()
    if not player:
        flash("Ese jugador no existe.", "error")
        return redirect(url_for("admin_member_roster", league_id=league_id, user_id=user_id, q=query))

    db.execute(
        "INSERT INTO rosters (league_id, user_id, player_id, acquired_price, clause_value) VALUES (?, ?, ?, 0, ?)",
        (league_id, user_id, player_id, default_clause_value(0, player["price"])),
    )
    db.commit()
    flash(f"{player['nombre']} añadido al equipo (gratis, como admin).", "success")
    return redirect(url_for("admin_member_roster", league_id=league_id, user_id=user_id))


@app.route("/admin/leagues/<league:league_id>/members/<int:user_id>/remove", methods=["POST"])
@admin_required
def admin_remove_player(league_id, user_id):
    db = get_db()
    player_id = request.form.get("player_id", type=int)
    db.execute(
        "DELETE FROM rosters WHERE league_id = ? AND user_id = ? AND player_id = ?",
        (league_id, user_id, player_id),
    )
    db.execute(
        "DELETE FROM lineup_selections WHERE league_id = ? AND user_id = ? AND player_id = ?",
        (league_id, user_id, player_id),
    )
    db.commit()
    flash("Jugador quitado del equipo.", "success")
    return redirect(url_for("admin_member_roster", league_id=league_id, user_id=user_id))


@app.route("/leagues")
@login_required
def leagues():
    db = get_db()
    rows = db.execute(
        """
        SELECT l.*, lm.team_name as my_team_name,
            (SELECT COUNT(*) FROM league_members WHERE league_id = l.id) as member_count
        FROM leagues l
        JOIN league_members lm ON lm.league_id = l.id
        WHERE lm.user_id = ?
        ORDER BY l.created_at DESC
        """,
        (session["user_id"],),
    ).fetchall()

    season_counts = {}
    raw_counts = {
        row["juego"]: row["c"]
        for row in db.execute("SELECT juego, COUNT(*) as c FROM players GROUP BY juego").fetchall()
    }
    raw_counts_exclude_scout = {
        row["juego"]: row["c"]
        for row in db.execute("SELECT juego, COUNT(*) as c FROM players WHERE es_scout = 0 GROUP BY juego").fetchall()
    }
    raw_counts_only_scout = {
        row["juego"]: row["c"]
        for row in db.execute("SELECT juego, COUNT(*) as c FROM players WHERE es_scout = 1 GROUP BY juego").fetchall()
    }
    season_counts_by_scout_filter = {"all": {}, "exclude": {}, "only": {}}
    for label, juegos in SEASON_GROUPS.items():
        season_counts[label] = sum(raw_counts.get(j, 0) for j in juegos)
        season_counts_by_scout_filter["all"][label] = season_counts[label]
        season_counts_by_scout_filter["exclude"][label] = sum(raw_counts_exclude_scout.get(j, 0) for j in juegos)
        season_counts_by_scout_filter["only"][label] = sum(raw_counts_only_scout.get(j, 0) for j in juegos)

    scout_count = db.execute("SELECT COUNT(*) as c FROM players WHERE es_scout = 1").fetchone()["c"]
    total_count = db.execute("SELECT COUNT(*) as c FROM players").fetchone()["c"]

    return render_template(
        "leagues.html",
        leagues=rows_to_list(rows),
        all_seasons=list(SEASON_GROUPS.keys()),
        season_labels_es={s: season_label_es(s) for s in SEASON_GROUPS},
        season_counts=season_counts,
        season_counts_by_scout_filter=season_counts_by_scout_filter,
        scout_count=scout_count,
        non_scout_count=total_count - scout_count,
        max_league_members=MAX_LEAGUE_MEMBERS,
    )


@app.route("/leagues/create", methods=["POST"])
@login_required
def create_league():
    name = request.form.get("name", "").strip()
    team_name = request.form.get("team_name", "").strip()
    budget_euros = request.form.get("budget", "80")
    try:
        budget = euros_to_points(float(budget_euros))
    except ValueError:
        budget = euros_to_points(80.0)

    selected_labels = [s for s in request.form.getlist("seasons") if s in SEASON_GROUPS]
    selected_seasons = [juego for label in selected_labels for juego in SEASON_GROUPS[label]]
    seasons_str = "" if not selected_labels or len(selected_labels) == len(SEASON_GROUPS) else "|".join(selected_seasons)

    scout_filter = request.form.get("scout_filter", "all")
    if scout_filter not in ("all", "exclude", "only"):
        scout_filter = "all"

    if not name or not team_name:
        flash("Nombre de liga y de equipo son obligatorios.", "error")
        return redirect(url_for("leagues"))

    db = get_db()
    code = generate_invite_code(db)
    slug = unique_league_slug(db, name)
    cur = db.execute(
        "INSERT INTO leagues (name, slug, invite_code, budget, creator_id, seasons, scout_filter) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (name, slug, code, budget, session["user_id"], seasons_str, scout_filter),
    )
    league_id = cur.lastrowid
    regenerate_league_schedule(db, league_id, 1)
    db.execute(
        "INSERT INTO league_members (league_id, user_id, team_name, formation) VALUES (?, ?, ?, ?)",
        (league_id, session["user_id"], team_name, DEFAULT_FORMATION),
    )
    league = row_to_dict(db.execute("SELECT * FROM leagues WHERE id = ?", (league_id,)).fetchone())
    refresh_league_market(db, league)
    assign_starting_roster(db, league, session["user_id"])
    db.commit()
    flash(f"¡Liga '{name}' creada! Código de invitación: {code}. Te hemos asignado un equipo inicial GRATIS y equilibrado.", "success")
    return redirect(url_for("team", league_id=league_id, new_squad=1))


@app.route("/leagues/join", methods=["POST"])
@login_required
def join_league():
    invite_code = request.form.get("invite_code", "").strip().upper()
    team_name = request.form.get("team_name", "").strip()

    if not invite_code or not team_name:
        flash("Código de invitación y nombre de equipo son obligatorios.", "error")
        return redirect(url_for("leagues"))

    db = get_db()
    league = row_to_dict(db.execute("SELECT * FROM leagues WHERE invite_code = ?", (invite_code,)).fetchone())
    if not league:
        flash("No existe ninguna liga con ese código.", "error")
        return redirect(url_for("leagues"))

    existing = get_membership(league["id"], session["user_id"])
    if existing:
        flash("Ya estás en esta liga.", "error")
        return redirect(url_for("league_detail", league_id=league["id"]))

    member_count = db.execute(
        "SELECT COUNT(*) as c FROM league_members WHERE league_id = ?", (league["id"],)
    ).fetchone()["c"]
    if member_count >= league["max_members"]:
        flash(f"Esta liga ya tiene el máximo de {league['max_members']} entrenadores.", "error")
        return redirect(url_for("leagues"))

    db.execute(
        "INSERT INTO league_members (league_id, user_id, team_name, formation) VALUES (?, ?, ?, ?)",
        (league["id"], session["user_id"], team_name, DEFAULT_FORMATION),
    )
    assign_starting_roster(db, league, session["user_id"])
    regenerate_league_schedule(db, league["id"], member_count + 1)
    db.commit()
    flash("¡Te has unido a la liga! Te hemos asignado un equipo inicial GRATIS y equilibrado.", "success")
    return redirect(url_for("team", league_id=league["id"], new_squad=1))


@app.route("/leagues/<league:league_id>")
@login_required
def league_detail(league_id):
    league, membership = require_membership(league_id)
    if not league:
        flash("No perteneces a esta liga o no existe.", "error")
        return redirect(url_for("leagues"))

    db = get_db()
    members = rows_to_list(
        db.execute(
            """
            SELECT lm.user_id, lm.team_name, u.username
            FROM league_members lm JOIN users u ON u.id = lm.user_id
            WHERE lm.league_id = ?
            """,
            (league_id,),
        ).fetchall()
    )
    squad_count = db.execute(
        "SELECT COUNT(*) as c FROM rosters WHERE league_id = ? AND user_id = ?",
        (league_id, session["user_id"]),
    ).fetchone()["c"]

    seasons = league_seasons_list(league)
    champion = None
    if league["ended"]:
        rows = compute_standings(db, league_id)
        champion = rows[0] if rows else None

    return render_template(
        "league_detail.html",
        league=league,
        members=members,
        is_creator=league["creator_id"] == session["user_id"],
        squad_count=squad_count,
        seasons_label="Todas las temporadas" if not seasons else ", ".join(sorted(set(season_label_es(season_group_label(j)) for j in seasons))),
        max_league_members=MAX_LEAGUE_MEMBERS,
        champion=champion,
    )


@app.route("/leagues/<league:league_id>/discord-webhook", methods=["POST"])
@login_required
def set_discord_webhook(league_id):
    league = get_league_or_404(league_id)
    if not league:
        flash("Liga no encontrada.", "error")
        return redirect(url_for("leagues"))
    if league["creator_id"] != session["user_id"]:
        flash("Solo el creador de la liga puede configurar el webhook de Discord.", "error")
        return redirect(url_for("league_detail", league_id=league_id))

    webhook = request.form.get("discord_webhook", "").strip()
    if webhook and not webhook.startswith("https://discord.com/api/webhooks/") and not webhook.startswith("https://discordapp.com/api/webhooks/"):
        flash("Eso no parece una URL de webhook de Discord válida (debe empezar por https://discord.com/api/webhooks/...).", "error")
        return redirect(url_for("league_detail", league_id=league_id))

    db = get_db()
    db.execute("UPDATE leagues SET discord_webhook = ? WHERE id = ?", (webhook or None, league_id))
    db.commit()
    flash("¡Webhook de Discord guardado! A partir de ahora los avisos de esta liga llegarán a ese canal." if webhook else "Webhook de Discord desactivado para esta liga.", "success")
    return redirect(url_for("league_detail", league_id=league_id))


@app.route("/leagues/<league:league_id>/delete", methods=["POST"])
@login_required
def delete_league(league_id):
    league = get_league_or_404(league_id)
    if not league:
        flash("Liga no encontrada.", "error")
        return redirect(url_for("leagues"))
    if league["creator_id"] != session["user_id"]:
        flash("Solo el creador de la liga puede eliminarla.", "error")
        return redirect(url_for("league_detail", league_id=league_id))

    confirm_name = request.form.get("confirm_name", "").strip()
    if confirm_name != league["name"]:
        flash("El nombre no coincide, así que no se ha eliminado nada. Escribe el nombre exacto de la liga para confirmar.", "error")
        return redirect(url_for("league_detail", league_id=league_id))

    db = get_db()
    gameweek_ids = [
        r["id"] for r in db.execute("SELECT id FROM gameweeks WHERE league_id = ?", (league_id,)).fetchall()
    ]
    if gameweek_ids:
        placeholders = ",".join("?" for _ in gameweek_ids)
        db.execute(f"DELETE FROM gameweek_scores WHERE gameweek_id IN ({placeholders})", gameweek_ids)
        db.execute(f"DELETE FROM gameweek_totals WHERE gameweek_id IN ({placeholders})", gameweek_ids)

    for table in (
        "gameweeks",
        "market_results",
        "league_player_value",
        "bids",
        "league_market",
        "lineup_selections",
        "rosters",
        "league_members",
    ):
        db.execute(f"DELETE FROM {table} WHERE league_id = ?", (league_id,))

    db.execute("DELETE FROM leagues WHERE id = ?", (league_id,))
    db.commit()
    flash(f"La liga '{league['name']}' se ha eliminado junto con todos sus datos.", "success")
    return redirect(url_for("leagues"))


# ---------------------------------------------------------------------------
# Weekly market & bidding
# ---------------------------------------------------------------------------
@app.route("/leagues/<league:league_id>/market")
@login_required
def market(league_id):
    league, membership = require_membership(league_id)
    if not league:
        flash("No perteneces a esta liga o no existe.", "error")
        return redirect(url_for("leagues"))

    db = get_db()

    posicion = request.args.get("posicion", "").strip()
    arquetipo = request.args.get("arquetipo", "").strip()
    sort = request.args.get("sort", "posicion")

    where = ["lm.league_id = ?"]
    params = [league_id]
    if posicion:
        where.append("p.posicion = ?")
        params.append(posicion)
    if arquetipo:
        where.append("p.arquetipo = ?")
        params.append(arquetipo)
    where_sql = " AND ".join(where)

    sort_map = {
        "posicion": "p.posicion, p.nombre",
        "valor_desc": "COALESCE(lpv.value, p.price) DESC",
        "valor_asc": "COALESCE(lpv.value, p.price) ASC",
        "puntos_desc": "total_points DESC",
        "nombre": "p.nombre ASC",
    }
    order_by = sort_map.get(sort, sort_map["posicion"])

    players = rows_to_list(
        db.execute(
            f"""
            SELECT p.*, lm.released_by_user_id, lpv.value as market_value,
                   seller.team_name as seller_team_name,
                   COALESCE(msp.points, 0) as total_points,
                   EXISTS(SELECT 1 FROM bids cb WHERE cb.league_id = lm.league_id AND cb.player_id = p.id
                          AND cb.user_id = ?) as has_cpu_offer,
                   (SELECT COUNT(*) FROM bids bc WHERE bc.league_id = lm.league_id AND bc.player_id = p.id) as bid_count
            FROM league_market lm
            JOIN players p ON p.id = lm.player_id
            LEFT JOIN league_player_value lpv ON lpv.league_id = lm.league_id AND lpv.player_id = lm.player_id
            LEFT JOIN league_members seller ON seller.league_id = lm.league_id AND seller.user_id = lm.released_by_user_id
            LEFT JOIN market_simulated_points msp ON msp.league_id = lm.league_id AND msp.player_id = lm.player_id
            WHERE {where_sql}
            ORDER BY {order_by}
            """,
            [CPU_USER_ID] + params,
        ).fetchall()
    )

    my_bids = {
        r["player_id"]: r["amount"]
        for r in db.execute(
            "SELECT player_id, amount FROM bids WHERE league_id = ? AND user_id = ?",
            (league_id, session["user_id"]),
        ).fetchall()
    }
    for p in players:
        p["my_bid"] = my_bids.get(p["id"])
        p["min_bid"] = round(p["market_value"] or p["price"])

    spent = squad_spent(db, league_id, session["user_id"])
    committed = pending_bids_total(db, league_id, session["user_id"])
    bonus_budget = membership["bonus_budget"] or 0
    total_budget = league["budget"] + bonus_budget
    available = round(total_budget - spent - committed)
    # Percentages for the budget bar, capped so spent+committed never
    # visually overflows past 100% even in edge cases (e.g. a negative
    # available balance from a clause payment right at the wire).
    spent_pct = round(min(100, spent / total_budget * 100), 1) if total_budget > 0 else 0
    committed_pct = round(min(100 - spent_pct, committed / total_budget * 100), 1) if total_budget > 0 else 0

    arquetipos = [
        r["arquetipo"]
        for r in db.execute("SELECT DISTINCT arquetipo FROM players WHERE arquetipo != 'Unknown' ORDER BY arquetipo").fetchall()
    ]

    return render_template(
        "market.html",
        league=league,
        players=players,
        position_labels=POSITION_LABELS,
        spent=round(spent),
        committed=round(committed),
        available=available,
        total_budget=round(total_budget),
        bonus_budget=round(bonus_budget),
        spent_pct=spent_pct,
        committed_pct=committed_pct,
        market_resolved=bool(league["market_resolved"]),
        posicion=posicion,
        arquetipo=arquetipo,
        sort=sort,
        arquetipos=arquetipos,
    )


@app.route("/leagues/<league:league_id>/players/<int:player_id>/detail")
@login_required
def player_detail_json(league_id, player_id):
    """JSON detail for the player-card popup: base stats, points per jornada
    played in this league (whether the player was owned or sitting unclaimed
    in the market that week), and the resulting market-value trajectory."""
    league, membership = require_membership(league_id)
    if not league:
        return jsonify({"error": "No perteneces a esta liga."}), 403

    db = get_db()
    player = db.execute("SELECT * FROM players WHERE id = ?", (player_id,)).fetchone()
    if not player:
        return jsonify({"error": "Jugador no encontrado."}), 404
    player = row_to_dict(player)

    value_row = db.execute(
        "SELECT value FROM league_player_value WHERE league_id = ? AND player_id = ?",
        (league_id, player_id),
    ).fetchone()
    current_value_points = round(value_row["value"] if value_row else player["price"])

    score_rows = rows_to_list(
        db.execute(
            """
            SELECT gw.number AS gameweek, SUM(gs.points) AS points
            FROM gameweek_scores gs
            JOIN gameweeks gw ON gw.id = gs.gameweek_id
            WHERE gs.league_id = ? AND gs.player_id = ?
            GROUP BY gw.number
            ORDER BY gw.number
            """,
            (league_id, player_id),
        ).fetchall()
    )
    points_by_gw = {r["gameweek"]: r["points"] for r in score_rows}

    # Replay the same value-adjustment formula used live, jornada by
    # jornada, to reconstruct how this player's value moved over time —
    # there's no separate history table, but the formula is deterministic
    # so replaying it from the base price reproduces the real trajectory.
    base_price = player["price"]
    value = base_price
    value_history = []
    for gw in range(1, (league["current_gameweek"] or 0) + 1):
        pts = points_by_gw.get(gw)
        if pts is not None:
            value = adjust_player_value(value, pts, base_price)
        value_history.append(
            {
                "gameweek": gw,
                "value_millions": round(points_to_euros_millions(value), 2),
                "value_label": format_euros(value),
            }
        )

    return jsonify(
        {
            "id": player["id"],
            "nombre": player["nombre"],
            "posicion": player["posicion"],
            "posicion_label": POSITION_LABELS.get(player["posicion"], player["posicion"]),
            "elemento": player["elemento"],
            "arquetipo": player["arquetipo"] if player["arquetipo"] != "Unknown" else None,
            "sprite_url": player["sprite_url"],
            "juego": season_label_es(season_group_label(player["juego"])),
            "stats": {
                "Potencia": player["potencia"],
                "Control": player["control"],
                "Técnica": player["tecnica"],
                "Presión": player["presion"],
                "Físico": player["fisico"],
                "Agilidad": player["agilidad"],
                "Inteligencia": player["inteligencia"],
            },
            "total": player["total"],
            "current_value_label": format_euros(current_value_points),
            "total_points": sum(points_by_gw.values()) if points_by_gw else 0,
            "points_history": [{"gameweek": gw, "points": points_by_gw[gw]} for gw in sorted(points_by_gw)],
            "value_history": value_history,
        }
    )


@app.route("/leagues/<league:league_id>/market/bid/<int:player_id>", methods=["POST"])
@login_required
def place_bid(league_id, player_id):
    def market_redirect():
        return redirect(
            url_for(
                "market",
                league_id=league_id,
                posicion=request.form.get("posicion", ""),
                arquetipo=request.form.get("arquetipo", ""),
                sort=request.form.get("sort", ""),
            )
        )

    league, membership = require_membership(league_id)
    if not league:
        flash("No perteneces a esta liga o no existe.", "error")
        return redirect(url_for("leagues"))

    db = get_db()
    in_market = db.execute(
        "SELECT id FROM league_market WHERE league_id = ? AND player_id = ?", (league_id, player_id)
    ).fetchone()
    if not in_market:
        flash("Ese personaje ya no está disponible en el mercado de esta semana.", "error")
        return market_redirect()

    try:
        amount = round(euros_to_points(float(request.form.get("amount", "0").replace(",", "."))))
    except ValueError:
        amount = 0

    if amount <= 0:
        flash("La puja debe ser mayor que 0.", "error")
        return market_redirect()

    value_row = db.execute(
        "SELECT value FROM league_player_value WHERE league_id = ? AND player_id = ?", (league_id, player_id)
    ).fetchone()
    price_row = db.execute("SELECT price FROM players WHERE id = ?", (player_id,)).fetchone()
    min_bid = round(value_row["value"] if value_row else price_row["price"])
    if amount < min_bid:
        flash(f"La puja mínima para este jugador es {format_euros(min_bid)} (su valor actual).", "error")
        return market_redirect()

    spent = squad_spent(db, league_id, session["user_id"])
    committed_other = pending_bids_total(db, league_id, session["user_id"], exclude_player_id=player_id)
    available = league["budget"] + (membership["bonus_budget"] or 0) - spent - committed_other
    if amount > available + 1e-9:
        flash(f"No puedes pujar {format_euros(amount)}: tu presupuesto disponible es {format_euros(available)}.", "error")
        return market_redirect()

    db.execute(
        """
        INSERT INTO bids (league_id, user_id, player_id, amount) VALUES (?, ?, ?, ?)
        ON CONFLICT(league_id, user_id, player_id) DO UPDATE SET amount = excluded.amount
        """,
        (league_id, session["user_id"], player_id, amount),
    )
    db.commit()
    flash(f"Puja de {amount}M registrada. ¡Se revelará el ganador al cerrar el mercado!", "success")
    return market_redirect()


@app.route("/leagues/<league:league_id>/market/bid/<int:player_id>/cancel", methods=["POST"])
@login_required
def cancel_bid(league_id, player_id):
    league, membership = require_membership(league_id)
    if not league:
        flash("No perteneces a esta liga o no existe.", "error")
        return redirect(url_for("leagues"))

    db = get_db()
    db.execute(
        "DELETE FROM bids WHERE league_id = ? AND user_id = ? AND player_id = ?",
        (league_id, session["user_id"], player_id),
    )
    db.commit()
    return redirect(
        url_for(
            "market",
            league_id=league_id,
            posicion=request.form.get("posicion", ""),
            arquetipo=request.form.get("arquetipo", ""),
            sort=request.form.get("sort", ""),
        )
    )

# ---------------------------------------------------------------------------
# Squad, formation & starting lineup
# ---------------------------------------------------------------------------
@app.route("/leagues/<league:league_id>/team/<int:user_id>")
@login_required
def view_member_team(league_id, user_id):
    """Read-only view of another manager's squad/lineup in this league —
    reached by clicking a team name in the standings."""
    league, membership = require_membership(league_id)
    if not league:
        flash("No perteneces a esta liga o no existe.", "error")
        return redirect(url_for("leagues"))

    if user_id == session["user_id"]:
        return redirect(url_for("team", league_id=league_id))

    target_membership = get_membership(league_id, user_id)
    if not target_membership:
        flash("Ese entrenador no pertenece a esta liga.", "error")
        return redirect(url_for("standings", league_id=league_id))

    db = get_db()
    squad = rows_to_list(
        db.execute(
            "SELECT p.*, r.acquired_price, r.clause_value FROM rosters r JOIN players p ON p.id = r.player_id "
            "WHERE r.league_id = ? AND r.user_id = ? ORDER BY p.posicion, p.nombre",
            (league_id, user_id),
        ).fetchall()
    )
    lineup_ids = {
        r["player_id"]
        for r in db.execute(
            "SELECT player_id FROM lineup_selections WHERE league_id = ? AND user_id = ?",
            (league_id, user_id),
        ).fetchall()
    }
    for p in squad:
        p["in_lineup"] = p["id"] in lineup_ids

    formation = target_membership["formation"] or DEFAULT_FORMATION
    lineup_players = [p for p in squad if p["in_lineup"]]
    requirements = formation_requirements(formation)

    pitch_rows = []
    for pos in ("FW", "MF", "DF", "GK"):  # top of the pitch to bottom
        needed = requirements.get(pos, 0)
        if needed <= 0:
            continue
        current = [p for p in lineup_players if p["posicion"] == pos]
        slots = current[:needed] + [None] * max(0, needed - len(current))
        pitch_rows.append({"position": pos, "slots": slots})

    spent = sum(p["acquired_price"] for p in squad)
    can_buy_clause = league_is_old_enough_for_clauses(league)

    return render_template(
        "team_view.html",
        league=league,
        team_name=target_membership["team_name"],
        username=db.execute("SELECT username FROM users WHERE id = ?", (user_id,)).fetchone()["username"],
        squad=squad,
        position_labels=POSITION_LABELS,
        current_formation=formation,
        pitch_rows=pitch_rows,
        spent=round(spent),
        can_buy_clause=can_buy_clause,
        clause_age_days=CLAUSE_LEAGUE_AGE_DAYS,
    )


@app.route("/leagues/<league:league_id>/team")
@login_required
def team(league_id):
    league, membership = require_membership(league_id)
    if not league:
        flash("No perteneces a esta liga o no existe.", "error")
        return redirect(url_for("leagues"))

    db = get_db()
    squad = rows_to_list(
        db.execute(
            "SELECT p.*, r.acquired_price, r.clause_value FROM rosters r JOIN players p ON p.id = r.player_id "
            "WHERE r.league_id = ? AND r.user_id = ? ORDER BY p.posicion, p.nombre",
            (league_id, session["user_id"]),
        ).fetchall()
    )
    lineup_ids = {
        r["player_id"]
        for r in db.execute(
            "SELECT player_id FROM lineup_selections WHERE league_id = ? AND user_id = ?",
            (league_id, session["user_id"]),
        ).fetchall()
    }
    for p in squad:
        p["in_lineup"] = p["id"] in lineup_ids

    # Per-gameweek points history for each squad player, for the hover
    # tooltip on their card (regardless of who fielded them that week).
    history_rows = rows_to_list(
        db.execute(
            "SELECT gs.player_id, gw.number, gs.points FROM gameweek_scores gs "
            "JOIN gameweeks gw ON gw.id = gs.gameweek_id "
            "WHERE gs.league_id = ? ORDER BY gw.number",
            (league_id,),
        ).fetchall()
    )
    history_by_player = {}
    for row in history_rows:
        history_by_player.setdefault(row["player_id"], []).append(
            {"number": row["number"], "points": row["points"]}
        )
    for p in squad:
        p["history"] = history_by_player.get(p["id"], [])

    formation = membership["formation"] or DEFAULT_FORMATION
    lineup_players = [p for p in squad if p["in_lineup"]]
    requirements = formation_requirements(formation)
    total_needed = sum(requirements.values())
    is_valid, error = validate_lineup(lineup_players, formation) if len(lineup_players) else (False, None)
    missing_msg = missing_positions_message(lineup_players, formation)

    spent = squad_spent(db, league_id, session["user_id"])
    bonus_budget = membership["bonus_budget"] or 0

    pitch_rows = []
    for pos in ("FW", "MF", "DF", "GK"):  # top of the pitch to bottom
        needed = requirements.get(pos, 0)
        if needed <= 0:
            continue
        current = [p for p in lineup_players if p["posicion"] == pos]
        slots = current[:needed] + [None] * max(0, needed - len(current))
        pitch_rows.append({"position": pos, "slots": slots})

    return render_template(
        "team.html",
        league=league,
        squad=squad,
        position_labels=POSITION_LABELS,
        formations=FORMATIONS,
        current_formation=formation,
        requirements=requirements,
        total_needed=total_needed,
        lineup_count=len(lineup_players),
        is_valid=is_valid and len(lineup_players) == total_needed,
        missing_msg=missing_msg,
        show_new_squad_popup=request.args.get("new_squad") == "1",
        spent=round(spent),
        bonus_budget=round(bonus_budget),
        remaining=round(league["budget"] + bonus_budget - spent),
        pitch_rows=pitch_rows,
    )


@app.route("/leagues/<league:league_id>/team/save", methods=["POST"])
@login_required
def save_team(league_id):
    league, membership = require_membership(league_id)
    if not league:
        flash("No perteneces a esta liga o no existe.", "error")
        return redirect(url_for("leagues"))

    db = get_db()
    formation = request.form.get("formation", DEFAULT_FORMATION)
    if formation not in FORMATIONS:
        formation = DEFAULT_FORMATION

    selected_ids = [int(pid) for pid in request.form.getlist("player_ids")]
    full_squad = rows_to_list(
        db.execute(
            """
            SELECT p.* FROM rosters r JOIN players p ON p.id = r.player_id
            WHERE r.league_id = ? AND r.user_id = ?
            """,
            (league_id, session["user_id"]),
        ).fetchall()
    )
    squad_ids = {p["id"] for p in full_squad}
    invalid = [pid for pid in selected_ids if pid not in squad_ids]
    if invalid:
        flash("Solo puedes alinear jugadores de tu propia plantilla.", "error")
        return redirect(url_for("team", league_id=league_id))

    selected_players = rows_to_list(
        db.execute(
            f"SELECT * FROM players WHERE id IN ({','.join('?' for _ in selected_ids)})", selected_ids
        ).fetchall()
    ) if selected_ids else []

    # Formation changes are allowed even with an incomplete squad for it —
    # empty required slots just stay empty (❓ on the pitch) until the
    # manager fills them, instead of blocking the save entirely. If a
    # position ends up with MORE checked players than the new formation
    # needs (e.g. switching from a formation with more DF slots), the
    # extras simply go back to the bench rather than erroring out.
    requirements = formation_requirements(formation)
    filled_counts = {}
    kept_ids = []
    by_id = {p["id"]: p for p in selected_players}
    for pid in selected_ids:
        player = by_id.get(pid)
        if not player:
            continue
        pos = player["posicion"]
        needed = requirements.get(pos, 0)
        if filled_counts.get(pos, 0) < needed:
            kept_ids.append(pid)
            filled_counts[pos] = filled_counts.get(pos, 0) + 1

    # Auto-fill any slot still short with the best available bench player
    # of that position from the rest of the squad, if there's one to use.
    kept_ids_set = set(kept_ids)
    bench_by_position = {}
    for p in full_squad:
        if p["id"] not in kept_ids_set:
            bench_by_position.setdefault(p["posicion"], []).append(p)
    for pos in bench_by_position:
        bench_by_position[pos].sort(key=lambda p: p["price"], reverse=True)

    for pos, needed in requirements.items():
        while filled_counts.get(pos, 0) < needed and bench_by_position.get(pos):
            reserve = bench_by_position[pos].pop(0)
            kept_ids.append(reserve["id"])
            filled_counts[pos] = filled_counts.get(pos, 0) + 1

    db.execute(
        "UPDATE league_members SET formation = ? WHERE league_id = ? AND user_id = ?",
        (formation, league_id, session["user_id"]),
    )
    db.execute("DELETE FROM lineup_selections WHERE league_id = ? AND user_id = ?", (league_id, session["user_id"]))
    for pid in kept_ids:
        db.execute(
            "INSERT INTO lineup_selections (league_id, user_id, player_id) VALUES (?, ?, ?)",
            (league_id, session["user_id"], pid),
        )
    db.commit()

    missing = missing_positions_message(rows_to_list(
        db.execute(f"SELECT * FROM players WHERE id IN ({','.join('?' for _ in kept_ids)})", kept_ids).fetchall()
    ) if kept_ids else [], formation)
    if missing:
        flash(f"Alineación guardada, pero incompleta para {formation}. {missing}.", "error")
    else:
        flash("¡Alineación guardada!", "success")
    return redirect(url_for("team", league_id=league_id))


@app.route("/leagues/<league:league_id>/team/drop/<int:player_id>", methods=["POST"])
@login_required
def drop_player(league_id, player_id):
    league, membership = require_membership(league_id)
    if not league:
        flash("No perteneces a esta liga o no existe.", "error")
        return redirect(url_for("leagues"))

    db = get_db()
    owned = db.execute(
        "SELECT 1 FROM rosters WHERE league_id = ? AND user_id = ? AND player_id = ?",
        (league_id, session["user_id"], player_id),
    ).fetchone()
    if not owned:
        flash("Ese jugador no está en tu plantilla.", "error")
        return redirect(url_for("team", league_id=league_id))

    player = row_to_dict(db.execute("SELECT nombre FROM players WHERE id = ?", (player_id,)).fetchone())

    db.execute(
        "DELETE FROM rosters WHERE league_id = ? AND user_id = ? AND player_id = ?",
        (league_id, session["user_id"], player_id),
    )
    db.execute(
        "DELETE FROM lineup_selections WHERE league_id = ? AND user_id = ? AND player_id = ?",
        (league_id, session["user_id"], player_id),
    )

    # Put the released player straight into this week's open market so any
    # other manager in the league can bid on them right away.
    already_in_market = db.execute(
        "SELECT 1 FROM league_market WHERE league_id = ? AND player_id = ?", (league_id, player_id)
    ).fetchone()
    if not already_in_market:
        db.execute(
            "INSERT INTO league_market (league_id, player_id, released_by_user_id) VALUES (?, ?, ?)",
            (league_id, player_id, session["user_id"]),
        )
        has_value = db.execute(
            "SELECT 1 FROM league_player_value WHERE league_id = ? AND player_id = ?", (league_id, player_id)
        ).fetchone()
        if not has_value:
            base_price = db.execute("SELECT price FROM players WHERE id = ?", (player_id,)).fetchone()["price"]
            db.execute(
                "INSERT INTO league_player_value (league_id, player_id, value) VALUES (?, ?, ?)",
                (league_id, player_id, base_price),
            )

    cpu_offer = maybe_add_cpu_offer(db, league_id, player_id)

    # If the weekly market had already been closed, this late arrival needs
    # the creator to resolve the market again before advancing, so its bids
    # (including any CPU offer) don't get silently wiped by the next cycle.
    db.execute("UPDATE leagues SET market_resolved = 0 WHERE id = ?", (league_id,))

    db.commit()
    name = player["nombre"] if player else "El jugador"
    flash(f"{name} ha vuelto al mercado — cualquiera de la liga puede pujar por él ahora.", "success")
    if cpu_offer:
        flash(f"📡 Un comprador de fuera de la liga (CPU) ha hecho una oferta sellada por {name}. ¡Puja para no perderlo!", "success")
    return redirect(url_for("team", league_id=league_id))


@app.route("/leagues/<league:league_id>/team/increase-clause/<int:player_id>", methods=["POST"])
@login_required
def increase_clause(league_id, player_id):
    """The owner pays extra budget to raise their own player's release
    clause, making them more expensive (and harder) for a rival to snipe."""
    league, membership = require_membership(league_id)
    if not league:
        flash("No perteneces a esta liga o no existe.", "error")
        return redirect(url_for("leagues"))

    db = get_db()
    roster_row = row_to_dict(
        db.execute(
            "SELECT * FROM rosters WHERE league_id = ? AND user_id = ? AND player_id = ?",
            (league_id, session["user_id"], player_id),
        ).fetchone()
    )
    if not roster_row:
        flash("Ese jugador no está en tu plantilla.", "error")
        return redirect(url_for("team", league_id=league_id))

    try:
        amount = round(euros_to_points(float(request.form.get("amount", "0").replace(",", "."))))
    except ValueError:
        amount = 0
    if amount < CLAUSE_MIN_INCREASE:
        flash(f"Introduce al menos {format_euros(CLAUSE_MIN_INCREASE)} para subir la cláusula.", "error")
        return redirect(url_for("team", league_id=league_id))

    spent = squad_spent(db, league_id, session["user_id"])
    committed = pending_bids_total(db, league_id, session["user_id"])
    available = league["budget"] + (membership["bonus_budget"] or 0) - spent - committed
    if amount > available + 1e-9:
        flash(f"No tienes suficiente presupuesto: solo tienes {format_euros(available)} disponible.", "error")
        return redirect(url_for("team", league_id=league_id))

    player = row_to_dict(db.execute("SELECT nombre FROM players WHERE id = ?", (player_id,)).fetchone())
    new_clause = roster_row["clause_value"] + amount
    db.execute(
        "UPDATE rosters SET clause_value = ?, acquired_price = acquired_price + ? "
        "WHERE league_id = ? AND user_id = ? AND player_id = ?",
        (new_clause, amount, league_id, session["user_id"], player_id),
    )
    db.commit()
    flash(f"Cláusula de {player['nombre']} subida a {round(new_clause)}.", "success")
    return redirect(url_for("team", league_id=league_id))


@app.route("/leagues/<league:league_id>/team/buy-clause/<int:player_id>", methods=["POST"])
@login_required
def buy_clause(league_id, player_id):
    """Pays another manager's player's release clause, instantly taking
    them onto your own squad. Only allowed once the league is old enough."""
    league, membership = require_membership(league_id)
    if not league:
        flash("No perteneces a esta liga o no existe.", "error")
        return redirect(url_for("leagues"))

    if not league_is_old_enough_for_clauses(league):
        flash(f"Las cláusulas no se pueden pagar hasta que la liga lleve al menos {CLAUSE_LEAGUE_AGE_DAYS} días creada.", "error")
        return redirect(url_for("leagues"))

    db = get_db()
    roster_row = row_to_dict(
        db.execute(
            "SELECT * FROM rosters WHERE league_id = ? AND player_id = ?", (league_id, player_id)
        ).fetchone()
    )
    if not roster_row:
        flash("Ese jugador no pertenece a ningún equipo de esta liga ahora mismo.", "error")
        return redirect(url_for("standings", league_id=league_id))
    if roster_row["user_id"] == session["user_id"]:
        flash("Ya tienes a ese jugador en tu plantilla.", "error")
        return redirect(url_for("team", league_id=league_id))
    if roster_row["user_id"] < 0:
        flash("Ese jugador no pertenece a ningún entrenador real.", "error")
        return redirect(url_for("standings", league_id=league_id))

    clause_value = roster_row["clause_value"]
    spent = squad_spent(db, league_id, session["user_id"])
    committed = pending_bids_total(db, league_id, session["user_id"])
    available = league["budget"] + (membership["bonus_budget"] or 0) - spent - committed
    if clause_value > available + 1e-9:
        flash(f"No tienes suficiente presupuesto para pagar la cláusula ({format_euros(clause_value)}); solo tienes {format_euros(available)}.", "error")
        return redirect(url_for("standings", league_id=league_id))

    player = row_to_dict(db.execute("SELECT nombre, price FROM players WHERE id = ?", (player_id,)).fetchone())
    old_user_id = roster_row["user_id"]
    old_team = get_membership(league_id, old_user_id)

    db.execute(
        "DELETE FROM rosters WHERE league_id = ? AND user_id = ? AND player_id = ?",
        (league_id, old_user_id, player_id),
    )
    db.execute(
        "DELETE FROM lineup_selections WHERE league_id = ? AND user_id = ? AND player_id = ?",
        (league_id, old_user_id, player_id),
    )
    db.execute(
        "INSERT INTO rosters (league_id, user_id, player_id, acquired_price, clause_value) VALUES (?, ?, ?, ?, ?)",
        (league_id, session["user_id"], player_id, clause_value, default_clause_value(clause_value, player["price"])),
    )
    # Also remove it from the open market/bids if it happened to be listed there.
    db.execute("DELETE FROM league_market WHERE league_id = ? AND player_id = ?", (league_id, player_id))
    db.execute("DELETE FROM bids WHERE league_id = ? AND player_id = ?", (league_id, player_id))
    db.commit()

    old_team_name = old_team["team_name"] if old_team else "otro equipo"
    flash(
        f"¡Le has pagado la cláusula de {round(clause_value)} a {player['nombre']} y se lo has robado a {old_team_name}!",
        "success",
    )
    return redirect(url_for("team", league_id=league_id))


# ---------------------------------------------------------------------------
# Standings & gameweeks
# ---------------------------------------------------------------------------
def compute_standings(db, league_id):
    """Simple fantasy-points ranking: total points accumulated across all
    gameweeks played so far, highest first."""
    rows = rows_to_list(
        db.execute(
            """
            SELECT lm.user_id, lm.team_name, u.username, u.avatar_sprite_url,
                COALESCE(SUM(gt.total_points), 0) as total_points
            FROM league_members lm
            JOIN users u ON u.id = lm.user_id
            LEFT JOIN gameweek_totals gt ON gt.league_id = lm.league_id AND gt.user_id = lm.user_id
            WHERE lm.league_id = ?
            GROUP BY lm.user_id
            ORDER BY total_points DESC
            """,
            (league_id,),
        ).fetchall()
    )
    return rows


# ---------------------------------------------------------------------------
# Standings & gameweeks
# ---------------------------------------------------------------------------
@app.route("/leagues/<league:league_id>/standings")
@login_required
def standings(league_id):
    league, membership = require_membership(league_id)
    if not league:
        flash("No perteneces a esta liga o no existe.", "error")
        return redirect(url_for("leagues"))

    db = get_db()
    rows = compute_standings(db, league_id)
    return render_template("standings.html", league=league, standings=rows)


@app.route("/leagues/<league:league_id>/leaderboards")
@login_required
def leaderboards(league_id):
    league, membership = require_membership(league_id)
    if not league:
        flash("No perteneces a esta liga o no existe.", "error")
        return redirect(url_for("leagues"))

    db = get_db()
    categories = []
    for column, label in LEADERBOARD_CATEGORIES:
        rows = rows_to_list(
            db.execute(
                f"""
                SELECT gs.player_id, p.nombre, p.posicion, p.sprite_url, SUM(gs.{column}) as total,
                    lm.team_name AS owner_team_name, r.user_id AS owner_user_id
                FROM gameweek_scores gs
                JOIN players p ON p.id = gs.player_id
                LEFT JOIN rosters r ON r.league_id = gs.league_id AND r.player_id = gs.player_id
                LEFT JOIN league_members lm ON lm.league_id = r.league_id AND lm.user_id = r.user_id
                WHERE gs.league_id = ? AND gs.user_id > 0
                GROUP BY gs.player_id
                HAVING total > 0
                ORDER BY total DESC
                LIMIT 8
                """,
                (league_id,),
            ).fetchall()
        )
        categories.append({"label": label, "column": column, "rows": rows})

    return render_template(
        "leaderboards.html",
        league=league,
        categories=categories,
        position_labels=POSITION_LABELS,
        my_user_id=session["user_id"],
    )


@app.route("/leagues/<league:league_id>/gameweeks")
@login_required
def gameweeks(league_id):
    league, membership = require_membership(league_id)
    if not league:
        flash("No perteneces a esta liga o no existe.", "error")
        return redirect(url_for("leagues"))

    db = get_db()
    gws = rows_to_list(
        db.execute("SELECT * FROM gameweeks WHERE league_id = ? ORDER BY number DESC", (league_id,)).fetchall()
    )
    market_count = db.execute(
        "SELECT COUNT(*) as c FROM league_market WHERE league_id = ?", (league_id,)
    ).fetchone()["c"]

    schedule = json.loads(league["schedule_json"] or "[]")
    total_rounds = len(schedule) if schedule and schedule[0] else 0
    max_gameweeks = total_rounds * MAX_LEAGUE_CYCLES if total_rounds else None

    champion = None
    if league["ended"]:
        rows = compute_standings(db, league_id)
        champion = rows[0] if rows else None

    return render_template(
        "gameweeks.html",
        league=league,
        gameweeks=gws,
        is_creator=league["creator_id"] == session["user_id"],
        market_resolved=bool(league["market_resolved"]),
        market_count=market_count,
        max_gameweeks=max_gameweeks,
        champion=champion,
    )


@app.route("/leagues/<league:league_id>/end", methods=["POST"])
@login_required
def end_league(league_id):
    league = get_league_or_404(league_id)
    if not league:
        flash("Liga no encontrada.", "error")
        return redirect(url_for("leagues"))
    if league["creator_id"] != session["user_id"]:
        flash("Solo el creador de la liga puede finalizarla.", "error")
        return redirect(url_for("gameweeks", league_id=league_id))
    if league["ended"]:
        flash("Esta liga ya estaba finalizada.", "error")
        return redirect(url_for("gameweeks", league_id=league_id))

    db = get_db()
    db.execute("UPDATE leagues SET ended = 1 WHERE id = ?", (league_id,))
    db.commit()

    rows = compute_standings(db, league_id)
    champion_name = rows[0]["team_name"] if rows else "—"
    flash(f"¡Liga finalizada! 🏆 Campeón: {champion_name}", "success")
    return redirect(url_for("gameweeks", league_id=league_id))


@app.route("/leagues/<league:league_id>/gameweeks/latest")
@login_required
def gameweek_latest(league_id):
    """Redirects to whichever gameweek is currently the league's most
    recent one. Exists so links (e.g. the Discord notification) don't need
    to hardcode a jornada number that changes every week."""
    league, membership = require_membership(league_id)
    if not league:
        flash("No perteneces a esta liga o no existe.", "error")
        return redirect(url_for("leagues"))
    if not league["current_gameweek"]:
        flash("Esta liga todavía no ha jugado ninguna jornada.", "error")
        return redirect(url_for("gameweeks", league_id=league_id))
    return redirect(url_for("gameweek_detail", league_id=league_id, number=league["current_gameweek"]))


@app.route("/leagues/<league:league_id>/gameweeks/<int:number>")
@login_required
def gameweek_detail(league_id, number):
    league, membership = require_membership(league_id)
    if not league:
        flash("No perteneces a esta liga o no existe.", "error")
        return redirect(url_for("leagues"))

    db = get_db()
    gw = row_to_dict(
        db.execute("SELECT * FROM gameweeks WHERE league_id = ? AND number = ?", (league_id, number)).fetchone()
    )
    if not gw:
        flash("Jornada no encontrada.", "error")
        return redirect(url_for("gameweeks", league_id=league_id))

    totals = rows_to_list(
        db.execute(
            """
            SELECT gt.user_id, lm.team_name, u.username, gt.total_points
            FROM gameweek_totals gt
            JOIN league_members lm ON lm.league_id = gt.league_id AND lm.user_id = gt.user_id
            JOIN users u ON u.id = gt.user_id
            WHERE gt.gameweek_id = ?
            ORDER BY gt.total_points DESC
            """,
            (gw["id"],),
        ).fetchall()
    )

    scores = rows_to_list(
        db.execute(
            """
            SELECT gs.user_id, gs.player_id, gs.points, gs.events, p.nombre, p.posicion, p.sprite_url
            FROM gameweek_scores gs JOIN players p ON p.id = gs.player_id
            WHERE gs.gameweek_id = ?
            ORDER BY gs.points DESC
            """,
            (gw["id"],),
        ).fetchall()
    )
    lineups = {}
    for s in scores:
        lineups.setdefault(s["user_id"], []).append(s)

    results = rows_to_list(
        db.execute(
            """
            SELECT mr.*, p.nombre, p.posicion, p.sprite_url, lm.team_name, u.username
            FROM market_results mr
            JOIN players p ON p.id = mr.player_id
            LEFT JOIN league_members lm ON lm.league_id = mr.league_id AND lm.user_id = mr.winner_user_id
            LEFT JOIN users u ON u.id = mr.winner_user_id
            WHERE mr.league_id = ? AND mr.gameweek_number = ?
            ORDER BY mr.amount DESC
            """,
            (league_id, number),
        ).fetchall()
    )

    fixtures = rows_to_list(
        db.execute("SELECT * FROM fixtures WHERE gameweek_id = ? ORDER BY id", (gw["id"],)).fetchall()
    )
    for f in fixtures:
        f["summary_lines"] = f["summary"].split("\n") if f["summary"] else []
        try:
            f["events"] = json.loads(f["events_json"]) if f["events_json"] else []
        except (ValueError, TypeError):
            f["events"] = []
        for ev in f["events"]:
            if ev.get("super_technique"):
                skill_id = ROTULOS_MANIFEST.get(ev.get("technique_name", ""))
                if skill_id:
                    ev["rotulo_url"] = url_for("static", filename=f"rotulos/{skill_id}.webp")
        try:
            home_lineup_data = json.loads(f["home_lineup_json"]) if f["home_lineup_json"] else []
            away_lineup_data = json.loads(f["away_lineup_json"]) if f["away_lineup_json"] else []
        except (ValueError, TypeError):
            home_lineup_data, away_lineup_data = [], []
        f["pitch_home"] = compute_match_pitch_positions(home_lineup_data, "home")
        f["pitch_away"] = compute_match_pitch_positions(away_lineup_data, "away")

    return render_template(
        "gameweek_detail.html",
        league=league,
        gameweek=gw,
        totals=totals,
        lineups=lineups,
        market_results=results,
        fixtures=fixtures,
    )


# ---------------------------------------------------------------------------
# Discord notifications
# ---------------------------------------------------------------------------
# Set this via an environment variable (same place as SECRET_KEY /
# SCHEDULER_TOKEN) — never hardcode the webhook URL here, since anyone with
# it can post messages to the channel.
DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK", "")


def league_url(path):
    """Builds an absolute URL to the deployed app for use in Discord
    messages (Discord needs a full https:// link, not a relative path)."""
    if PYTHONANYWHERE_DOMAIN:
        return f"https://{PYTHONANYWHERE_DOMAIN}{path}"
    return path


DISCORD_COLOR_MARKET = 0xF5A623  # orange
DISCORD_COLOR_GAMEWEEK = 0x4CAF50  # green


def send_discord_message(content=None, embed=None, webhook_url=None):
    """Posts a message to a Discord webhook. Silently does nothing if no
    webhook is available, and never lets a Discord failure break the
    caller (market/gameweek logic must succeed either way).

    `webhook_url` lets each LEAGUE use its own Discord channel (set by its
    creator in the league's settings). If it's not given/empty, falls back
    to the site-wide DISCORD_WEBHOOK env var (so leagues that haven't set
    their own still work if that's configured).

    Pass `embed` (a dict with title/description/url/color) instead of, or
    together with, `content` to get a clean clickable title in Discord
    instead of a raw URL with league/gameweek IDs in it — Discord only
    turns a link into a plain, ugly line of text when it's pasted as plain
    content; inside an embed's `url` it becomes the title's hyperlink."""
    webhook = webhook_url or DISCORD_WEBHOOK
    if not webhook:
        return
    payload = {}
    if content:
        payload["content"] = content
    if embed:
        payload["embeds"] = [embed]
    if not payload:
        return
    try:
        requests.post(webhook, json=payload, timeout=5)
    except Exception:
        pass


DISCORD_LIST_LIMIT = 25  # cap long lists so a single message never gets anywhere near Discord's 2000-char limit


def format_market_sold_for_discord(sold):
    """Turns the list returned by rotate_market_for_league() into a
    readable bullet list of who signed whom for how much."""
    if not sold:
        return "Nadie ha pujado por ningún jugador esta vez."
    lines = [f"• **{s['player']}** → {s['team']} ({format_euros(s['amount'])})" for s in sold]
    if len(lines) > DISCORD_LIST_LIMIT:
        extra = len(lines) - DISCORD_LIST_LIMIT
        lines = lines[:DISCORD_LIST_LIMIT] + [f"…y {extra} más."]
    return "\n".join(lines)


def fetch_gameweek_fixtures(db, league_id, number):
    return rows_to_list(
        db.execute(
            "SELECT f.home_label, f.away_label, f.home_goals, f.away_goals "
            "FROM fixtures f JOIN gameweeks g ON f.gameweek_id = g.id "
            "WHERE g.league_id = ? AND g.number = ? ORDER BY f.id",
            (league_id, number),
        ).fetchall()
    )


def format_gameweek_results_for_discord(db, league_id, number):
    """Builds a readable list of every fixture score for a played gameweek."""
    fixtures = fetch_gameweek_fixtures(db, league_id, number)
    if not fixtures:
        return "No se han registrado partidos para esta jornada."
    lines = [
        f"• {f['home_label']} **{f['home_goals']}-{f['away_goals']}** {f['away_label']}" for f in fixtures
    ]
    if len(lines) > DISCORD_LIST_LIMIT:
        extra = len(lines) - DISCORD_LIST_LIMIT
        lines = lines[:DISCORD_LIST_LIMIT] + [f"…y {extra} más."]
    return "\n".join(lines)


@app.route("/leagues/<league:league_id>/resolve-market", methods=["POST"])
@login_required
def resolve_market(league_id):
    league = get_league_or_404(league_id)
    if not league:
        flash("Liga no encontrada.", "error")
        return redirect(url_for("leagues"))
    if league["creator_id"] != session["user_id"]:
        flash("Solo el creador de la liga puede cerrar el mercado.", "error")
        return redirect(url_for("gameweeks", league_id=league_id))

    db = get_db()
    sold = rotate_market_for_league(db, league_id)
    send_discord_message(
        embed={
            "title": f"🛒 {league['name']} — Mercado cerrado",
            "description": format_market_sold_for_discord(sold),
            "url": league_url(url_for("market", league_id=league_id)),
            "color": DISCORD_COLOR_MARKET,
        },
        webhook_url=league["discord_webhook"],
    )
    flash("¡Mercado cerrado! Revisa quién ha ganado cada puja. Ya hay un mercado nuevo abierto.", "success")
    return redirect(url_for("gameweeks", league_id=league_id))


@app.route("/leagues/<league:league_id>/advance", methods=["POST"])
@login_required
def advance_gameweek(league_id):
    league = get_league_or_404(league_id)
    if not league:
        flash("Liga no encontrada.", "error")
        return redirect(url_for("leagues"))
    if league["creator_id"] != session["user_id"]:
        flash("Solo el creador de la liga puede avanzar de jornada.", "error")
        return redirect(url_for("gameweeks", league_id=league_id))

    db = get_db()
    ok, result = play_gameweek_for_league(db, league_id)
    if not ok:
        flash(result, "error")
        return redirect(url_for("gameweeks", league_id=league_id))

    send_discord_message(
        embed={
            "title": f"⚽ {league['name']} — Jornada {result} jugada",
            "description": format_gameweek_results_for_discord(db, league_id, result),
            "url": league_url(url_for("gameweek_latest", league_id=league_id)),
            "color": DISCORD_COLOR_GAMEWEEK,
        },
        webhook_url=league["discord_webhook"],
    )
    flash(f"¡Jornada {result} jugada!", "success")
    return redirect(url_for("gameweek_detail", league_id=league_id, number=result))


# Secret token for the external scheduler (e.g. cron-job.org) to call the
# route below once a day. Set this via the SCHEDULER_TOKEN environment
# variable (same place as SECRET_KEY) — never hardcode it here.
SCHEDULER_TOKEN = os.environ.get("SCHEDULER_TOKEN", "")


@app.route("/tasks/run-daily", methods=["GET", "POST"])
def run_daily_task():
    """Meant to be hit once a day by an external scheduler (cron-job.org or
    similar), not by a person. Depending on today's weekday it rotates the
    market and/or plays a gameweek for every league — see daily_task.py for
    the full explanation of the weekly schedule (they share the exact same
    logic).
    """
    token = request.args.get("token", "")
    if not SCHEDULER_TOKEN or token != SCHEDULER_TOKEN:
        return "Forbidden", 403

    now = datetime.now(MADRID_TZ) if MADRID_TZ else datetime.now()
    weekday = now.weekday()  # Monday=0 ... Sunday=6
    is_market_day = weekday in (0, 1, 2, 3, 4)  # Monday-Friday
    is_match_day = weekday in (4, 5, 6)  # Friday-Saturday-Sunday

    db = get_db()
    leagues = rows_to_list(db.execute("SELECT id, name, discord_webhook FROM leagues").fetchall())

    lines = [f"{now.isoformat()} weekday={weekday} market_day={is_market_day} match_day={is_match_day}"]
    lines.append(f"Found {len(leagues)} league(s).")

    for league in leagues:
        league_id, name, webhook = league["id"], league["name"], league["discord_webhook"]

        if is_market_day:
            try:
                sold = rotate_market_for_league(db, league_id)
                lines.append(f"[{name}] market rotated OK")
                send_discord_message(
                    embed={
                        "title": f"🛒 {name} — Mercado cerrado",
                        "description": format_market_sold_for_discord(sold),
                        "url": league_url(url_for("market", league_id=league_id)),
                        "color": DISCORD_COLOR_MARKET,
                    },
                    webhook_url=webhook,
                )
            except Exception as exc:
                lines.append(f"[{name}] market rotation FAILED: {exc}")

        if is_match_day:
            try:
                ok, result = play_gameweek_for_league(db, league_id)
                if ok:
                    lines.append(f"[{name}] gameweek {result} played OK")
                    send_discord_message(
                        embed={
                            "title": f"⚽ {name} — Jornada {result} jugada",
                            "description": format_gameweek_results_for_discord(db, league_id, result),
                            "url": league_url(url_for("gameweek_latest", league_id=league_id)),
                            "color": DISCORD_COLOR_GAMEWEEK,
                        },
                        webhook_url=webhook,
                    )
                else:
                    lines.append(f"[{name}] gameweek NOT played: {result}")
            except Exception as exc:
                lines.append(f"[{name}] gameweek FAILED: {exc}")

    lines.append("Done.")
    return "\n".join(lines), 200, {"Content-Type": "text/plain; charset=utf-8"}


# ---------------------------------------------------------------------------
# GitHub auto-deploy webhook
# ---------------------------------------------------------------------------
# Set these via environment variables (same place as SECRET_KEY):
#   GITHUB_WEBHOOK_SECRET        the same secret you type into GitHub's
#                                 webhook settings ("Secret" field)
#   PYTHONANYWHERE_API_TOKEN     from the "API Token" tab in your
#                                 PythonAnywhere Account page
#   PYTHONANYWHERE_USERNAME      your PythonAnywhere username
#   PYTHONANYWHERE_DOMAIN        e.g. yellowalberto.pythonanywhere.com
GITHUB_WEBHOOK_SECRET = os.environ.get("GITHUB_WEBHOOK_SECRET", "")
PYTHONANYWHERE_API_TOKEN = os.environ.get("PYTHONANYWHERE_API_TOKEN", "")
PYTHONANYWHERE_USERNAME = os.environ.get("PYTHONANYWHERE_USERNAME", "")
PYTHONANYWHERE_DOMAIN = os.environ.get("PYTHONANYWHERE_DOMAIN", "")


def _verify_github_signature(secret, payload_body, signature_header):
    if not secret or not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode("utf-8"), payload_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header)


@app.route("/deploy-webhook", methods=["POST"])
def deploy_webhook():
    if not GITHUB_WEBHOOK_SECRET:
        return "Webhook no configurado (falta GITHUB_WEBHOOK_SECRET)", 503

    signature = request.headers.get("X-Hub-Signature-256", "")
    if not _verify_github_signature(GITHUB_WEBHOOK_SECRET, request.data, signature):
        return "Firma inválida", 403

    # GitHub sends a harmless "ping" event right after you create the
    # webhook, just to confirm it's reachable — answer it without deploying.
    event = request.headers.get("X-GitHub-Event", "")
    if event == "ping":
        return "pong", 200

    payload = request.get_json(silent=True) or {}
    ref = payload.get("ref", "")
    if ref and ref not in ("refs/heads/main", "refs/heads/master"):
        return f"Ignorado (push a '{ref}', no a main/master)", 200

    repo_dir = os.path.dirname(os.path.abspath(__file__))
    try:
        pull = subprocess.run(
            ["git", "pull"], cwd=repo_dir, capture_output=True, text=True, timeout=60
        )
        pull_output = (pull.stdout or "") + (pull.stderr or "")
    except Exception as exc:
        return f"Error al hacer git pull: {exc}", 500

    reload_ok = False
    reload_detail = "Reload automático no configurado (faltan variables PYTHONANYWHERE_*)"
    if PYTHONANYWHERE_API_TOKEN and PYTHONANYWHERE_USERNAME and PYTHONANYWHERE_DOMAIN:
        try:
            resp = requests.post(
                f"https://www.pythonanywhere.com/api/v0/user/{PYTHONANYWHERE_USERNAME}"
                f"/webapps/{PYTHONANYWHERE_DOMAIN}/reload/",
                headers={"Authorization": f"Token {PYTHONANYWHERE_API_TOKEN}"},
                timeout=30,
            )
            reload_ok = resp.status_code == 200
            reload_detail = "OK" if reload_ok else f"status {resp.status_code}: {resp.text}"
        except Exception as exc:
            reload_detail = f"error: {exc}"

    body = f"git pull:\n{pull_output}\n\nreload: {reload_detail}\n"
    return body, 200, {"Content-Type": "text/plain; charset=utf-8"}


# Runs at import time too (not just "python app.py" directly), so the
# database/tables exist even when a WSGI server (PythonAnywhere, gunicorn,
# etc.) imports this module instead of running it as a script.
init_db()

if __name__ == "__main__":
    app.run(debug=True, port=5000)
