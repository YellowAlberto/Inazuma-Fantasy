"""Clasificación, tablas de líderes, calendario de jornadas y el motor de
avance semanal (cerrar mercado / jugar la jornada) para una liga."""

import json

from flask import flash, redirect, render_template, request, session, url_for

from core import (
    LEADERBOARD_CATEGORIES,
    MAX_LEAGUE_CYCLES,
    ROTULOS_MANIFEST,
    get_db,
    get_league_or_404,
    league_url,
    login_required,
    require_membership,
    row_to_dict,
    rows_to_list,
)
from discord_notify import (
    DISCORD_COLOR_GAMEWEEK,
    DISCORD_COLOR_MARKET,
    format_gameweek_results_for_discord,
    format_market_sold_for_discord,
    send_discord_message,
)
from league_engine import compute_match_pitch_positions, compute_standings, play_gameweek_for_league, rotate_market_for_league
from scoring import POSITION_LABELS


def register_gameweeks_routes(app):
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

