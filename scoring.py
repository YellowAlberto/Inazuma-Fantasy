import json
import random
from collections import Counter

# Maps each season checkbox shown when creating a league to the underlying
# `juego` values stored on each player. Most seasons are 1:1, but Ares and
# Orion are merged into a single option since Orion barely has any
# characters of its own (not enough to field a team on its own).
# Ordered chronologically by release, which is also the order shown to users.
SEASON_GROUPS = {
    "Inazuma Eleven": ["Inazuma Eleven"],
    "Inazuma Eleven 2: Firestorm / Blizzard": ["Inazuma Eleven 2: Firestorm / Blizzard"],
    "Inazuma Eleven 3: Lightning Bolt / Bomb Blast / Team Ogre Attacks!": [
        "Inazuma Eleven 3: Lightning Bolt / Bomb Blast / Team Ogre Attacks!"
    ],
    "Inazuma Eleven GO: Light / Shadow": ["Inazuma Eleven GO: Light / Shadow"],
    "Inazuma Eleven GO Chrono Stones: Wildfire / Thunderflash": [
        "Inazuma Eleven GO Chrono Stones: Wildfire / Thunderflash"
    ],
    "Inazuma Eleven GO Galaxy: Big Bang / Supernova": ["Inazuma Eleven GO Galaxy: Big Bang / Supernova"],
    "Inazuma Eleven Ares/Orion": ["Inazuma Eleven Ares", "Inazuma Eleven Orion"],
    "Inazuma Eleven: Victory Road": ["Inazuma Eleven: Victory Road"],
}

# Official (or closest-to-official) Spanish titles, used only for display —
# the dict keys above (matching SEASON_GROUPS) are still used internally as
# the checkbox values/identifiers so existing leagues keep working.
SEASON_LABELS_ES = {
    "Inazuma Eleven": "Inazuma Eleven",
    "Inazuma Eleven 2: Firestorm / Blizzard": "Inazuma Eleven 2: Tormenta de Fuego / Ventisca Eterna",
    "Inazuma Eleven 3: Lightning Bolt / Bomb Blast / Team Ogre Attacks!":
        "Inazuma Eleven 3: Rayo Celeste / Fuego Explosivo / ¡La Amenaza del Ogro!",
    "Inazuma Eleven GO: Light / Shadow": "Inazuma Eleven GO: Luz y Sombra",
    "Inazuma Eleven GO Chrono Stones: Wildfire / Thunderflash":
        "Inazuma Eleven GO Chrono Stones: Llamarada / Trueno",
    "Inazuma Eleven GO Galaxy: Big Bang / Supernova": "Inazuma Eleven GO Galaxy: Big Bang / Supernova",
    "Inazuma Eleven Ares/Orion": "Inazuma Eleven Ares / Orion",
    "Inazuma Eleven: Victory Road": "Inazuma Eleven: Heroes' Victory Road",
}

# Flattened list of raw `juego` values (what's actually stored in the DB and
# used for filtering), regardless of how they're grouped for display.
ALL_SEASONS = [juego for juegos in SEASON_GROUPS.values() for juego in juegos]


def season_group_label(juego):
    """Reverse lookup: maps a raw `juego` DB value back to the checkbox
    label it belongs to (e.g. both 'Inazuma Eleven Ares' and 'Inazuma Eleven
    Orion' map back to 'Inazuma Eleven Ares/Orion')."""
    for label, juegos in SEASON_GROUPS.items():
        if juego in juegos:
            return label
    return juego


def season_label_es(label):
    """Spanish display text for a season checkbox label, falling back to
    the internal (English) label if there's no translation on file."""
    return SEASON_LABELS_ES.get(label, label)

# Selectable formations. GK is always exactly 1 and isn't listed here.
FORMATIONS = {
    "4-4-2": {"DF": 4, "MF": 4, "FW": 2},
    "4-3-3": {"DF": 4, "MF": 3, "FW": 3},
    "3-5-2": {"DF": 3, "MF": 5, "FW": 2},
    "3-4-3": {"DF": 3, "MF": 4, "FW": 3},
    "5-3-2": {"DF": 5, "MF": 3, "FW": 2},
    "5-4-1": {"DF": 5, "MF": 4, "FW": 1},
}
DEFAULT_FORMATION = "4-4-2"

# The free starting squad always uses a 4-4-2 shape (1 GK, 4 DF, 4 MF, 2 FW).
STARTER_FORMATION = [("GK", 1), ("DF", 4), ("MF", 4), ("FW", 2)]

# The weekly market offers 22 players, proportioned the same way as a squad
# (double the starter formation), so there's always a healthy spread of
# every position available to bid on.
MARKET_POSITION_COUNTS = {"GK": 2, "DF": 8, "MF": 8, "FW": 4}
MARKET_SIZE = sum(MARKET_POSITION_COUNTS.values())

# Every league gets its own fixed pool of players at creation time, and the
# weekly market only ever draws from that pool (rotating through it,
# shrinking as players get bought) instead of the whole database. This is
# what creates real scarcity: with the entire database up for grabs, there's
# always "one more" fresh batch next week, so nobody feels pressure to bid
# on anyone. A bounded pool means a coveted player who slips away might not
# come back, and eventually the pool itself runs low, forcing decisions.
# Sized as "enough distinct players for ~N weeks of full rotation before
# repeats start", proportioned the same way as the weekly market itself.
POOL_ROTATION_WEEKS = 8
POOL_POSITION_COUNTS = {pos: count * POOL_ROTATION_WEEKS for pos, count in MARKET_POSITION_COUNTS.items()}

POSITION_LABELS = {"GK": "Portero", "DF": "Defensa", "MF": "Centrocampista", "FW": "Delantero"}

# Player prices/budgets are a plain point currency (no "millions" unit).
# Using a wide integer range with a little random jitter gives fairer, more
# granular bidding than a narrow 0.5-10.0 scale, and avoids every price
# looking suspiciously round.
PRICE_MIN = 8
PRICE_RANGE = 140  # so top-tier players land around 145-150


def compute_price_from_form_index(form_index, jitter=True):
    """Turns a player's 0-1 quality percentile (form_index) into a point
    price. Adds a small random nudge so values aren't perfectly tidy."""
    base = PRICE_MIN + (form_index or 0) * PRICE_RANGE
    if jitter:
        base += random.uniform(-4, 4)
    return max(3, round(base))


def formation_requirements(formation_key):
    """Returns the full {position: count} requirement for a formation,
    including the fixed goalkeeper."""
    shape = FORMATIONS.get(formation_key, FORMATIONS[DEFAULT_FORMATION])
    return {"GK": 1, **shape}


