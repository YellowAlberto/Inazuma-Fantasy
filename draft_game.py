"""
Lógica del modo Draft: carga de jugadores, media (OVR), formaciones,
generación de opciones diarias y cálculo de química.

Adaptado del proyecto standalone "Inazuma Draft" para vivir dentro de la
web principal: reutiliza el mismo seed/players.json y el mismo
static/sprites/ que ya usa Inazuma Fantasy en vez de mantener una copia
aparte.
"""
import json
import os
import random
import hashlib
import bisect

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PLAYERS_FILE = os.environ.get("INAZUMA_PLAYERS", os.path.join(BASE_DIR, "seed", "players.json"))
TEAMS_FILE = os.path.join(BASE_DIR, "draft_data", "teams.json")
SPRITES_DIR = os.environ.get("INAZUMA_SPRITES", os.path.join(BASE_DIR, "static", "sprites"))

STATS = ["potencia", "control", "tecnica", "presion", "fisico", "agilidad", "inteligencia"]

# Peso de cada estadística según la posición para calcular la media
POSITION_WEIGHTS = {
    "GK": {"fisico": .30, "agilidad": .25, "presion": .20, "inteligencia": .15, "control": .10},
    "DF": {"presion": .30, "fisico": .30, "inteligencia": .15, "agilidad": .15, "control": .10},
    "MF": {"control": .25, "tecnica": .25, "inteligencia": .20, "potencia": .10, "agilidad": .10, "presion": .10},
    "FW": {"potencia": .35, "control": .20, "tecnica": .20, "agilidad": .15, "inteligencia": .10},
}

# Nombre corto de cada saga (cada juego cuenta como una saga)
SAGAS = [
    ("Chrono", "Chrono Stones"),
    ("Galaxy", "Galaxy"),
    ("GO", "GO"),
    ("Inazuma Eleven 3", "IE3"),
    ("Inazuma Eleven 2", "IE2"),
    ("Ares", "Ares"),
    ("Orion", "Orion"),
    ("Victory Road", "Victory Road"),
    ("Inazuma Eleven", "IE1"),
]


def saga_of(juego: str) -> str:
    for key, label in SAGAS:
        if key in juego:
            return label
    return juego


# ---------------------------------------------------------------------------
# Formaciones: cada hueco tiene posición, etiqueta y coordenadas (% del campo).
# "links" son las líneas de química entre huecos vecinos (estilo FUT).
# ---------------------------------------------------------------------------
FORMATIONS = {
    "4-4-2": {
        "slots": [
            ("GK", "POR", 50, 90),
            ("DF", "LI", 14, 70), ("DF", "DFC", 37, 75), ("DF", "DFC", 63, 75), ("DF", "LD", 86, 70),
            ("MF", "MI", 14, 44), ("MF", "MC", 37, 50), ("MF", "MC", 63, 50), ("MF", "MD", 86, 44),
            ("FW", "DC", 36, 18), ("FW", "DC", 64, 18),
        ],
        "links": [(0, 2), (0, 3), (1, 2), (2, 3), (3, 4), (1, 5), (2, 6), (3, 7), (4, 8),
                  (5, 6), (6, 7), (7, 8), (5, 9), (6, 9), (7, 10), (8, 10), (9, 10)],
    },
    "4-3-3": {
        "slots": [
            ("GK", "POR", 50, 90),
            ("DF", "LI", 14, 70), ("DF", "DFC", 37, 75), ("DF", "DFC", 63, 75), ("DF", "LD", 86, 70),
            ("MF", "MC", 28, 50), ("MF", "MCD", 50, 56), ("MF", "MC", 72, 50),
            ("FW", "EI", 16, 22), ("FW", "DC", 50, 14), ("FW", "ED", 84, 22),
        ],
        "links": [(0, 2), (0, 3), (1, 2), (2, 3), (3, 4), (1, 5), (2, 6), (3, 6), (4, 7),
                  (5, 6), (6, 7), (5, 8), (5, 9), (7, 9), (7, 10), (8, 9), (9, 10)],
    },
    "3-5-2": {
        "slots": [
            ("GK", "POR", 50, 90),
            ("DF", "DFC", 25, 74), ("DF", "DFC", 50, 77), ("DF", "DFC", 75, 74),
            ("MF", "MI", 10, 46), ("MF", "MC", 32, 54), ("MF", "MCO", 50, 40), ("MF", "MC", 68, 54), ("MF", "MD", 90, 46),
            ("FW", "DC", 36, 16), ("FW", "DC", 64, 16),
        ],
        "links": [(0, 1), (0, 2), (0, 3), (1, 2), (2, 3), (1, 4), (1, 5), (2, 5), (2, 7), (3, 7), (3, 8),
                  (4, 5), (5, 6), (6, 7), (7, 8), (4, 9), (6, 9), (6, 10), (8, 10), (9, 10)],
    },
    "4-2-3-1": {
        "slots": [
            ("GK", "POR", 50, 90),
            ("DF", "LI", 14, 70), ("DF", "DFC", 37, 75), ("DF", "DFC", 63, 75), ("DF", "LD", 86, 70),
            ("MF", "MCD", 36, 56), ("MF", "MCD", 64, 56),
            ("MF", "MI", 14, 34), ("MF", "MCO", 50, 36), ("MF", "MD", 86, 34),
            ("FW", "DC", 50, 13),
        ],
        "links": [(0, 2), (0, 3), (1, 2), (2, 3), (3, 4), (2, 5), (3, 6), (1, 7), (4, 9),
                  (5, 6), (5, 8), (6, 8), (5, 7), (6, 9), (7, 8), (8, 9), (7, 10), (8, 10), (9, 10)],
    },
}

