"""Punto de entrada de la aplicación: crea la app Flask, la conecta con la
base de datos y engancha cada grupo de rutas. Toda la lógica en sí (qué hace
cada página, cómo se simula un partido, cómo se manda un aviso a Discord...)
vive en módulos aparte:

  core.py            - sesión, decoradores, filtros de plantilla, helpers genéricos
  league_engine.py   - mercado, pujas CPU, simulación de jornadas, clasificaciones
  discord_notify.py  - avisos por webhook de Discord
  auth_routes.py      /register /login /logout /profile
  admin_routes.py     /admin/leagues...
  leagues_routes.py   /leagues...
  market_routes.py    /leagues/<id>/market...
  team_routes.py       /leagues/<id>/team...
  gameweeks_routes.py  /leagues/<id>/standings /leaderboards /gameweeks...
  tasks_routes.py      /tasks/run-daily /deploy-webhook
  draft_bp.py         el modo Draft diario (ya modular desde antes)

Cada módulo de rutas expone una única función register_X_routes(app) que
registra sus vistas directamente sobre `app` (no usa Blueprint), así que
todos los endpoints se siguen llamando exactamente igual que antes
(register, login, market, team, gameweek_detail...) y ningún url_for()
de las plantillas ha tenido que cambiar.
"""
import os

from flask import Flask

from db import init_db
from core import (
    LeagueSlugConverter,
    close_db,
    display_season_filter,
    euromillions_filter,
    euros_filter,
    inject_user,
)

app = Flask(__name__)
app.secret_key = os.environ.get(
    "SECRET_KEY",
    "inazuma-fantasy-dev-secret-change-me",  # only used for local testing; never used in production
)

app.url_map.converters["league"] = LeagueSlugConverter

app.teardown_appcontext(close_db)
app.context_processor(inject_user)
app.template_filter("display_season")(display_season_filter)
app.template_filter("euros")(euros_filter)
app.template_filter("euromillions")(euromillions_filter)

from auth_routes import register_auth_routes
from admin_routes import register_admin_routes
from leagues_routes import register_leagues_routes
from market_routes import register_market_routes
from team_routes import register_team_routes
from gameweeks_routes import register_gameweeks_routes
from tasks_routes import register_tasks_routes

register_auth_routes(app)
register_admin_routes(app)
register_leagues_routes(app)
register_market_routes(app)
register_team_routes(app)
register_gameweeks_routes(app)
register_tasks_routes(app)

# Runs at import time too (not just "python app.py" directly), so the
# database/tables exist even when a WSGI server (PythonAnywhere, gunicorn,
# etc.) imports this module instead of running it as a script.
init_db()

# Modo Draft diario (sobres estilo FUT): vive en su propio módulo, pero
# reutiliza las cuentas de usuario y la conexión a la base de datos de
# arriba en vez de tener su propio login independiente.
from draft_bp import build_draft_blueprint
from core import get_db, login_required, is_admin_user

app.register_blueprint(build_draft_blueprint(get_db, login_required, is_admin_user))

if __name__ == "__main__":
    app.run(debug=True, port=5000)
