"""La plantilla de cada usuario dentro de una liga: ver equipo (propio o
de otro miembro), guardar alineación, vender jugadores y gestionar
cláusulas de rescisión."""

from flask import flash, redirect, render_template, request, session, url_for

from core import (
    CLAUSE_LEAGUE_AGE_DAYS,
    CLAUSE_MIN_INCREASE,
    euros_to_points,
    format_euros,
    get_db,
    get_membership,
    login_required,
    points_to_euros_millions,
    require_membership,
    row_to_dict,
    rows_to_list,
)
from league_engine import (
    default_clause_value,
    league_is_old_enough_for_clauses,
    maybe_add_cpu_offer,
    pending_bids_total,
    squad_spent,
)
from scoring import DEFAULT_FORMATION, FORMATIONS, POSITION_LABELS, count_missing_slots, formation_requirements, missing_positions_message, validate_lineup


def register_team_routes(app):
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

