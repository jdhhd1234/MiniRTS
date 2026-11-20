"""Select a permanent battalion unlock or upgrade."""

from dataclasses import dataclass
from random import SystemRandom

from .game import data


@dataclass(frozen=True)
class GachaResult:
    kind: str        # "unlock" 또는 "upgrade"
    battalion: str
    level: int


def pull(unlocks, rng=None):
    rng = rng if rng is not None else SystemRandom()


    # 1. 미해금 대대와 강화 가능한 대대
    locked = []

    for key in data.BATTALIONS:
        if key not in unlocks:
            locked.append(key)

    upgrades = []

    for key, level in unlocks.items():
        if level < data.MAX_LEVEL:
            upgrades.append(key)

    if not locked and not upgrades:
        raise ValueError("모든 대대가 최고 레벨입니다.")


    # 2. 해금: 희귀도를 가중치로 고른 뒤 그 희귀도의 대대 하나
    if locked and (not upgrades or rng.random() < data.GACHA_UNLOCK_CHANCE):
        by_rarity = {}

        for rarity in data.GACHA_RARITY_WEIGHT:
            by_rarity[rarity] = []

        for key in locked:
            rarity = data.BATTALIONS[key].rarity
            if rarity in by_rarity:
                by_rarity[rarity].append(key)

        available = []
        weights = []

        for rarity, keys in by_rarity.items():
            if keys:
                available.append(rarity)
                weights.append(data.GACHA_RARITY_WEIGHT[rarity])

        rarity = rng.choices(available, weights=weights)[0]
        return GachaResult("unlock", rng.choice(by_rarity[rarity]), 1)


    # 3. 강화
    key = rng.choice(upgrades)
    return GachaResult("upgrade", key, unlocks[key] + 1)
