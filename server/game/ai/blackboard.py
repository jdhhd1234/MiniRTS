"""Blackboard: AI가 판단에 쓰는 공유 기억.

- 인지(perception): 생각할 때마다 게임 상태에서 다시 계산하는 사실들
- 기억(memory): 생각과 생각 사이에 유지되는 분대(Squad) 편성
"""

from dataclasses import dataclass, field
from math import dist

from .. import data

THREAT_RADIUS = 100     # 기지 주변 이 거리(km) 안의 적은 위협
NEAR_RADIUS = 60        # 표적 방어력을 셀 때 쓰는 반경(km)
BASE_VALUE = {"hq": 1.0, "corps": 0.8, "div": 0.6}


def distance(a, b):
    return dist((a.x, a.y), (b.x, b.y))


def unit_power(unit):
    """사단 전투력 추정: 남은 체력 × 공격력 합."""

    attack = 0.0

    for part in unit.attack:
        attack += part.atk

    return unit.hp * attack / 100


def base_power(base):
    kind = data.BASE_KINDS[base.kind]
    return base.hp * kind.atk * kind.dfn / 2500


@dataclass
class Threat:
    base: object
    intruders: list
    power: float
    nearest: float


@dataclass
class Squad:
    id: int
    goal: str              # "attack" 또는 "defend"
    target_key: str        # Utility 선택지 이름 (예: "attack:base:9")
    unit_ids: list
    score: float
    formed_at: float
    waypoint: int | None = None                  # 고정된 집결지 셀 (HTN Advance:staged)
    plan: list = field(default_factory=list)    # 마지막 GOAP 계획 (행동 이름)
    trace: list = field(default_factory=list)   # 마지막 HTN 분해 (작업:방법)
    facts: dict = field(default_factory=dict)   # 마지막 GOAP 시작 사실 (디버그 창)


@dataclass
class Shelved:
    """잠시 포기한 공격 목표: until(게임 시각)까지 새 분대를 만들지 않는다."""
    until: float
    reason: str


@dataclass
class Blackboard:
    side: int


    # 기억
    squads: dict = field(default_factory=dict)      # id -> Squad
    next_squad_id: int = 1
    shelved: dict = field(default_factory=dict)     # 목표 이름 -> Shelved


    # 인지 (refresh에서 다시 채운다)
    time: float = 0.0
    resources: float = 0.0
    queue_length: int = 0
    own_units: list = field(default_factory=list)
    enemies: list = field(default_factory=list)
    free_units: list = field(default_factory=list)
    own_bases: list = field(default_factory=list)
    enemy_bases: list = field(default_factory=list)
    threats: list = field(default_factory=list)
    enemy_classes: dict = field(default_factory=dict)   # 병과 -> 대대 수
    own_power: float = 0.0
    enemy_power: float = 0.0
    units_by_id: dict = field(default_factory=dict)

    @property
    def enemy_side(self):
        return 3 - self.side

    def refresh(self, game):
        side = self.side

        # 1. 시간·경제
        self.time = game.time
        self.resources = game.resources[side]
        self.queue_length = len(game.queues[side])


        # 2. 사단 분류
        self.own_units = []
        self.enemies = []
        self.units_by_id = game.units

        for unit in game.units.values():
            if unit.side == side:
                self.own_units.append(unit)
            else:
                self.enemies.append(unit)

        self.own_power = 0.0
        self.enemy_power = 0.0
        self.enemy_classes = {}

        for unit in self.own_units:
            self.own_power += unit_power(unit)

        for unit in self.enemies:
            self.enemy_power += unit_power(unit)

            for cls in unit.classes:
                self.enemy_classes[cls] = self.enemy_classes.get(cls, 0) + 1


        # 3. 기지와 위협
        self.own_bases = game.friendly_bases(side)
        self.enemy_bases = game.friendly_bases(self.enemy_side)
        self.threats = []

        for base in self.own_bases:
            intruders = []
            power = 0.0
            nearest = float("inf")

            for unit in self.enemies:
                gap = distance(base, unit)

                if gap <= THREAT_RADIUS:
                    intruders.append(unit)
                    power += unit_power(unit)
                    nearest = min(nearest, gap)

            if intruders:
                self.threats.append(Threat(base, intruders, power, nearest))


        # 4. 분대 정리: 죽거나 후퇴한 사단은 분대에서 뺀다.
        assigned = set()

        for squad in list(self.squads.values()):
            alive = []

            for unit_id in squad.unit_ids:
                unit = game.units.get(unit_id)

                if unit is not None and not unit.retreating:
                    alive.append(unit_id)
                    assigned.add(unit_id)

            squad.unit_ids = alive

            if not alive:
                del self.squads[squad.id]


        # 5. 분대에 속하지 않은 가용 사단
        self.free_units = []

        for unit in self.own_units:
            if not unit.retreating and unit.id not in assigned:
                self.free_units.append(unit)

    def squad_units(self, squad):
        units = []

        for unit_id in squad.unit_ids:
            units.append(self.units_by_id[unit_id])

        return units

    def enemy_power_near(self, x, y, radius=NEAR_RADIUS):
        power = 0.0

        for unit in self.enemies:
            if dist((x, y), (unit.x, unit.y)) <= radius:
                power += unit_power(unit)

        return power

    def new_squad(self, goal, target_key, score):
        squad = Squad(self.next_squad_id, goal, target_key, [], score, self.time)
        self.squads[squad.id] = squad
        self.next_squad_id += 1
        return squad

    def squad_for(self, target_key):
        for squad in self.squads.values():
            if squad.target_key == target_key:
                return squad

        return None

    def assign(self, squad, unit):
        squad.unit_ids.append(unit.id)
        self.free_units.remove(unit)

    def release(self, squad):
        for unit in self.squad_units(squad):
            self.free_units.append(unit)

        del self.squads[squad.id]
