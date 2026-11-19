"""Select a permanent battalion unlock or upgrade."""

from random import SystemRandom

from .game import data


def pull(unlocks, rng=None):
    rng = rng if rng is not None else SystemRandom()
    locked = [key for key in data.BATTALIONS if key not in unlocks]
    upgrades = [key for key, level in unlocks.items() if level < data.MAX_LEVEL]
    if not locked and not upgrades:
        raise ValueError("모든 대대가 최고 레벨입니다.")
    if locked and (not upgrades or rng.random() < data.GACHA_UNLOCK_CHANCE):
        by_rarity = {rarity: [key for key in locked if data.BATTALIONS[key]["rarity"] == rarity]
                     for rarity in data.GACHA_RARITY_WEIGHT}
        available = [rarity for rarity, keys in by_rarity.items() if keys]
        rarity = rng.choices(available, weights=[data.GACHA_RARITY_WEIGHT[r] for r in available])[0]
        return {"kind": "unlock", "battalion": rng.choice(by_rarity[rarity]), "level": 1}
    key = rng.choice(upgrades)
    return {"kind": "upgrade", "battalion": key, "level": unlocks[key] + 1}
