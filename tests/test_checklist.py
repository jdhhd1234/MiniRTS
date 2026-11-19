"""설계서 8.2 '서버·UI 체크리스트' 자동 검증.

근거: Planner/MinRTS 장비·자원·능력치 설계서.md 8.2
실행: .venv/Scripts/python -m unittest tests.test_checklist -v
서버 실행이 필요 없다 (Game·Database를 직접 사용).
"""
import json
import math
import re
import sys
import tempfile
import unittest
from pathlib import Path
from random import Random
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from server import gacha  # noqa: E402
from server.db import AccountError, Database  # noqa: E402
from server.game import data  # noqa: E402
from server.game import hexmap as world  # noqa: E402
from server.game.sim import Game, Unit, TICK_HZ  # noqa: E402

FOG = data.WEATHER["fog"]["id"]
CLEAR = data.WEATHER["clear"]["id"]
ALL_UNLOCKED = {k: 1 for k in data.BATTALIONS}


def new_game(**kw):
    kw.setdefault("seed", 1)
    kw.setdefault("unlocks", ALL_UNLOCKED)
    return Game(**kw)


def plain_cell(game):
    return next(i for i, t in enumerate(game.terrain) if t == data.TERRAIN["plain"]["id"])


def make_unit(game, battalions, side=1, levels=None, cell=None):
    return game.spawn(side, {"name": "시험", "battalions": list(battalions)}, cell if cell is not None else plain_cell(game))


def no_random(game):
    game.rng.uniform = lambda a, b: 1.0


class Roster(unittest.TestCase):
    def test_20_official_ids(self):
        self.assertEqual(len(data.BATTALIONS), 20)

    def test_6_base_14_gacha(self):
        base = [k for k, v in data.BATTALIONS.items() if v["rarity"] == "base"]
        self.assertEqual(len(base), 6)
        self.assertEqual(len(data.BATTALIONS) - len(base), 14)

    def test_frontend_has_no_hardcoded_battalion_list(self):
        """프론트 편제 목록은 /api/data에서 받아야 서버 20종과 항상 일치한다."""
        js = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "web" / "js").glob("*.js"))
        hits = [k for k in data.BATTALIONS if re.search(rf"""["']{k}["']\s*:""", js)]
        self.assertLessEqual(len(hits), 3, f"프론트에 대대 ID가 하드코딩된 것으로 보임: {hits}")


class Templates(unittest.TestCase):
    unlocked = {k: 1 for k in data.BASE_UNLOCKED}

    def test_0_and_5_rejected(self):
        self.assertIsNotNone(data.validate_template([], self.unlocked))
        self.assertIsNotNone(data.validate_template(["inf", "inf", "mech", "mech", "tank"], self.unlocked))

    def test_triple_rejected(self):
        self.assertIsNotNone(data.validate_template(["tank"] * 3, self.unlocked))

    def test_locked_rejected(self):
        self.assertIsNotNone(data.validate_template(["heli"], self.unlocked))

    def test_valid_accepted(self):
        self.assertIsNone(data.validate_template(["tank", "tank", "mech", "arty"], self.unlocked))

    def test_db_rejects_bad_template(self):
        with tempfile.TemporaryDirectory() as d:
            db = Database(Path(d) / "t.db")
            db.initialize()
            reg = db.register("tester", "pw123456")
            uid, _ = db.authenticate(reg["token"])
            with self.assertRaises(AccountError):
                db.save_templates(uid, [{"name": "x", "battalions": ["tank"] * 3}])
            with self.assertRaises(AccountError):
                db.save_templates(uid, [{"name": "x", "battalions": ["heli"]}])


class Duplicates(unittest.TestCase):
    def test_duplicate_counts_in_cost_and_stats(self):
        g = new_game()
        t = g._template("x", ["tank", "tank"])
        self.assertEqual(t["cost"], 2 * data.BATTALIONS["tank"]["cost"])
        u = make_unit(g, ["tank", "tank"])
        self.assertAlmostEqual(u.mhp, 2 * data.BATTALIONS["tank"]["hp"])

    def test_duplicate_support_effect_applied_once(self):
        g = new_game()
        one = make_unit(g, ["inf", "med"])
        two = make_unit(g, ["inf", "med", "med"])
        self.assertEqual(one.effects, two.effects)
        # 회복: 의무 1개든 2개든 1%/초
        for u in (one, two):
            u.hp = u.mhp * 0.5
        hp_before = {u.id: u.hp for u in (one, two)}
        g.units = {one.id: one, two.id: two}
        g._unit_index = __import__("server.game.sim", fromlist=["UnitIndex"]).UnitIndex(g.units.values())
        g._combat({one.id: [], two.id: []}, {1: [], 2: []})
        for u in (one, two):
            self.assertAlmostEqual((u.hp - hp_before[u.id]) / u.mhp, 0.01 / TICK_HZ, places=6)

    def test_medical_plus_maintenance_is_1_5_percent(self):
        g = new_game()
        u = make_unit(g, ["inf", "med", "maint"])
        u.hp = u.mhp * 0.5
        g.units = {u.id: u}
        g._unit_index = __import__("server.game.sim", fromlist=["UnitIndex"]).UnitIndex(g.units.values())
        g._combat({u.id: []}, {1: [], 2: []})
        self.assertAlmostEqual((u.hp - u.mhp * 0.5) / u.mhp, 0.015 / TICK_HZ, places=6)


