"""Difficulty-driven production, defense, and coordinated attacks."""

from dataclasses import dataclass
from math import dist


def _distance(a, b):
    return dist((a.x, a.y), (b.x, b.y))


@dataclass
class Threat:
    """적 사단이 100km 안에 들어온 아군 기지."""
    not_hq: bool
    distance: float
    base_id: int
    count: int
    base: object

    def priority(self):
        # 본부 우선, 가까운 위협 우선, 동률이면 기지 번호 순.
        return (self.not_hq, self.distance, self.base_id)


class AI:
    def __init__(self, difficulty):
        self.think = difficulty.think
        self.group_size = difficulty.group
        self.next_think = 0
        self.previous_attackers = set()

    def update(self, game):
        if game.time < self.next_think:
            return
        self.next_think = round(self.next_think + self.think, 6)

        # 1. 생산: 자원으로 살 수 있는 편제 중 하나를 무작위 생산
        affordable = []

        for i, template in enumerate(game.templates[2]):
            if template.cost <= game.resources[2]:
                affordable.append(i)

        if affordable:
            game.build(2, game.rng.choice(affordable))


        # 2. 전력 분류: 약한 사단은 후퇴, 나머지는 가용 전력
        own_bases = game.friendly_bases(2)
        enemy_bases = game.friendly_bases(1)
        enemies = []
        available = []

        for unit in game.units.values():
            if unit.side == 1:
                enemies.append(unit)

        for unit in game.units.values():
            if unit.side != 2:
                continue
            if unit.hp < unit.mhp * unit.retreat_threshold:
                if not unit.retreating:
                    game.retreat_unit(unit)
            elif not unit.retreating:
                available.append(unit)


        # 3. 방어: 위협받는 기지에 가까운 사단부터 배치
        threatened = []

        for base in own_bases:
            intruders = []

            for unit in enemies:
                distance = _distance(base, unit)
                if distance <= 100:
                    intruders.append(distance)

            if intruders:
                threatened.append(Threat(base.kind != "hq", min(intruders), base.id,
                                         len(intruders), base))

        for threat in sorted(threatened, key=Threat.priority):
            base = threat.base
            defenders = sorted(available, key=lambda unit: _distance(unit, base))
            assigned = 0

            for unit in defenders:
                if game.move_unit(unit, base.cell):
                    available.remove(unit)
                    assigned += 1
                    if assigned >= min(threat.count, self.group_size):
                        break


        # 4. 공격: 그룹마다 공동 목표를 주고, 가까운 사단끼리 함께 이동
        active_attackers = set()

        while len(available) >= self.group_size or self._has_previous_attacker(available):
            lead = available.pop(0)
            nearby = sorted(available, key=lambda unit: _distance(lead, unit))
            followers = nearby[:self.group_size - 1]

            for unit in followers:
                available.remove(unit)

            objectives = sorted([*enemy_bases, *enemies], key=lambda target: _distance(lead, target))

            for target in objectives:
                if game.move_unit(lead, target.cell):
                    active_attackers.add(lead.id)

                    for unit in followers:
                        if game.move_unit(unit, target.cell):
                            active_attackers.add(unit.id)
                    break


        # 5. 남은 사단은 대기
        for unit in available:
            unit.path.clear()
            unit.destination = None

        self.previous_attackers = active_attackers

    def _has_previous_attacker(self, units):
        for unit in units:
            if unit.id in self.previous_attackers:
                return True

        return False
