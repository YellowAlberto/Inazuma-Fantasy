# ⚡ Inazuma Fantasy

Un simulador de manager hecho con **Python puro (Flask)**, en el podras fichar y alinear a personajes de Inazuma Eleven, jugar jornadas
contra tus amigos y competir en la clasificación.

## Qué incluye

- **5145 personajes** (jugadores, entrenadores, managers...) con sus estadísticas.
- Precio de los fichajes autoregulados, en base a sus estadisticas y rendimiento.
- Sistema de **ligas privadas** con código de invitación para jugar con amigos,
  donde podras elegir la **temporada** que quieres jugar y si jugar con **scouts**  o no
- **Mercado de fichajes** diario con pujas selladas y filtros (posición,
  elemento, juego, búsqueda, orden).
- **Cláusulas de rescisión**: pasado un tiempo, puedes pagar la cláusula de un
  jugador de otro equipo y robárselo, o subir la tuya propia para protegerte.
- Varias **alineaciones** para encontrar el estilo que mas te gusta
- **Simulación de jornadas** con un motor propio: goles, paradas, robos,
  despejes, bloqueos, afinidades de equipo y elementos... incluyendo
  **supertécnicas** que garantizan el resultado de la jugada con una probabilidad baja.
- **Calendario tipo liga real**: cada jornada sigue un calendario fijo de
  "todos contra todos" (round-robin) ajustado al número de entrenadores, con un
  límite de 3 vueltas completas antes de coronar un campeón.
- **Repetición animada** de cada partido, con jugadores moviéndose por el
  campo y sonido.
- **Clasificación** general y clasificaciones individuales por liga.
- **Perfil personalizable**: cada usuario puede elegir el sprite de cualquier
  personaje como su icono.

Todo renderizado en el servidor con plantillas HTML (Jinja2) y formularios
normales — apenas hace falta JavaScript para tocar nada de esto.

## Estructura del proyecto

```
inazuma-fantasy-py/
├── app.py                 # Punto de entrada: crea la app y engancha cada módulo de rutas
├── core.py                 # Sesión, decoradores, filtros de plantilla, helpers genéricos
├── league_engine.py         # Mercado, pujas CPU, simulación de jornadas, clasificaciones
├── discord_notify.py        # Avisos por webhook de Discord
├── auth_routes.py            # /register /login /logout /profile
├── admin_routes.py           # /admin/leagues... (panel de administración)
├── leagues_routes.py         # /leagues... (crear, unirse, detalle, borrar)
├── market_routes.py          # /leagues/<id>/market... (mercado semanal)
├── team_routes.py             # /leagues/<id>/team... (plantilla, cláusulas)
├── gameweeks_routes.py        # /standings /leaderboards /gameweeks... y el avance semanal
├── tasks_routes.py            # /tasks/run-daily y /deploy-webhook (sin interfaz, para el cron/GitHub)
├── draft_bp.py               # Modo Draft diario (sobres estilo FUT)
├── draft_game.py              # Motor del Draft (sobres, química, formaciones)
├── db.py                   # Conexión SQLite + creación/siembra/migraciones de tablas
├── scoring.py               # Motor de simulación de partidos, formaciones y puntuación
├── daily_task.py             # Script para la rotación diaria del mercado y jornadas
├── requirements.txt
├── seed/
│   └── players.json        # Datos de los 5145 personajes ya procesados
├── static/
│   ├── style.css
│   ├── team.js, replay.js  # Campo interactivo y repetición animada de partidos
│   ├── draft.css, draft.js  # Interfaz del modo Draft
│   ├── sprites/             # ~4929 imágenes de personajes (.png) — no va en git
│   └── sounds/               # Sonidos opcionales para la repetición — no va en git
└── templates/               # Plantillas HTML (Jinja2)
```

`app.py` solo crea la aplicación Flask y llama a `register_X_routes(app)` de cada
módulo — cada uno registra sus vistas directamente sobre `app` (no usa
Blueprint), así que todas las rutas se siguen llamando exactamente igual que
antes (`register`, `login`, `market`, `team`, `gameweek_detail`...) y ningún
`url_for()` de las plantillas ha tenido que cambiar. El modo Draft es la
excepción: es lo bastante independiente como para vivir en su propio
Blueprint (`draft_bp.py`), con sus rutas bajo `/draft/...`.

## Instalación (en tu ordenador)

Necesitas **Python 3.10 o superior**.

1. Descomprime el `.zip` en una carpeta.
2. Abre una terminal dentro de esa carpeta y crea un entorno virtual (recomendado):

   ```bash
   python3 -m venv venv
   source venv/bin/activate        # En Windows: venv\Scripts\activate
   ```

3. Instala las dependencias:

   ```bash
   pip install -r requirements.txt
   ```

4. Arranca la aplicación:

   ```bash
   python app.py
   ```

5. Abre tu navegador en **http://127.0.0.1:5000**

La primera vez que arranques, Flask creará automáticamente el fichero
`inazuma_fantasy.sqlite` y sembrará la base de datos con los 5145 personajes
(tarda un par de segundos, verás el mensaje `Seeded 5145 players...` en la consola).

## Cómo jugar

1. **Regístrate** con un usuario y contraseña.
2. **Crea una liga** (eliges nombre, nombre de tu equipo y presupuesto en
   millones) — te dará un **código de invitación** de 6 caracteres.
3. Comparte ese código con tus amigos para que se unan desde "Unirme con un código".
4. Cada uno entra en **Mercado de fichajes**, filtra por posición/elemento/juego
   y ficha jugadores hasta completar su alineación de 11 (respetando la
   formación y el presupuesto).
5. Las jornadas se juegan todos los fines de semana (viernes, sabado o domingo) o el creador puede hacer que se jueguen al instante.
6. Consultad la **Clasificación** para ver quién va ganando.

## Créditos de los datos

Los datos de personajes y arquetipos provienen de la hoja de cálculo hecha por
@nimofe200 (Discord), con arquetipos obtenidos por @King.Javii.
