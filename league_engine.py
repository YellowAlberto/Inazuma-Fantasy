"""El "motor" del juego: generación y rotación del mercado semanal, pujas
de la CPU, simulación de una jornada completa, cálculo de clasificaciones
y generación del calendario de una liga. Todo esto vive fuera de app.py
para separar "cómo se juega" de "qué ruta HTTP lo dispara"."""
import json
import random
import string
from datetime import datetime, timedelta

from core import (
    ADMIN_USERNAME,
    BUDGET_BONUS_POINTS_PER_UNIT,
    CLAUSE_LEAGUE_AGE_DAYS,
    CLAUSE_MIN_INCREASE,
    CLAUSE_MULTIPLIER,
    CPU_BID_CHANCE,
    CPU_USER_ID,
    MARKET_SIM_USER_ID,
    MAX_LEAGUE_CYCLES,
    POINTS_PER_MILLION_EUROS,
    league_seasons_list,
    row_to_dict,
    rows_to_list,
)
from scoring import (
    ALL_SEASONS,
    DEFAULT_FORMATION,
    FORMATIONS,
    NPC_TEAM_NAMES,
    POSITION_LABELS,
    adjust_player_value,
    count_missing_slots,
    formation_requirements,
    generate_balanced_roster,
    generate_league_pool,
    generate_weekly_market,
    missing_positions_message,
    simulate_fixture,
    simulate_market_player_points,
    validate_lineup,
)


def owned_player_ids(db, league_id):
    """All player ids currently owned by ANY member of a league."""
    rows = db.execute("SELECT DISTINCT player_id FROM rosters WHERE league_id = ?", (league_id,)).fetchall()
    return {r["player_id"] for r in rows}



def current_market_ids(db, league_id):
    rows = db.execute("SELECT player_id FROM league_market WHERE league_id = ?", (league_id,)).fetchall()
    return {r["player_id"] for r in rows}



def default_clause_value(acquired_price, base_price):
    """A newly-acquired player's starting release clause: 1.5x whatever
    they're worth (what was actually paid, or their base price if they
    came free), rounded to a whole point."""
    reference = acquired_price if acquired_price and acquired_price > 0 else base_price
    return max(1, round(CLAUSE_MULTIPLIER * reference))



