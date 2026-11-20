"""MinRTS 지휘 AI: Blackboard + Utility AI + GOAP + HTN.

생각 주기(난이도 think초)마다:
1. Blackboard 갱신 (인지)
2. Utility: 생산할 편제와 목표(방어할 기지·공격할 표적)에 점수를 매긴다
3. 배정: 점수 높은 목표부터 분대(Squad)를 만들거나 유지하고, 급한 방어에는 약한 공격 분대를 돌린다
4. GOAP: 분대마다 지금 사실에서 목표 사실까지 행동 순서를 짠다
5. HTN: 계획의 각 행동을 상황에 맞는 방법으로 쪼개 실제 게임 명령(이동·생산)을 낸다
"""

from dataclasses import dataclass, field
from math import dist
from time import perf_counter

from .. import data, hexmap as world
from . import goap, utility
from .blackboard import BASE_VALUE, Blackboard, Shelved, base_power, distance, unit_power
from .htn import Compound, Domain, Method, Primitive

ENGAGE_NEAR = 50          # 표적까지 이 거리(km) 안이면 '근접'
COHESION = 70             # 분대원이 중심에서 이 거리(km) 안이면 '집결'
STAGING_GAP = 120         # 먼 표적은 이 거리(km) 앞 집결지를 먼저 거친다
STAGING_FROM = 220        # 표적이 이보다 멀면 집결지를 쓴다
PATIENCE = 40             # 증원을 이만큼(초) 기다린 뒤에는 MIN_COMMIT 이상이면 진격
MIN_COMMIT = 0.5          # PATIENCE 뒤에도 필요 전력의 이 비율이 안 되면 목표를 포기
OVERKILL = 2.0            # 남는 사단은 이 배수 미만인 분대부터 채운다 (한 분대 몰빵 방지)
SHELVE_SECONDS = 30       # 포기한 목표는 이만큼(초) 다시 고르지 않는다
MAX_SQUAD = 8
EMERGENCY = 0.55          # 이 이상인 방어 목표는 공격 분대 병력을 끌어온다
MIN_GOAL_SCORE = 0.05
MIN_BUILD_SCORE = 0.1
MAX_QUEUE = 2


@dataclass
class Need:
    """distribute_spare용: 공격 분대의 현재 전력과 필요 전력."""
    squad: object
    power: float
    required: float

    @property
    def ratio(self):
        return self.power / self.required


@dataclass
class AIProfile:
    """AI 성향 (Utility 가중치). Lua `ai` 테이블로 바꿀 수 있다."""
    attack: float = 1.0
    defend: float = 1.0
    build: float = 1.0
    caution: float = 1.0      # 공격 전에 표적 방어력의 몇 배를 모을지


@dataclass
class Target:
    kind: str        # "base" 또는 "unit"
    ref: object      # Base 또는 Unit
    value: float

    @property
    def x(self):
        return self.ref.x

    @property
    def y(self):
        return self.ref.y

    @property
    def cell(self):
        return self.ref.cell


@dataclass
class TaskContext:
    """HTN 조건과 기본 작업이 보는 값."""
    game: object
    ai: object
    squad: object
    target: object = None       # Target (공격)
    threat: object = None       # Threat (방어)
    trace: list = field(default_factory=list)

    @property
    def units(self):
        return self.ai.blackboard.squad_units(self.squad)


# ------------------------------------------------------------------ 계산 도우미

def _centroid(units):
    x = 0.0
    y = 0.0

    for unit in units:
        x += unit.x
        y += unit.y

    return x / len(units), y / len(units)


def _spread(units):
    cx, cy = _centroid(units)
    widest = 0.0

    for unit in units:
        widest = max(widest, dist((cx, cy), (unit.x, unit.y)))

    return widest


def _nearest(units, x, y):
    return sorted(units, key=lambda unit: dist((unit.x, unit.y), (x, y)))


def _power(units):
    total = 0.0

    for unit in units:
        total += unit_power(unit)

    return total


def _counter_score(game, template, enemy_classes):
    """편제가 지금 보이는 적 병과에 얼마나 유리한지 (상성 배율 평균)."""

    if not enemy_classes:
        return 1.0

    total = 0.0
    weight = 0

    for key in template.battalions:
        cls = game.catalog[key].cls

        for enemy_cls, count in enemy_classes.items():
            total += data.COUNTERS.get((cls, enemy_cls), 1.0) * count
            weight += count

    return total / weight


