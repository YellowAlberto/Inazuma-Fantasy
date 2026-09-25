"""Rutas de cuenta: portada, registro, login/logout y el perfil (incluida
la búsqueda/selección de avatar)."""

from flask import flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from core import (
    AVATAR_RESULT_LIMIT,
    ROTULOS_MANIFEST,
    SEASON_GROUPS,
    _search_avatar_candidates,
    get_db,
    login_required,
    row_to_dict,
    rows_to_list,
    season_label_es,
)
from scoring import POSITION_LABELS


def register_auth_routes(app):
    @app.route("/")

    def index():
        return render_template("home.html")



    @app.route("/register", methods=["GET", "POST"])

    def register():
        if request.method == "POST":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            if len(username) < 3 or len(password) < 4:
                flash("Usuario (mín. 3 caracteres) y contraseña (mín. 4 caracteres) requeridos.", "error")
                return render_template("register.html")

            db = get_db()
            existing = db.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
            if existing:
                flash("Ese nombre de usuario ya existe.", "error")
                return render_template("register.html")

            password_hash = generate_password_hash(password)
            cur = db.execute(
                "INSERT INTO users (username, password_hash) VALUES (?, ?)", (username, password_hash)
            )
            db.commit()
            session["user_id"] = cur.lastrowid
            session["username"] = username
            return redirect(url_for("leagues"))

        return render_template("register.html")



    @app.route("/login", methods=["GET", "POST"])

    def login():
        if request.method == "POST":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            db = get_db()
            user = db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
            if not user or not check_password_hash(user["password_hash"], password):
                flash("Usuario o contraseña incorrectos.", "error")
                return render_template("login.html")

            session["user_id"] = user["id"]
            session["username"] = user["username"]
            return redirect(url_for("leagues"))

        return render_template("login.html")



    @app.route("/logout")

    def logout():
        session.clear()
        return redirect(url_for("login"))



    @app.route("/profile")
    @login_required

    def profile():
        db = get_db()
        user = row_to_dict(db.execute("SELECT * FROM users WHERE id = ?", (session["user_id"],)).fetchone())

        picker_open = request.args.get("picker") == "1"
        query = request.args.get("q", "").strip()
        season = request.args.get("season", "")

        search_results = []
        result_count = 0
        if picker_open and (query or season):
            result_count, search_results = _search_avatar_candidates(db, season, query, AVATAR_RESULT_LIMIT)

        return render_template(
            "profile.html",
            user=user,
            query=query,
            season=season,
            picker_open=picker_open,
            search_results=search_results,
            result_count=result_count,
            result_limit=AVATAR_RESULT_LIMIT,
            all_seasons=list(SEASON_GROUPS.keys()),
            season_labels_es={s: season_label_es(s) for s in SEASON_GROUPS},
            position_labels=POSITION_LABELS,
        )



    @app.route("/profile/avatar-search")
    @login_required

    def avatar_search():
        """JSON endpoint powering the live (as-you-type) avatar search box."""
        db = get_db()
        query = request.args.get("q", "").strip()
        season = request.args.get("season", "")

        if not query and not season:
            return {"count": 0, "results": []}

        count, results = _search_avatar_candidates(db, season, query, AVATAR_RESULT_LIMIT)
        return {
            "count": count,
            "limit": AVATAR_RESULT_LIMIT,
            "results": [
                {
                    "id": p["id"],
                    "nombre": p["nombre"],
                    "posicion": p["posicion"],
                    "posicion_label": POSITION_LABELS.get(p["posicion"], p["posicion"]),
                    "sprite_url": p["sprite_url"],
                }
                for p in results
            ],
        }



    @app.route("/profile/set-avatar", methods=["POST"])
    @login_required

    def set_avatar():
        db = get_db()
        player_id = request.form.get("player_id", type=int)
        query = request.form.get("q", "")
        season = request.form.get("season", "")

        if not player_id:
            flash("Selecciona un jugador válido.", "error")
            return redirect(url_for("profile", picker=1, q=query, season=season))

        player = db.execute("SELECT nombre, sprite_url FROM players WHERE id = ?", (player_id,)).fetchone()
        if not player or not player["sprite_url"]:
            flash("Ese jugador no tiene sprite disponible.", "error")
            return redirect(url_for("profile", picker=1, q=query, season=season))

        db.execute("UPDATE users SET avatar_sprite_url = ? WHERE id = ?", (player["sprite_url"], session["user_id"]))
        db.commit()
        flash(f"¡Tu icono ahora es {player['nombre']}!", "success")
        return redirect(url_for("profile"))



    @app.route("/profile/clear-avatar", methods=["POST"])
    @login_required

    def clear_avatar():
        db = get_db()
        db.execute("UPDATE users SET avatar_sprite_url = NULL WHERE id = ?", (session["user_id"],))
        db.commit()
        flash("Icono restablecido al de por defecto.", "success")
        return redirect(url_for("profile"))


    # ---------------------------------------------------------------------------
    # Leagues
    # ---------------------------------------------------------------------------

