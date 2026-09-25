"""
Blueprint del modo Draft diario (estilo FUT) para Inazuma Fantasy.

Se integra en la web principal en vez de vivir como una app aparte:
  - usa las mismas cuentas de usuario (session["user_id"] / tabla users)
  - usa el mismo seed/players.json y el mismo static/sprites/
  - guarda sus partidas en una tabla "drafts" nueva, en la misma base
    de datos que el resto de la web (ver db.py)

build_draft_blueprint() recibe get_db/login_required/is_admin_user de
app.py para no duplicar la conexión a la base de datos ni la lógica de
sesión/admin.
"""
import json
import os
from datetime import datetime, timedelta

from flask import Blueprint, jsonify, redirect, render_template, request, session, url_for

try:
    from zoneinfo import ZoneInfo
    DRAFT_TZ = ZoneInfo(os.environ.get("INAZUMA_TZ", "Europe/Madrid"))
except Exception:  # pragma: no cover - entorno sin tzdata
    DRAFT_TZ = None

import draft_game as game


def _now():
    return datetime.now(DRAFT_TZ) if DRAFT_TZ else datetime.now()


def _today():
    return _now().date().isoformat()


def _seconds_to_next_day():
    n = _now()
    tomorrow = (n + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return int((tomorrow - n).total_seconds())


TOTAL_SLOTS = 11 + game.BENCH_SIZE


def build_draft_blueprint(get_db, login_required, is_admin_user):
    bp = Blueprint("draft", __name__, url_prefix="/draft")

    def card(pid, pos=None):
        """Datos de la carta. Si se indica posición, añade la media en esa posición."""
        if not pid:
            return None
        c = dict(game.get_db().public(pid))
        if pos and c.get("ovr_pos"):
            c["ovr_here"] = c["ovr_pos"][pos]
            c["out_of_position"] = pos != c["posicion"]
        return c

    def get_state(row):
        """Carga el estado y lo adapta a la versión actual (suplentes, huecos abiertos)."""
        state = json.loads(row["state"])
        picks = state.get("picks", [])
        state["picks"] = picks + [None] * (TOTAL_SLOTS - len(picks))
        if "opened" not in state:
            state["opened"] = [i for i, p in enumerate(state["picks"]) if p]
            if state.get("offer"):
                state["opened"].append(state["offer"]["slot"])
        return state

    # -----------------------------------------------------------------
    # Estado del draft (se guarda en el servidor: no se puede repetir ni trucar)
    # -----------------------------------------------------------------
    def load_draft(create=True):
        db = get_db()
        day = _today()
        row = db.execute("SELECT * FROM drafts WHERE user_id=? AND day=?", (session["user_id"], day)).fetchone()
        if row is None and create:
            state = {"formation": None, "formation_options": game.formation_options(day),
                     "picks": [None] * TOTAL_SLOTS, "opened": [], "offer": None, "manager": None, "manager_offer": None}
            db.execute("INSERT INTO drafts(user_id, day, state) VALUES (?,?,?)",
                       (session["user_id"], day, json.dumps(state)))
            db.commit()
            row = db.execute("SELECT * FROM drafts WHERE user_id=? AND day=?", (session["user_id"], day)).fetchone()
        return row

    def save_state(row, state):
        db = get_db()
        db.execute("UPDATE drafts SET state=? WHERE id=?", (json.dumps(state), row["id"]))
        db.commit()

    def serialize(row):
        db = get_db()
        state = get_state(row)
        out = {
            "day": row["day"],
            "finished": bool(row["finished"]),
            "formation": state["formation"],
            "formation_options": [{"name": f, "slots": game.FORMATIONS[f]["slots"]} for f in state["formation_options"]],
            "next_in": _seconds_to_next_day(),
        }
        if state["formation"]:
            f = game.FORMATIONS[state["formation"]]
            result = game.compute_score(state["formation"], state["picks"], state["manager"])
            out.update({
                "slots": [{"pos": s[0], "label": s[1], "x": s[2], "y": s[3]} for s in f["slots"]],
                "links": result["chemistry"]["links"],
                "picks": [card(p, f["slots"][i][0]) for i, p in enumerate(state["picks"][:11])],
                "bench": [card(p) for p in state["picks"][11:]],
                "player_chem": result["chemistry"]["per_player"],
                "player_points": result["chemistry"]["points"],
                "manager": card(state["manager"]),
                "rating": result["rating"], "chem": result["chem"], "score": result["score"],
            })
            if state.get("offer"):
                out["offer"] = offer_payload(state, state["offer"]["slot"], state["offer"]["options"])
            if state.get("manager_offer"):
                out["manager_offer"] = manager_payload(state, state["manager_offer"])
        if row["finished"]:
            pos = db.execute("SELECT COUNT(*)+1 FROM drafts WHERE day=? AND finished=1 AND score>?",
                             (row["day"], row["score"])).fetchone()[0]
            total = db.execute("SELECT COUNT(*) FROM drafts WHERE day=? AND finished=1", (row["day"],)).fetchone()[0]
            out["rank"] = {"position": pos, "total": total}
        return out

    def offer_payload(state, slot, options):
        """Opciones con la química que tendría el equipo si eliges cada una."""
        base = game.compute_score(state["formation"], state["picks"], state["manager"])
        res = []
        for pid in options:
            picks = list(state["picks"])
            picks[slot] = pid
            after = game.compute_score(state["formation"], picks, state["manager"])
            c = card(pid, game.slot_position(state["formation"], slot))
            c["chem_after"] = after["chem"]
            c["chem_delta"] = after["chem"] - base["chem"]
            c["slot_chem"] = after["chemistry"]["per_player"][slot] if slot < 11 else None
            res.append(c)
        return {"slot": slot, "options": res}

    def manager_payload(state, options):
        base = game.compute_score(state["formation"], state["picks"], state["manager"])
        res = []
        for pid in options:
            after = game.compute_score(state["formation"], state["picks"], pid)
            c = card(pid)
            c["chem_after"] = after["chem"]
            c["chem_delta"] = after["chem"] - base["chem"]
            res.append(c)
        return {"options": res}

    def err(msg, code=400):
        return jsonify(error=msg), code

    # -----------------------------------------------------------------
    # Páginas
    # -----------------------------------------------------------------
    @bp.route("/")
    @login_required
    def index():
        return render_template("draft/index.html")

    @bp.route("/ranking")
    def ranking():
        return render_template("draft/ranking.html")

    # -----------------------------------------------------------------
    # API del draft
    # -----------------------------------------------------------------
    @bp.get("/api/state")
    @login_required
    def api_state():
        return jsonify(serialize(load_draft()))

    @bp.post("/api/formation")
    @login_required
    def api_formation():
        row = load_draft()
        state = get_state(row)
        f = (request.json or {}).get("formation")
        if row["finished"] or state["formation"]:
            return err("La formación ya está elegida.")
        if f not in state["formation_options"]:
            return err("Formación no disponible hoy.")
        state["formation"] = f
        save_state(row, state)
        return jsonify(serialize(load_draft()))

    @bp.post("/api/slot/<int:slot>/open")
    @login_required
    def api_open(slot):
        row = load_draft()
        state = get_state(row)
        if row["finished"] or not state["formation"]:
            return err("No puedes abrir sobres ahora.")
        if not 0 <= slot < TOTAL_SLOTS or state["picks"][slot]:
            return err("Esa posición ya está ocupada.")
        if slot in state["opened"] and not (state.get("offer") and state["offer"]["slot"] == slot):
            return err("Ese sobre ya se abrió.")
        if state.get("manager_offer"):
            return err("Primero elige entrenador.")
        if state.get("offer"):
            if state["offer"]["slot"] != slot:
                return err("Termina de elegir en la posición que ya has abierto.")
        else:
            state["offer"] = {"slot": slot, "options": game.slot_options(row["day"], state["formation"], slot, state["picks"])}
            state["opened"].append(slot)
            save_state(row, state)
        return jsonify(serialize(load_draft()))

    @bp.post("/api/slot/<int:slot>/pick")
    @login_required
    def api_pick(slot):
        row = load_draft()
        state = get_state(row)
        pid = (request.json or {}).get("player_id")
        offer = state.get("offer")
        if row["finished"] or not offer or offer["slot"] != slot or pid not in offer["options"]:
            return err("Elección no válida.")
        state["picks"][slot] = pid
        state["offer"] = None
        save_state(row, state)
        return jsonify(serialize(load_draft()))

    @bp.post("/api/swap")
    @login_required
    def api_swap():
        """Intercambia dos cartas cualesquiera: titular<->titular (cambio de posición),
        titular<->suplente o suplente<->suplente."""
        row = load_draft()
        state = get_state(row)
        a, b = (request.json or {}).get("a"), (request.json or {}).get("b")
        if row["finished"] or not state["formation"] or state.get("offer") or state.get("manager_offer"):
            return err("Ahora no puedes hacer cambios.")
        if a not in range(TOTAL_SLOTS) or b not in range(TOTAL_SLOTS) or a == b:
            return err("Intercambio no válido.")
        if not state["picks"][a] or not state["picks"][b]:
            return err("Solo puedes intercambiar jugadores ya elegidos.")
        state["picks"][a], state["picks"][b] = state["picks"][b], state["picks"][a]
        save_state(row, state)
        return jsonify(serialize(load_draft()))

    @bp.get("/api/swap/preview")
    @login_required
    def api_swap_preview():
        """Para cada jugador ya elegido con el que 'a' se podría intercambiar,
        cuánto subiría o bajaría la media, la química y la puntuación del
        equipo si se hiciera ese cambio. No guarda nada: solo es para mostrar
        el efecto al pasar el ratón antes de confirmar un intercambio."""
        row = load_draft()
        state = get_state(row)
        a = request.args.get("a", type=int)
        if row["finished"] or not state["formation"]:
            return err("No disponible ahora.")
        if a is None or not 0 <= a < TOTAL_SLOTS or not state["picks"][a]:
            return err("Vista previa no válida.")
        base = game.compute_score(state["formation"], state["picks"], state["manager"])
        options = {}
        for b in range(TOTAL_SLOTS):
            if b == a or not state["picks"][b]:
                continue
            picks = list(state["picks"])
            picks[a], picks[b] = picks[b], picks[a]
            after = game.compute_score(state["formation"], picks, state["manager"])
            options[str(b)] = {
                "rating_delta": round(after["rating"] - base["rating"], 1),
                "chem_delta": after["chem"] - base["chem"],
                "score_delta": after["score"] - base["score"],
            }
        return jsonify(base={"rating": base["rating"], "chem": base["chem"], "score": base["score"]}, options=options)

    @bp.post("/api/manager/open")
    @login_required
    def api_manager_open():
        row = load_draft()
        state = get_state(row)
        if row["finished"] or not state["formation"] or state["manager"] or state.get("offer"):
            return err("No puedes elegir entrenador ahora.")
        if not state.get("manager_offer"):
            state["manager_offer"] = game.manager_options(row["day"])
            save_state(row, state)
        return jsonify(serialize(load_draft()))

    @bp.post("/api/manager/pick")
    @login_required
    def api_manager_pick():
        row = load_draft()
        state = get_state(row)
        pid = (request.json or {}).get("player_id")
        if row["finished"] or not state.get("manager_offer") or pid not in state["manager_offer"]:
            return err("Elección no válida.")
        state["manager"] = pid
        state["manager_offer"] = None
        save_state(row, state)
        return jsonify(serialize(load_draft()))

    @bp.post("/api/finish")
    @login_required
    def api_finish():
        db = get_db()
        row = load_draft()
        state = get_state(row)
        if row["finished"]:
            return err("Ya has enviado tu equipo hoy.")
        if not state["formation"] or not all(state["picks"]) or not state["manager"]:
            return err("Completa los 11 titulares, los suplentes y el entrenador.")
        r = game.compute_score(state["formation"], state["picks"], state["manager"])
        db.execute("UPDATE drafts SET finished=1, score=?, rating=?, chem=?, finished_at=? WHERE id=?",
                   (r["score"], r["rating"], r["chem"], _now().isoformat(), row["id"]))
        db.commit()
        return jsonify(serialize(load_draft()))

    # -----------------------------------------------------------------
    # Solo admin: reiniciar el draft de hoy para poder probar sin
    # esperar a mañana. No hay botón ni ruta visible para nadie más.
    # -----------------------------------------------------------------
    @bp.post("/api/admin/reset")
    @login_required
    def api_admin_reset():
        if not is_admin_user():
            return err("No tienes acceso.", 403)
        db = get_db()
        db.execute("DELETE FROM drafts WHERE user_id=? AND day=?", (session["user_id"], _today()))
        db.commit()
        return jsonify(serialize(load_draft()))

    # -----------------------------------------------------------------
    # Ranking
    # -----------------------------------------------------------------
    @bp.get("/api/ranking")
    def api_ranking():
        db = get_db()
        day = request.args.get("day") or _today()
        daily = db.execute("""
            SELECT u.username, d.score, d.rating, d.chem, d.state, d.finished_at
            FROM drafts d JOIN users u ON u.id=d.user_id
            WHERE d.day=? AND d.finished=1
            ORDER BY d.score DESC, d.chem DESC, d.finished_at ASC LIMIT 100""", (day,)).fetchall()
        gdb = game.get_db()
        daily_out = []
        for r in daily:
            st = json.loads(r["state"])
            best = max((gdb.players[p] for p in st["picks"][:11] if p in gdb.players), key=lambda p: p["ovr"], default=None)
            daily_out.append({"username": r["username"], "score": r["score"], "rating": r["rating"],
                              "chem": r["chem"], "formation": st["formation"],
                              "star": {"nombre": best["nombre"], "sprite": best["sprite"]} if best else None})
        overall = db.execute("""
            SELECT u.username, SUM(d.score) AS total, COUNT(*) AS days, MAX(d.score) AS best,
                   ROUND(AVG(d.score), 0) AS avg
            FROM drafts d JOIN users u ON u.id=d.user_id
            WHERE d.finished=1 GROUP BY u.id
            ORDER BY total DESC LIMIT 100""").fetchall()
        return jsonify(day=day, today=_today(), daily=daily_out, overall=[dict(r) for r in overall])

    @bp.get("/api/history")
    @login_required
    def api_history():
        db = get_db()
        rows = db.execute("""SELECT day, score, rating, chem FROM drafts
                             WHERE user_id=? AND finished=1 ORDER BY day DESC LIMIT 30""",
                          (session["user_id"],)).fetchall()
        return jsonify([dict(r) for r in rows])

    @bp.get("/api/rules")
    def api_rules():
        return jsonify(same_team=game.CHEM_SAME_TEAM, same_saga=game.CHEM_SAME_SAGA,
                       same_element=game.CHEM_SAME_ELEMENT, same_archetype=game.CHEM_SAME_ARCHETYPE,
                       thresholds=game.CHEM_THRESHOLDS, max_player=game.CHEM_MAX_PLAYER,
                       manager=game.MANAGER_BONUS)

    return bp