# Escala de medias: el peor jugador es OVR_MIN, el mejor OVR_MAX.
# OVR_CURVE > 1 hace que haya menos cartas altas (1.0 = reparto lineal).
OVR_MIN, OVR_MAX, OVR_CURVE = 70, 99, 1.1

# Probabilidad de que un sobre incluya una carta de 90+ ("élite"). Con 1.0
# saldría siempre una (como antes); más bajo da sobres más variados, donde
# a veces lo mejor que hay es un 88 o un 85.
ELITE_GUARANTEE_CHANCE = 0.55

# Penalización de media al jugar fuera de su posición natural
_LINES = {"GK": 0, "DF": 1, "MF": 2, "FW": 3}
POSITION_PENALTY = {}
for _a in _LINES:
    for _b in _LINES:
        if _a == _b:
            POSITION_PENALTY[(_a, _b)] = 0
        elif "GK" in (_a, _b):
            POSITION_PENALTY[(_a, _b)] = 25        # portero de campo o jugador de campo en portería
        else:
            POSITION_PENALTY[(_a, _b)] = 3 * abs(_LINES[_a] - _LINES[_b])   # DF<->MF -3, DF<->FW -6

BENCH_SIZE = 5          # suplentes (no suman a la puntuación, sirven para rotar)

# Reglas de química
# Cada línea entre dos vecinos suma puntos de enlace:
CHEM_SAME_TEAM = 2      # han compartido equipo
CHEM_SAME_SAGA = 1      # misma saga (solo si no comparten equipo)
# Los "ojeados" (jugadores sin equipo registrado, ~la mitad de la base de
# datos) no pueden compartir equipo con nadie y quedaban en desventaja de
# química frente a los que sí tienen club. Para compensarlo, se tratan como
# si todos perteneciesen a un mismo equipo virtual entre ellos: dos jugadores
# sin equipo dan el mismo +CHEM_SAME_TEAM que si hubiesen jugado juntos.
SCOUT_TEAM = "__scouts__"
CHEM_SAME_ELEMENT = 1   # misma afinidad (Fuego, Viento, Bosque, Montaña)
CHEM_SAME_ARCHETYPE = 1 # mismo arquetipo (Justicia, Brecha, ...; "Unknown" no cuenta)
# La química de un jugador (0-3) sale de sumar los puntos de todos sus enlaces:
CHEM_THRESHOLDS = (2, 4, 6)   # >=2 puntos -> 1, >=4 -> 2, >=6 -> 3
CHEM_MAX_PLAYER = 3     # máximo por jugador -> 11 x 3 = 33
MANAGER_BONUS = 1       # +1 de química a cada jugador de la misma saga que el entrenador


