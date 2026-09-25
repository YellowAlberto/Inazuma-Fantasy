"""Panel de administración (solo accesible para ADMIN_USERNAME): gestión
de ligas y de las plantillas de sus miembros."""

from flask import flash, redirect, render_template, request, url_for

from core import MAX_LEAGUE_MEMBERS, admin_required, get_db, get_league_or_404, row_to_dict, rows_to_list
from league_engine import (
    default_clause_value,
    owned_player_ids,
    regenerate_league_schedule,
)
from scoring import POSITION_LABELS


def register_admin_routes(app):
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



