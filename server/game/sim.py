"""Authoritative fixed-step simulation shared by WebSocket and headless play."""

from collections import defaultdict, deque
from dataclasses import dataclass, field
from math import dist, floor
from random import Random

from . import data, hexmap as world
from .ai import AI

TICK_HZ = 10
STATE_HZ = 4
DT = 1 / TICK_HZ
SIDES = (1, 2)
ENGAGE = data.ENGAGE_KM
MAX_RANGE = max(b["rng"] for b in data.BATTALIONS.values())
BASE_REWARDS = {"corps": data.REWARD_CORPS, "hq": data.REWARD_HQ}


def _distance(a, b):
    return dist((a.x, a.y), (b.x, b.y))


def _weather_factor(value, effects):
    return (1 + value) / 2 if value < 1 and "cbrn" in effects else value


@dataclass
class Base:
    id: int
    side: int
    kind: str
    name: str
    cell: int
    original_side: int = field(init=False)
    x: float = field(init=False)
    y: float = field(init=False)
    hp: float = field(init=False)
    mhp: int = field(init=False)

    def __post_init__(self):
        self.original_side = self.side
        self.x, self.y = world.CENTERS[self.cell]
        self.hp = self.mhp = data.BASE_KINDS[self.kind]["hp"]

    def state(self):
        return {"id": self.id, "side": self.side, "hp": round(self.hp, 2), "mhp": self.mhp}

    def initial_state(self):
        col, row = world.OFFSETS[self.cell]
        return dict(self.state(), kind=self.kind, name=self.name,
                    x=self.x, y=self.y, col=col, row=row)


@dataclass
class Unit:
    id: int
    side: int
    name: str
    b: tuple[str, ...]
    cell: int
    levels: dict
    x: float = field(init=False)
    y: float = field(init=False)
    hp: float = field(init=False)
    mhp: float = field(init=False)
    defense: float = field(init=False)
    speed: float = field(init=False)
    attack: tuple = field(init=False)
    classes: tuple = field(init=False)
    effects: set = field(init=False)
    fn: str = field(init=False)
    path: deque = field(default_factory=deque)
    destination: int | None = None
    retreating: bool = False
    retreat_base: int | None = None
    engaged: bool = False
    target: tuple[float, float] | None = None
    supply: float = 1.0

    def __post_init__(self):
        self.x, self.y = world.CENTERS[self.cell]
        battalions = [data.BATTALIONS[key] for key in self.b]
        bonuses = [1 + (self.levels.get(key, 1) - 1) * data.LEVEL_BONUS for key in self.b]
        self.mhp = self.hp = sum(b["hp"] * bonus for b, bonus in zip(battalions, bonuses))
        self.defense = sum(b["dfn"] * bonus for b, bonus in zip(battalions, bonuses)) / len(self.b)
        self.speed = min(b["spd"] for b in battalions)
        self.effects = {b["effect"] for b in battalions if "effect" in b}
        self.classes = tuple(b["cls"] for b in battalions)
        self.attack = tuple((b["cls"], b["atk"] * bonus, b["rng"])
                            for b, bonus in zip(battalions, bonuses))
        self.fn = battalions[0]["fn"]

    @property
    def retreat_threshold(self):
        return 0.20 if "police" in self.effects else data.RETREAT_HP

    def state(self):
        status = ("retreat" if self.retreating else "engaged" if self.engaged
                  else "move" if self.path else "idle")
        dest = world.CENTERS[self.destination] if self.side == 1 and self.destination is not None else None
        return {"id": self.id, "side": self.side, "name": self.name,
                "x": round(self.x, 3), "y": round(self.y, 3), "hp": round(self.hp, 2),
                "mhp": round(self.mhp, 2), "st": status,
                "tgt": list(self.target) if self.target is not None else None,
                "dest": list(dest) if dest is not None else None,
                "b": list(self.b), "fn": self.fn, "supply": round(self.supply, 3)}


@dataclass
class Production:
    template: int
    progress: float = 0.0