# ---------------------------------------------------------------------------
# Carga de datos
# ---------------------------------------------------------------------------
class Database:
    def __init__(self):
        with open(PLAYERS_FILE, encoding="utf-8") as f:
            raw = json.load(f)
        teams = {}
        if os.path.exists(TEAMS_FILE):
            with open(TEAMS_FILE, encoding="utf-8") as f:
                teams = json.load(f)

        has_sprites = os.path.isdir(SPRITES_DIR) and os.environ.get("INAZUMA_REQUIRE_SPRITES", "1") == "1"
        sprite_set = set(os.listdir(SPRITES_DIR)) if has_sprites else set()

        self.players = {}
        for p in raw:
            if p.get("rol") not in ("Player", "Manager"):
                continue
            if p.get("rol") == "Player" and p.get("posicion") not in POSITION_WEIGHTS:
                continue
            if has_sprites and p.get("sprite_file") not in sprite_set:
                continue  # sin sprite no entra en el draft
            pid = int(p["id"])
            self.players[pid] = {
                "id": pid,
                "nombre": p["nombre"],
                "apodo": p.get("apodo") or p["nombre"],
                "juego": p["juego"],
                "saga": saga_of(p["juego"]),
                "rol": p["rol"],
                "posicion": p.get("posicion"),
                "elemento": p.get("elemento"),
                "arquetipo": p.get("arquetipo"),
                "stats": {s: int(p.get(s) or 0) for s in STATS},
                "tecnicas": (p.get("tecnicas") or [])[:6],
                "sprite": p.get("sprite_file"),
                "equipos": teams.get(str(p["id"]), []),
                "_price": float(p.get("price") or 0),
                "_ntech": len(p.get("tecnicas") or []),
            }
        self._compute_ovr()
        # pools por posición, ordenados por id para que el sorteo diario sea estable
        self.pool = {pos: sorted(pid for pid, p in self.players.items()
                                 if p["rol"] == "Player" and p["posicion"] == pos)
                     for pos in POSITION_WEIGHTS}
        self.managers = sorted(pid for pid, p in self.players.items() if p["rol"] == "Manager")

    def _compute_ovr(self):
        """Media 70-99 de cada jugador.

        Las stats de players.json son casi idénticas entre jugadores (plantillas por
        nivel), así que por sí solas no distinguen bien. La valoración combina:
          - price (valor del jugador)         60 %
          - stats ponderadas por su posición  30 %
          - número de supertécnicas           10 %
        y se reparte por percentil dentro de cada posición (empates = mismo percentil).
        Fuera de su posición natural un jugador pierde POSITION_PENALTY puntos."""
        def norm(values):
            lo, hi = min(values), max(values)
            return [(v - lo) / (hi - lo) if hi > lo else 0.5 for v in values]

        for pos, weights in POSITION_WEIGHTS.items():
            group = [p for p in self.players.values() if p["rol"] == "Player" and p["posicion"] == pos]
            if not group:
                continue
            price = norm([p["_price"] for p in group])
            stats = norm([sum(p["stats"][s] * w for s, w in weights.items()) for p in group])
            techs = norm([min(p["_ntech"], 15) for p in group])
            for p, a, b, c in zip(group, price, stats, techs):
                p["_score"] = 0.6 * a + 0.3 * b + 0.1 * c
            scores = sorted(p["_score"] for p in group)
            n = len(scores)
            for p in group:
                lo = bisect.bisect_left(scores, p["_score"])
                hi = bisect.bisect_right(scores, p["_score"])
                pct = ((lo + hi - 1) / 2) / max(n - 1, 1)      # rango medio en empates
                p["ovr"] = int(round(OVR_MIN + (OVR_MAX - OVR_MIN) * pct ** OVR_CURVE))

        for p in self.players.values():
            p.pop("_score", None); p.pop("_price", None); p.pop("_ntech", None)
            if p["rol"] != "Player":
                p["ovr"] = None
                continue
            p["ovr_pos"] = {pos: max(40, p["ovr"] - POSITION_PENALTY[(p["posicion"], pos)])
                            for pos in POSITION_WEIGHTS}

    def public(self, pid):
        return self.players[pid]