def league_is_old_enough_for_clauses(league):
    """Clauses can only be paid off once a league has been running for at
    least CLAUSE_LEAGUE_AGE_DAYS, so brand new leagues get a settling-in
    period before anyone can be sniped."""
    created_at = league["created_at"]
    if not created_at:
        return True
    try:
        created = datetime.strptime(created_at.split(".")[0], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return True
    return (datetime.utcnow() - created).days >= CLAUSE_LEAGUE_AGE_DAYS



def squad_spent(db, league_id, user_id):
    row = db.execute(
        "SELECT COALESCE(SUM(acquired_price), 0) as spent FROM rosters WHERE league_id = ? AND user_id = ?",
        (league_id, user_id),
    ).fetchone()
    return row["spent"]



def pending_bids_total(db, league_id, user_id, exclude_player_id=None):
    if exclude_player_id is not None:
        row = db.execute(
            "SELECT COALESCE(SUM(amount), 0) as total FROM bids WHERE league_id = ? AND user_id = ? AND player_id != ?",
            (league_id, user_id, exclude_player_id),
        ).fetchone()
    else:
        row = db.execute(
            "SELECT COALESCE(SUM(amount), 0) as total FROM bids WHERE league_id = ? AND user_id = ?",
            (league_id, user_id),
        ).fetchone()
    return row["total"]



def maybe_add_cpu_offer(db, league_id, player_id):
    """When a player hits the market, there's a chance a 'CPU' buyer from
    outside the league puts in a sealed offer too — scaled by the player's
    base value and how well they've recently performed. Returns the offer
    amount if one was made, otherwise None."""
    if random.random() > CPU_BID_CHANCE:
        return None

    value_row = db.execute(
        "SELECT value FROM league_player_value WHERE league_id = ? AND player_id = ?", (league_id, player_id)
    ).fetchone()
    price_row = db.execute("SELECT price FROM players WHERE id = ?", (player_id,)).fetchone()
    base_value = value_row["value"] if value_row else price_row["price"]

    recent_rows = db.execute(
        "SELECT gs.points FROM gameweek_scores gs JOIN gameweeks gw ON gw.id = gs.gameweek_id "
        "WHERE gs.league_id = ? AND gs.player_id = ? ORDER BY gw.number DESC LIMIT 3",
        (league_id, player_id),
    ).fetchall()
    if recent_rows:
        recent_avg = sum(r["points"] for r in recent_rows) / len(recent_rows)
        perf_multiplier = max(0.8, min(1.4, 0.8 + (recent_avg / 15) * 0.6))
    else:
        perf_multiplier = 1.0

    offer = round(base_value * perf_multiplier * random.uniform(0.9, 1.15))
    offer = max(offer, round(base_value))  # never insults the seller below the player's own value

    db.execute(
        """
        INSERT INTO bids (league_id, user_id, player_id, amount) VALUES (?, ?, ?, ?)
        ON CONFLICT(league_id, user_id, player_id) DO UPDATE SET amount = excluded.amount
        """,
        (league_id, CPU_USER_ID, player_id, offer),
    )
    return offer


PITCH_LINE_X = {
    "home": {"GK": 6, "DF": 22, "MF": 38, "FW": 48},
    "away": {"GK": 94, "DF": 78, "MF": 62, "FW": 52},
}



def compute_match_pitch_positions(lineup_data, side):
    """Lays out a saved lineup (list of {id, nombre, posicion, sprite_url})
    onto x/y percentage positions for the match replay pitch — one column
    per position line (GK/DF/MF/FW), players spread evenly down that line,
    mirrored so home attacks rightward and away attacks leftward."""
    by_pos = {}
    for p in lineup_data:
        by_pos.setdefault(p["posicion"], []).append(p)

    positions = []
    for pos in ("GK", "DF", "MF", "FW"):
        players = by_pos.get(pos, [])
        n = len(players)
        if n == 0:
            continue
        x = PITCH_LINE_X[side][pos]
        for i, p in enumerate(players):
            y = (i + 1) / (n + 1) * 100
            positions.append(
                {
                    "id": p["id"],
                    "nombre": p["nombre"],
                    "posicion": pos,
                    "sprite_url": p["sprite_url"],
                    "x": round(x, 1),
                    "y": round(y, 1),
                }
            )
    return positions



def ensure_league_pool(db, league):
    """Every league has a fixed pool of players (generated once, at creation
    time) that its weekly market rotates through. Generates it now if it
    doesn't exist yet (brand new league, or an older league upgraded by a
    migration that somehow missed it) and returns the pool's player ids."""
    pool_ids = [
        r["player_id"]
        for r in db.execute("SELECT player_id FROM league_player_pool WHERE league_id = ?", (league["id"],)).fetchall()
    ]
    if pool_ids:
        return pool_ids

    seasons = league_seasons_list(league)
    pool_ids = generate_league_pool(db, seasons=seasons, scout_filter=league["scout_filter"])
    for pid in pool_ids:
        db.execute(
            "INSERT OR IGNORE INTO league_player_pool (league_id, player_id) VALUES (?, ?)",
            (league["id"], pid),
        )
    return pool_ids



def refresh_league_market(db, league):
    """Clears and regenerates a league's weekly market, excluding anyone
    already owned by a member, and seeds/keeps market values. Only ever
    draws from the league's own fixed player pool (see ensure_league_pool),
    so the market is a closed, rotating set rather than infinite variety."""
    db.execute("DELETE FROM league_market WHERE league_id = ?", (league["id"],))
    exclude = owned_player_ids(db, league["id"])
    seasons = league_seasons_list(league)
    pool_ids = ensure_league_pool(db, league)
    new_ids = generate_weekly_market(
        db, seasons=seasons, exclude_ids=exclude, scout_filter=league["scout_filter"], pool_ids=pool_ids
    )
    for pid in new_ids:
        db.execute(
            "INSERT OR IGNORE INTO league_market (league_id, player_id) VALUES (?, ?)",
            (league["id"], pid),
        )
        has_value = db.execute(
            "SELECT id FROM league_player_value WHERE league_id = ? AND player_id = ?",
            (league["id"], pid),
        ).fetchone()
        if not has_value:
            base_price = db.execute("SELECT price FROM players WHERE id = ?", (pid,)).fetchone()["price"]
            db.execute(
                "INSERT INTO league_player_value (league_id, player_id, value) VALUES (?, ?, ?)",
                (league["id"], pid, base_price),
            )
    # NOTE: market players intentionally do NOT get simulated points here.
    # Their "puntos hechos" should only start moving once the league has
    # actually played a real jornada (see play_gameweek_for_league), so a
    # brand new league's market correctly shows 0 for everyone until then.



def simulate_points_for_market_pool(db, league_id, gw_id):
    """Gives every player currently sitting unclaimed in the market a
    simulated performance, so their "puntos hechos" keeps moving as the
    league's real jornadas get played -- even though these players aren't
    owned by anyone and so never take part in a simulated fixture directly.

    Called once per real jornada played (from play_gameweek_for_league),
    right after that jornada's gameweek row is created, so market points
    only start accumulating once jornadas actually start being played --
    a brand new league's market correctly shows 0 "puntos hechos" until its
    first jornada. The running total lives in market_simulated_points,
    independent of the real gameweeks table; the same points are also filed
    under the just-played gameweek (via gw_id) so older per-gameweek views
    keep working."""
    pool_players = rows_to_list(
        db.execute(
            "SELECT p.* FROM league_market lm JOIN players p ON p.id = lm.player_id WHERE lm.league_id = ?",
            (league_id,),
        ).fetchall()
    )
    for p in pool_players:
        pts = simulate_market_player_points(p)
        db.execute(
            """
            INSERT INTO market_simulated_points (league_id, player_id, points) VALUES (?, ?, ?)
            ON CONFLICT(league_id, player_id) DO UPDATE SET points = points + excluded.points
            """,
            (league_id, p["id"], pts),
        )
        if gw_id is not None:
            db.execute(
                "INSERT INTO gameweek_scores (gameweek_id, league_id, user_id, player_id, points, events) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (gw_id, league_id, MARKET_SIM_USER_ID, p["id"], pts, "🎲 Rendimiento estimado en el mercado"),
            )
        value_row = db.execute(
            "SELECT value FROM league_player_value WHERE league_id = ? AND player_id = ?",
            (league_id, p["id"]),
        ).fetchone()
        current_value = value_row["value"] if value_row else p["price"]
        new_value = adjust_player_value(current_value, pts, p["price"])
        db.execute(
            """
            INSERT INTO league_player_value (league_id, player_id, value) VALUES (?, ?, ?)
            ON CONFLICT(league_id, player_id) DO UPDATE SET value = excluded.value
            """,
            (league_id, p["id"], new_value),
        )



def rotate_market_for_league(db, league_id):
    """Resolves whatever bids are currently in the league's market pool
    (awarding each player to the highest bidder) and immediately opens a
    fresh pool. This is the daily market rotation — it doesn't touch
    gameweeks/fixtures at all, so it can run any day of the week,
    independently of whether a match day is also being played.

    Returns a list of dicts (one per player that actually got sold, i.e.
    had at least one bid) with keys: player (name), team (buyer's team
    name), amount — so callers can announce who signed whom."""
    league = row_to_dict(db.execute("SELECT * FROM leagues WHERE id = ?", (league_id,)).fetchone())
    if not league:
        return []

    next_number = league["current_gameweek"] + 1
    market_ids = [
        r["player_id"]
        for r in db.execute("SELECT player_id FROM league_market WHERE league_id = ?", (league_id,)).fetchall()
    ]

    sold = []

    for pid in market_ids:
        bids = db.execute(
            "SELECT user_id, amount FROM bids WHERE league_id = ? AND player_id = ?", (league_id, pid)
        ).fetchall()
        if bids:
            top_amount = max(b["amount"] for b in bids)
            winners = [b for b in bids if b["amount"] == top_amount]
            winner = random.choice(winners)
            player_row = db.execute("SELECT nombre, price FROM players WHERE id = ?", (pid,)).fetchone()
            base_price = player_row["price"]
            db.execute(
                "INSERT OR IGNORE INTO rosters (league_id, user_id, player_id, acquired_price, clause_value) "
                "VALUES (?, ?, ?, ?, ?)",
                (league_id, winner["user_id"], pid, top_amount, default_clause_value(top_amount, base_price)),
            )
            db.execute(
                "INSERT INTO market_results (league_id, gameweek_number, player_id, winner_user_id, amount) "
                "VALUES (?, ?, ?, ?, ?)",
                (league_id, next_number, pid, winner["user_id"], top_amount),
            )
            team_row = db.execute(
                "SELECT team_name FROM league_members WHERE league_id = ? AND user_id = ?",
                (league_id, winner["user_id"]),
            ).fetchone()
            sold.append(
                {
                    "player": player_row["nombre"],
                    "team": team_row["team_name"] if team_row else "—",
                    "amount": top_amount,
                }
            )
        else:
            db.execute(
                "INSERT INTO market_results (league_id, gameweek_number, player_id, winner_user_id, amount) "
                "VALUES (?, ?, ?, NULL, NULL)",
                (league_id, next_number, pid),
            )

    db.execute("DELETE FROM bids WHERE league_id = ?", (league_id,))
    db.execute("DELETE FROM league_market WHERE league_id = ?", (league_id,))

    league = row_to_dict(db.execute("SELECT * FROM leagues WHERE id = ?", (league_id,)).fetchone())
    refresh_league_market(db, league)
    db.commit()

    sold.sort(key=lambda s: s["amount"], reverse=True)
    return sold



def play_gameweek_for_league(db, league_id):
    """Plays one gameweek for a league: pairs managers 1v1 (or vs an NPC if
    odd) following the league's fixed round-robin calendar, simulates each
    match, persists scores/values, and hands out budget bonuses. Returns
    (ok, message). Does NOT touch the market at all — market rotation is a
    fully separate, independent action now."""
    league = row_to_dict(db.execute("SELECT * FROM leagues WHERE id = ?", (league_id,)).fetchone())
    if not league:
        return False, "Liga no encontrada."

    if league["ended"]:
        champion = compute_standings(db, league_id)
        champion_name = champion[0]["team_name"] if champion else "—"
        return False, f"Esta liga ya ha terminado. Campeón: {champion_name}."

    members = rows_to_list(db.execute("SELECT * FROM league_members WHERE league_id = ?", (league_id,)).fetchall())
    if not members:
        return False, "Esta liga todavía no tiene entrenadores."

    incomplete = []
    for m in members:
        lineup = rows_to_list(
            db.execute(
                "SELECT p.* FROM lineup_selections ls JOIN players p ON p.id = ls.player_id "
                "WHERE ls.league_id = ? AND ls.user_id = ?",
                (league_id, m["user_id"]),
            ).fetchall()
        )
        missing = count_missing_slots(lineup, m["formation"] or DEFAULT_FORMATION)
        if missing:
            msg = missing_positions_message(lineup, m["formation"] or DEFAULT_FORMATION) or "alineación incompleta"
            incomplete.append(f"{m['team_name']} ({msg})")

    if incomplete:
        pass  # no longer blocks play — incomplete teams just take a scoring penalty (see below)

    next_number = league["current_gameweek"] + 1

    # Load (or lazily generate) the round-robin calendar BEFORE creating the
    # gameweek, so we can check the 3-cycle limit first and avoid creating
    # an empty gameweek nobody asked for.
    schedule = json.loads(league["schedule_json"] or "[]")
    if not schedule or not schedule[0]:
        schedule = generate_round_robin_schedule(len(members))
        db.execute("UPDATE leagues SET schedule_json = ? WHERE id = ?", (json.dumps(schedule), league_id))

    total_rounds = len(schedule)
    max_gameweeks = total_rounds * MAX_LEAGUE_CYCLES

    if next_number > max_gameweeks:
        db.execute("UPDATE leagues SET ended = 1 WHERE id = ?", (league_id,))
        db.commit()
        champion = compute_standings(db, league_id)
        champion_name = champion[0]["team_name"] if champion else "—"
        return False, f"Esta liga ha alcanzado el límite de {max_gameweeks} jornadas ({MAX_LEAGUE_CYCLES} vueltas completas). Campeón: {champion_name}."

    cur = db.execute("INSERT INTO gameweeks (league_id, number) VALUES (?, ?)", (league_id, next_number))
    gameweek_id = cur.lastrowid

    def get_lineup(user_id):
        return rows_to_list(
            db.execute(
                "SELECT p.* FROM lineup_selections ls JOIN players p ON p.id = ls.player_id "
                "WHERE ls.league_id = ? AND ls.user_id = ?",
                (league_id, user_id),
            ).fetchall()
        )

    def persist_side(lineup, user_id, player_results, missing_penalty=0):
        """Saves individual scores/value nudges for one side of a simulated
        fixture, and (for real managers) tops up their budget bonus at a
        rate of +1 point of budget for every 15 fantasy points scored.
        `missing_penalty` is a flat point deduction (already negative or
        zero) for any required lineup slots the manager left empty."""
        total_points = missing_penalty
        for p in lineup:
            r = player_results[p["id"]]
            pts, breakdown = r["points"], r["breakdown"]
            total_points += pts

            if user_id is not None:
                db.execute(
                    "INSERT INTO gameweek_scores "
                    "(gameweek_id, league_id, user_id, player_id, points, events, "
                    " goals, assists, saves, steals, interceptions, clearances, key_passes, losses, clean_sheet, blocks) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        gameweek_id, league_id, user_id, p["id"], pts, "|".join(breakdown),
                        r["goals"], r["assists"], r["saves"], r["steals"], r["interceptions"],
                        r["clearances"], r["key_passes"], r["losses"], r["clean_sheet"], r["blocks"],
                    ),
                )

            value_row = db.execute(
                "SELECT value FROM league_player_value WHERE league_id = ? AND player_id = ?",
                (league_id, p["id"]),
            ).fetchone()
            current_value = value_row["value"] if value_row else p["price"]
            new_value = adjust_player_value(current_value, pts, p["price"])
            db.execute(
                """
                INSERT INTO league_player_value (league_id, player_id, value) VALUES (?, ?, ?)
                ON CONFLICT(league_id, player_id) DO UPDATE SET value = excluded.value
                """,
                (league_id, p["id"], new_value),
            )

        if user_id is not None:
            db.execute(
                "INSERT INTO gameweek_totals (gameweek_id, league_id, user_id, total_points) VALUES (?, ?, ?, ?)",
                (gameweek_id, league_id, user_id, total_points),
            )
            budget_bonus = max(0, total_points // BUDGET_BONUS_POINTS_PER_UNIT)
            if budget_bonus:
                db.execute(
                    "UPDATE league_members SET bonus_budget = bonus_budget + ? WHERE league_id = ? AND user_id = ?",
                    (budget_bonus, league_id, user_id),
                )

        return total_points

    # Pair up managers for this gameweek following the league's fixed
    # round-robin calendar (slots assigned in join order), instead of
    # random pairing — this guarantees everyone faces everyone else
    # exactly once per cycle, same as a real league table.
    members_ordered = sorted(members, key=lambda m: m["id"])
    slot_to_member = {i + 1: m for i, m in enumerate(members_ordered)}

    round_index = (next_number - 1) % total_rounds if total_rounds else 0
    round_pairs = schedule[round_index]

    pairs = []
    for slot_a, slot_b in round_pairs:
        member_a = slot_to_member.get(slot_a) if slot_a else None
        member_b = slot_to_member.get(slot_b) if slot_b else None
        if member_a is None and member_b is None:
            continue  # neither slot is filled by a real entrenador this cycle
        if member_a is None:
            pairs.append((member_b, None))
        elif member_b is None:
            pairs.append((member_a, None))
        else:
            pairs.append((member_a, member_b))

    already_owned = owned_player_ids(db, league_id)
    seasons = league_seasons_list(league)
    league_pool_ids = ensure_league_pool(db, league)

    for home_m, away_m in pairs:
        home_lineup = get_lineup(home_m["user_id"])
        home_label = home_m["team_name"]

        if away_m is not None:
            away_lineup = get_lineup(away_m["user_id"])
            away_label = away_m["team_name"]
            away_user_id = away_m["user_id"]
        else:
            # Improvise a balanced NPC squad drawn from the league's own
            # fixed player pool (same closed set the market rotates
            # through), excluding anyone already owned, so CPU opponents
            # don't interfere with the market or introduce players from
            # outside the pool.
            npc_ids = generate_balanced_roster(
                db,
                league["budget"],
                seasons=seasons,
                exclude_ids=already_owned,
                scout_filter=league["scout_filter"],
                pool_ids=league_pool_ids,
            )
            if npc_ids:
                placeholders = ",".join("?" for _ in npc_ids)
                away_lineup = rows_to_list(
                    db.execute(f"SELECT * FROM players WHERE id IN ({placeholders})", npc_ids).fetchall()
                )
            else:
                away_lineup = []
            away_label = random.choice(NPC_TEAM_NAMES)
            away_user_id = None

        result = simulate_fixture(home_lineup, home_label, away_lineup, away_label)

        home_formation = home_m["formation"] or DEFAULT_FORMATION
        away_formation = (away_m["formation"] if away_m is not None else None) or DEFAULT_FORMATION

        home_missing = count_missing_slots(home_lineup, home_formation)
        home_penalty = -3 * home_missing
        home_total = persist_side(home_lineup, home_m["user_id"], result["home_players"], home_penalty)

        if away_m is not None:
            away_missing = count_missing_slots(away_lineup, away_formation)
            away_penalty = -3 * away_missing
        else:
            away_missing = 0
            away_penalty = 0
        away_total = persist_side(away_lineup, away_user_id, result["away_players"], away_penalty)

        timeline = result["timeline"]
        if home_missing:
            timeline = timeline + [f"⚠️ {home_label} jugó con {home_missing} hueco(s) vacío(s) en la alineación ({home_penalty} pts)"]
        if away_missing:
            timeline = timeline + [f"⚠️ {away_label} jugó con {away_missing} hueco(s) vacío(s) en la alineación ({away_penalty} pts)"]

        home_lineup_data = [
            {"id": p["id"], "nombre": p["nombre"], "posicion": p["posicion"], "sprite_url": p["sprite_url"]}
            for p in home_lineup
        ]
        away_lineup_data = [
            {"id": p["id"], "nombre": p["nombre"], "posicion": p["posicion"], "sprite_url": p["sprite_url"]}
            for p in away_lineup
        ]

        db.execute(
            """
            INSERT INTO fixtures
                (league_id, gameweek_id, home_user_id, away_user_id, home_label, away_label,
                 home_goals, away_goals, home_points, away_points, summary, events_json,
                 home_lineup_json, away_lineup_json, home_formation, away_formation)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                league_id, gameweek_id, home_m["user_id"], away_user_id, home_label, away_label,
                result["home_goals"], result["away_goals"], home_total, away_total,
                "\n".join(timeline), json.dumps(result["events"]),
                json.dumps(home_lineup_data), json.dumps(away_lineup_data), home_formation, away_formation,
            ),
        )

    db.execute("UPDATE leagues SET current_gameweek = ? WHERE id = ?", (next_number, league_id))
    if next_number >= max_gameweeks:
        db.execute("UPDATE leagues SET ended = 1 WHERE id = ?", (league_id,))

    # Now that a real jornada has actually been played, give whoever is
    # currently sitting unclaimed in the market a simulated performance for
    # it too, so their "puntos hechos" grows in step with real jornadas
    # instead of existing before any jornada has been played.
    simulate_points_for_market_pool(db, league_id, gameweek_id)

    db.commit()
    return True, next_number



def assign_starting_roster(db, league, user_id):
    """Gives a brand new member of a league a FREE, budget-balanced starting
    11 (doesn't cost any of their transfer budget), avoiding players already
    owned by other members or currently up for bidding."""
    seasons = league_seasons_list(league)
    exclude = owned_player_ids(db, league["id"]) | current_market_ids(db, league["id"])
    player_ids = generate_balanced_roster(db, league["budget"], seasons=seasons, exclude_ids=exclude, scout_filter=league["scout_filter"])
    for pid in player_ids:
        base_price = db.execute("SELECT price FROM players WHERE id = ?", (pid,)).fetchone()["price"]
        db.execute(
            "INSERT OR IGNORE INTO rosters (league_id, user_id, player_id, acquired_price, clause_value) "
            "VALUES (?, ?, ?, 0, ?)",
            (league["id"], user_id, pid, default_clause_value(0, base_price)),
        )
        db.execute(
            "INSERT OR IGNORE INTO lineup_selections (league_id, user_id, player_id) VALUES (?, ?, ?)",
            (league["id"], user_id, pid),
        )


# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------

def generate_round_robin_schedule(n):
    """Standard 'circle method' round-robin schedule for n slots: every slot
    faces every other slot exactly once across the cycle. If n is odd, a
    'bye' (None) slot is added so it stays fair — in this app a bye just
    means that week's opponent is an improvised NPC instead of a real slot.
    Returns a list of rounds; each round is a list of (slot_a, slot_b)
    pairs (1-indexed slot numbers, or None for a bye)."""
    if n < 1:
        return [[]]
    teams = list(range(1, n + 1))
    if n % 2 == 1:
        teams.append(None)
    total = len(teams)
    rounds = []
    for _ in range(total - 1):
        pairs = [(teams[i], teams[total - 1 - i]) for i in range(total // 2)]
        rounds.append(pairs)
        teams = [teams[0]] + [teams[-1]] + teams[1:-1]
    return rounds



def regenerate_league_schedule(db, league_id, max_members):
    """(Re)builds a league's round-robin calendar for its current max
    number of entrenadores and saves it. Call this whenever the league is
    created or its max_members changes."""
    schedule = generate_round_robin_schedule(max_members)
    db.execute(
        "UPDATE leagues SET schedule_json = ? WHERE id = ?",
        (json.dumps(schedule), league_id),
    )
    return schedule



def generate_invite_code(db):
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    while True:
        code = "".join(random.choices(alphabet, k=6))
        exists = db.execute("SELECT id FROM leagues WHERE invite_code = ?", (code,)).fetchone()
        if not exists:
            return code



def compute_standings(db, league_id):
    """Simple fantasy-points ranking: total points accumulated across all
    gameweeks played so far, highest first."""
    rows = rows_to_list(
        db.execute(
            """
            SELECT lm.user_id, lm.team_name, u.username, u.avatar_sprite_url,
                COALESCE(SUM(gt.total_points), 0) as total_points
            FROM league_members lm
            JOIN users u ON u.id = lm.user_id
            LEFT JOIN gameweek_totals gt ON gt.league_id = lm.league_id AND gt.user_id = lm.user_id
            WHERE lm.league_id = ?
            GROUP BY lm.user_id
            ORDER BY total_points DESC
            """,
            (league_id,),
        ).fetchall()
    )
    return rows


# ---------------------------------------------------------------------------
# Standings & gameweeks
# ---------------------------------------------------------------------------