def _template_power(game, template):
    power = 0.0

    for key in template.battalions:
        battalion = game.catalog[key]
        power += battalion.hp * battalion.atk / 100

    return power


# ------------------------------------------------------------------ Utility 고려 사항

def _affordable(option, ai):
    return 1.0 if option.payload.cost <= ai.blackboard.resources else 0.0


def _counter(option, ai):
    # 배율 0.8~1.6 → 0.2~1.0
    return (_counter_score(ai.game, option.payload, ai.blackboard.enemy_classes) - 0.6)


def _army_need(option, ai):
    board = ai.blackboard
    total = board.own_power + board.enemy_power
    need = board.enemy_power / total if total else 0.5
    return 0.3 + 0.7 * need


def _efficiency(option, ai):
    # 비용 대비 전투력. 기본 편제는 비용 1당 0.3~0.5
    return _template_power(ai.game, option.payload) / option.payload.cost / 0.5


def _target_value(option, ai):
    return option.payload.value


def _target_proximity(option, ai):
    origin = ai.blackboard.free_units or ai.blackboard.own_units or ai.blackboard.own_bases

    if not origin:
        return 0.0

    nearest = _nearest(origin, option.payload.x, option.payload.y)[0]
    return 1 / (1 + distance(nearest, option.payload) / 400)


def _target_odds(option, ai):
    board = ai.blackboard
    available = _power(board.free_units)
    squad = board.squad_for(option.name)

    if squad is not None:
        available += _power(board.squad_units(squad))

    defense = ai.defense_of(option.payload) * ai.profile.caution
    return available / (available + defense + 1)


def _target_weakened(option, ai):
    ref = option.payload.ref
    return 1 - 0.5 * ref.hp / ref.mhp


def _base_importance(option, ai):
    return BASE_VALUE[option.payload.base.kind]


def _danger(option, ai):
    threat = option.payload
    guard = base_power(threat.base) + _power(ai.blackboard.free_units) * 0.25
    return threat.power / (threat.power + guard + 1)


def _urgency(option, ai):
    return 1 - option.payload.nearest / 120


BUILD_CONSIDERATIONS = [
    utility.Consideration("affordable", _affordable),
    utility.Consideration("counter", _counter),
    utility.Consideration("army_need", _army_need),
    utility.Consideration("efficiency", _efficiency),
]
ATTACK_CONSIDERATIONS = [
    utility.Consideration("value", _target_value),
    utility.Consideration("proximity", _target_proximity),
    utility.Consideration("odds", _target_odds),
    utility.Consideration("weakened", _target_weakened),
]
DEFEND_CONSIDERATIONS = [
    utility.Consideration("importance", _base_importance),
    utility.Consideration("danger", _danger),
    utility.Consideration("urgency", _urgency),
]


# ------------------------------------------------------------------ GOAP 행동

ACTIONS = [
    goap.Action("FormSquad", {"free_units": True, "has_squad": False}, {"has_squad": True}, 1),
    goap.Action("RequestUnits", {"can_afford": True, "free_units": False}, {"free_units": True}, 5),
    goap.Action("Reinforce", {"has_squad": True, "free_units": True, "squad_strong": False},
                {"squad_strong": True, "free_units": False}, 2),
    goap.Action("Regroup", {"has_squad": True, "cohesive": False}, {"cohesive": True}, 1),
    goap.Action("Advance", {"has_squad": True, "squad_strong": True, "cohesive": True, "near_target": False},
                {"near_target": True}, 2),
    goap.Action("Assault", {"has_squad": True, "squad_strong": True, "near_target": True},
                {"target_engaged": True}, 1),
    goap.Action("Intercept", {"has_squad": True}, {"base_defended": True}, 1),
]
GOALS = {"attack": {"target_engaged": True}, "defend": {"base_defended": True}}

# 이동 행동은 여러 생각 주기에 걸쳐 진행되므로, 실행하면 그 주기의 계획 실행을 멈춘다.
ONGOING = {"Regroup", "Advance", "Assault", "Intercept"}


# ------------------------------------------------------------------ HTN 기본 작업

def _assign_group(context):
    board = context.ai.blackboard
    x, y = context.ai.goal_point(context)

    for unit in _nearest(board.free_units, x, y)[:context.ai.group_size]:
        board.assign(context.squad, unit)