DB = None


def get_db():
    global DB
    if DB is None:
        DB = Database()
    return DB


# ---------------------------------------------------------------------------
# Sorteo de opciones (determinista por día y por jugador: cada persona tiene
# sus propias opciones, pero siempre las mismas si recarga la página)
# ---------------------------------------------------------------------------
def _rng(*parts):
    seed = hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()
    return random.Random(int(seed[:16], 16))


def slot_position(formation, slot_idx):
    """Posición de un hueco: 0-10 titulares, 11+ suplentes (None = cualquiera)."""
    if slot_idx < 11:
        return FORMATIONS[formation]["slots"][slot_idx][0]
    return None


def slot_options(day, formation, slot_idx, picks, user_seed, n=5):
    """5 opciones: casi siempre una élite (90+, con ELITE_GUARANTEE_CHANCE de
    probabilidad — no todos los sobres traen una), 1 compañero de equipo de
    un vecino, 1 de la saga de un vecino y el resto buenas. Determinista a
    partir del día, del jugador (user_seed) y de tus elecciones: cada persona
    ve una combinación distinta, pero siempre la misma si recarga la página.
    Para los suplentes salen jugadores de cualquier posición."""
    db = get_db()
    pos = slot_position(formation, slot_idx)
    rng = _rng(day, user_seed, formation, slot_idx)
    taken_names = {db.players[p]["nombre"] for p in picks if p}
    base_pool = db.pool[pos] if pos else sorted(pid for lst in db.pool.values() for pid in lst)
    pool = [pid for pid in base_pool if db.players[pid]["nombre"] not in taken_names]
    ovr = lambda pid: db.players[pid]["ovr"]
    elite = [pid for pid in pool if ovr(pid) >= 90]
    good = [pid for pid in pool if 82 <= ovr(pid) < 90]
    decent = [pid for pid in pool if ovr(pid) >= 77]

    mates, saga_mates = [], []
    if slot_idx < 11:
        neighbours = [db.players[picks[b if a == slot_idx else a]]
                      for a, b in FORMATIONS[formation]["links"]
                      if slot_idx in (a, b) and picks[b if a == slot_idx else a]]
        n_teams = {t for p in neighbours for t in p["equipos"]}
        mates = [pid for pid in pool if n_teams & set(db.players[pid]["equipos"])]
        # buena media y enlaza bien con los vecinos (saga, afinidad o arquetipo)
        need = max(2, len(neighbours))
        saga_mates = [pid for pid in decent
                      if sum(link_value(db.players[pid], nb) for nb in neighbours) >= need]
    for lst in (elite, good, decent, mates, saga_mates, pool):
        rng.shuffle(lst)

    chosen, names = [], set()

    def take(src, k):
        for pid in src:
            if k <= 0:
                break
            nm = db.players[pid]["nombre"]
            if pid in chosen or nm in names:
                continue
            chosen.append(pid); names.add(nm); k -= 1

    if elite and rng.random() < ELITE_GUARANTEE_CHANCE:
        take(elite, 1)
    take(mates, 1)
    take(saga_mates, 1)
    take(good, n - 1 - len(chosen))
    take(decent, n - len(chosen))
    take(pool, n - len(chosen))
    rng.shuffle(chosen)
    return chosen


def manager_options(day, user_seed, n=4):
    db = get_db()
    rng = _rng(day, user_seed, "manager")
    pool = list(db.managers)
    rng.shuffle(pool)
    out, names = [], set()
    for pid in pool:
        nm = db.players[pid]["nombre"]
        if nm not in names:
            out.append(pid); names.add(nm)
        if len(out) == n:
            break
    return out


