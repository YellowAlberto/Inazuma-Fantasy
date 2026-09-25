"""El mercado semanal de una liga: listado, ficha de jugador (para el
popup de detalle), pujar y cancelar puja."""

from flask import flash, jsonify, redirect, render_template, request, session, url_for

from core import (
    ROTULOS_MANIFEST,
    archetype_icon,
    element_icon,
    element_label,
    euros_to_points,
    format_euros,
    get_db,
    login_required,
    points_to_euros_millions,
    require_membership,
    row_to_dict,
    rows_to_list,
)
from league_engine import (
    CPU_USER_ID,
    current_market_ids,
    ensure_league_pool,
    maybe_add_cpu_offer,
    owned_player_ids,
    pending_bids_total,
    refresh_league_market,
    squad_spent,
)
from scoring import POSITION_LABELS, adjust_player_value, season_group_label, season_label_es


def register_market_routes(app):
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
                "elemento": element_label(player["elemento"]),
                "elemento_icon_url": (
                    url_for("static", filename=f"icons/elements/{element_icon(player['elemento'])}")
                    if element_icon(player["elemento"]) else None
                ),
                "arquetipo": player["arquetipo"] if player["arquetipo"] != "Unknown" else None,
                "arquetipo_icon_url": (
                    url_for("static", filename=f"icons/archetypes/{archetype_icon(player['arquetipo'])}")
                    if archetype_icon(player["arquetipo"]) else None
                ),
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

