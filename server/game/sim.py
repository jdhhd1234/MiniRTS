"""Authoritative fixed-step simulation shared by WebSocket and headless play."""

from collections import defaultdict, deque
from dataclasses import dataclass, field
from math import dist, floor
from random import Random

from . import data, hexmap as world
from .ai import AI
from .scripting import ScriptError

TICK_HZ = 10
STATE_HZ = 4
DT = 1 / TICK_HZ
SIDES = (1, 2)
ENGAGE = data.ENGAGE_KM
BASE_REWARDS = {"corps": data.REWARD_CORPS, "hq": data.REWARD_HQ}
MAX_HOOK_HEAL = 0.2   # Lua on_tick 회복 상한 (최대 체력 비율/초)


def _max_range(catalog):
    longest = 0

    for battalion in catalog.values():
        longest = max(longest, battalion.rng)

    return longest


MAX_RANGE = _max_range(data.BATTALIONS)


def _distance(a, b):
    return dist((a.x, a.y), (b.x, b.y))


def _weather_factor(value, effects):
    return (1 + value) / 2 if value < 1 and "cbrn" in effects else value


@dataclass
class Template:
    name: str
    battalions: list
    cost: int

    def to_json(self):
        return {"name": self.name, "battalions": list(self.battalions), "cost": self.cost}


@dataclass
class Scenario:
    """샌드박스 한 판의 설정: 맵, 대대 목록(Lua 대대 포함), 적 편제."""
    map: world.MapData
    catalog: dict
    ai_templates: list | None = None


@dataclass(frozen=True)
class Attack:
    """대대 하나의 공격 성분: 병과, 공격력(레벨 보정 포함), 사거리."""
    cls: str
    atk: float
    rng: float


@dataclass(slots=True)
class Contact:
    """UnitIndex.nearby가 돌려주는 적 사단과 그 거리."""
    distance: float
    unit: "Unit"

    def sort_key(self):
        return (self.distance, self.unit.id)


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
        self.hp = self.mhp = data.BASE_KINDS[self.kind].hp

    def state(self):
        return {"id": self.id, "side": self.side, "hp": round(self.hp, 2), "mhp": self.mhp}

    def initial_state(self):
        col, row = world.OFFSETS[self.cell]
        message = self.state()
        message.update({
            "kind": self.kind,
            "name": self.name,
            "x": self.x,
            "y": self.y,
            "col": col,
            "row": row,
        })
        return message


