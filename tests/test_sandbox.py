"""샌드박스: 배속, GeoJSON 맵, Lua 대대(격리 포함).

실행: .venv/Scripts/python -B -m unittest tests.test_sandbox
"""

import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from server.game import data, geomap, hexmap, sandbox, scripting  # noqa: E402
from server.game.sim import Game  # noqa: E402

EXAMPLES = ROOT / "Planner" / "sandbox"
USER = {"templates": data.DEFAULT_TEMPLATES, "unlocks": dict.fromkeys(data.BASE_UNLOCKED, 1)}


def example_map():
    return json.loads((EXAMPLES / "example.geojson").read_text(encoding="utf-8"))


def example_script(name="bots.lua"):
    return (EXAMPLES / name).read_text(encoding="utf-8")


def sandbox_game(map_json=None, script=None, speed=1, seed=1, bots=False):
    setup = sandbox.build(map_json, script, USER, bots)
    return Game(None, "normal", setup.templates, setup.levels, seed=seed, speed=speed,
                scenario=setup.scenario)


def battalion_script(body):
    return ("return { battalions = { x = { name='X', cls='inf', atk=10, dfn=10, spd=1, rng=20, "
            f"hp=100, cost=10, {body} }} }} }}")


def collection(*features):
    corners = [
        {"type": "Feature", "properties": {}, "geometry": {"type": "Point", "coordinates": [0, 45]}},
        {"type": "Feature", "properties": {}, "geometry": {"type": "Point", "coordinates": [6, 49]}},
    ]
    return {"type": "FeatureCollection", "features": corners + list(features)}


def base(kind, side, lon, lat):
    return {"type": "Feature", "properties": {"base": kind, "side": side, "name": f"{kind}{side}"},
            "geometry": {"type": "Point", "coordinates": [lon, lat]}}


class Speed(unittest.TestCase):
    def test_campaign_speed_fixed(self):
        game = Game(1, "easy", seed=1, speed=10)
        self.assertEqual(game.init_message()["speed"], 10)
        with self.assertRaises(ValueError):
            game.command({"t": "speed", "value": 2})

    def test_invalid_speed(self):
        for speed in (0, 3, 21, True, "2"):
            with self.assertRaises(ValueError):
                Game(1, "easy", seed=1, speed=speed)

    def test_sandbox_speed_change(self):
        game = sandbox_game()
        game.command({"t": "speed", "value": 20})
        self.assertEqual(game.state_message()["speed"], 20)
        with self.assertRaises(ValueError):
            game.command({"t": "speed", "value": 7})


