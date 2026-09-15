# ⚡ Inazuma Fantasy

Un "Fantasy football" hecho con **Python puro (Flask)** — sin React ni JavaScript
complicado — para fichar y alinear a personajes de Inazuma Eleven, jugar jornadas
contra tus amigos y competir en la clasificación.

## Qué incluye

- **5145 personajes** importados desde `Database.csv` (jugadores, entrenadores,
  managers...) con sus estadísticas (Potencia, Control, Técnica, Presión, Físico,
  Agilidad, Inteligencia).
- **5024 sprites** ya vinculados automáticamente a sus personajes por nombre.
- Precio de fichaje calculado a partir de sus estadísticas (según su posición).
- Sistema de **ligas privadas** con código de invitación para jugar con amigos.
- **Mercado de fichajes** con filtros (posición, elemento, juego, búsqueda, orden).
- **Alineación de 11** con reglas de formación (1 portero, 3-5 defensas,
  3-5 centrocampistas, 1-3 delanteros) y control de presupuesto.
- **Simulación de jornadas**: el creador de la liga pulsa un botón y se calculan
  los puntos de todos los equipos según las estadísticas de sus 11 jugadores
  (con un punto de aleatoriedad, como un partido real).
- **Clasificación** y detalle de cada jornada jugada.

Todo renderizado en el servidor con plantillas HTML (Jinja2) y formularios
normales — no hace falta saber JavaScript para tocar nada de esto.

## Estructura del proyecto

```
inazuma-fantasy-py/
├── app.py              # Rutas de Flask (toda la lógica de la app)
├── db.py                # Conexión SQLite + creación/siembra de tablas
├── scoring.py            # Reglas de formación y cálculo de puntos
├── requirements.txt
├── seed/
│   └── players.json     # Datos de los 5145 personajes ya procesados
├── static/
│   ├── style.css
│   └── sprites/          # 5024 imágenes de personajes (.png)
└── templates/            # Plantillas HTML (Jinja2)
```

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
5. Cuando todos tengan sus 11 completos, el **creador de la liga** entra en
   "Jornadas" y pulsa "Jugar jornada X" para simular los puntos.
6. Consultad la **Clasificación** para ver quién va ganando.

## Notas para producción

- Cambia `app.secret_key` en `app.py` por un valor secreto propio antes de
  desplegar esto en un servidor público.
- El servidor de desarrollo de Flask (`python app.py`) **no** está pensado para
  producción. Si quieres desplegarlo de verdad (por ejemplo en una VPS), usa
  algo como `gunicorn app:app` detrás de un proxy (nginx, Caddy, etc.).
- La base de datos es SQLite (un único fichero `inazuma_fantasy.sqlite`),
  perfecta para jugar entre unos pocos amigos. Si la app creciera mucho,
  se podría migrar a PostgreSQL sin cambiar demasiado el código.

## Créditos de los datos

Los datos de personajes y arquetipos provienen de la hoja de cálculo hecha por
@nimofe200 (Discord), con arquetipos obtenidos por @King.Javii.