class UnitIndex:
    """Spatial lookup for engagement, ranged fire, and contested territory."""

    BUCKET_SIZE = MAX_RANGE

    def __init__(self, units):
        self.buckets = defaultdict(list)
        for unit in units:
            self.buckets[(unit.side, floor(unit.x / self.BUCKET_SIZE),
                          floor(unit.y / self.BUCKET_SIZE))].append(unit)

    def nearby(self, side, x, y, radius):
        size = self.BUCKET_SIZE
        for i in range(floor((x - radius) / size), floor((x + radius) / size) + 1):
            for j in range(floor((y - radius) / size), floor((y + radius) / size) + 1):
                for unit in self.buckets.get((side, i, j), ()):
                    distance = dist((x, y), (unit.x, unit.y))
                    if unit.hp > 0 and distance <= radius + 1e-8:
                        yield distance, unit


class Game:
    """One match; call tick() at 10 Hz or advance directly in headless play."""

    def __init__(self, stage=1, difficulty="normal", templates=None, unlocks=None,
                 *, seed=None, on_reward=None):
        self.stage = stage
        self.difficulty = difficulty
        self.stage_data = data.STAGES[stage]
        self.difficulty_data = data.DIFFICULTY[difficulty]
        self.rng = Random(seed)
        self.on_reward = on_reward
        self.ticks = 0
        self.winner = None
        self.reason = None
        self.points_earned = 0
        self.rewarded_bases = set()
        self.events = []
        self.terrain, self.cities = world.generate_map()
        self.bases = []
        for i, (side, kind, name, lon, lat) in enumerate(data.BASES):
            cell = world.index(*world.pixel_to_hex(*world.lonlat_to_world(lon, lat)))
            self.bases.append(Base(i, side, kind, name, cell))
        self.owner = [
            min(self.bases, key=lambda base: dist((base.x, base.y), xy)).side
            if terrain != world.SEA else 0
            for xy, terrain in zip(world.CENTERS, self.terrain)
        ]
        self.territory = [0, self.owner.count(1), self.owner.count(2)]
        self._last_owner = self.owner.copy()
        self._changed = set()
        self.weather = [self._roll_weather() for _ in range(data.WEATHER_CELLS ** 2)]
        self._last_weather = self.weather.copy()
        self.weather_due = [self._weather_interval() for _ in self.weather]
        self.resources = {side: float(data.START_RESOURCES) for side in SIDES}
        self.queues = {side: deque() for side in SIDES}
        self.levels = {1: dict(unlocks or {}), 2: {}}
        self.templates = {
            1: [self._template(t["name"], t["battalions"])
                for t in (data.DEFAULT_TEMPLATES if templates is None else templates)],
            2: [self._template("사단", b) for b in self.stage_data["ai_templates"]],
        }
        self.units = {}
        self.unit_serial = {side: 0 for side in SIDES}
        self.next_unit_id = 0
        self.ai = AI(self.difficulty_data)
        self._deploy_starting_units()

    @property
    def time(self):
        return self.ticks / TICK_HZ

    @staticmethod
    def _template(name, battalions):
        return {"name": name, "battalions": list(battalions),
                "cost": sum(data.BATTALIONS[b]["cost"] for b in battalions)}

    def _deploy_starting_units(self):
        for side, count_key in ((1, "player_start"), (2, "ai_start")):
            friendly = self.friendly_bases(side)
            enemy = self.friendly_bases(3 - side)
            forward_bases = sorted(friendly, key=lambda base: min(_distance(base, b) for b in enemy))
            templates = self.templates[side] or data.DEFAULT_TEMPLATES
            for i in range(self.stage_data[count_key]):
                self.spawn(side, templates[i % len(templates)], forward_bases[i % len(forward_bases)].cell)

    def spawn(self, side, template, cell):
        self.unit_serial[side] += 1
        unit = Unit(self.next_unit_id, side, f"제{self.unit_serial[side]}{template['name']}",
                    tuple(template["battalions"]), cell, self.levels[side])
        self.units[unit.id] = unit
        self.next_unit_id += 1
        unit.supply = self._supply(unit, self.friendly_bases(side))
        return unit

    def friendly_bases(self, side):
        return [base for base in self.bases if base.side == side]

    def income(self, side):
        income = (data.INCOME_BASE + self.territory[side] * data.INCOME_PER_HEX
                  + sum(data.BASE_KINDS[b.kind]["income"] for b in self.friendly_bases(side)))
        if side == 2:
            income *= self.stage_data["ai_income"] * self.difficulty_data["income"]
        return income

    def build_speed(self, side):
        speed = 1.0
        for base in self.bases:
            if base.original_side == side and base.side != side:
                speed *= 1 - data.BASE_KINDS[base.kind]["penalty"]
        return speed

    def build(self, side, template):
        if type(template) is not int or not 0 <= template < len(self.templates[side]):
            raise ValueError("편제 번호가 올바르지 않습니다.")
        cost = self.templates[side][template]["cost"]
        if self.resources[side] < cost:
            raise ValueError("생산 자원이 부족합니다.")
        self.resources[side] -= cost
        self.queues[side].append(Production(template))

    def _produce(self, side):
        self.resources[side] += self.income(side) * DT
        queue = self.queues[side]
        if not queue:
            return
        friendly = self.friendly_bases(side)
        if not friendly:
            return
        order = queue[0]
        order.progress += self.build_speed(side) * DT / data.BUILD_TIME
        if order.progress >= 1 - 1e-9:
            base = next((b for b in friendly if b.kind == "hq"), friendly[0])
            self.spawn(side, self.templates[side][order.template], base.cell)
            queue.popleft()

    def _roll_weather(self):
        return self.rng.choices(tuple(data.WEATHER_BY_ID), weights=(65, 17, 8, 10))[0]

    def _weather_interval(self):
        return round(self.rng.uniform(*data.WEATHER_CHANGE) * TICK_HZ)

    def _weather_at(self, x, y):
        n = data.WEATHER_CELLS
        c = min(n - 1, max(0, int(x * n / world.WIDTH)))
        r = min(n - 1, max(0, int(y * n / world.HEIGHT)))
        return data.WEATHER[data.WEATHER_BY_ID[self.weather[r * n + c]]]

    def _terrain_at(self, unit):
        return data.TERRAIN_BY_ID[self.terrain[unit.cell]]

    def _path(self, unit, cell):
        return world.find_path(unit.cell, cell, "engineer" in unit.effects, "mountain" in unit.effects)

    def _set_path(self, unit, cell, path):
        # Recenter first if a new order reverses direction mid-hex.
        if (unit.x, unit.y) != world.CENTERS[unit.cell]:
            path = (unit.cell, *path)
        unit.path = deque(path)
        unit.destination = cell if unit.path else None

    def move_unit(self, unit, cell):
        if unit.destination == cell and not unit.retreating:
            return True
        path = self._path(unit, cell)
        if path is None:
            return False
        self._set_path(unit, cell, path)
        unit.retreating = False
        unit.retreat_base = None
        return True

    def retreat_unit(self, unit):
        friendly = sorted(self.friendly_bases(unit.side), key=lambda b: _distance(unit, b))
        unit.retreating = True
        unit.retreat_base = None
        unit.path.clear()
        unit.destination = None
        for base in friendly:
            path = self._path(unit, base.cell)
            if path is not None:
                self._set_path(unit, base.cell, path)
                unit.retreat_base = base.id
                break

    def _march(self, unit):
        remaining = DT
        while unit.path and remaining > 0:
            physical_cell = world.index(*world.pixel_to_hex(unit.x, unit.y))
            if physical_cell is not None and self.terrain[physical_cell] != world.SEA:
                unit.cell = physical_cell
            x, y = world.CENTERS[unit.path[0]]
            distance = dist((x, y), (unit.x, unit.y))
            terrain_move = world.movement_factor(self._terrain_at(unit), "engineer" in unit.effects,
                                                 "mountain" in unit.effects)
            weather_move = _weather_factor(self._weather_at(unit.x, unit.y)["move"], unit.effects)
            speed = unit.speed * terrain_move * weather_move
            if distance <= speed * remaining:
                unit.x, unit.y = x, y
                unit.cell = unit.path.popleft()
                remaining -= distance / speed
            else:
                fraction = speed * remaining / distance
                unit.x += (x - unit.x) * fraction
                unit.y += (y - unit.y) * fraction
                break
        physical_cell = world.index(*world.pixel_to_hex(unit.x, unit.y))
        if physical_cell is not None and self.terrain[physical_cell] != world.SEA:
            unit.cell = physical_cell
        if not unit.path:
            unit.destination = None

    @staticmethod
    def _supply(unit, friendly):
        distance = min((_distance(unit, base) for base in friendly), default=float("inf"))
        if "transport" in unit.effects:
            distance /= 1.5
        falloff = (distance - data.SUPPLY_FULL_KM) / (data.SUPPLY_ZERO_KM - data.SUPPLY_FULL_KM)
        return 1 - min(1, max(0, falloff)) * (1 - data.SUPPLY_MIN)

    def _near_base(self, unit, bases):
        return min((base for base in bases if _distance(unit, base) <= ENGAGE + 1e-8),
                   key=lambda b: _distance(unit, b), default=None)

    def _unit_damage(self, unit, target, distance, disrupted):
        terrain = self._terrain_at(unit)
        weather = self._weather_at(unit.x, unit.y)
        range_factor = _weather_factor(weather["rng"], unit.effects)
        total = 0.0
        for cls, attack, radius in unit.attack:
            if distance > max(ENGAGE, radius * range_factor) + 1e-8:
                continue
            if isinstance(target, Unit):
                attack *= (sum(data.COUNTERS.get((cls, enemy), 1.0) for enemy in target.classes)
                           / len(target.classes))
            if cls in ("armor", "mech"):
                attack *= data.ARMOR_TERRAIN_PENALTY.get(terrain, 1.0)
            total += attack
        if not total:
            return 0.0
        if "recon" in unit.effects:
            total *= 1.1
        if "signals" in unit.effects:
            total *= 1.1
        if "mountain" in unit.effects and terrain in ("mountain", "highland"):
            total *= 1.3
        if disrupted:
            total *= 0.9
        return (total * (unit.hp / unit.mhp) * unit.supply
                * _weather_factor(weather["atk"], unit.effects)
                * self.rng.uniform(1 - weather["var"], 1 + weather["var"])
                * data.DAMAGE_K * 50 / (50 + self._defense(target)) * DT)

    def _defense(self, target):
        if isinstance(target, Base):
            defense = data.BASE_KINDS[target.kind]["dfn"]
        else:
            defense = target.defense * (1.1 if "signals" in target.effects else 1)
        terrain = data.TERRAIN_BY_ID[self.terrain[target.cell]]
        return defense * data.TERRAIN[terrain]["dfn"]

    def _combat(self, nearby, bases):
        unit_damage = defaultdict(float)
        base_damage = defaultdict(float)
        disrupted = set()
        # Determine all engagements before applying EW or simultaneous damage.
        for unit in self.units.values():
            enemies = nearby[unit.id]
            close_base = self._near_base(unit, bases[3 - unit.side])
            unit.engaged = bool(enemies and enemies[0][0] <= ENGAGE + 1e-8) or close_base is not None
            unit.target = None
            if "ew" in unit.effects and not unit.retreating:
                disrupted.update(enemy.id for distance, enemy in enemies if distance <= ENGAGE + 1e-8)
        for unit in self.units.values():
            if unit.retreating:
                continue
            enemies = nearby[unit.id]
            close_base = self._near_base(unit, bases[3 - unit.side])
            jammed = unit.id in disrupted
            if enemies and enemies[0][0] <= ENGAGE + 1e-8:
                distance, enemy = enemies[0]
                unit_damage[enemy.id] += self._unit_damage(unit, enemy, distance, jammed)
                unit.target = (enemy.x, enemy.y)
            elif close_base is not None:
                base_damage[close_base.id] += self._unit_damage(unit, close_base, _distance(unit, close_base), jammed)
                unit.target = (close_base.x, close_base.y)
            elif enemies:
                distance, enemy = enemies[0]
                damage = self._unit_damage(unit, enemy, distance, jammed)
                if damage:
                    unit_damage[enemy.id] += damage
                    unit.target = (enemy.x, enemy.y)

        occupied_bases = set()
        for base in self.bases:
            intruders = list(self._unit_index.nearby(3 - base.side, base.x, base.y, ENGAGE))
            if intruders:
                occupied_bases.add(base.id)
                _, target = min(intruders, key=lambda pair: pair[0])
                attack = data.BASE_KINDS[base.kind]["atk"]
                unit_damage[target.id] += attack * data.DAMAGE_K * 50 / (50 + self._defense(target)) * DT

        for unit in tuple(self.units.values()):
            damage = unit_damage[unit.id]
            unit.hp -= damage
            if unit.hp <= 0:
                del self.units[unit.id]
            elif damage and unit.hp < unit.mhp * unit.retreat_threshold and not unit.retreating:
                self.retreat_unit(unit)
            elif not unit.engaged and not damage:
                recovery = (0.01 * ("medical" in unit.effects)
                            + 0.005 * ("maintenance" in unit.effects))
                unit.hp = min(unit.mhp, unit.hp + unit.mhp * recovery * DT)

        captured = []
        for base in self.bases:
            base.hp -= base_damage[base.id]
            if base.hp <= 0:
                captured.append(base)
            elif base.id not in occupied_bases:
                base.hp = min(base.mhp, base.hp + DT)
        return captured

    def _claim(self, cell, side):
        old = self.owner[cell]
        if old != 0 and old != side:
            self.owner[cell] = side
            self.territory[old] -= 1
            self.territory[side] += 1
            self._changed.add(cell)

    def _claim_territory(self):
        claims = {}
        contested = set()
        for unit in self.units.values():
            cells = [unit.cell]
            if not any(self._unit_index.nearby(3 - unit.side, unit.x, unit.y, 30)):
                cells.extend(cell for cell in world.NEIGHBORS[unit.cell] if cell is not None)
            for cell in cells:
                if cell in claims and claims[cell] != unit.side:
                    contested.add(cell)
                claims[cell] = unit.side
        for cell in claims.keys() - contested:
            self._claim(cell, claims[cell])

    def _capture(self, base):
        base.side = 3 - base.side
        base.hp = 0.3 * base.mhp
        cells = {base.cell}
        for neighbor in world.NEIGHBORS[base.cell]:
            if neighbor is not None:
                cells.add(neighbor)
                cells.update(cell for cell in world.NEIGHBORS[neighbor] if cell is not None)
        for cell in cells:
            self._claim(cell, base.side)
        kind = data.BASE_KINDS[base.kind]["name"]
        self.events.append({"t": "event", "msg": f"{base.name} {kind}가 함락되었습니다.",
                            "kind": "good" if base.side == 1 else "bad"})
        reward = BASE_REWARDS.get(base.kind, 0)
        if reward and base.side == 1 and base.original_side == 2 and base.id not in self.rewarded_bases:
            if self.on_reward is not None:
                self.on_reward(reward)
            self.rewarded_bases.add(base.id)
            self.points_earned += reward
        if base.kind == "hq" and self.winner is None:
            self.winner, self.reason = base.side, "hq"

    def tick(self):
        if self.winner is not None:
            return
        self.ticks += 1
        for i, deadline in enumerate(self.weather_due):
            if self.ticks >= deadline:
                self.weather[i] = self._roll_weather()
                self.weather_due[i] = self.ticks + self._weather_interval()
        self.ai.update(self)
        for side in SIDES:
            self._produce(side)
        bases = {side: self.friendly_bases(side) for side in SIDES}
        before = UnitIndex(self.units.values())
        marching = []
        for unit in self.units.values():
            if unit.retreating and (unit.retreat_base is None or self.bases[unit.retreat_base].side != unit.side):
                self.retreat_unit(unit)
            close = (any(before.nearby(3 - unit.side, unit.x, unit.y, ENGAGE))
                     or self._near_base(unit, bases[3 - unit.side]) is not None)
            if (unit.retreating and unit.retreat_base is not None and not unit.path and not close
                    and unit.hp >= unit.mhp * unit.retreat_threshold):
                unit.retreating = False
                unit.retreat_base = None
            if unit.path and (unit.retreating or not close):
                marching.append(unit)
        for unit in marching:
            self._march(unit)
        self._unit_index = UnitIndex(self.units.values())
        nearby = {
            unit.id: sorted(self._unit_index.nearby(3 - unit.side, unit.x, unit.y, MAX_RANGE),
                            key=lambda pair: (pair[0], pair[1].id))
            for unit in self.units.values()
        }
        for unit in self.units.values():
            unit.supply = self._supply(unit, bases[unit.side])
        captured = self._combat(nearby, bases)
        self._claim_territory()
        # Resolve simultaneous HQ falls by base order, consistently with other captures.
        for base in captured:
            self._capture(base)
        if captured:
            bases = {side: self.friendly_bases(side) for side in SIDES}
            for unit in self.units.values():
                unit.supply = self._supply(unit, bases[unit.side])
        if self.winner is None and self.ticks >= data.MATCH_SECONDS * TICK_HZ:
            self.winner = 1 if self.territory[1] > self.territory[2] else 2
            self.reason = "time"

    def command(self, message):
        if self.winner is not None:
            return
        command = message.get("t")
        if command == "surrender":
            self.winner, self.reason = 2, "surrender"
        elif command == "build":
            self.build(1, message.get("template"))
        elif command in ("move", "retreat"):
            ids = message.get("ids")
            if not isinstance(ids, list) or any(type(i) is not int for i in ids):
                raise ValueError("사단 선택이 올바르지 않습니다.")
            units = [self.units[i] for i in dict.fromkeys(ids)
                     if i in self.units and self.units[i].side == 1]
            if command == "retreat":
                for unit in units:
                    self.retreat_unit(unit)
                return
            col, row = message.get("col"), message.get("row")
            cell = world.index(col, row) if type(col) is int and type(row) is int else None
            if cell is None or self.terrain[cell] == world.SEA:
                raise ValueError("이동할 수 없는 위치입니다.")
            moved = [self.move_unit(unit, cell) for unit in units]
            if moved and not any(moved):
                raise ValueError("이동 경로가 없습니다.")
        else:
            raise ValueError("알 수 없는 명령입니다.")

    def init_message(self):
        return {"t": "init", "you": 1, "cols": world.COLS, "rows": world.ROWS,
                "terrain": list(self.terrain), "owner": self.owner.copy(),
                "bases": [base.initial_state() for base in self.bases],
                "cities": [{"name": name, "col": col, "row": row} for name, col, row in self.cities],
                "templates": self.templates[1], "weather": self.weather.copy(),
                "weather_cells": data.WEATHER_CELLS, "stage": self.stage,
                "difficulty": self.difficulty, "match_seconds": data.MATCH_SECONDS}

    def state_message(self):
        changes = [[cell, self.owner[cell]] for cell in sorted(self._changed)
                   if self.owner[cell] != self._last_owner[cell]]
        for cell, side in changes:
            self._last_owner[cell] = side
        self._changed.clear()
        weather = None
        if self.weather != self._last_weather:
            weather = self.weather.copy()
            self._last_weather = weather.copy()
        return {"t": "state", "time": self.time, "res": round(self.resources[1], 2),
                "income": round(self.income(1), 4), "build_speed": self.build_speed(1),
                "queue": [{"template": order.template, "name": self.templates[1][order.template]["name"],
                           "progress": round(order.progress, 4)} for order in self.queues[1]],
                "units": [unit.state() for unit in self.units.values()],
                "bases": [base.state() for base in self.bases], "own": changes,
                "weather": weather, "terr": self.territory[1:]}

    def pop_events(self):
        events, self.events = self.events, []
        return events

    def end_message(self, user):
        return {"t": "end", "winner": self.winner, "reason": self.reason,
                "points_earned": self.points_earned, "user": user}
