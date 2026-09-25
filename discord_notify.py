"""Todo lo relacionado con mandar avisos a Discord: el propio POST al
webhook y los formateadores que convierten resultados de mercado/jornada
en texto legible para un mensaje."""
import requests

from core import DISCORD_WEBHOOK, format_euros, rows_to_list


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
