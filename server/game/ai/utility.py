"""Utility AI: 여러 고려 사항(Consideration) 점수를 곱해 선택지(Option)의 효용을 매긴다.

고려 사항 점수는 0~1. 곱셈이라 하나라도 0이면 그 선택지는 버려진다.
고려 사항이 많을수록 곱이 작아지는 문제는 보정 계수(Dave Mark 방식)로 줄인다.
"""

from dataclasses import dataclass, field


@dataclass
class Consideration:
    name: str
    score: object          # callable(option, context) -> 0~1


@dataclass
class Option:
    name: str
    considerations: list
    weight: float = 1.0
    payload: object = None   # 선택되면 실행 쪽에서 쓰는 값 (기지, 표적, 편제 번호 등)


@dataclass
class ScoredOption:
    option: Option
    score: float
    details: dict = field(default_factory=dict)   # 고려 사항별 점수 (디버깅용)


def _clamp(value):
    return min(1.0, max(0.0, value))


def evaluate(option, context):
    count = len(option.considerations)

    if count == 0:
        return ScoredOption(option, option.weight)


    # 1. 고려 사항별 점수
    details = {}
    total = 1.0

    for consideration in option.considerations:
        value = _clamp(consideration.score(option, context))
        details[consideration.name] = value
        total *= value

        if total == 0:
            break


    # 2. 개수 보정: 고려 사항이 많아도 점수가 지나치게 작아지지 않게 한다.
    modification = 1 - 1 / count
    compensated = 1.0

    for value in details.values():
        make_up = (1 - value) * modification
        compensated *= value + make_up * value

    if total == 0:
        compensated = 0.0

    return ScoredOption(option, compensated * option.weight, details)


def rank(options, context):
    """효용이 높은 순으로 정렬. 점수가 같으면 원래 순서를 지킨다."""

    scored = []

    for option in options:
        scored.append(evaluate(option, context))

    scored.sort(key=lambda entry: -entry.score)
    return scored