class GeoMap(unittest.TestCase):
    def test_example_map(self):
        world = geomap.load(example_map())
        self.assertEqual(world.name, "두 섬의 해협")
        self.assertEqual(len(world.bases), 6)
        self.assertIn(data.TERRAIN["mountain"].id, world.terrain)
        self.assertIn(data.TERRAIN["forest"].id, world.terrain)
        self.assertIn(data.TERRAIN["river"].id, world.terrain)
        self.assertIn(hexmap.SEA, world.terrain)
        for spot in world.bases:
            self.assertEqual(world.terrain[spot.cell], data.TERRAIN["city"].id)

        # 바다 해협은 육로로 다리목을 거쳐야 건널 수 있다.
        west = world.bases[0].cell
        east = world.bases[3].cell
        path = world.pathfinder.find(west, east, hexmap.move_profile())
        self.assertIsNotNone(path)

    def test_too_large(self):
        too_wide = collection(base("hq", 1, 0, 47), base("hq", 2, 6, 47))
        too_wide["features"][1]["geometry"]["coordinates"] = [20, 49]
        with self.assertRaisesRegex(geomap.MapError, "1200km"):
            geomap.load(too_wide)

    def test_requires_one_hq_per_side(self):
        with self.assertRaisesRegex(geomap.MapError, "본부"):
            geomap.load(collection(base("hq", 1, 1, 47)))

    def test_rejects_projected_crs(self):
        bad = collection(base("hq", 1, 1, 47), base("hq", 2, 5, 47))
        bad["crs"] = {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::3857"}}
        with self.assertRaisesRegex(geomap.MapError, "EPSG:4326"):
            geomap.load(bad)

    def test_polygon_needs_terrain(self):
        polygon = {"type": "Feature", "properties": {},
                   "geometry": {"type": "Polygon", "coordinates": [[[1, 46], [2, 46], [2, 47], [1, 46]]]}}
        with self.assertRaisesRegex(geomap.MapError, "terrain"):
            geomap.load(collection(polygon, base("hq", 1, 1, 47), base("hq", 2, 5, 47)))

    def test_sandbox_game_on_custom_map(self):
        game = sandbox_game(example_map())
        message = game.init_message()
        self.assertTrue(message["sandbox"])
        self.assertEqual(message["map_name"], "두 섬의 해협")
        self.assertEqual(len(message["bases"]), 6)
        for _ in range(300):
            game.tick()
        self.assertEqual(game.points_earned, 0)


class LuaBattalions(unittest.TestCase):
    def test_example_scripts(self):
        for name in ("example.lua", "bots.lua"):
            with self.subTest(name=name):
                sandbox_game(example_map(), example_script(name))

        result = scripting.load(example_script())
        self.assertEqual(set(result.battalions), {"dragon", "mage"})
        self.assertEqual(result.battalions["dragon"].move["sea"], 1.0)
        self.assertEqual(result.start, {1: ["비룡사단", "용기병사단", "용기병사단"], 2: 4})
        self.assertEqual(result.ai_profiles[2]["caution"], 1.5)

    def test_dragon_crosses_sea(self):
        game = sandbox_game(example_map(), example_script())
        names = []
        for template in game.templates[1]:
            names.append(template.name)
        dragons = game.spawn(1, game.templates[1][names.index("비룡사단")], game.bases[0].cell)
        mixed = game.spawn(1, game.templates[1][names.index("용기병사단")], game.bases[0].cell)

        sea_cell = None
        for cell, terrain in enumerate(game.terrain):
            if terrain == hexmap.SEA and game.map.pathfinder.find(dragons.cell, cell, dragons.move_profile):
                sea_cell = cell
                break

        self.assertIsNotNone(sea_cell)
        self.assertGreater(dragons.move_profile[hexmap.SEA], 0)
        self.assertEqual(mixed.move_profile[hexmap.SEA], 0)
        self.assertIsNone(game.map.pathfinder.find(mixed.cell, sea_cell, mixed.move_profile))

    def test_hooks_change_combat(self):
        script = battalion_script("on_attack = function(ctx) return ctx.damage * 3 end, "
                                  "on_damaged = function(ctx) return 0 end")
        game = sandbox_game(script=script + "")
        game.templates[1].append(game._template("X", ["x"]))
        unit = game.spawn(1, game.templates[1][-1], game.bases[0].cell)
        enemy = game.spawn(2, game.templates[2][0], game.bases[0].cell)
        base_damage = game._unit_damage(game.spawn(1, game._template("I", ["inf"]), unit.cell),
                                        enemy, 0, False)
        self.assertGreater(base_damage, 0)
        self.assertEqual(len(unit.hooks), 1)
        for _ in range(30):
            game.tick()
        self.assertAlmostEqual(unit.hp, unit.mhp)  # on_damaged가 모든 피해를 0으로

    def test_runtime_error_disables_hook(self):
        game = sandbox_game(script=battalion_script("on_tick = function(ctx) return nil + 1 end"))
        unit = game.spawn(1, game._template("X", ["x"]), game.bases[0].cell)
        game.tick()
        self.assertTrue(unit.hooks[0].disabled)
        messages = []
        for event in game.pop_events():
            messages.append(event["msg"])
        self.assertTrue(any_contains(messages, "x.on_tick"))

    def test_validation_errors(self):
        cases = {
            "return 1": "return",
            "return { battalions = { inf = {} } }": "기본 대대",
            battalion_script("cost = 5000"): "cost",
            battalion_script("move = { lava = 1 }"): "lava",
            battalion_script("effect = 'teleport'"): "effect",
            "return {": "문법",
            "\x1bLua": "문법",
        }
        for code, expected in cases.items():
            with self.subTest(code=code):
                with self.assertRaisesRegex(scripting.ScriptError, expected):
                    scripting.load(code)

    def test_template_uses_unknown_battalion(self):
        script = "return { templates = { { name = 'A', battalions = { 'ghost' } } } }"
        with self.assertRaisesRegex(ValueError, "ghost"):
            sandbox.build(None, script, USER)


class EnemyLuaAndBots(unittest.TestCase):
    def test_enemy_setup_from_lua(self):
        game = sandbox_game(example_map(), example_script())
        names = []
        for template in game.templates[2]:
            names.append(template.name)
        self.assertEqual(names, ["마도사단", "마법보병사단"])

        counts = {1: 0, 2: 0}
        enemy_has_mage = False
        for unit in game.units.values():
            counts[unit.side] += 1
            if unit.side == 2 and "mage" in unit.b:
                enemy_has_mage = True
        self.assertEqual(counts, {1: 3, 2: 4})
        self.assertTrue(enemy_has_mage)
        self.assertEqual(game.ai.profile.defend, 1.5)

    def test_unknown_start_template(self):
        script = "return { enemy_start = { 'ghost' } }"
        with self.assertRaisesRegex(ValueError, "ghost"):
            sandbox.build(None, script, USER)

    def test_bad_ai_profile(self):
        with self.assertRaisesRegex(scripting.ScriptError, "ai.enemy.attack"):
            scripting.load("return { ai = { enemy = { attack = 99 } } }")

    def test_bot_battle(self):
        game = sandbox_game(example_map(), example_script(), bots=True)
        self.assertTrue(game.init_message()["bots"])
        self.assertEqual(game.bot.profile.attack, 1.4)

        with self.assertRaisesRegex(ValueError, "봇전투"):
            game.command({"t": "build", "template": 0})

        game.command({"t": "speed", "value": 20})
        moved = set()

        for _ in range(900):
            game.tick()
            for unit in game.units.values():
                if unit.path:
                    moved.add(unit.side)

        self.assertEqual(moved, {1, 2})   # 양쪽 AI가 모두 지휘한다
        self.assertTrue(game.bot.blackboard.squads or game.ai.blackboard.squads)


class AIEngines(unittest.TestCase):
    def test_goap_plans_cheapest(self):
        from server.game.ai import goap
        actions = [
            goap.Action("walk", {}, {"there": True}, 5),
            goap.Action("get_car", {}, {"car": True}, 1),
            goap.Action("drive", {"car": True}, {"there": True}, 1),
        ]
        steps = goap.plan({}, {"there": True}, actions)
        names = []
        for action in steps:
            names.append(action.name)
        self.assertEqual(names, ["get_car", "drive"])
        self.assertEqual(goap.plan({"there": True}, {"there": True}, actions), [])
        self.assertIsNone(goap.plan({}, {"fly": True}, actions))

    def test_htn_first_valid_method(self):
        from server.game.ai.htn import Compound, Domain, Method, Primitive
        done = []

        class Context:
            trace = []
            ready = False

        domain = Domain([
            Compound("Attack", [
                Method("pincer", lambda c: c.ready, ["left", "right"]),
                Method("direct", lambda c: True, ["front"]),
            ]),
            Primitive("left", lambda c: done.append("left")),
            Primitive("right", lambda c: done.append("right")),
            Primitive("front", lambda c: done.append("front")),
        ])
        context = Context()
        for primitive in domain.decompose("Attack", context):
            primitive.operator(context)
        context.ready = True
        for primitive in domain.decompose("Attack", context):
            primitive.operator(context)
        self.assertEqual(done, ["front", "left", "right"])

    def test_utility_zero_vetoes(self):
        from server.game.ai import utility
        high = utility.Option("high", [utility.Consideration("a", lambda o, c: 0.9),
                                       utility.Consideration("b", lambda o, c: 0.8)])
        vetoed = utility.Option("vetoed", [utility.Consideration("a", lambda o, c: 1.0),
                                           utility.Consideration("b", lambda o, c: 0.0)])
        low = utility.Option("low", [utility.Consideration("a", lambda o, c: 0.3)])
        ranked = utility.rank([vetoed, low, high], None)
        names = []
        for entry in ranked:
            names.append(entry.option.name)
        self.assertEqual(names, ["high", "low", "vetoed"])
        self.assertEqual(ranked[2].score, 0)

    def test_campaign_ai_takes_idle_hq(self):
        game = Game(2, "hard", seed=4)
        while game.winner is None:
            game.tick()
        self.assertEqual((game.winner, game.reason), (2, "hq"))


class LuaIsolation(unittest.TestCase):
    def test_dangerous_globals_hidden(self):
        for name in ("os", "io", "require", "load", "loadfile", "dofile", "debug", "package",
                     "coroutine", "python", "setmetatable", "getmetatable", "pcall", "collectgarbage"):
            with self.subTest(name=name):
                result = scripting.Sandbox().execute(f"return type({name})")
                self.assertEqual(result, "nil")

    def test_slow_string_functions_removed(self):
        for code in ("return ('a'):rep(10)", "return ('a'):find('a')", "return string.gsub('a','a','b')"):
            with self.subTest(code=code):
                with self.assertRaises(scripting.ScriptError):
                    scripting.Sandbox().execute(code)

    def test_infinite_loop_stopped(self):
        with self.assertRaisesRegex(scripting.ScriptError, "한도"):
            scripting.load("while true do end")

    def test_infinite_loop_in_hook_stopped(self):
        game = sandbox_game(script=battalion_script("on_tick = function(ctx) while true do end end"))
        unit = game.spawn(1, game._template("X", ["x"]), game.bases[0].cell)
        game.tick()
        self.assertTrue(unit.hooks[0].disabled)

    def test_memory_limited(self):
        with self.assertRaises(scripting.ScriptError):
            scripting.load("local t = {} for i = 1, 1e9 do t[i] = i end")

    def test_hook_cannot_reach_python(self):
        game = sandbox_game(script=battalion_script(
            "on_tick = function(ctx) return ctx.__class__ end"))
        unit = game.spawn(1, game._template("X", ["x"]), game.bases[0].cell)
        game.tick()  # ctx는 Lua 테이블이라 Python 속성이 없다: nil 반환 → 회복 0
        self.assertFalse(unit.hooks[0].disabled)


def any_contains(messages, text):
    for message in messages:
        if text in message:
            return True

    return False


if __name__ == "__main__":
    unittest.main()