# ---------------------------------------------------------------------------
# Event-based scoring: goals, assists, saves and clean sheets simulated from
# each player's actual stats, instead of one blended random number. Feels
# closer to a real fantasy-football scoreboard and is easy to narrate
# ("⚽ 2 goles · 🅰️ 1 asistencia") in the gameweek results screen.
# ---------------------------------------------------------------------------
APPEARANCE_POINTS = 2
ASSIST_POINTS = 3
GOAL_POINTS = {"GK": 10, "DF": 7, "MF": 6, "FW": 5}
CLEAN_SHEET_POINTS = {"GK": 3, "DF": 3, "MF": 2, "FW": 1}
SAVE_POINTS = 1
STEAL_POINTS = 1
STEAL_POINTS_CAP = 3  # per player, so one very active tackler can't outscore goals
LOSS_POINTS = -1
LOSS_POINTS_CAP = 3  # per player, so one unlucky player can't be tanked into deep negatives
GOALS_CONCEDED_DIVISOR = {"GK": 2, "DF": 2, "MF": 4, "FW": 4}  # -1 point every N goals conceded
KEY_PASS_POINTS = 1
KEY_PASS_POINTS_CAP = 3
CLEARANCE_POINTS = 1
CLEARANCE_POINTS_CAP = 3
INTERCEPTION_POINTS = 1
INTERCEPTION_POINTS_CAP = 3
BLOCK_POINTS_PER_UNIT = 2  # every N blocks = 1 point
BLOCK_POINTS_CAP = 6  # raw blocks counted toward points (max +3)

ACTIONS_PER_MATCH = 80

# The classic Inazuma Eleven elemental rock-paper-scissors cycle: each
# element is strong against the next one and weak against the previous one.
ELEMENT_ADVANTAGE = {
    "Fire": "Forest",
    "Forest": "Wind",
    "Wind": "Mountain",
    "Mountain": "Fire",
}
ELEMENT_DUEL_BONUS = 0.10


def element_duel_bonus(attacker_element, defender_element):
    """Returns (attacker_bonus, defender_bonus) for a direct 1v1 duel (a
    tackle, or a shot against a keeper) based on the elemental cycle. Only
    the side with the type advantage gets a nudge, same as in the games."""
    if ELEMENT_ADVANTAGE.get(attacker_element) == defender_element:
        return ELEMENT_DUEL_BONUS, 0.0
    if ELEMENT_ADVANTAGE.get(defender_element) == attacker_element:
        return 0.0, ELEMENT_DUEL_BONUS
    return 0.0, 0.0


# ---------------------------------------------------------------------------
# Super técnicas — a rare, flashy move (Inazuma Eleven style) that GUARANTEES
# whatever the player is attempting: a save, a block, a dribble past a
# defender, or a goal. Only real, correctly-categorized hissatsu técnicas
# from the game are ever used — no invented/generic names. A player with no
# real técnica on file for a given category simply never triggers one there.
# ---------------------------------------------------------------------------
SUPER_TECHNIQUE_CHANCE = 0.05


def roll_super_technique(player, category):
    """A small, flat chance that this player pulls off a super técnica this
    action. `category` must be one of 'tiro', 'porteria', 'defensa', 'regate'
    — only a técnica actually classified under that category (e.g. never a
    dribble move for a shot) will ever be picked. Returns the technique's
    real name, or None if this player has no real técnica on file for that
    category (no invented/generic names are ever used as a stand-in)."""
    if not player or random.random() >= SUPER_TECHNIQUE_CHANCE:
        return None

    raw = player.get("tecnicas_por_tipo")
    if not raw:
        return None
    try:
        by_type = raw if isinstance(raw, dict) else json.loads(raw)
    except (ValueError, TypeError):
        return None
    real_names = by_type.get(category) if by_type else None
    if not real_names:
        return None
    return random.choice(real_names)


def _normalize_stat(value):
    """Inazuma Eleven stats typically run ~95-175; squash that onto 0-1."""
    return max(0.0, min(1.0, ((value or 0) - 95) / 80))


def _pick_weighted(players, weight_fn):
    weights = [max(0.02, weight_fn(p)) for p in players]
    return random.choices(players, weights=weights, k=1)[0]


def _compute_lineup_y_positions(lineup):
    """Assigns each player a rough lateral pitch position (0-100, same idea
    as the y-coordinate used to lay them out on the visual pitch) based on
    where they sit within their position line, spread evenly. This gives
    the engine a notion of 'nearby' vs 'far away' so, for example, the
    right-sided midfielder who has the ball tends to be closed down by a
    nearby defender/midfielder rather than by whoever happens to be picked
    at random anywhere on the pitch."""
    by_pos = {}
    for p in lineup:
        by_pos.setdefault(p["posicion"], []).append(p)
    y_by_id = {}
    for players in by_pos.values():
        n = len(players)
        for i, p in enumerate(players):
            y_by_id[p["id"]] = (i + 1) / (n + 1) * 100
    return y_by_id


def _proximity_weight(attacker_y, candidate_y):
    """Multiplier applied on top of a defender's stats: close to the
    attacker's lateral position -> boosted; far away -> reduced (but never
    to zero — real defenses do sometimes shift/cover across the pitch)."""
    if attacker_y is None or candidate_y is None:
        return 1.0
    distance = abs(attacker_y - candidate_y)
    return max(0.3, 1.8 - 1.5 * (distance / 100))


def _nearby_pool(candidates, ball_y, y_positions, max_distance=40):
    """Restricts a pool of candidates to those actually near the ball's
    current lateral position — a player standing on the far side of the
    pitch simply isn't involved in this particular play. Falls back to the
    full pool if nobody qualifies, so the simulation never gets stuck with
    an empty choice."""
    nearby = [p for p in candidates if abs(ball_y - y_positions.get(p["id"], 50)) <= max_distance]
    return nearby or candidates


class MarkingMemory:
    """Experimental: a lightweight persistent 'state' for defenders. Once a
    defender starts marking a specific attacker, they tend to keep marking
    that same player for a stretch of actions — a simple state machine
    (assigned / not-assigned, with a countdown) instead of every challenge
    re-rolling from scratch with no memory of what just happened."""

    STICKY_CHANCE = 0.7  # how often we honor the existing assignment vs re-rolling
    DEFAULT_DURATION = 22  # actions an assignment survives before going stale (most of a match)

    def __init__(self):
        self._assignments = {}  # attacker_id -> [defender_id, actions_left]

    def get_marker(self, attacker_id, eligible_ids):
        entry = self._assignments.get(attacker_id)
        if not entry:
            return None
        defender_id, _ = entry
        if defender_id not in eligible_ids:
            return None
        if random.random() >= self.STICKY_CHANCE:
            return None  # sometimes the marker gets dragged away regardless
        return defender_id

    def assign(self, attacker_id, defender_id, duration=None):
        self._assignments[attacker_id] = [defender_id, duration or self.DEFAULT_DURATION]

    def tick(self):
        for attacker_id in list(self._assignments.keys()):
            self._assignments[attacker_id][1] -= 1
            if self._assignments[attacker_id][1] <= 0:
                del self._assignments[attacker_id]