@dataclass
class Unit:
    id: int
    side: int
    name: str
    b: tuple[str, ...]
    cell: int
    levels: dict
    catalog: dict | None = field(default=None, repr=False)
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
    move_profile: tuple = field(init=False, repr=False)
    hooks: tuple = field(init=False, repr=False)
    path: deque = field(default_factory=deque)
    destination: int | None = None
    retreating: bool = False
    retreat_base: int | None = None
    engaged: bool = False
    target: tuple[float, float] | None = None
    supply: float = 1.0

    def __post_init__(self):
        self.x, self.y = world.CENTERS[self.cell]
        catalog = self.catalog if self.catalog is not None else data.BATTALIONS

        hp = 0
        defense = 0
        speeds = []
        effects = set()
        classes = []
        attack = []

        for key in self.b:
            battalion = catalog[key]
            bonus = 1 + (self.levels.get(key, 1) - 1) * data.LEVEL_BONUS

            hp += battalion.hp * bonus
            defense += battalion.dfn * bonus
            speeds.append(battalion.spd)
            if battalion.effect is not None:
                effects.add(battalion.effect)
            classes.append(battalion.cls)
            attack.append(Attack(battalion.cls, battalion.atk * bonus, battalion.rng))

        self.mhp = self.hp = hp
        self.defense = defense / len(self.b)
        self.speed = min(speeds)
        self.effects = effects
        self.classes = tuple(classes)
        self.attack = tuple(attack)
        self.fn = catalog[self.b[0]].fn
        self.move_profile = self._move_profile(catalog)

        # Lua 훅: 같은 대대가 여러 개여도 한 번만
        hooks = []
        seen = set()

        for key in self.b:
            battalion_hooks = catalog[key].hooks
            if battalion_hooks is not None and key not in seen:
                hooks.append(battalion_hooks)
                seen.add(key)

        self.hooks = tuple(hooks)

    def _move_profile(self, catalog):
        """지형별 이동 배율. 사단 전체 효과(공병·산악) 기준에 Lua 대대의 move 덮어쓰기를
        반영하고, 가장 느린 대대를 따른다. 기본 대대만 있으면 hexmap.move_profile과 같다."""

        base = world.move_profile("engineer" in self.effects, "mountain" in self.effects)
        profile = []

        for ident, factor in enumerate(base):
            kind = data.TERRAIN_BY_ID[ident]
            slowest = None

            for key in self.b:
                move = catalog[key].move
                value = move.get(kind, factor) if move else factor
                slowest = value if slowest is None else min(slowest, value)

            profile.append(slowest)

        return tuple(profile)

    @property
    def retreat_threshold(self):
        return 0.20 if "police" in self.effects else data.RETREAT_HP

    def state(self):
        if self.retreating:
            status = "retreat"
        elif self.engaged:
            status = "engaged"
        elif self.path:
            status = "move"
        else:
            status = "idle"

        dest = None
        if self.side == 1 and self.destination is not None:
            dest = list(world.CENTERS[self.destination])

        target = None
        if self.target is not None:
            target = list(self.target)

        return {
            "id": self.id,
            "side": self.side,
            "name": self.name,
            "x": round(self.x, 3),
            "y": round(self.y, 3),
            "hp": round(self.hp, 2),
            "mhp": round(self.mhp, 2),
            "st": status,
            "tgt": target,
            "dest": dest,
            "b": list(self.b),
            "fn": self.fn,
            "supply": round(self.supply, 3),
        }


@dataclass
class Production:
    template: int
    progress: float = 0.0


class UnitIndex:
    """Spatial lookup for engagement, ranged fire, and contested territory."""

    BUCKET_SIZE = MAX_RANGE

    def __init__(self, units, bucket_size=MAX_RANGE):
        self.BUCKET_SIZE = bucket_size
        self.buckets = defaultdict(list)
        for unit in units:
            self.buckets[(unit.side, floor(unit.x / self.BUCKET_SIZE),
                          floor(unit.y / self.BUCKET_SIZE))].append(unit)

    # 병목을 지켜보겠음... (2026/11/20 이 주석은 AI수정금지)
    def nearby(self, side, x, y, radius):
        size = self.BUCKET_SIZE
        for i in range(floor((x - radius) / size), floor((x + radius) / size) + 1):
            for j in range(floor((y - radius) / size), floor((y + radius) / size) + 1):
                for unit in self.buckets.get((side, i, j), ()):
                    distance = dist((x, y), (unit.x, unit.y))
                    if unit.hp > 0 and distance <= radius + 1e-8:
                        yield Contact(distance, unit)

    def any_nearby(self, side, x, y, radius):
        for _ in self.nearby(side, x, y, radius):
            return True

        return False


