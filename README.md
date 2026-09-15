# ⚡ Inazuma Fantasy

Un "Fantasy football" hecho con **Python puro (Flask)** — sin React ni JavaScript
complicado — para fichar y alinear a personajes de Inazuma Eleven, jugar jornadas
contra tus amigos y competir en la clasificación.

## Qué incluye

- **5145 personajes** importados desde `Database.csv` (jugadores, entrenadores,
  managers...) con sus estadísticas (Potencia, Control, Técnica, Presión, Físico,
  Agilidad, Inteligencia).
- **4929 sprites** ya vinculados automáticamente a sus personajes por nombre.
- Precio de fichaje calculado a partir de sus estadísticas (según su posición),
  mostrado en **euros** (1 punto interno = 100.000€) para que se sienta como un
  mercado de fichajes real.
- Sistema de **ligas privadas** con código de invitación para jugar con amigos,
  con filtro opcional por **temporada** (qué juegos de la saga incluir) y por
  **personajes scout** (solo scouts, solo con equipo conocido, o todos).
- **Mercado de fichajes** diario con pujas selladas y filtros (posición,
  elemento, juego, búsqueda, orden).
- **Cláusulas de rescisión**: pasado un tiempo, puedes pagar la cláusula de un
  jugador de otro equipo y robárselo, o subir la tuya propia para protegerte.
- **Alineación de 11** con reglas de formación (1 portero, 3-5 defensas,
  3-5 centrocampistas, 1-3 delanteros), control de presupuesto, y relleno
  automático del banquillo al cambiar de formación.
- **Simulación de jornadas** con un motor propio: goles, paradas, robos,
  despejes, bloqueos, afinidades de equipo y elementos... incluyendo
  **supertécnicas** (con los nombres reales de las técnicas del juego, cuando
  las tenemos) que garantizan el resultado de la jugada con una probabilidad baja.
- **Calendario tipo liga real**: cada jornada sigue un calendario fijo de
  "todos contra todos" (round-robin) ajustado al número de entrenadores, con un
  límite de 3 vueltas completas antes de coronar un campeón.
- **Repetición animada** de cada partido, con jugadores moviéndose por el
  campo y sonido.
- **Clasificación** general y clasificaciones individuales (goleadores,
  asistencias, paradas...) por liga.
- **Perfil personalizable**: cada usuario puede elegir el sprite de cualquier
  personaje como su icono.
- **Panel de administración** (para el creador del sitio) con vista de todas
  las ligas y capacidad de gestionar la plantilla de cualquier entrenador.

Todo renderizado en el servidor con plantillas HTML (Jinja2) y formularios
normales — apenas hace falta JavaScript para tocar nada de esto.

## Estructura del proyecto

```
inazuma-fantasy-py/
├── app.py              # Rutas de Flask (toda la lógica de la app)
├── db.py                # Conexión SQLite + creación/siembra/migraciones de tablas
├── scoring.py            # Motor de simulación de partidos, formaciones y puntuación
├── daily_task.py          # Script para la rotación diaria del mercado y jornadas
├── requirements.txt
├── seed/
│   └── players.json     # Datos de los 5145 personajes ya procesados
├── static/
│   ├── style.css
│   ├── team.js, replay.js # Campo interactivo y repetición animada de partidos
│   ├── sprites/          # ~4929 imágenes de personajes (.png) — no va en git
│   └── sounds/            # Sonidos opcionales para la repetición — no va en git
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

## Créditos de los datos

Los datos de personajes y arquetipos provienen de la hoja de cálculo hecha por
@nimofe200 (Discord), con arquetipos obtenidos por @King.Javii.