def _team_possession_strength(lineup):
    outfield = [p for p in lineup if p["posicion"] != "GK"] or lineup
    if not outfield:
        return 0.3
    total = sum(
        _normalize_stat(p["control"]) + _normalize_stat(p["tecnica"]) + _normalize_stat(p["potencia"])
        for p in outfield
    )
    return total / (len(outfield) * 3)


def simulate_market_player_points(player):
    """A lightweight, standalone point estimate for a player nobody in the
    league currently owns. Doesn't run the full match engine (there's no
    real fixture for them) — just a quick quality-based simulation so
    unclaimed market players build up a "points hechos" track record and
    give bidders a reason to want them."""
    pos = player["posicion"]
    shooting = _normalize_stat(player["potencia"]) * 0.5 + _normalize_stat(player["tecnica"]) * 0.5
    defending = _normalize_stat(player["fisico"]) * 0.5 + _normalize_stat(player["presion"]) * 0.5
    goalkeeping = _normalize_stat(player["agilidad"]) * 0.5 + _normalize_stat(player["inteligencia"]) * 0.5
    quality = {"FW": shooting, "MF": (shooting + defending) / 2, "DF": defending, "GK": goalkeeping}.get(pos, 0.5)
    base = APPEARANCE_POINTS + quality * 8
    points = round(base + random.uniform(-2, 2))
    return max(0, points)


class _PlayerLog:
    """Accumulates raw match events for one player as the simulation runs."""

    __slots__ = ("player", "goals", "assists", "saves", "steals", "losses", "key_passes", "clearances", "interceptions", "blocks")

    def __init__(self, player):
        self.player = player
        self.goals = 0
        self.assists = 0
        self.saves = 0
        self.steals = 0
        self.losses = 0
        self.key_passes = 0
        self.clearances = 0
        self.interceptions = 0
        self.blocks = 0


