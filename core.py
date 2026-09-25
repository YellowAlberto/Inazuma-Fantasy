"""Cosas transversales que casi todos los demás módulos necesitan: acceso a
la base de datos por petición, los decoradores de sesión/admin, los filtros
de plantilla, el conversor de URL de las ligas y un puñado de helpers
genéricos (euros, avatares, membresías...). No importa nada de app.py ni de
los módulos de rutas, así que cualquiera puede importar de aquí sin crear
un import circular."""
import functools
import json
import os

try:
    from zoneinfo import ZoneInfo
    MADRID_TZ = ZoneInfo("Europe/Madrid")
except Exception:  # pragma: no cover - extremely unlikely on a modern Python
    MADRID_TZ = None

from flask import flash, g, redirect, request, session, url_for
from werkzeug.routing import BaseConverter, ValidationError

from db import get_connection
from scoring import SEASON_GROUPS, season_group_label, season_label_es

# Secret token for the external scheduler (e.g. cron-job.org) to call
# /tasks/run-daily once a day. Set via the SCHEDULER_TOKEN environment
# variable (same place as SECRET_KEY) — never hardcode it here.
SCHEDULER_TOKEN = os.environ.get("SCHEDULER_TOKEN", "")

# Afinidad elemental de un jugador (Fire/Wind/Forest/Mountain en los datos
# originales). ELEMENT_ICON_FILE apunta a los iconos reales en
# static/icons/elements/ (aportados por el usuario), para no depender de
# emojis que se ven distinto según el dispositivo/fuente.
ELEMENT_ES = {"Fire": "Fuego", "Wind": "Viento", "Forest": "Bosque", "Mountain": "Montaña"}
ELEMENT_ICON_FILE = {"Fire": "fuego.png", "Wind": "viento.png", "Forest": "bosque.png", "Mountain": "montana.png"}


def element_label(value):
    """Filtro de plantilla: traduce el elemento al español (o lo deja tal
    cual si no se reconoce)."""
    return ELEMENT_ES.get(value, value or "")


def element_icon(value):
    """Filtro de plantilla: nombre de fichero del icono en
    static/icons/elements/, o None si el elemento no tiene icono."""
    return ELEMENT_ICON_FILE.get(value)


# Iconos de arquetipo (siluetas blancas aportadas por el usuario, en
# static/icons/archetypes/). "Unknown" no tiene icono a propósito: no es un
# arquetipo de verdad, sino la ausencia de dato.
ARCHETYPE_ICON_FILE = {
    "Justicia": "justicia.png",
    "Contraataque": "contraataque.png",
    "Juego Sucio": "juego_sucio.png",
    "Tensión": "tension.png",
    "Afinidad": "afinidad.png",
    "Brecha": "brecha.png",
}


def archetype_icon(value):
    """Filtro de plantilla: nombre de fichero del icono en
    static/icons/archetypes/, o None si el arquetipo no tiene icono."""
    return ARCHETYPE_ICON_FILE.get(value)


# Escalones de "calidad" de una carta, sólo para el aspecto visual: el valor de
# mercado es lo que ya mide lo bueno que es un jugador en la liga, así que la
# carta se pinta de bronce/plata/oro/leyenda según ese valor. Los cortes están
# puestos sobre la distribución real de la base de datos, de forma que las
# leyendas sean ~3% de los personajes y el oro ~8%, y una parrilla de mercado
# se lea de un vistazo en vez de ser 22 cajas iguales.
CARD_TIERS = ((100, "leyenda"), (80, "oro"), (60, "plata"))


def value_tier(points):
    """Filtro de plantilla: 'bronce' | 'plata' | 'oro' | 'leyenda' a partir del
    valor interno del jugador (en puntos, donde 10 puntos = 1M €)."""
    value = points or 0
    for threshold, name in CARD_TIERS:
        if value >= threshold:
            return name
    return "bronce"


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



def euros_filter(points):
    return format_euros(points)



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



def is_admin_user():
    username = session.get("username", "")
    return bool(ADMIN_USERNAME) and username.lower() == ADMIN_USERNAME.lower()



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


GITHUB_WEBHOOK_SECRET = os.environ.get("GITHUB_WEBHOOK_SECRET", "")
PYTHONANYWHERE_API_TOKEN = os.environ.get("PYTHONANYWHERE_API_TOKEN", "")
PYTHONANYWHERE_USERNAME = os.environ.get("PYTHONANYWHERE_USERNAME", "")
PYTHONANYWHERE_DOMAIN = os.environ.get("PYTHONANYWHERE_DOMAIN", "")