class Game:
    """One match; call tick() at 10 Hz or advance directly in headless play."""

    def __init__(self, stage=1, difficulty="normal", templates=None, unlocks=None,
                 *, seed=None, on_reward=None, speed=1, scenario=None):
        """scenario가 있으면 샌드박스: 스테이지·보상 없이 scenario의 맵과 대대를 쓴다."""
        if speed not in data.SPEEDS or isinstance(speed, bool):
            raise ValueError("배속이 올바르지 않습니다.")

        self.sandbox = scenario is not None
        self.speed = speed
        self.stage = None if self.sandbox else stage
        self.difficulty = difficulty
        self.stage_data = data.SANDBOX_STAGE if self.sandbox else data.STAGES[stage]
        self.map = scenario.map if self.sandbox else world.default_map()
        self.catalog = scenario.catalog if self.sandbox else data.BATTALIONS
        self.max_range = _max_range(self.catalog)
        self.difficulty_data = data.DIFFICULTY[difficulty]
        self.rng = Random(seed)
        self.on_reward = on_reward
        self.ticks = 0
        self.winner = None
        self.reason = None
        self.points_earned = 0
        self.rewarded_bases = set()
        self.events = []
        self.terrain, self.cities = self.map.terrain, self.map.cities


        # 1. 기지 배치
        self.bases = []

        for i, spot in enumerate(self.map.bases):
            self.bases.append(Base(i, spot.side, spot.kind, spot.name, spot.cell))


        # 2. 초기 영토: 육지 셀은 가장 가까운 기지의 진영 소유
        self.owner = []

        for xy, terrain in zip(world.CENTERS, self.terrain):
            if terrain == world.SEA:
                self.owner.append(0)
            else:
                nearest = min(self.bases, key=lambda base: dist((base.x, base.y), xy))
                self.owner.append(nearest.side)

        self.territory = [0, self.owner.count(1), self.owner.count(2)]
        self._last_owner = self.owner.copy()
        self._changed = set()


        # 3. 날씨 (난수 순서 유지: 날씨 전부 굴린 뒤 변경 주기)
        self.weather = []

        for _ in range(data.WEATHER_CELLS ** 2):
            self.weather.append(self._roll_weather())

        self._last_weather = self.weather.copy()
        self.weather_due = []

        for _ in self.weather:
            self.weather_due.append(self._weather_interval())


        # 4. 진영별 자원·생산·편제
        self.resources = {}
        self.queues = {}
        self.unit_serial = {}

        for side in SIDES:
            self.resources[side] = float(data.START_RESOURCES)
            self.queues[side] = deque()
            self.unit_serial[side] = 0

        self.levels = {1: dict(unlocks or {}), 2: {}}

        player_templates = data.DEFAULT_TEMPLATES if templates is None else templates
        self.templates = {1: [], 2: []}

        for template in player_templates:
            self.templates[1].append(self._template(template["name"], template["battalions"]))

        ai_templates = self.stage_data.ai_templates

        if self.sandbox and scenario.ai_templates:
            ai_templates = scenario.ai_templates

        for battalions in ai_templates:
            self.templates[2].append(self._template("사단", battalions))


        # 5. 유닛·AI
        self.units = {}
        self.next_unit_id = 0
        self.ai = AI(self.difficulty_data)
        self._deploy_starting_units()

    @property
    def time(self):
        return self.ticks / TICK_HZ

    def _template(self, name, battalions):
        cost = 0

        for key in battalions:
            cost += self.catalog[key].cost

        return Template(name, list(battalions), cost)

    def _default_templates(self):
        templates = []

        for template in data.DEFAULT_TEMPLATES:
            templates.append(self._template(template["name"], template["battalions"]))

        return templates

    def _bases_by_side(self):
        bases = {}

        for side in SIDES:
            bases[side] = self.friendly_bases(side)

        return bases

    def _deploy_starting_units(self):
        for side, count_key in ((1, "player_start"), (2, "ai_start")):
            friendly = self.friendly_bases(side)
            enemy = self.friendly_bases(3 - side)

            # 적 기지에 가까운 전방 기지부터 배치
            def front_distance(base):
                nearest = float("inf")
                for other in enemy:
                    nearest = min(nearest, _distance(base, other))
                return nearest

            forward_bases = sorted(friendly, key=front_distance)

            templates = self.templates[side]
            if not templates:
                templates = self._default_templates()

            for i in range(getattr(self.stage_data, count_key)):
                template = templates[i % len(templates)]
                base = forward_bases[i % len(forward_bases)]
                self.spawn(side, template, base.cell)

    def spawn(self, side, template, cell):
        self.unit_serial[side] += 1
        unit = Unit(self.next_unit_id, side, f"제{self.unit_serial[side]}{template.name}",
                    tuple(template.battalions), cell, self.levels[side], self.catalog)
        self.units[unit.id] = unit
        self.next_unit_id += 1
        unit.supply = self._supply(unit, self.friendly_bases(side))
        return unit

    def friendly_bases(self, side):
        bases = []

        for base in self.bases:
            if base.side == side:
                bases.append(base)

        return bases

    def income(self, side):
        base_income = 0

        for base in self.friendly_bases(side):
            base_income += data.BASE_KINDS[base.kind].income

        income = data.INCOME_BASE + self.territory[side] * data.INCOME_PER_HEX + base_income

        if side == 2:
            income *= self.stage_data.ai_income * self.difficulty_data.income

        return income

    def build_speed(self, side):
        speed = 1.0
        for base in self.bases:
            if base.original_side == side and base.side != side:
                speed *= 1 - data.BASE_KINDS[base.kind].penalty
        return speed

    def build(self, side, template):
        if type(template) is not int or not 0 <= template < len(self.templates[side]):
            raise ValueError("편제 번호가 올바르지 않습니다.")
        cost = self.templates[side][template].cost
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
            # 본부가 있으면 본부에서, 없으면 첫 아군 기지에서 배치
            spawn_base = friendly[0]
            for base in friendly:
                if base.kind == "hq":
                    spawn_base = base
                    break

            self.spawn(side, self.templates[side][order.template], spawn_base.cell)
            queue.popleft()

    def _roll_weather(self):
        return self.rng.choices(tuple(data.WEATHER_BY_ID), weights=(65, 17, 8, 10))[0]

    def _weather_interval(self):
        return round(self.rng.uniform(*data.WEATHER_CHANGE) * TICK_HZ)

    def _weather_at(self, x, y):
        return data.WEATHER[data.WEATHER_BY_ID[self.weather[self._weather_index(x, y)]]]

    def _weather_index(self, x, y):
        n = data.WEATHER_CELLS
        c = min(n - 1, max(0, int(x * n / world.WIDTH)))
        r = min(n - 1, max(0, int(y * n / world.HEIGHT)))
        return r * n + c

    def _terrain_at(self, unit):
        return data.TERRAIN_BY_ID[self.terrain[unit.cell]]

    def _path(self, unit, cell):
        return self.map.pathfinder.find(unit.cell, cell, unit.move_profile)

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
            if physical_cell is not None and unit.move_profile[self.terrain[physical_cell]] > 0:
                unit.cell = physical_cell
            x, y = world.CENTERS[unit.path[0]]
            distance = dist((x, y), (unit.x, unit.y))
            terrain_move = unit.move_profile[self.terrain[unit.cell]]
            weather_move = _weather_factor(self._weather_at(unit.x, unit.y).move, unit.effects)
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
        if physical_cell is not None and unit.move_profile[self.terrain[physical_cell]] > 0:
            unit.cell = physical_cell
        if not unit.path:
            unit.destination = None

    @staticmethod
    def _supply(unit, friendly):
        distance = float("inf")

        for base in friendly:
            distance = min(distance, _distance(unit, base))

        if "transport" in unit.effects:
            distance /= 1.5

        falloff = (distance - data.SUPPLY_FULL_KM) / (data.SUPPLY_ZERO_KM - data.SUPPLY_FULL_KM)
        return 1 - min(1, max(0, falloff)) * (1 - data.SUPPLY_MIN)

    def _near_base(self, unit, bases):
        """교전 반경 안의 가장 가까운 기지. 없으면 None."""

        nearest = None
        nearest_distance = None

        for base in bases:
            distance = _distance(unit, base)

            if distance > ENGAGE + 1e-8:
                continue

            if nearest is None or distance < nearest_distance:
                nearest = base
                nearest_distance = distance

        return nearest

    def _unit_damage(self, unit, target, distance, disrupted):
        terrain = self._terrain_at(unit)
        weather = self._weather_at(unit.x, unit.y)
        range_factor = _weather_factor(weather.rng, unit.effects)


        # 1. 사거리 안의 대대 공격력 합산 (상성·지형 보정)
        total = 0.0

        for part in unit.attack:
            if distance > max(ENGAGE, part.rng * range_factor) + 1e-8:
                continue

            attack = part.atk

            if isinstance(target, Unit):
                counter = 0
                for enemy in target.classes:
                    counter += data.COUNTERS.get((part.cls, enemy), 1.0)
                attack *= counter / len(target.classes)

            if part.cls in ("armor", "mech"):
                attack *= data.ARMOR_TERRAIN_PENALTY.get(terrain, 1.0)

            total += attack

        if not total:
            return 0.0


        # 2. 지원부대 효과
        if "recon" in unit.effects:
            total *= 1.1
        if "signals" in unit.effects:
            total *= 1.1
        if "mountain" in unit.effects and terrain in ("mountain", "highland"):
            total *= 1.3
        if disrupted:
            total *= 0.9


        # 3. 체력·보급·날씨·방어 보정
        damage = (total * (unit.hp / unit.mhp) * unit.supply
                  * _weather_factor(weather.atk, unit.effects)
                  * self.rng.uniform(1 - weather.var, 1 + weather.var)
                  * data.DAMAGE_K * 50 / (50 + self._defense(target)) * DT)


        # 4. Lua on_attack 훅: 반환값이 새 피해량
        for hooks in unit.hooks:
            if not hooks.has("on_attack"):
                continue

            classes = list(target.classes) if isinstance(target, Unit) else []
            context = self._hook_context(unit)
            context.update({"damage": damage, "distance": distance,
                            "target": "unit" if isinstance(target, Unit) else "base",
                            "target_classes": classes})
            damage = self._call_hook(hooks, "on_attack", context, damage, 0, 1e6)

        return damage

    def _hook_context(self, unit):
        weather = data.WEATHER_BY_ID[self.weather[self._weather_index(unit.x, unit.y)]]
        return {"hp": unit.hp, "mhp": unit.mhp, "supply": unit.supply, "engaged": unit.engaged,
                "side": unit.side, "terrain": self._terrain_at(unit), "weather": weather,
                "time": self.time}

    def _call_hook(self, hooks, name, context, default, low, high):
        """Lua 훅 호출. nil이면 default. 오류면 그 대대의 훅을 끄고 작전 기록에 남긴다."""
        try:
            value = hooks.number(name, context, low, high)
        except ScriptError as exc:
            self.events.append({"t": "event", "msg": f"Lua 오류로 훅을 껐습니다 · {exc}", "kind": "bad"})
            return default

        return default if value is None else value

    def _defense(self, target):
        if isinstance(target, Base):
            defense = data.BASE_KINDS[target.kind].dfn
        else:
            defense = target.defense * (1.1 if "signals" in target.effects else 1)
        terrain = data.TERRAIN_BY_ID[self.terrain[target.cell]]
        return defense * data.TERRAIN[terrain].dfn

    def _combat(self, nearby, bases):
        unit_damage = defaultdict(float)
        base_damage = defaultdict(float)
        disrupted = set()


        # 1. 교전 판정과 전자전 방해 대상 (피해 적용 전에 모두 확정)
        for unit in self.units.values():
            enemies = nearby[unit.id]
            close_base = self._near_base(unit, bases[3 - unit.side])
            in_contact = bool(enemies) and enemies[0].distance <= ENGAGE + 1e-8
            unit.engaged = in_contact or close_base is not None
            unit.target = None

            if "ew" in unit.effects and not unit.retreating:
                for contact in enemies:
                    if contact.distance <= ENGAGE + 1e-8:
                        disrupted.add(contact.unit.id)


        # 2. 사단 공격: 근접 적 > 근처 기지 > 사거리 안 원거리 적
        for unit in self.units.values():
            if unit.retreating:
                continue

            enemies = nearby[unit.id]
            close_base = self._near_base(unit, bases[3 - unit.side])
            jammed = unit.id in disrupted

            if enemies and enemies[0].distance <= ENGAGE + 1e-8:
                enemy = enemies[0].unit
                unit_damage[enemy.id] += self._unit_damage(unit, enemy, enemies[0].distance, jammed)
                unit.target = (enemy.x, enemy.y)
            elif close_base is not None:
                distance = _distance(unit, close_base)
                base_damage[close_base.id] += self._unit_damage(unit, close_base, distance, jammed)
                unit.target = (close_base.x, close_base.y)
            elif enemies:
                enemy = enemies[0].unit
                damage = self._unit_damage(unit, enemy, enemies[0].distance, jammed)
                if damage:
                    unit_damage[enemy.id] += damage
                    unit.target = (enemy.x, enemy.y)


        # 3. 기지 반격: 가장 가까운 침입 사단
        occupied_bases = set()

        for base in self.bases:
            intruders = list(self._unit_index.nearby(3 - base.side, base.x, base.y, ENGAGE))

            if intruders:
                occupied_bases.add(base.id)
                target = min(intruders, key=lambda contact: contact.distance).unit
                attack = data.BASE_KINDS[base.kind].atk
                unit_damage[target.id] += attack * data.DAMAGE_K * 50 / (50 + self._defense(target)) * DT


        # 4. 사단 피해 적용: 전멸, 자동 후퇴, 비교전 회복 (Lua on_damaged / on_tick 포함)
        for unit in tuple(self.units.values()):
            damage = unit_damage[unit.id]

            for hooks in unit.hooks:
                if damage and hooks.has("on_damaged"):
                    context = self._hook_context(unit)
                    context["damage"] = damage
                    damage = self._call_hook(hooks, "on_damaged", context, damage, 0, 1e6)

            unit.hp -= damage

            if unit.hp <= 0:
                del self.units[unit.id]
            elif damage and unit.hp < unit.mhp * unit.retreat_threshold and not unit.retreating:
                self.retreat_unit(unit)
            elif not unit.engaged and not damage:
                recovery = (0.01 * ("medical" in unit.effects)
                            + 0.005 * ("maintenance" in unit.effects))
                unit.hp = min(unit.mhp, unit.hp + unit.mhp * recovery * DT)

            if unit.hp > 0:
                for hooks in unit.hooks:
                    if hooks.has("on_tick"):
                        heal = self._call_hook(hooks, "on_tick", self._hook_context(unit), 0,
                                               0, MAX_HOOK_HEAL)
                        unit.hp = min(unit.mhp, unit.hp + unit.mhp * heal * DT)


        # 5. 기지 피해 적용: 함락 목록 반환, 비점령 기지는 회복
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

            # 근처(30km)에 적이 없으면 인접 셀까지 점령
            if not self._unit_index.any_nearby(3 - unit.side, unit.x, unit.y, 30):
                for cell in world.NEIGHBORS[unit.cell]:
                    if cell is not None:
                        cells.append(cell)

            for cell in cells:
                if cell in claims and claims[cell] != unit.side:
                    contested.add(cell)
                claims[cell] = unit.side

        for cell in claims.keys() - contested:
            self._claim(cell, claims[cell])

    def _capture(self, base):
        base.side = 3 - base.side
        base.hp = 0.3 * base.mhp


        # 1. 기지 주변 2칸 영토 획득
        cells = {base.cell}

        for neighbor in world.NEIGHBORS[base.cell]:
            if neighbor is None:
                continue

            cells.add(neighbor)

            for cell in world.NEIGHBORS[neighbor]:
                if cell is not None:
                    cells.add(cell)

        for cell in cells:
            self._claim(cell, base.side)


        # 2. 함락 알림
        kind = data.BASE_KINDS[base.kind].name
        self.events.append({"t": "event", "msg": f"{base.name} {kind}가 함락되었습니다.",
                            "kind": "good" if base.side == 1 else "bad"})


        # 3. 가챠 포인트 보상 (적 기지를 처음 점령할 때만)
        reward = BASE_REWARDS.get(base.kind, 0)

        if (reward and not self.sandbox and base.side == 1 and base.original_side == 2
                and base.id not in self.rewarded_bases):
            if self.on_reward is not None:
                self.on_reward(reward)
            self.rewarded_bases.add(base.id)
            self.points_earned += reward


        # 4. 본부 함락 시 승패 결정
        if base.kind == "hq" and self.winner is None:
            self.winner, self.reason = base.side, "hq"

    def _update_supply(self, bases):
        for unit in self.units.values():
            unit.supply = self._supply(unit, bases[unit.side])

    def tick(self):
        if self.winner is not None:
            return
        self.ticks += 1


        # 1. 날씨 변경
        for i, deadline in enumerate(self.weather_due):
            if self.ticks >= deadline:
                self.weather[i] = self._roll_weather()
                self.weather_due[i] = self.ticks + self._weather_interval()


        # 2. AI 판단과 생산
        self.ai.update(self)

        for side in SIDES:
            self._produce(side)


        # 3. 이동: 후퇴 갱신 후, 교전 중이 아닌(또는 후퇴 중인) 사단만 행군
        bases = self._bases_by_side()
        before = UnitIndex(self.units.values(), self.max_range)
        marching = []

        for unit in self.units.values():
            if unit.retreating and (unit.retreat_base is None or self.bases[unit.retreat_base].side != unit.side):
                self.retreat_unit(unit)

            close = (before.any_nearby(3 - unit.side, unit.x, unit.y, ENGAGE)
                     or self._near_base(unit, bases[3 - unit.side]) is not None)

            if (unit.retreating and unit.retreat_base is not None and not unit.path and not close
                    and unit.hp >= unit.mhp * unit.retreat_threshold):
                unit.retreating = False
                unit.retreat_base = None

            if unit.path and (unit.retreating or not close):
                marching.append(unit)

        for unit in marching:
            self._march(unit)


        # 4. 사거리 안의 적 목록 (거리, id 순)
        self._unit_index = UnitIndex(self.units.values(), self.max_range)
        nearby = {}

        for unit in self.units.values():
            contacts = self._unit_index.nearby(3 - unit.side, unit.x, unit.y, self.max_range)
            nearby[unit.id] = sorted(contacts, key=Contact.sort_key)


        # 5. 보급, 전투, 영토
        self._update_supply(bases)
        captured = self._combat(nearby, bases)
        self._claim_territory()


        # 6. 기지 함락: 동시 함락은 기지 순서대로 처리
        for base in captured:
            self._capture(base)

        if captured:
            self._update_supply(self._bases_by_side())


        # 7. 시간 종료: 영토가 많은 쪽 승리
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

        elif command == "speed":
            value = message.get("value")

            if not self.sandbox:
                raise ValueError("캠페인에서는 전투 중 배속을 바꿀 수 없습니다.")
            if value not in data.SPEEDS or isinstance(value, bool):
                raise ValueError("배속이 올바르지 않습니다.")

            self.speed = value

        elif command in ("move", "retreat"):

            # 1. 선택한 아군 사단 (중복 id 제거, 순서 유지)
            ids = message.get("ids")

            if not isinstance(ids, list):
                raise ValueError("사단 선택이 올바르지 않습니다.")

            for unit_id in ids:
                if type(unit_id) is not int:
                    raise ValueError("사단 선택이 올바르지 않습니다.")

            units = []

            for unit_id in dict.fromkeys(ids):
                if unit_id in self.units and self.units[unit_id].side == 1:
                    units.append(self.units[unit_id])

            if command == "retreat":
                for unit in units:
                    self.retreat_unit(unit)
                return


            # 2. 이동 목적지 검사
            col, row = message.get("col"), message.get("row")
            cell = world.index(col, row) if type(col) is int and type(row) is int else None

            # 샌드박스는 바다를 건너는 Lua 대대가 있을 수 있어 경로 탐색에 맡긴다.
            if cell is None or (self.terrain[cell] == world.SEA and not self.sandbox):
                raise ValueError("이동할 수 없는 위치입니다.")


            # 3. 이동 명령 (경로가 하나도 없으면 오류)
            any_moved = False

            for unit in units:
                if self.move_unit(unit, cell):
                    any_moved = True

            if units and not any_moved:
                raise ValueError("이동 경로가 없습니다.")

        else:
            raise ValueError("알 수 없는 명령입니다.")

    def init_message(self):
        bases = []

        for base in self.bases:
            bases.append(base.initial_state())

        cities = []

        for name, col, row in self.cities:
            cities.append({"name": name, "col": col, "row": row})

        templates = []

        for template in self.templates[1]:
            templates.append(template.to_json())

        # 클라이언트가 이름을 모르는 Lua 대대
        custom = []

        for key, battalion in self.catalog.items():
            if key not in data.BATTALIONS:
                custom.append({"id": key, "name": battalion.name, "cls": battalion.cls})

        message = {
            "t": "init",
            "sandbox": self.sandbox,
            "speed": self.speed,
            "map_name": self.map.name,
            "custom_battalions": custom,
            "you": 1,
            "cols": world.COLS,
            "rows": world.ROWS,
            "terrain": list(self.terrain),
            "owner": self.owner.copy(),
            "bases": bases,
            "cities": cities,
            "templates": templates,
            "weather": self.weather.copy(),
            "weather_cells": data.WEATHER_CELLS,
            "stage": self.stage,
            "difficulty": self.difficulty,
            "match_seconds": data.MATCH_SECONDS,
        }

        return message

    def state_message(self):

        # 1. 변경된 소유권 정보 처리
        changes = []

        for cell in sorted(self._changed):
            side = self.owner[cell]

            if side != self._last_owner[cell]:
                changes.append([cell, side])

        for cell, side in changes:
            self._last_owner[cell] = side

        self._changed.clear()


        # 2. 날씨 정보 처리
        weather = None

        if self.weather != self._last_weather:
            weather = self.weather.copy()
            self._last_weather = weather.copy()


        # 3. 생산 대기열 정보 처리
        queue = []

        for order in self.queues[1]:
            template = order.template
            name = self.templates[1][template].name
            progress = round(order.progress, 4)

            queue.append({
                "template": template,
                "name": name,
                "progress": progress
            })


        # 4. 유닛 정보 처리
        units = []

        for unit in self.units.values():
            units.append(unit.state())


        # 5. 기지 정보 처리
        bases = []

        for base in self.bases:
            bases.append(base.state())


        # 6. 최종 상태 메시지 생성
        message = {
            "t": "state",
            "time": self.time,
            "res": round(self.resources[1], 2),
            "income": round(self.income(1), 4),
            "build_speed": self.build_speed(1),
            "queue": queue,
            "units": units,
            "bases": bases,
            "own": changes,
            "weather": weather,
            "terr": self.territory[1:],
            "speed": self.speed
        }

        return message

    def pop_events(self):
        events, self.events = self.events, []
        return events

    def end_message(self, user):
        return {"t": "end", "winner": self.winner, "reason": self.reason,
                "points_earned": self.points_earned, "user": user}