class Levels(unittest.TestCase):
    def test_tank_lv3(self):
        g = new_game(unlocks={**ALL_UNLOCKED, "tank": 3})
        u = make_unit(g, ["tank"])
        self.assertAlmostEqual(u.attack[0][1], 24.2)
        self.assertAlmostEqual(u.defense, 17.6)
        self.assertAlmostEqual(u.mhp, 132)
        self.assertEqual(u.speed, 2.2)
        self.assertEqual(u.attack[0][2], 20)
        self.assertEqual(g._template("x", ["tank"])["cost"], 50)


class Production(unittest.TestCase):
    def test_insufficient_resources(self):
        g = new_game()
        g.resources[1] = 10
        with self.assertRaises(ValueError):
            g.build(1, 0)
        self.assertEqual(g.resources[1], 10)
        self.assertGreaterEqual(g.resources[1], 0)

    def test_order_deducts(self):
        g = new_game()
        g.resources[1] = 300
        g.templates[1].append(g._template("기갑", ["tank", "tank", "mech", "arty"]))
        g.build(1, len(g.templates[1]) - 1)
        self.assertEqual(g.resources[1], 130)


class Bases(unittest.TestCase):
    def _base(self, g, side, kind):
        return next(b for b in g.bases if b.original_side == side and b.kind == kind)

    def test_penalty_and_recapture(self):
        g = new_game()
        corps, div = self._base(g, 1, "corps"), self._base(g, 1, "div")
        g._capture(corps)
        g._capture(div)
        self.assertAlmostEqual(g.build_speed(1), 0.425)
        self.assertAlmostEqual(data.BUILD_TIME / g.build_speed(1), 35.294, places=2)
        g._capture(corps)  # 탈환
        self.assertAlmostEqual(g.build_speed(1), 0.85)
        self.assertAlmostEqual(corps.hp, 0.3 * corps.mhp)

    def test_reward_once_per_base(self):
        got = []
        g = new_game(on_reward=got.append)
        corps = self._base(g, 2, "corps")
        g._capture(corps)      # 플레이어가 함락 → +50
        g._capture(corps)      # AI 탈환
        g._capture(corps)      # 다시 함락 → 보상 없음
        self.assertEqual(got, [data.REWARD_CORPS])
        self.assertEqual(g.points_earned, data.REWARD_CORPS)

    def test_hq_capture_ends_game(self):
        g = new_game()
        g._capture(self._base(g, 2, "hq"))
        self.assertEqual((g.winner, g.reason), (1, "hq"))


class MapRules(unittest.TestCase):
    def test_hex_spacing_and_engage(self):
        a = world.CENTERS[world.index(10, 10)]
        for n in world.NEIGHBORS[world.index(10, 10)]:
            self.assertAlmostEqual(math.dist(a, world.CENTERS[n]), 20.0, places=6)
        self.assertEqual(data.ENGAGE_KM, 20.0)

    def test_heli_division_is_one_unit(self):
        g = new_game()
        before = len(g.units)
        make_unit(g, ["heli", "heli", "aa"])
        self.assertEqual(len(g.units), before + 1)

    def test_sea_forbidden(self):
        g = new_game()
        sea = next(i for i, t in enumerate(g.terrain) if t == world.SEA)
        self.assertIsNone(world.find_path(plain_cell(g), sea))


class Combat(unittest.TestCase):
    def _pair(self, g, attacker, distance):
        u = make_unit(g, attacker)
        e = make_unit(g, ["inf"], side=2)
        e.x, e.y = u.x + distance, u.y
        e.cell = u.cell
        return u, e

    def test_fog_range_and_floor(self):
        g = new_game()
        no_random(g)
        g.weather = [FOG] * len(g.weather)
        arty, e = self._pair(g, ["arty"], 20.0)
        self.assertGreater(g._unit_damage(arty, e, 20.0, False), 0)
        self.assertEqual(g._unit_damage(arty, e, 21.0, False), 0)   # 40*0.5=20
        heli, e = self._pair(g, ["heli"], 20.0)
        self.assertGreater(g._unit_damage(heli, e, 20.0, False), 0)  # 15 → 하한 20
        g.weather = [CLEAR] * len(g.weather)
        self.assertGreater(g._unit_damage(arty, e, 39.0, False), 0)  # 맑음: 40km

    def test_ew_0_9(self):
        g = new_game()
        no_random(g)
        g.weather = [CLEAR] * len(g.weather)
        u, e = self._pair(g, ["tank"], 10.0)
        normal = g._unit_damage(u, e, 10.0, False)
        jammed = g._unit_damage(u, e, 10.0, True)
        self.assertAlmostEqual(jammed / normal, 0.9)

    def test_retreat_thresholds(self):
        g = new_game()
        self.assertEqual(make_unit(g, ["inf"]).retreat_threshold, 0.30)
        self.assertEqual(make_unit(g, ["inf", "mp"]).retreat_threshold, 0.20)


