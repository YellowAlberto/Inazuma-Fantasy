"""Endpoints que no visita ninguna persona: el disparador diario del
scheduler externo (cron-job.org) y el webhook de auto-deploy de GitHub."""

import hashlib
import hmac
import os
import subprocess
from datetime import datetime

import requests
from flask import request, url_for

from core import (
    GITHUB_WEBHOOK_SECRET,
    MADRID_TZ,
    PYTHONANYWHERE_API_TOKEN,
    PYTHONANYWHERE_DOMAIN,
    PYTHONANYWHERE_USERNAME,
    SCHEDULER_TOKEN,
    get_db,
    league_url,
    rows_to_list,
)
from discord_notify import (
    DISCORD_COLOR_GAMEWEEK,
    DISCORD_COLOR_MARKET,
    format_gameweek_results_for_discord,
    format_market_sold_for_discord,
    send_discord_message,
)
from league_engine import play_gameweek_for_league, rotate_market_for_league


def register_tasks_routes(app):
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
