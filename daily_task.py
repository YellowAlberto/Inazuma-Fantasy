#!/usr/bin/env python3
"""Daily scheduled task for Inazuma Fantasy.

Meant to be run ONCE A DAY (e.g. via a PythonAnywhere "Scheduled task") at
20:00 Europe/Madrid time. Depending on the day of the week it:

  - Monday through Friday: closes each league's current market (awarding
    the highest bidder each player) and immediately opens a fresh one for
    the next day. This does NOT touch gameweeks/fixtures at all.

  - Friday, Saturday and Sunday: plays one gameweek for every league whose
    managers all have a complete, valid starting lineup. Leagues that
    aren't ready are skipped (with a message in the log) instead of
    crashing the whole run — they'll just be tried again the next match day.

This script is completely independent of Flask's request/session handling,
so it can run outside of a web request (that's the whole point of a cron
job). It reuses the exact same core logic as the "Cerrar mercado" and
"Jugar jornada" buttons in the web app, imported straight from app.py, so
behaviour never drifts between the manual buttons and the automatic runs.

Manual test:  python3 daily_task.py
"""
from datetime import datetime

try:
    from zoneinfo import ZoneInfo
    MADRID_TZ = ZoneInfo("Europe/Madrid")
except Exception:  # pragma: no cover - extremely unlikely on a modern Python
    MADRID_TZ = None

from app import (
    rotate_market_for_league,
    play_gameweek_for_league,
    send_discord_message,
    format_market_sold_for_discord,
    format_gameweek_results_for_discord,
    DISCORD_COLOR_MARKET,
    DISCORD_COLOR_GAMEWEEK,
    PYTHONANYWHERE_DOMAIN,
)
from db import get_connection


def _league_url(path):
    if PYTHONANYWHERE_DOMAIN:
        return f"https://{PYTHONANYWHERE_DOMAIN}{path}"
    return path

# Monday=0 ... Sunday=6
MARKET_ROTATION_WEEKDAYS = {0, 1, 2, 3, 4}  # Monday-Friday
MATCH_WEEKDAYS = {4, 5, 6}  # Friday-Saturday-Sunday


def run():
    now = datetime.now(MADRID_TZ) if MADRID_TZ else datetime.now()
    weekday = now.weekday()
    is_market_day = weekday in MARKET_ROTATION_WEEKDAYS
    is_match_day = weekday in MATCH_WEEKDAYS

    print(f"[{now.isoformat()}] weekday={weekday} market_day={is_market_day} match_day={is_match_day}")

    db = get_connection()
    leagues = db.execute("SELECT id, name, slug, discord_webhook FROM leagues").fetchall()
    print(f"Found {len(leagues)} league(s).")

    for league in leagues:
        league_id, name, webhook = league["id"], league["name"], league["discord_webhook"]
        # Falls back to the raw numeric id if a league somehow has no slug
        # yet (shouldn't happen once app.py's migration has run at least
        # once, but this script can run standalone).
        slug = league["slug"] or str(league_id)

        if is_market_day:
            try:
                sold = rotate_market_for_league(db, league_id)
                print(f"  [{name}] market rotated OK")
                send_discord_message(
                    embed={
                        "title": f"🛒 {name} — Mercado cerrado",
                        "description": format_market_sold_for_discord(sold),
                        "url": _league_url(f"/leagues/{slug}/market"),
                        "color": DISCORD_COLOR_MARKET,
                    },
                    webhook_url=webhook,
                )
            except Exception as exc:
                print(f"  [{name}] market rotation FAILED: {exc}")

        if is_match_day:
            try:
                ok, result = play_gameweek_for_league(db, league_id)
                if ok:
                    print(f"  [{name}] gameweek {result} played OK")
                    send_discord_message(
                        embed={
                            "title": f"⚽ {name} — Jornada {result} jugada",
                            "description": format_gameweek_results_for_discord(db, league_id, result),
                            "url": _league_url(f"/leagues/{slug}/gameweeks/latest"),
                            "color": DISCORD_COLOR_GAMEWEEK,
                        },
                        webhook_url=webhook,
                    )
                else:
                    print(f"  [{name}] gameweek NOT played: {result}")
            except Exception as exc:
                print(f"  [{name}] gameweek FAILED: {exc}")

    db.close()
    print("Done.")


if __name__ == "__main__":
    run()