def simulate_fixture(home_lineup, home_label, away_lineup, away_label, use_marking_memory=False):
    """Simulates a full 90-minute match as ~50 discrete actions (passes,
    tackles, shots, saves, goals...) instead of rolling each player's
    gameweek independently. The scoreline, clean sheets and each player's
    points all fall out of the same sequence of events, and a chronological
    commentary of the notable moments is produced for display.

    Returns a dict with home/away goals & fantasy points, a per-player
    {player_id: (points, breakdown_lines)} map for each side, and a
    "timeline" list of narrated highlight lines.

    `use_marking_memory` (experimental, default off): when True, defenders
    tend to keep marking the same attacker for a stretch of actions instead
    of a fresh random pick every single challenge — a small persistent
    state per player rather than no memory at all between actions.
    """
    sides = {
        "home": {
            "lineup": home_lineup,
            "label": home_label,
            "outfield": [p for p in home_lineup if p["posicion"] != "GK"],
            "gk": next((p for p in home_lineup if p["posicion"] == "GK"), None),
            "logs": {p["id"]: _PlayerLog(p) for p in home_lineup},
            "last_passer": None,
            "shots": 0,
            "affinities": active_match_affinities(home_lineup),
            "y_positions": _compute_lineup_y_positions(home_lineup),
            "marking": MarkingMemory() if use_marking_memory else None,
        },
        "away": {
            "lineup": away_lineup,
            "label": away_label,
            "outfield": [p for p in away_lineup if p["posicion"] != "GK"],
            "gk": next((p for p in away_lineup if p["posicion"] == "GK"), None),
            "logs": {p["id"]: _PlayerLog(p) for p in away_lineup},
            "last_passer": None,
            "shots": 0,
            "affinities": active_match_affinities(away_lineup),
            "y_positions": _compute_lineup_y_positions(away_lineup),
            "marking": MarkingMemory() if use_marking_memory else None,
        },
    }

    home_strength = _team_possession_strength(home_lineup)
    away_strength = _team_possession_strength(away_lineup)
    total_strength = home_strength + away_strength or 1
    # Keep possession bounded so even weak teams get a fair share of the ball.
    home_possession_prob = 0.3 + 0.4 * (home_strength / total_strength)

    # Momentum: the team that just won the ball (a tackle, interception,
    # save, or a rebound off a blocked shot) is more likely to keep it for
    # the next couple of actions, like a real spell of pressure — instead
    # of every action being an independent coin flip regardless of what
    # just happened. It always relaxes back toward each side's underlying
    # quality (home_possession_prob) rather than staying stuck.
    momentum = home_possession_prob
    MOMENTUM_DECAY = 0.18
    MOMENTUM_PUSH = 0.3

    def shift_momentum(winner_key):
        nonlocal momentum
        target = 1.0 if winner_key == "home" else 0.0
        momentum = momentum + (target - momentum) * MOMENTUM_PUSH

    running_goals = {"home": 0, "away": 0}
    # The ball's actual lateral position on the pitch (0-100), carried over
    # between actions — only players actually near it can be involved in
    # the next play, instead of picking anyone on the pitch at random
    # regardless of where the action is really happening.
    ball_y = 50.0

    timeline = []
    minutes = sorted(random.randint(1, 90) for _ in range(ACTIONS_PER_MATCH))

    for minute in minutes:
        # Relax back toward the baseline set by each team's real quality.
        momentum = momentum + (home_possession_prob - momentum) * MOMENTUM_DECAY
        side_key = "home" if random.random() < momentum else "away"
        other_key = "away" if side_key == "home" else "home"
        side, other = sides[side_key], sides[other_key]

        if use_marking_memory:
            sides["home"]["marking"].tick()
            sides["away"]["marking"].tick()

        # Widened on purpose: this decides who's generally "involved" in
        # advancing the play, which should stay fairly open (real football
        # doesn't confine attackers to a narrow band), unlike defensive
        # marking further down, which uses a much tighter radius.
        attackers = _nearby_pool(side["outfield"] or side["lineup"], ball_y, side["y_positions"], max_distance=65)
        if not attackers:
            continue

        roll = random.random()

        if roll >= 0.82:
            # Shot attempt: realistically forwards take the large majority
            # of shots, midfielders a fair share, and defenders only rarely
            # (set pieces, a rare forward burst) — not just whoever happens
            # to have the best stats today, regardless of position.
            def _shot_weight(p):
                base = _normalize_stat(p["potencia"]) + _normalize_stat(p["tecnica"]) + _normalize_stat(p["control"])
                position_factor = {"FW": 1.6, "MF": 0.75, "DF": 0.18}.get(p["posicion"], 1.0)
                return base * position_factor

            attacker = _pick_weighted(attackers, _shot_weight)
        else:
            attacker = _pick_weighted(
                attackers,
                lambda p: _normalize_stat(p["potencia"]) + _normalize_stat(p["tecnica"]) + _normalize_stat(p["control"]),
            )

        # The ball is now with whoever we just picked as the protagonist of
        # this action — the next action's participants get judged against
        # this new spot, not the old one.
        ball_y = side["y_positions"].get(attacker["id"], ball_y)

        if roll < 0.28:
            # Buildup pass: the ball carrier usually looks to pass it on —
            # most often a short, safe ball to whoever's nearest, with a
            # smaller chance of a longer diagonal ball / switch of play to
            # someone farther away — but sometimes decides to carry it
            # themselves (a dribble) instead of passing at all.
            attacker_y_here = side["y_positions"].get(attacker["id"], 50)
            teammates = [p for p in attackers if p["id"] != attacker["id"]]
            if teammates and random.random() >= 0.25:
                recipient = _pick_weighted(
                    teammates,
                    lambda p: _proximity_weight(attacker_y_here, side["y_positions"].get(p["id"])),
                )
                ball_y = side["y_positions"].get(recipient["id"], ball_y)
            # else: keeps it themselves — the ball stays right where they are.

            side["last_passer"] = attacker
            # Afinidad: sharp team chemistry turns some routine passes into
            # genuine chances on their own.
            if "Afinidad" in side["affinities"] and random.random() < 0.35 * affinity_scale(side["affinities"]["Afinidad"]):
                log = side["logs"][attacker["id"]]
                log.key_passes += 1
                if log.key_passes <= KEY_PASS_POINTS_CAP:
                    timeline.append((
                        minute,
                        f"🔑 Pase clave de {attacker['nombre']} ({side['label']})",
                        {"minute": minute, "type": "key_pass", "side": side_key, "player": attacker["nombre"], "player_id": attacker["id"], "sprite_url": attacker.get("sprite_url")},
                    ))

        elif roll < 0.40:
            # Key pass: a playmaker threads a dangerous ball through. Earns a
            # small reward on its own, and counts as a strong assist setup.
            passer_pool = [p for p in attackers if p["posicion"] in ("MF", "FW")] or attackers
            passer = _pick_weighted(
                passer_pool,
                lambda p: _normalize_stat(p["control"]) + _normalize_stat(p["tecnica"]),
            )
            log = side["logs"][passer["id"]]
            log.key_passes += 1
            side["last_passer"] = passer
            if log.key_passes <= KEY_PASS_POINTS_CAP:
                timeline.append((
                    minute,
                    f"🔑 Pase clave de {passer['nombre']} ({side['label']})",
                    {"minute": minute, "type": "key_pass", "side": side_key, "player": passer["nombre"], "player_id": passer["id"], "sprite_url": passer.get("sprite_url")},
                ))

        elif roll < 0.60:
            # A defender/midfielder from the other side tries a tackle.
            defenders_pool = _nearby_pool(
                [p for p in other["lineup"] if p["posicion"] in ("DF", "MF")] or other["outfield"],
                ball_y,
                other["y_positions"],
            )
            if defenders_pool:
                attacker_y = side["y_positions"].get(attacker["id"])
                by_id = {p["id"]: p for p in defenders_pool}
                marked_id = other["marking"].get_marker(attacker["id"], by_id.keys()) if other["marking"] else None
                if marked_id:
                    defender = by_id[marked_id]
                else:
                    defender = _pick_weighted(
                        defenders_pool,
                        lambda p: (_normalize_stat(p["fisico"]) + _normalize_stat(p["presion"]))
                        * _proximity_weight(attacker_y, other["y_positions"].get(p["id"])),
                    )
                    if other["marking"]:
                        other["marking"].assign(attacker["id"], defender["id"])
                # Super técnica: a flashy move guarantees the outcome outright,
                # skipping the normal stat-based duel. The defender's technique
                # takes priority if both happen to roll one on the same play.
                defender_technique = roll_super_technique(defender, "defensa")
                attacker_technique = None if defender_technique else roll_super_technique(attacker, "regate")

                if defender_technique:
                    log = other["logs"][defender["id"]]
                    log.steals += 1
                    side["logs"][attacker["id"]].losses += 1
                    shift_momentum(other_key)
                    ball_y = other["y_positions"].get(defender["id"], ball_y)
                    if log.steals <= STEAL_POINTS_CAP:
                        timeline.append((
                            minute,
                            f"✨ ¡SÚPER TÉCNICA! {defender['nombre']} despliega {defender_technique} y le quita el balón a {attacker['nombre']} ({other['label']})",
                            {"minute": minute, "type": "steal", "side": other_key, "player": defender["nombre"], "player_id": defender["id"], "sprite_url": defender.get("sprite_url"), "super_technique": True, "technique_name": defender_technique},
                        ))
                elif attacker_technique:
                    # Guaranteed dribble: the attacker keeps the ball with flair.
                    side["last_passer"] = attacker
                    timeline.append((
                        minute,
                        f"✨ ¡SÚPER TÉCNICA! {attacker['nombre']} regatea a {defender['nombre']} con {attacker_technique} ({side['label']})",
                        {"minute": minute, "type": "dribble", "side": side_key, "player": attacker["nombre"], "player_id": attacker["id"], "sprite_url": attacker.get("sprite_url"), "super_technique": True, "technique_name": attacker_technique},
                    ))
                    continue
                else:
                    attack_skill = _normalize_stat(attacker["control"]) + _normalize_stat(attacker["tecnica"])
                    defend_skill = _normalize_stat(defender["fisico"]) + _normalize_stat(defender["presion"])
                    # Elemental duel: Fuego > Bosque > Viento > Montaña > Fuego.
                    atk_elem_bonus, def_elem_bonus = element_duel_bonus(attacker.get("elemento"), defender.get("elemento"))
                    attack_skill += atk_elem_bonus
                    defend_skill += def_elem_bonus
                    # Juego Sucio: a more aggressive, no-nonsense press wins the
                    # ball back more often.
                    if "Juego Sucio" in other["affinities"]:
                        defend_skill += 0.15 * affinity_scale(other["affinities"]["Juego Sucio"])
                    if defend_skill + random.uniform(0, 0.35) > attack_skill:
                        log = other["logs"][defender["id"]]
                        log.steals += 1
                        side["logs"][attacker["id"]].losses += 1
                        shift_momentum(other_key)
                        ball_y = other["y_positions"].get(defender["id"], ball_y)
                        if log.steals <= STEAL_POINTS_CAP:
                            timeline.append((
                                minute,
                                f"🛡️ Robo de balón de {defender['nombre']} ({other['label']})",
                                {"minute": minute, "type": "steal", "side": other_key, "player": defender["nombre"], "player_id": defender["id"], "sprite_url": defender.get("sprite_url")},
                            ))
                    elif "Brecha" in other["affinities"] and random.random() < 0.35 * affinity_scale(other["affinities"]["Brecha"]):
                        # Brecha: even when the tackle itself fails, a sharp
                        # defensive read cleans up the danger anyway.
                        log = other["logs"][defender["id"]]
                        log.clearances += 1
                        shift_momentum(other_key)
                        ball_y = other["y_positions"].get(defender["id"], ball_y)
                        if log.clearances <= CLEARANCE_POINTS_CAP:
                            timeline.append((
                                minute,
                                f"🧹 Despeje de {defender['nombre']} ({other['label']})",
                                {"minute": minute, "type": "clearance", "side": other_key, "player": defender["nombre"], "player_id": defender["id"], "sprite_url": defender.get("sprite_url")},
                            ))
            side["last_passer"] = None

        elif roll < 0.72:
            # Clearance: a defender (or keeper) hoofs a dangerous ball away.
            clear_pool = [p for p in other["lineup"] if p["posicion"] in ("DF", "GK")] or other["outfield"]
            if clear_pool:
                defender = _pick_weighted(
                    clear_pool,
                    lambda p: _normalize_stat(p["fisico"]) + _normalize_stat(p["presion"]),
                )
                log = other["logs"][defender["id"]]
                log.clearances += 1
                shift_momentum(other_key)
                ball_y = other["y_positions"].get(defender["id"], ball_y)
                if log.clearances <= CLEARANCE_POINTS_CAP:
                    timeline.append((
                        minute,
                        f"🧹 Despeje de {defender['nombre']} ({other['label']})",
                        {"minute": minute, "type": "clearance", "side": other_key, "player": defender["nombre"], "player_id": defender["id"], "sprite_url": defender.get("sprite_url")},
                    ))
            side["last_passer"] = None

        elif roll < 0.82:
            # Interception: a midfielder reads the game and cuts out the pass.
            intercept_pool = _nearby_pool(
                [p for p in other["lineup"] if p["posicion"] == "MF"] or other["outfield"],
                ball_y,
                other["y_positions"],
            )
            if intercept_pool:
                attacker_y = side["y_positions"].get(attacker["id"])
                by_id = {p["id"]: p for p in intercept_pool}
                marked_id = other["marking"].get_marker(attacker["id"], by_id.keys()) if other["marking"] else None
                if marked_id:
                    defender = by_id[marked_id]
                else:
                    defender = _pick_weighted(
                        intercept_pool,
                        lambda p: (_normalize_stat(p["inteligencia"]) + _normalize_stat(p["presion"]))
                        * _proximity_weight(attacker_y, other["y_positions"].get(p["id"])),
                    )
                    if other["marking"]:
                        other["marking"].assign(attacker["id"], defender["id"])
                log = other["logs"][defender["id"]]
                log.interceptions += 1
                shift_momentum(other_key)
                ball_y = other["y_positions"].get(defender["id"], ball_y)
                if log.interceptions <= INTERCEPTION_POINTS_CAP:
                    timeline.append((
                        minute,
                        f"🎯 Intercepción de {defender['nombre']} ({other['label']})",
                        {"minute": minute, "type": "interception", "side": other_key, "player": defender["nombre"], "player_id": defender["id"], "sprite_url": defender.get("sprite_url")},
                    ))
            side["last_passer"] = None

        else:
            # Shot on goal.
            side["shots"] += 1
            shot_power = _normalize_stat(attacker["potencia"]) + _normalize_stat(attacker["tecnica"])

            # A nearby defender might smother the shot before it even
            # reaches the keeper — a last-ditch block, distinct from a
            # goalkeeper save and from a routine clearance/interception.
            block_pool = _nearby_pool(
                [p for p in other["lineup"] if p["posicion"] in ("DF", "MF")] or other["outfield"],
                ball_y,
                other["y_positions"],
            )
            blocker = None
            block_technique = None
            if block_pool:
                attacker_y = side["y_positions"].get(attacker["id"])
                by_id = {p["id"]: p for p in block_pool}
                marked_id = other["marking"].get_marker(attacker["id"], by_id.keys()) if other["marking"] else None
                if marked_id:
                    candidate = by_id[marked_id]
                else:
                    candidate = _pick_weighted(
                        block_pool,
                        lambda p: (_normalize_stat(p["fisico"]) + _normalize_stat(p["presion"]))
                        * _proximity_weight(attacker_y, other["y_positions"].get(p["id"])),
                    )
                    if other["marking"]:
                        other["marking"].assign(attacker["id"], candidate["id"])
                block_technique = roll_super_technique(candidate, "defensa")
                if block_technique:
                    blocker = candidate
                else:
                    block_skill = _normalize_stat(candidate["fisico"]) + _normalize_stat(candidate["presion"])
                    block_chance = 0.1 + max(0.0, block_skill - shot_power) * 0.25
                    if random.random() < min(0.3, block_chance):
                        blocker = candidate

            if blocker:
                log = other["logs"][blocker["id"]]
                log.blocks += 1
                shift_momentum(other_key)
                ball_y = other["y_positions"].get(blocker["id"], ball_y)
                if log.blocks <= BLOCK_POINTS_CAP:
                    if block_technique:
                        timeline.append((
                            minute,
                            f"✨ ¡SÚPER TÉCNICA! {blocker['nombre']} bloquea el disparo con {block_technique} ({other['label']})",
                            {"minute": minute, "type": "block", "side": other_key, "player": blocker["nombre"], "player_id": blocker["id"], "sprite_url": blocker.get("sprite_url"), "super_technique": True, "technique_name": block_technique},
                        ))
                    else:
                        timeline.append((
                            minute,
                            f"🚧 Bloqueo de {blocker['nombre']} ({other['label']})",
                            {"minute": minute, "type": "block", "side": other_key, "player": blocker["nombre"], "player_id": blocker["id"], "sprite_url": blocker.get("sprite_url")},
                        ))
                side["last_passer"] = None
                continue

            gk = other["gk"]
            save_power = (
                (_normalize_stat(gk["agilidad"]) + _normalize_stat(gk["inteligencia"])) if gk else 0.5
            )
            # Elemental duel: Fuego > Bosque > Viento > Montaña > Fuego.
            if gk:
                atk_elem_bonus, def_elem_bonus = element_duel_bonus(attacker.get("elemento"), gk.get("elemento"))
                shot_power += atk_elem_bonus
                save_power += def_elem_bonus
            goal_chance = 0.26 + (shot_power - save_power) * 0.3

            # Shooting angle: a shot from right in front of goal is a much
            # better chance than one from a tight angle out near the touch
            # line — reuse the shooter's own lateral position for this.
            shooter_y = side["y_positions"].get(attacker["id"], 50)
            angle_penalty = abs(shooter_y - 50) / 50  # 0 = dead center, 1 = touchline
            goal_chance -= angle_penalty * 0.13

            # Justicia: a well-organized defensive block is harder to break down.
            if "Justicia" in other["affinities"]:
                goal_chance -= 0.07 * affinity_scale(other["affinities"]["Justicia"])
            # Contraataque: clinical when soaking up pressure and hitting on
            # the break, i.e. when this side doesn't dominate possession.
            if "Contraataque" in side["affinities"]:
                side_possession = home_possession_prob if side_key == "home" else (1 - home_possession_prob)
                if side_possession < 0.5:
                    goal_chance += 0.08 * affinity_scale(side["affinities"]["Contraataque"])
            # Tensión: thrives under pressure in the closing stages.
            if "Tensión" in side["affinities"] and minute >= 70:
                goal_chance += 0.08 * affinity_scale(side["affinities"]["Tensión"])

            # Cautious opening exchanges: both sides feel each other out
            # before really committing to chances.
            if minute <= 8:
                goal_chance -= 0.05
            # Real matches ebb and flow with the scoreline, not just raw
            # quality: a team behind late on throws bodies forward (more
            # to gain by pushing), while a team ahead tends to see out the
            # game a bit more cautiously (less to gain from a risky punt).
            if minute >= 75:
                goal_diff = running_goals[side_key] - running_goals[other_key]
                if goal_diff < 0:
                    goal_chance += 0.09
                elif goal_diff > 0:
                    goal_chance -= 0.05

            goal_chance = max(0.05, min(0.55, goal_chance))

            shot_technique = roll_super_technique(attacker, "tiro")
            save_technique = None if shot_technique else (roll_super_technique(gk, "porteria") if gk else None)

            if shot_technique or random.random() < goal_chance:
                log = side["logs"][attacker["id"]]
                log.goals += 1
                running_goals[side_key] += 1
                assist_note = ""
                assist_name = None
                assist_id = None
                provider = side["last_passer"]
                if provider and provider["id"] != attacker["id"]:
                    side["logs"][provider["id"]].assists += 1
                    assist_note = f" (asistencia de {provider['nombre']})"
                    assist_name = provider["nombre"]
                    assist_id = provider["id"]
                if shot_technique:
                    timeline.append((
                        minute,
                        f"✨ ¡SÚPER TÉCNICA! ¡GOL! {attacker['nombre']} anota con {shot_technique}{assist_note} ({side['label']})",
                        {"minute": minute, "type": "goal", "side": side_key, "player": attacker["nombre"], "player_id": attacker["id"], "sprite_url": attacker.get("sprite_url"), "assist": assist_name, "assist_id": assist_id, "super_technique": True, "technique_name": shot_technique},
                    ))
                else:
                    timeline.append((
                        minute,
                        f"⚽ ¡GOL! {attacker['nombre']} ({side['label']}){assist_note}",
                        {"minute": minute, "type": "goal", "side": side_key, "player": attacker["nombre"], "player_id": attacker["id"], "sprite_url": attacker.get("sprite_url"), "assist": assist_name, "assist_id": assist_id},
                    ))
                # Kick-off restart: possession is anyone's game again,
                # rather than carrying over the scorer's momentum.
                momentum = home_possession_prob
                ball_y = 50.0
            elif gk and (save_technique or random.random() < 0.6):
                other["logs"][gk["id"]].saves += 1
                shift_momentum(other_key)
                ball_y = 50.0
                if save_technique:
                    timeline.append((
                        minute,
                        f"✨ ¡SÚPER TÉCNICA! {gk['nombre']} detiene el disparo con {save_technique} ({other['label']})",
                        {"minute": minute, "type": "save", "side": other_key, "player": gk["nombre"], "player_id": gk["id"], "sprite_url": gk.get("sprite_url"), "super_technique": True, "technique_name": save_technique},
                    ))
                else:
                    timeline.append((
                        minute,
                        f"🧤 Parada de {gk['nombre']} ({other['label']})",
                        {"minute": minute, "type": "save", "side": other_key, "player": gk["nombre"], "player_id": gk["id"], "sprite_url": gk.get("sprite_url")},
                    ))
            else:
                shift_momentum(other_key)
                timeline.append((
                    minute,
                    f"🚫 Disparo fuera de {attacker['nombre']} ({side['label']})",
                    {"minute": minute, "type": "shot_off", "side": side_key, "player": attacker["nombre"], "player_id": attacker["id"], "sprite_url": attacker.get("sprite_url")},
                ))

            side["last_passer"] = None

    timeline.sort(key=lambda t: t[0])
    timeline_lines = [line for _, line, _ in timeline]
    raw_events = [ev for _, _, ev in timeline]

    home_goals = sum(log.goals for log in sides["home"]["logs"].values())
    away_goals = sum(log.goals for log in sides["away"]["logs"].values())
    home_clean_sheet = away_goals == 0
    away_clean_sheet = home_goals == 0
    if home_clean_sheet:
        timeline_lines.append(f"🛡️ Portería a cero para {home_label}")
        raw_events.append({"minute": 90, "type": "clean_sheet", "side": "home", "player": None})
    if away_clean_sheet:
        timeline_lines.append(f"🛡️ Portería a cero para {away_label}")
        raw_events.append({"minute": 90, "type": "clean_sheet", "side": "away", "player": None})

    def score_side(side, clean_sheet, goals_conceded):
        affinity = compute_affinity_bonuses(side["lineup"])
        total_points = 0
        player_results = {}
        for p in side["lineup"]:
            log = side["logs"][p["id"]]
            pos = p["posicion"]
            points = APPEARANCE_POINTS
            breakdown = [f"✅ Jugó (+{APPEARANCE_POINTS})"]

            if log.goals:
                pts = log.goals * GOAL_POINTS[pos]
                points += pts
                breakdown.append(f"⚽ {log.goals} gol{'es' if log.goals > 1 else ''} (+{pts})")
            if log.assists:
                pts = log.assists * ASSIST_POINTS
                points += pts
                breakdown.append(f"🅰️ {log.assists} asistencia{'s' if log.assists > 1 else ''} (+{pts})")
            if log.saves:
                points += log.saves * SAVE_POINTS
                breakdown.append(f"🧤 {log.saves} parada{'s' if log.saves > 1 else ''} (+{log.saves * SAVE_POINTS})")
            if log.steals:
                counted = min(log.steals, STEAL_POINTS_CAP)
                points += counted * STEAL_POINTS
                breakdown.append(f"🛡️ {log.steals} robo{'s' if log.steals > 1 else ''} (+{counted * STEAL_POINTS})")
            if log.losses:
                counted = min(log.losses, LOSS_POINTS_CAP)
                penalty = counted * LOSS_POINTS
                points += penalty
                breakdown.append(f"❌ {log.losses} pérdida{'s' if log.losses > 1 else ''} de balón ({penalty})")
            if log.key_passes:
                counted = min(log.key_passes, KEY_PASS_POINTS_CAP)
                pts = counted * KEY_PASS_POINTS
                points += pts
                breakdown.append(f"🔑 {log.key_passes} pase{'s' if log.key_passes > 1 else ''} clave (+{pts})")
            if log.clearances:
                counted = min(log.clearances, CLEARANCE_POINTS_CAP)
                pts = counted * CLEARANCE_POINTS
                points += pts
                breakdown.append(f"🧹 {log.clearances} despeje{'s' if log.clearances > 1 else ''} (+{pts})")
            if log.interceptions:
                counted = min(log.interceptions, INTERCEPTION_POINTS_CAP)
                pts = counted * INTERCEPTION_POINTS
                points += pts
                word = "intercepción" if log.interceptions == 1 else "intercepciones"
                breakdown.append(f"🎯 {log.interceptions} {word} (+{pts})")
            if log.blocks:
                counted = min(log.blocks, BLOCK_POINTS_CAP)
                pts = counted // BLOCK_POINTS_PER_UNIT
                if pts:
                    points += pts
                    breakdown.append(f"🚧 {log.blocks} bloqueo{'s' if log.blocks > 1 else ''} (+{pts})")
            clean_sheet_applied = False
            if clean_sheet and CLEAN_SHEET_POINTS[pos] > 0:
                points += CLEAN_SHEET_POINTS[pos]
                breakdown.append(f"🧱 Portería a cero (+{CLEAN_SHEET_POINTS[pos]})")
                clean_sheet_applied = True
            if goals_conceded > 0:
                penalty = -(goals_conceded // GOALS_CONCEDED_DIVISOR[pos])
                if penalty != 0:
                    points += penalty
                    label = "gol" if goals_conceded == 1 else "goles"
                    breakdown.append(f"🥅 {goals_conceded} {label} en contra ({penalty})")
            if p["id"] in affinity:
                bonus, arquetipo = affinity[p["id"]]
                points += bonus
                breakdown.append(f"🔥 Afinidad de equipo: {arquetipo} (+{bonus})")

            player_results[p["id"]] = {
                "points": points,
                "breakdown": breakdown,
                "goals": log.goals,
                "assists": log.assists,
                "saves": log.saves,
                "steals": log.steals,
                "interceptions": log.interceptions,
                "clearances": log.clearances,
                "blocks": log.blocks,
                "key_passes": log.key_passes,
                "losses": log.losses,
                "clean_sheet": 1 if clean_sheet_applied else 0,
            }
            total_points += points
        return total_points, player_results

    home_points, home_players = score_side(sides["home"], home_clean_sheet, away_goals)
    away_points, away_players = score_side(sides["away"], away_clean_sheet, home_goals)

    return {
        "home_goals": home_goals,
        "away_goals": away_goals,
        "home_points": home_points,
        "away_points": away_points,
        "home_players": home_players,
        "away_players": away_players,
        "home_shots": sides["home"]["shots"],
        "away_shots": sides["away"]["shots"],
        "timeline": timeline_lines,
        "events": raw_events,
    }


# ---------------------------------------------------------------------------
# Team affinity ("arquetipo") bonus: rewards building a lineup around
# several characters who share the same in-game personality archetype
# (Justicia, Contraataque, Tensión...), not just raw stats.
# ---------------------------------------------------------------------------
AFFINITY_THRESHOLDS = [(8, 3), (6, 2), (4, 1)]  # (min players sharing it, bonus points)
MATCH_AFFINITY_THRESHOLD = 4  # players needed for a live in-match effect to kick in

# Once an affinity is active (>=4 sharing players), its in-match effect keeps
# growing a little with every extra player sharing it, instead of being a
# flat on/off switch -- but capped well below "excessive" so a stacked
# lineup is meaningfully better, not game-breaking.
AFFINITY_SCALE_PER_EXTRA_PLAYER = 0.08
AFFINITY_SCALE_CAP = 1.5


def affinity_scale(count):
    """Multiplier applied to an active affinity's effect size, based on how
    many players in the lineup share that archetype. 1.0x right at the
    threshold (4 players), growing modestly per extra player, capped at
    AFFINITY_SCALE_CAP so it's never excessive."""
    if count is None or count < MATCH_AFFINITY_THRESHOLD:
        return 0.0
    extra = count - MATCH_AFFINITY_THRESHOLD
    return min(AFFINITY_SCALE_CAP, 1.0 + extra * AFFINITY_SCALE_PER_EXTRA_PLAYER)


def active_match_affinities(lineup):
    """Archetypes with enough players (>=4) in this lineup for their in-match
    gameplay effect (see simulate_fixture) to kick in, mapped to how many
    players share them -- used both as a membership check ("X" in ...) and
    to scale each effect's magnitude via affinity_scale(count)."""
    counts = Counter(p["arquetipo"] for p in lineup if p.get("arquetipo") and p["arquetipo"] != "Unknown")
    return {arquetipo: count for arquetipo, count in counts.items() if count >= MATCH_AFFINITY_THRESHOLD}


def compute_affinity_bonuses(lineup):
    """Returns {player_id: (bonus_points, arquetipo)} for players in a
    starting lineup who share a common archetype with enough teammates."""
    counts = Counter(p["arquetipo"] for p in lineup if p.get("arquetipo") and p["arquetipo"] != "Unknown")
    bonuses = {}
    for p in lineup:
        arquetipo = p.get("arquetipo")
        if not arquetipo or arquetipo == "Unknown":
            continue
        count = counts.get(arquetipo, 0)
        for min_count, bonus in AFFINITY_THRESHOLDS:
            if count >= min_count:
                bonuses[p["id"]] = (bonus, arquetipo)
                break
    return bonuses


# ---------------------------------------------------------------------------
# 1v1 gameweek matches: two lineups face off in a simulate_fixture() match.
# ---------------------------------------------------------------------------
NPC_TEAM_NAMES = [
    "Instituto Fantasma",
    "Onze Sombra",
    "Selección Improvisada",
    "Rivales del Barrio",
    "Equipo Comodín",
    "Once Suplente",
    "Academia Desconocida",
    "Combinado Amateur",
]


def adjust_player_value(current_value, points, base_price):
    """Nudges a player's market value up or down after a gameweek based on
    how well they scored, kept within a sane range of their original base
    price so values don't drift forever in one direction."""
    if points >= 11:
        delta = 5
    elif points >= 8:
        delta = 2
    elif points <= 2:
        delta = -4
    elif points <= 4:
        delta = -2
    else:
        delta = 0

    new_value = current_value + delta
    floor = max(3, base_price * 0.4)
    ceiling = base_price * 2.5
    return round(min(max(new_value, floor), ceiling))


def position_counts(players):
    counts = {"GK": 0, "DF": 0, "MF": 0, "FW": 0}
    for p in players:
        counts[p["posicion"]] = counts.get(p["posicion"], 0) + 1
    return counts


def validate_lineup(players, formation_key):
    """Exact-match validation: a chosen formation requires precise counts,
    not a min/max range."""
    requirements = formation_requirements(formation_key)
    total_needed = sum(requirements.values())
    if len(players) != total_needed:
        return False, f"Esta formación necesita exactamente {total_needed} jugadores (tienes {len(players)})."
    counts = position_counts(players)
    for pos, needed in requirements.items():
        if counts.get(pos, 0) != needed:
            return False, f"Formación inválida: necesitas {needed} en posición {pos} (tienes {counts.get(pos, 0)})."
    return True, None


def missing_positions_message(players, formation_key):
    """Describes exactly which positions (and how many) are still missing
    from the current selection to complete the chosen formation."""
    requirements = formation_requirements(formation_key)
    counts = position_counts(players)
    missing = []
    extra = []
    for pos, needed in requirements.items():
        diff = needed - counts.get(pos, 0)
        if diff > 0:
            missing.append(f"{diff} {POSITION_LABELS[pos]}{'s' if diff > 1 else ''}")
        elif diff < 0:
            extra.append(f"{-diff} {POSITION_LABELS[pos]}{'s' if -diff > 1 else ''} de más")
    parts = []
    if missing:
        parts.append("Te falta: " + ", ".join(missing))
    if extra:
        parts.append("Te sobra: " + ", ".join(extra))
    return " · ".join(parts) if parts else None


def count_missing_slots(players, formation_key):
    """Total number of empty required slots (e.g. missing a DF and a MF
    counts as 2), used to apply the -3-per-empty-slot penalty when a team
    plays a gameweek without a full, valid lineup."""
    requirements = formation_requirements(formation_key)
    counts = position_counts(players)
    return sum(max(0, needed - counts.get(pos, 0)) for pos, needed in requirements.items())


def _scout_filter_sql(scout_filter):
    """Builds the SQL fragment for the league's scout filter.
    'exclude' -> only non-scouts (players with a known team).
    'only'    -> only scout characters (unaffiliated).
    anything else (None/'all') -> no filter."""
    if scout_filter == "exclude":
        return "AND es_scout = 0"
    if scout_filter == "only":
        return "AND es_scout = 1"
    return ""


def generate_balanced_roster(db, budget, seasons=None, exclude_ids=None, scout_filter=None):
    """Auto-generates a FREE starting 11 (4-4-2) for a new team so every team
    in a league starts with a roughly similar total squad value, without
    spending any of their transfer budget."""
    target_per_slot = budget / 11
    picked_ids = []
    picked_total = 0.0
    exclude_ids = set(exclude_ids or [])

    season_where = ""
    season_params = []
    if seasons:
        placeholders = ",".join("?" for _ in seasons)
        season_where = f"AND juego IN ({placeholders})"
        season_params = list(seasons)

    scout_where = _scout_filter_sql(scout_filter)

    for pos, count in STARTER_FORMATION:
        for _ in range(count):
            remaining_slots = 11 - len(picked_ids)
            slot_budget = max((budget * 1.3) - picked_total, 0) / max(remaining_slots, 1)
            target = min(target_per_slot, slot_budget) if remaining_slots > 1 else slot_budget

            all_excluded = exclude_ids | set(picked_ids)
            exclude_sql = ""
            exclude_params = []
            if all_excluded:
                exclude_sql = f"AND id NOT IN ({','.join('?' for _ in all_excluded)})"
                exclude_params = list(all_excluded)

            rows = db.execute(
                f"""
                SELECT id, price FROM players
                WHERE posicion = ? {season_where} {scout_where} {exclude_sql}
                ORDER BY ABS(price - ?) ASC
                LIMIT 25
                """,
                [pos] + season_params + exclude_params + [target],
            ).fetchall()

            if not rows:
                continue

            choice = random.choice(rows)
            picked_ids.append(choice["id"])
            picked_total += choice["price"]

    return picked_ids


def generate_league_pool(db, seasons=None, scout_filter=None):
    """Picks a league's fixed player pool at creation time: POOL_POSITION_COUNTS
    players per position (proportioned like the weekly market, just bigger),
    respecting the league's season/scout settings. This is the closed universe
    the weekly market will keep drawing from and rotating through for the
    life of the league."""
    picked_ids = []

    season_where = ""
    season_params = []
    if seasons:
        placeholders = ",".join("?" for _ in seasons)
        season_where = f"AND juego IN ({placeholders})"
        season_params = list(seasons)

    scout_where = _scout_filter_sql(scout_filter)

    for pos, count in POOL_POSITION_COUNTS.items():
        exclude_sql = ""
        exclude_params = []
        if picked_ids:
            exclude_sql = f"AND id NOT IN ({','.join('?' for _ in picked_ids)})"
            exclude_params = list(picked_ids)

        rows = db.execute(
            f"""
            SELECT id FROM players
            WHERE posicion = ? {season_where} {scout_where} {exclude_sql}
            ORDER BY RANDOM()
            LIMIT ?
            """,
            [pos] + season_params + exclude_params + [count],
        ).fetchall()
        picked_ids.extend(r["id"] for r in rows)

    return picked_ids


def generate_weekly_market(db, seasons=None, exclude_ids=None, scout_filter=None, pool_ids=None):
    """Picks a fresh weekly market of MARKET_SIZE (22) players, proportioned
    across positions, excluding anyone already owned in the league. When
    pool_ids is given, only draws from that fixed set (the league's pool)
    instead of the whole players table — that's what makes the market a
    closed, rotating, eventually-depleting set rather than infinite variety."""
    exclude_ids = set(exclude_ids or [])
    picked_ids = []

    season_where = ""
    season_params = []
    if seasons:
        placeholders = ",".join("?" for _ in seasons)
        season_where = f"AND juego IN ({placeholders})"
        season_params = list(seasons)

    scout_where = _scout_filter_sql(scout_filter)

    pool_where = ""
    pool_params = []
    if pool_ids:
        pool_where = f"AND id IN ({','.join('?' for _ in pool_ids)})"
        pool_params = list(pool_ids)

    for pos, count in MARKET_POSITION_COUNTS.items():
        all_excluded = exclude_ids | set(picked_ids)
        exclude_sql = ""
        exclude_params = []
        if all_excluded:
            exclude_sql = f"AND id NOT IN ({','.join('?' for _ in all_excluded)})"
            exclude_params = list(all_excluded)

        rows = db.execute(
            f"""
            SELECT id FROM players
            WHERE posicion = ? {season_where} {scout_where} {pool_where} {exclude_sql}
            ORDER BY RANDOM()
            LIMIT ?
            """,
            [pos] + season_params + pool_params + exclude_params + [count],
        ).fetchall()
        picked_ids.extend(r["id"] for r in rows)

    return picked_ids
