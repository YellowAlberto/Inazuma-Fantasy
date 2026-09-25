"""Alta y gestión de ligas: crear, unirse, ver el detalle, cambiar el
webhook de Discord, terminar o borrar una liga."""

from flask import flash, redirect, render_template, request, session, url_for

from core import (
    MAX_LEAGUE_MEMBERS,
    euros_to_points,
    get_db,
    get_league_or_404,
    get_membership,
    league_seasons_list,
    login_required,
    require_membership,
    row_to_dict,
    rows_to_list,
)
from db import unique_league_slug
from league_engine import (
    assign_starting_roster,
    compute_standings,
    ensure_league_pool,
    generate_invite_code,
    generate_round_robin_schedule,
    refresh_league_market,
    regenerate_league_schedule,
)
from scoring import ALL_SEASONS, DEFAULT_FORMATION, SEASON_GROUPS, season_group_label, season_label_es


def register_leagues_routes(app):
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



