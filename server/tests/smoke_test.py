"""Run with .venv/Scripts/python -B -m server.tests.smoke_test."""

import json
from pathlib import Path
import sys
from uuid import uuid4

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from server.db import AccountError, Database
from server.game import data, hexmap
from server.game.sim import Game, TICK_HZ


def check_codex():
    with Path(data.__file__).with_name("codex.json").open(encoding="utf-8") as codex_file:
        codex = json.load(codex_file)
    assert set(codex) == {"categories", "tags", "equipment", "parts"}
    assert len(codex["categories"]) == len(codex["tags"]) == 6
    assert len(codex["equipment"]) == 70
    assert len(codex["parts"]) == 110
    for entry in codex["equipment"]:
        assert entry["battalion"] in data.BATTALIONS, entry["id"]


def advance_until(game, predicate, seconds):
    for _ in range(seconds * TICK_HZ):
        game.tick()
        if predicate():
            return
        if game.winner is not None:
            break
    raise AssertionError(f"Condition failed after {game.time}s ({game.winner=}, {game.reason=})")


def move(game, units, base):
    col, row = hexmap.OFFSETS[base.cell]
    game.command({"t": "move", "ids": [unit.id for unit in units], "col": col, "row": row})


def approach(base, game):
    return next(cell for cell, tile in enumerate(game.terrain)
                if tile != hexmap.SEA and hexmap.hex_distance(cell, base.cell) == 2
                and hexmap.find_path(cell, base.cell))


def check_account(db):
    registration = db.register("smoke", "smoke-test-password")
    user_id, user = db.authenticate(registration["token"])
    assert db.login("smoke", "smoke-test-password")["user"] == user
    assert user["points"] == 300 and user["max_stage"] == 1
    assert user["unlocks"] == dict.fromkeys(data.BASE_UNLOCKED, 1)

    for points in (200, 100, 0):
        roll = db.pull(user_id)
        result = roll["result"]
        assert result["kind"] in {"unlock", "upgrade"}
        assert result["level"] == roll["user"]["unlocks"][result["battalion"]]
        assert roll["user"]["points"] == points
    try:
        db.pull(user_id)
    except AccountError as exc:
        assert exc.status == 400
    else:
        raise AssertionError("A pull with insufficient points succeeded")
    return registration["token"], user_id, roll["user"]


def check_map(game):
    initial = game.init_message()
    assert (initial["cols"], initial["rows"]) == (60, 69)
    assert len(initial["terrain"]) == len(initial["owner"]) == 4140
    assert len(initial["bases"]) == 14 and len(initial["weather"]) == 16
    assert set(initial["terrain"]) == set(data.TERRAIN_BY_ID)
    assert all((tile == hexmap.SEA) == (owner == 0)
               for tile, owner in zip(initial["terrain"], initial["owner"]))
    assert all(hexmap.pixel_to_hex(*xy) == cell for xy, cell in zip(hexmap.CENTERS, hexmap.OFFSETS))
    for base in game.bases:
        assert initial["terrain"][base.cell] == data.TERRAIN["city"]["id"]


def check_match(db, token, user_id, user):
    game = Game(1, "easy", user["templates"], user["unlocks"], seed=121,
                on_reward=lambda points: db.add_points(user_id, points))
    check_map(game)
    original = game.owner.copy()
    corps = next(base for base in game.bases if base.side == 2 and base.kind == "corps")
    hq = next(base for base in game.bases if base.side == 2 and base.kind == "hq")

    # Stage extra forces so the smoke test exercises real damage and capture
    # via normal move orders without depending on a full-map playthrough.
    approach_cell = approach(corps, game)
    path = hexmap.find_path(approach_cell, corps.cell)
    defender = game.spawn(2, game.templates[2][0], path[0])
    assault = [game.spawn(1, game.templates[1][1], approach_cell) for _ in range(10)]
    move(game, assault, corps)
    game.command({"t": "build", "template": 0})
    assert len(game.queues[1]) == 1

    advance_until(game, lambda: defender.hp < defender.mhp
                  and any(unit.engaged and unit.hp < unit.mhp for unit in assault), 20)
    advance_until(game, lambda: corps.side == 1 and not game.queues[1], 120)
    state = game.state_message()
    assert state["own"] and game.owner != original
    assert state["terr"] == [game.owner.count(1), game.owner.count(2)]
    assert all(game.owner[cell] == side for cell, side in state["own"])
    assert game.state_message()["own"] == []
    assert game.build_speed(2) <= 0.5
    assert any(e["kind"] == "good" for e in game.pop_events())
    assert game.points_earned == db.authenticate(token)[1]["points"] == data.REWARD_CORPS

    hq_assault = [game.spawn(1, game.templates[1][1], approach(hq, game)) for _ in range(12)]
    move(game, hq_assault, hq)
    advance_until(game, lambda: game.winner is not None, 160)
    assert (game.winner, game.reason) == (1, "hq")
    updated = db.finish_match(user_id, game.stage, game.winner)
    result = game.end_message(updated)
    assert result["points_earned"] == updated["points"] == data.REWARD_CORPS + data.REWARD_HQ
    assert result["user"]["max_stage"] == 2


def main():
    check_codex()
    path = Path(__file__).with_name(f"minrts-smoke-{uuid4().hex}.db")
    try:
        db = Database(path)
        db.initialize()
        check_match(db, *check_account(db))
    finally:
        path.unlink(missing_ok=True)
    print("PASS: codex, account, gacha, map, production, combat, territory, corps and HQ capture, progression")


if __name__ == "__main__":
    main()