def formation_options(day, user_seed):
    rng = _rng(day, user_seed, "formations")
    names = list(FORMATIONS)
    rng.shuffle(names)
    return names[:3]


# ---------------------------------------------------------------------------
# Química
# ---------------------------------------------------------------------------
ELEMENT_ES = {"Fire": "Fuego", "Wind": "Viento", "Forest": "Bosque", "Mountain": "Montaña"}


def link_reasons(a, b):
    """Motivos por los que dos jugadores tienen química, con sus puntos."""
    if a is None or b is None:
        return []
    out = []
    a_teams = a["equipos"] or [SCOUT_TEAM]
    b_teams = b["equipos"] or [SCOUT_TEAM]
    shared = set(a_teams) & set(b_teams)
    if shared == {SCOUT_TEAM}:
        out.append(("Ambos son ojeados (sin equipo)", CHEM_SAME_TEAM))
    elif shared:
        out.append(("Equipo: " + sorted(shared)[0], CHEM_SAME_TEAM))
    elif a["saga"] == b["saga"]:
        out.append(("Saga: " + a["saga"], CHEM_SAME_SAGA))
    if a.get("elemento") and a["elemento"] == b.get("elemento"):
        out.append(("Afinidad: " + ELEMENT_ES.get(a["elemento"], a["elemento"]), CHEM_SAME_ELEMENT))
    if a.get("arquetipo") and a["arquetipo"] != "Unknown" and a["arquetipo"] == b.get("arquetipo"):
        out.append(("Arquetipo: " + a["arquetipo"], CHEM_SAME_ARCHETYPE))
    return out


def link_value(a, b):
    return sum(v for _, v in link_reasons(a, b))


def points_to_chem(points):
    return sum(1 for t in CHEM_THRESHOLDS if points >= t)


def compute_chemistry(formation, picks, manager_id=None):
    """picks: lista de 11 ids (o None). Devuelve química por jugador, por enlace y total."""
    db = get_db()
    players = [db.players[p] if p else None for p in picks]
    manager = db.players[manager_id] if manager_id else None
    raw = [0] * len(players)
    links = []
    for a, b in FORMATIONS[formation]["links"]:
        reasons = link_reasons(players[a], players[b])
        v = sum(p for _, p in reasons)
        links.append({"a": a, "b": b, "value": v, "reasons": [f"{r} (+{p})" for r, p in reasons]})
        raw[a] += v
        raw[b] += v
    per_player = []
    for i, p in enumerate(players):
        if p is None:
            per_player.append(0)
            continue
        c = points_to_chem(raw[i])
        if manager and manager["saga"] == p["saga"]:
            c += MANAGER_BONUS
        per_player.append(min(CHEM_MAX_PLAYER, c))
    return {"per_player": per_player, "points": raw, "links": links, "total": sum(per_player)}


def ovr_at(pid, pos):
    return get_db().players[pid]["ovr_pos"][pos]


def compute_score(formation, picks, manager_id=None):
    """Solo cuentan los 11 titulares (picks[0:11]). La media de cada uno es la de la
    posición en la que está colocado."""
    starters = picks[:11]
    slots = FORMATIONS[formation]["slots"]
    chem = compute_chemistry(formation, starters, manager_id)
    ovrs = [ovr_at(p, slots[i][0]) for i, p in enumerate(starters) if p]
    rating = sum(ovrs) / len(ovrs) if ovrs else 0
    # Cada jugador rinde entre el 70% (0 química) y el 100% (3 de química)
    effective = sum(ovr_at(p, slots[i][0]) * (0.70 + 0.10 * chem["per_player"][i])
                    for i, p in enumerate(starters) if p)
    score = int(round(effective * 10 / 11)) if ovrs else 0
    return {"rating": round(rating, 1), "chem": chem["total"], "score": score, "chemistry": chem}
