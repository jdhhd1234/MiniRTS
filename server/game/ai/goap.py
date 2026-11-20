"""GOAP (Goal-Oriented Action Planning): 사실(fact) 상태에서 목표 상태까지 행동 순서를 A*로 찾는다.

상태는 {사실 이름: bool} dict. 행동은 전제 조건(preconditions)이 모두 맞을 때 쓸 수 있고,
쓰면 효과(effects)가 상태에 덮어쓰인다. 비용 합이 가장 작은 행동 순서를 돌려준다.
"""

from dataclasses import dataclass
from heapq import heappop, heappush


@dataclass(frozen=True)
class Action:
    name: str
    preconditions: dict
    effects: dict
    cost: float = 1.0

    def usable(self, state):
        for fact, value in self.preconditions.items():
            if state.get(fact, False) != value:
                return False

        return True

    def apply(self, state):
        result = dict(state)
        result.update(self.effects)
        return result


def _key(state):
    return tuple(sorted(state.items()))


def _unmet(state, goal):
    count = 0

    for fact, value in goal.items():
        if state.get(fact, False) != value:
            count += 1

    return count


def plan(start, goal, actions, max_steps=8):
    """start 상태에서 goal을 만족하는 행동 목록. 이미 만족하면 [], 길이 없으면 None."""

    # 큐 항목: (예상 총비용, 순번, 지금까지 비용, 상태, 행동 목록). 순번은 동률일 때 삽입 순서를 지킨다.
    frontier = [(_unmet(start, goal), 0, 0.0, start, [])]
    best = {_key(start): 0.0}
    serial = 1

    while frontier:
        _, _, cost, state, steps = heappop(frontier)

        if _unmet(state, goal) == 0:
            return steps

        if len(steps) >= max_steps:
            continue

        for action in actions:
            if not action.usable(state):
                continue

            next_state = action.apply(state)
            next_cost = cost + action.cost
            key = _key(next_state)

            if next_cost >= best.get(key, float("inf")):
                continue

            best[key] = next_cost
            estimate = next_cost + _unmet(next_state, goal)
            heappush(frontier, (estimate, serial, next_cost, next_state, steps + [action]))
            serial += 1

    return None