def _assign_until_strong(context):
    board = context.ai.blackboard
    x, y = context.ai.goal_point(context)
    required = context.ai.required_power(context)

    for unit in _nearest(board.free_units, x, y):
        if _power(context.units) >= required or len(context.squad.unit_ids) >= MAX_SQUAD:
            break
        board.assign(context.squad, unit)

    if _power(context.units) < required:
        context.ai.note(context.squad.target_key, "증원 중 · 가용 사단이 모자람")


def _build_best(context):
    context.ai.build_best(context.game)


def _rally(context):
    x, y = context.ai.goal_point(context)
    units = _nearest(context.units, x, y)
    lead = units[0]

    # 선두는 멈추고 나머지가 선두에게 모인다.
    lead.path.clear()
    lead.destination = None

    for unit in units[1:]:
        context.game.move_unit(unit, lead.cell)


def _move_staging(context):
    """표적 앞 집결지로 이동. 집결지는 도착할 때까지 고정한다.

    (이동 명령을 바꿀 때마다 사단이 현재 헥스 중심으로 되돌아가므로 매번 새로 잡으면 느려진다.)
    """

    squad = context.squad
    cx, cy = _centroid(context.units)

    if squad.waypoint is not None:
        wx, wy = world.CENTERS[squad.waypoint]
        if dist((cx, cy), (wx, wy)) <= ENGAGE_NEAR:
            squad.waypoint = None

    if squad.waypoint is None:
        x, y = context.ai.goal_point(context)
        ratio = STAGING_GAP / dist((cx, cy), (x, y))
        squad.waypoint = world.index(*world.pixel_to_hex(x + (cx - x) * ratio, y + (cy - y) * ratio))

    goal_cell = context.ai.goal_cell(context)

    for unit in context.units:
        if squad.waypoint is None or not context.game.move_unit(unit, squad.waypoint):
            context.game.move_unit(unit, goal_cell)


def _move_target(context):
    goal_cell = context.ai.goal_cell(context)

    for unit in context.units:
        context.game.move_unit(unit, goal_cell)


def _flank(context, first_half):
    """표적 기지의 서로 반대쪽 인접 헥스로 반씩 나눠 들어간다."""

    cell = context.target.cell
    neighbors = world.NEIGHBORS[cell]
    side_cell = neighbors[0] if first_half else neighbors[3]
    units = sorted(context.units, key=lambda unit: unit.id)
    half = len(units) // 2
    group = units[:half] if first_half else units[half:]

    for unit in group:
        if side_cell is None or not context.game.move_unit(unit, side_cell):
            context.game.move_unit(unit, cell)


def _move_intruder(context):
    base = context.threat.base
    intruder = _nearest(context.threat.intruders, base.x, base.y)[0]

    for unit in context.units:
        if not context.game.move_unit(unit, intruder.cell):
            context.game.move_unit(unit, base.cell)


def _move_base(context):
    for unit in context.units:
        context.game.move_unit(unit, context.threat.base.cell)


def _always(context):
    return True


def _far(context):
    cx, cy = _centroid(context.units)
    x, y = context.ai.goal_point(context)
    return dist((cx, cy), (x, y)) > STAGING_FROM


def _can_pincer(context):
    return context.target.kind == "base" and len(context.squad.unit_ids) >= 3


def _has_intruders(context):
    return bool(context.threat.intruders)


def _has_free(context):
    return bool(context.ai.blackboard.free_units)


def build_domain():
    return Domain([
        Compound("FormSquad", [Method("nearest_group", _always, ["assign_group"])]),
        Compound("Reinforce", [Method("nearest_until_strong", _has_free, ["assign_until_strong"])]),
        Compound("RequestUnits", [Method("best_template", _always, ["build_best"])]),
        Compound("Regroup", [Method("rally_on_lead", _always, ["rally"])]),
        Compound("Advance", [
            Method("staged", _far, ["move_staging"]),
            Method("direct", _always, ["move_target"]),
        ]),
        Compound("Assault", [
            Method("pincer", _can_pincer, ["flank_left", "flank_right"]),
            Method("direct", _always, ["move_target"]),
        ]),
        Compound("Intercept", [
            Method("strike_intruder", _has_intruders, ["move_intruder"]),
            Method("garrison", _always, ["move_base"]),
        ]),
        Primitive("assign_group", _assign_group),
        Primitive("assign_until_strong", _assign_until_strong),
        Primitive("build_best", _build_best),
        Primitive("rally", _rally),
        Primitive("move_staging", _move_staging),
        Primitive("move_target", _move_target),
        Primitive("flank_left", lambda context: _flank(context, True)),
        Primitive("flank_right", lambda context: _flank(context, False)),
        Primitive("move_intruder", _move_intruder),
        Primitive("move_base", _move_base),
    ])