class Supply(unittest.TestCase):
    def _at(self, km, effects=()):
        unit = SimpleNamespace(x=km, y=0.0, effects=set(effects))
        base = SimpleNamespace(x=0.0, y=0.0)
        return Game._supply(unit, [base])

    def test_breakpoints(self):
        self.assertAlmostEqual(self._at(250), 1.0)
        self.assertAlmostEqual(self._at(650), 0.5)
        self.assertAlmostEqual(self._at(450), 0.75)
        self.assertAlmostEqual(self._at(450, ["transport"]), 0.9375)


class TerrainWeatherEffects(unittest.TestCase):
    def test_engineer_mountain_cbrn(self):
        self.assertAlmostEqual(world.movement_factor("swamp", engineer=True), 0.7)   # (1+0.4)/2
        self.assertEqual(world.movement_factor("mountain", mountain=True), 1.0)
        self.assertEqual(world.movement_factor("highland", mountain=True), 1.0)
        from server.game.sim import _weather_factor
        self.assertAlmostEqual(_weather_factor(0.6, {"cbrn"}), 0.8)
        self.assertAlmostEqual(_weather_factor(0.6, set()), 0.6)


class Victory(unittest.TestCase):
    def test_time_limit_territory(self):
        g = new_game()
        g.ticks = data.MATCH_SECONDS * TICK_HZ - 1
        g.territory = [0, 3000, 100]
        g.ai.update = lambda game: None
        g.tick()
        self.assertEqual((g.winner, g.reason), (1, "time"))

    def test_stage_clear_saved(self):
        with tempfile.TemporaryDirectory() as d:
            db = Database(Path(d) / "t.db")
            db.initialize()
            reg = db.register("tester", "pw123456")
            uid, _ = db.authenticate(reg["token"])
            self.assertEqual(db.finish_match(uid, 1, 2)["max_stage"], 1)   # 패배는 저장 안 함
            self.assertEqual(db.finish_match(uid, 1, 1)["max_stage"], 2)
            self.assertEqual(db.finish_match(uid, 3, 1)["max_stage"], 3)   # 최대 3


class Gacha(unittest.TestCase):
    def test_single_unlock_or_upgrade(self):
        rng = Random(7)
        unlocks = {k: 1 for k in data.BASE_UNLOCKED}
        total = (len(data.BATTALIONS) - len(unlocks)) + len(data.BATTALIONS) * (data.MAX_LEVEL - 1)
        for _ in range(total):
            r = gacha.pull(unlocks, rng)
            self.assertIn(r["kind"], ("unlock", "upgrade"))
            self.assertIn(r["battalion"], data.BATTALIONS)
            if r["kind"] == "unlock":
                self.assertNotIn(r["battalion"], unlocks)
                self.assertEqual(r["level"], 1)
            else:
                self.assertEqual(r["level"], unlocks[r["battalion"]] + 1)
            unlocks[r["battalion"]] = r["level"]
        self.assertEqual(set(unlocks.values()), {data.MAX_LEVEL})
        with self.assertRaises(ValueError):   # 전부 최고 레벨이면 더 뽑을 수 없음
            gacha.pull(unlocks, rng)


class Codex(unittest.TestCase):
    """장비·구성품 도감 (backend 에이전트가 생성하는 codex.json)."""
    path = ROOT / "server" / "game" / "codex.json"

    def setUp(self):
        if not self.path.exists():
            self.skipTest("codex.json 아직 생성 안 됨")
        self.codex = json.loads(self.path.read_text(encoding="utf-8"))

    def test_counts_and_ids(self):
        eq, parts = self.codex["equipment"], self.codex["parts"]
        self.assertEqual(len(eq), 70)
        self.assertEqual(len(parts), 110)
        self.assertEqual(len({e["id"] for e in eq}), 70)
        self.assertEqual(len({p["id"] for p in parts}), 110)

    def test_links_and_tags(self):
        tags = set(self.codex["tags"])
        self.assertEqual(len(tags), 6)
        for e in self.codex["equipment"]:
            self.assertIn(e["battalion"], data.BATTALIONS, e["id"])
            self.assertTrue(e["parts"], e["id"])
        for p in self.codex["parts"]:
            self.assertIn(p["tag"], tags, p["id"])

    def test_no_resource_fields_added(self):
        """태그 6종이 생산 로직·계정 저장소에 새 자원 필드를 만들지 않는다."""
        for name in ("금속", "경량합금", "복합재", "기계부품", "전자부품", "광학부품"):
            for f in ("sim.py", "data.py"):
                self.assertNotIn(name, (ROOT / "server" / "game" / f).read_text(encoding="utf-8"), f)
            self.assertNotIn(name, (ROOT / "server" / "db.py").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