DOMAIN = build_domain()


# ------------------------------------------------------------------ AI

class AI:
    def __init__(self, side, difficulty, profile=None):
        self.side = side
        self.think = difficulty.think
        self.group_size = difficulty.group
        self.max_attack_squads = difficulty.group
        self.profile = profile if profile is not None else AIProfile()
        self.blackboard = Blackboard(side)
        self.next_think = 0
        self.game = None


        # 디버그 창: 마지막 생각의 결정과 시간. profiler는 창이 열려 있을 때만 Game이 넣어 준다.
        self.profiler = None
        self.last_goals = []
        self.last_builds = []
        self.last_think_at = None
        self.last_think_ms = 0.0
        self.last_phases = {}
        self.decisions = {}       # 목표 이름 -> 이번 생각의 선택 결과·사유
        self._phase_start = 0.0

    def note(self, key, reason, chosen=None):
        """목표 결정 사유 기록. chosen이 None이면 기존 선택 여부를 유지한다."""

        previous = self.decisions.get(key)

        if chosen is None:
            chosen = previous["chosen"] if previous is not None else False

        self.decisions[key] = {"chosen": chosen, "reason": reason}

    def _phase(self, name):
        now = perf_counter()
        seconds = now - self._phase_start
        self.last_phases[name] = round(seconds * 1000, 3)

        if self.profiler is not None:
            self.profiler.add(f"ai{self.side}/{name}", seconds)

        self._phase_start = now

    def update(self, game):
        if game.time < self.next_think:
            return
        self.next_think = round(self.next_think + self.think, 6)
        self.game = game
        board = self.blackboard
        started = self._phase_start = perf_counter()
        self.last_phases = {}
        self.decisions = {}


        # 1. 인지
        board.refresh(game)


        # 2. 반사 행동: 많이 다친 사단은 후퇴 (분대에서 자동으로 빠진다)
        for unit in board.own_units:
            if unit.hp < unit.mhp * unit.retreat_threshold and not unit.retreating:
                game.retreat_unit(unit)

        board.refresh(game)
        self._phase("perceive")


        # 3. 생산 (Utility)
        if board.queue_length < MAX_QUEUE:
            self.build_best(game)

        self._phase("production")


        # 4. 목표 선택 (Utility)
        ranked = self.rank_goals()
        self.last_goals = ranked[:10]
        self._phase("utility")


        # 5. 분대 배정
        self.assign_squads(ranked)
        self._phase("assign")


        # 6. 분대별 GOAP 계획 → HTN 실행 (점수 높은 분대가 가용 사단을 먼저 모은다)
        squads = sorted(board.squads.values(), key=lambda squad: -squad.score)

        for squad in squads:
            self.run_squad(squad)

        self._phase("squads")


        # 7. 그래도 남은 사단은 전력이 가장 모자란 공격 분대에 보탠다.
        self.distribute_spare()


        # 8. 남은 사단은 대기 (예비대)
        for unit in board.free_units:
            unit.path.clear()
            unit.destination = None

        self.last_think_at = game.time
        self.last_think_ms = round((perf_counter() - started) * 1000, 3)

    # -------------------------------------------------------------- Utility

    def build_best(self, game):
        board = self.blackboard
        options = []

        for index, template in enumerate(game.templates[self.side]):
            options.append(utility.Option(f"build:{index}", BUILD_CONSIDERATIONS,
                                          self.profile.build, template))

        ranked = utility.rank(options, self)
        self.last_builds = ranked

        if not ranked or ranked[0].score < MIN_BUILD_SCORE:
            return False

        index = game.templates[self.side].index(ranked[0].option.payload)
        game.build(self.side, index)
        board.resources = game.resources[self.side]
        board.queue_length += 1
        return True

    def rank_goals(self):
        board = self.blackboard
        options = []

        for threat in board.threats:
            options.append(utility.Option(f"defend:{threat.base.id}", DEFEND_CONSIDERATIONS,
                                          self.profile.defend, threat))

        for base in board.enemy_bases:
            options.append(utility.Option(f"attack:base:{base.id}", ATTACK_CONSIDERATIONS,
                                          self.profile.attack, Target("base", base, BASE_VALUE[base.kind])))

        for unit in board.enemies:
            # 고립된 적 사단(근처 아군이 적은)일수록 좋은 표적
            support = board.enemy_power_near(unit.x, unit.y) - unit_power(unit)
            value = 0.25 + 0.25 / (1 + support / 50)
            options.append(utility.Option(f"attack:unit:{unit.id}", ATTACK_CONSIDERATIONS,
                                          self.profile.attack, Target("unit", unit, value)))

        ranked = []

        for entry in utility.rank(options, self):
            if entry.score >= MIN_GOAL_SCORE:
                ranked.append(entry)

        return ranked

    # -------------------------------------------------------------- 배정

    def assign_squads(self, ranked):
        board = self.blackboard


        # 1. 목표가 사라진(점령·전멸·위협 해소) 분대 해산
        alive_keys = {}

        for entry in ranked:
            alive_keys[entry.option.name] = entry

        for squad in list(board.squads.values()):
            entry = alive_keys.get(squad.target_key)

            if entry is None:
                board.release(squad)
            else:
                squad.score = entry.score


        # 2. 공격 분대 전력 점검: 오래 기다려도 너무 약하면 포기
        for squad in list(board.squads.values()):
            if squad.goal == "attack":
                self.check_strength(squad)

        for key, shelf in list(board.shelved.items()):
            if board.time >= shelf.until:
                del board.shelved[key]


        # 3. 점수 높은 목표부터 분대 유지·생성
        attack_squads = 0
        saturated = True

        for squad in board.squads.values():
            if squad.goal == "attack":
                attack_squads += 1

                if squad.unit_ids and self.strength(squad) < OVERKILL:
                    saturated = False

        for entry in ranked:
            name = entry.option.name
            goal = "defend" if name.startswith("defend:") else "attack"
            existing = board.squad_for(name)

            if existing is not None:
                self.note(name, f"기존 분대 {existing.id} 유지", True)
                continue

            shelf = board.shelved.get(name)

            if shelf is not None:
                left = round(shelf.until - board.time)
                self.note(name, f"{shelf.reason} · {left}초 뒤 재검토", False)
                continue

            if goal == "defend" and entry.score >= EMERGENCY and not board.free_units:
                self.draft_for_defense(entry)

            # 기존 공격 분대가 모두 충분하고 남는 사단이 많으면 한도를 넘어 새 전선을 연다 (몰빵 방지).
            surplus = saturated and len(board.free_units) > self.group_size * 2
            reason = "새 분대 편성"

            if goal == "attack" and attack_squads >= self.max_attack_squads:
                if not surplus:
                    self.note(name, f"공격 분대 한도 {self.max_attack_squads}개 도달", False)
                    continue

                reason = f"남는 사단으로 추가 편성 (한도 {self.max_attack_squads}개 초과)"
                saturated = False

            if not board.free_units:
                self.note(name, "가용 사단 없음", False)
                continue

            board.new_squad(goal, name, entry.score)
            self.note(name, reason, True)

            if goal == "attack":
                attack_squads += 1

    def strength(self, squad):
        """필요 전력 대비 분대 전력 (필요 전력이 0이면 무한대)."""

        required = self.required_power(self.context_for(squad))
        power = _power(self.blackboard.squad_units(squad))

        if required <= 0:
            return float("inf")

        return power / required

    def check_strength(self, squad):
        board = self.blackboard
        context = self.context_for(squad)
        units = board.squad_units(squad)
        required = self.required_power(context)

        if not units or required <= 0:
            return

        ratio = _power(units) / required


        # 1. 너무 약함: 증원을 기다린 뒤에도 MIN_COMMIT 미만이면 목표를 잠시 포기
        if ratio < MIN_COMMIT and board.time - squad.formed_at >= PATIENCE:
            reason = f"전력 부족으로 포기 (필요 전력의 {ratio:.0%})"
            board.shelved[squad.target_key] = Shelved(board.time + SHELVE_SECONDS, reason)
            board.release(squad)

    def distribute_spare(self):
        """분대에 못 들어간 사단을 필요 전력 대비 가장 약한 공격 분대에 하나씩 보탠다.

        모든 분대가 OVERKILL 배수를 넘으면 group_size만큼은 예비대로 남기고 나머지는 점수 높은 분대에 몰아준다.
        """

        board = self.blackboard


        # 1. 보탤 수 있는 공격 분대와 현재 충족률
        needs = []

        for squad in board.squads.values():
            if squad.goal != "attack" or not squad.unit_ids:
                continue

            required = self.required_power(self.context_for(squad))

            if required > 0:
                needs.append(Need(squad, _power(board.squad_units(squad)), required))


        # 2. 남는 사단을 '가장 모자란 분대'부터 배정
        for unit in list(board.free_units):
            spare = len(board.free_units) > self.group_size
            best = None

            for need in needs:
                if len(need.squad.unit_ids) >= MAX_SQUAD:
                    continue
                if need.ratio >= OVERKILL and not spare:
                    continue
                if best is None or _spare_priority(need) < _spare_priority(best):
                    best = need

            if best is None:
                break

            board.assign(best.squad, unit)
            best.power += unit_power(unit)

    def draft_for_defense(self, entry):
        """급한 방어: 점수가 낮은 공격 분대의 사단을 풀어 방어에 쓸 수 있게 한다."""

        board = self.blackboard
        weakest = None

        for squad in board.squads.values():
            if squad.goal != "attack" or squad.score >= entry.score:
                continue
            if weakest is None or squad.score < weakest.score:
                weakest = squad

        if weakest is not None:
            reason = f"{entry.option.name} 긴급 방어에 병력 징발"
            board.shelved[weakest.target_key] = Shelved(board.time + SHELVE_SECONDS, reason)
            board.release(weakest)

    # -------------------------------------------------------------- GOAP + HTN

    def context_for(self, squad):
        context = TaskContext(self.game, self, squad)
        kind, _, rest = squad.target_key.partition(":")

        if kind == "defend":
            base_id = int(rest)
            for threat in self.blackboard.threats:
                if threat.base.id == base_id:
                    context.threat = threat
        else:
            target_kind, _, ident = rest.partition(":")
            ident = int(ident)
            if target_kind == "base":
                ref = self.game.bases[ident]
                context.target = Target("base", ref, BASE_VALUE[ref.kind])
            else:
                context.target = Target("unit", self.game.units[ident], 0)

        return context

    def facts(self, context):
        board = self.blackboard
        squad = context.squad
        units = board.squad_units(squad) if squad.unit_ids else []
        can_afford = False

        if board.queue_length < MAX_QUEUE:
            for template in self.game.templates[self.side]:
                if template.cost <= board.resources:
                    can_afford = True
                    break

        state = {
            "has_squad": bool(units),
            "free_units": bool(board.free_units),
            "can_afford": can_afford,
            "squad_strong": False,
            "cohesive": True,
            "near_target": False,
        }

        if units:
            x, y = self.goal_point(context)
            cx, cy = _centroid(units)
            waited = board.time - squad.formed_at >= PATIENCE
            power = _power(units)
            required = self.required_power(context)
            state["squad_strong"] = power >= required or (waited and power >= required * MIN_COMMIT)
            state["cohesive"] = _spread(units) <= COHESION
            state["near_target"] = dist((cx, cy), (x, y)) <= ENGAGE_NEAR

        return state

    def run_squad(self, squad):
        context = self.context_for(squad)
        state = self.facts(context)
        planned = perf_counter()
        steps = goap.plan(state, GOALS[squad.goal], ACTIONS)

        if self.profiler is not None:
            self.profiler.add(f"ai{self.side}/goap", perf_counter() - planned)

        squad.plan = []
        squad.trace = context.trace
        squad.facts = state

        if steps is None:
            return

        for action in steps:
            squad.plan.append(action.name)

        executed = perf_counter()

        for action in steps:
            primitives = DOMAIN.decompose(action.name, context)

            if primitives is None:
                break

            # 이동 행동은 분대원이 있어야 한다 (FormSquad가 아무도 못 모았을 수 있다).
            if action.name in ONGOING and not context.units:
                break

            for primitive in primitives:
                primitive.operator(context)

            if action.name in ONGOING:
                break

        if self.profiler is not None:
            self.profiler.add(f"ai{self.side}/htn", perf_counter() - executed)

        if not squad.unit_ids and squad.id in self.blackboard.squads:
            # 사단을 못 모은 분대는 다음 생각 때 다시 만든다.
            del self.blackboard.squads[squad.id]
            self.note(squad.target_key, "편성 실패 · 점수 높은 분대가 가용 사단을 먼저 가져감", False)

    # -------------------------------------------------------------- 디버그 창

    def debug_state(self):
        """마지막 생각의 결정을 JSON용 dict로."""

        board = self.blackboard


        # 1. 목표 순위 (고려 사항별 점수 포함)
        goals = []

        for entry in self.last_goals:
            goal = _scored_json(entry, entry.option.name)
            decision = self.decisions.get(entry.option.name, {"chosen": False, "reason": "검토 안 함"})
            goal["chosen"] = decision["chosen"]
            goal["reason"] = decision["reason"]
            goals.append(goal)


        # 2. 생산 순위
        builds = []

        for entry in self.last_builds:
            builds.append(_scored_json(entry, entry.option.payload.name))


        # 3. 분대: 목표, 병력, 전력/필요 전력, GOAP 사실·계획, HTN 분해
        squads = []

        for squad in board.squads.values():
            units = []
            power = 0.0

            for unit_id in squad.unit_ids:
                unit = self.game.units.get(unit_id)
                if unit is not None:
                    units.append({"id": unit.id, "name": unit.name, "x": round(unit.x, 1), "y": round(unit.y, 1)})
                    power += unit_power(unit)

            required = None

            if squad.goal == "attack" and squad.target_key in _live_targets(self):
                required = round(self.required_power(self.context_for(squad)), 1)

            squads.append({
                "id": squad.id,
                "goal": squad.goal,
                "target": squad.target_key,
                "score": round(squad.score, 3),
                "age": round(board.time - squad.formed_at, 1),
                "units": units,
                "power": round(power, 1),
                "required": required,
                "facts": dict(squad.facts),
                "plan": list(squad.plan),
                "trace": list(squad.trace),
                "waypoint": squad.waypoint,
                "patience_left": round(max(0.0, PATIENCE - (board.time - squad.formed_at)), 1),
            })

        shelved = []

        for key, shelf in board.shelved.items():
            shelved.append({"target": key, "reason": shelf.reason, "left": round(shelf.until - board.time, 1)})


        # 4. 최종
        state = {
            "side": self.side,
            "profile": {"attack": self.profile.attack, "defend": self.profile.defend,
                        "build": self.profile.build, "caution": self.profile.caution},
            "think_interval": self.think,
            "last_think_at": self.last_think_at,
            "next_think": self.next_think,
            "think_ms": self.last_think_ms,
            "phases": dict(self.last_phases),
            "resources": round(board.resources, 1),
            "own_power": round(board.own_power, 1),
            "enemy_power": round(board.enemy_power, 1),
            "free_units": len(board.free_units),
            "threats": len(board.threats),
            "goals": goals,
            "builds": builds,
            "squads": squads,
            "shelved": shelved,
            "min_commit": MIN_COMMIT,
            "overkill": OVERKILL,
        }

        return state

    # -------------------------------------------------------------- 목표 위치·전력

    def goal_point(self, context):
        if context.target is not None:
            return context.target.x, context.target.y
        return context.threat.base.x, context.threat.base.y

    def goal_cell(self, context):
        if context.target is not None:
            return context.target.cell
        return context.threat.base.cell

    def defense_of(self, target):
        power = self.blackboard.enemy_power_near(target.x, target.y)

        if target.kind == "base":
            power += base_power(target.ref)

        return power

    def required_power(self, context):
        if context.target is None:
            return 0.0
        return self.defense_of(context.target) * self.profile.caution


def _scored_json(entry, label):
    details = {}

    for name, value in entry.details.items():
        details[name] = round(value, 3)

    return {"name": label, "score": round(entry.score, 4), "details": details}


def _live_targets(ai):
    """분대 표적이 아직 유효한지 (전멸한 적 사단, 이미 점령한 기지가 아닌지)."""

    keys = set()

    for base in ai.blackboard.enemy_bases:
        keys.add(f"attack:base:{base.id}")

    for unit in ai.game.units.values():
        if unit.side != ai.side:
            keys.add(f"attack:unit:{unit.id}")

    return keys


def _spare_priority(need):
    """작을수록 먼저 받는다: OVERKILL 미만 분대는 충족률 낮은 순, 그 위로는 점수 높은 분대 순."""

    if need.ratio < OVERKILL:
        return (0, need.ratio)

    return (1, -need.squad.score)
