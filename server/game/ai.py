"""Difficulty-driven production, defense, and coordinated attacks."""

from math import dist


def _distance(a, b):
    return dist((a.x, a.y), (b.x, b.y))


class AI:
    def __init__(self, difficulty):
        self.think = difficulty["think"]
        self.group_size = difficulty["group"]
        self.next_think = 0
        self.previous_attackers = set()

    def update(self, game):
        if game.time < self.next_think:
            return
        self.next_think = round(self.next_think + self.think, 6)

        affordable = [i for i, template in enumerate(game.templates[2])
                      if template["cost"] <= game.resources[2]]
        if affordable:
            game.build(2, game.rng.choice(affordable))

        own_bases = game.friendly_bases(2)
        enemy_bases = game.friendly_bases(1)
        enemies = [unit for unit in game.units.values() if unit.side == 1]
        available = []
        for unit in game.units.values():
            if unit.side != 2:
                continue
            if unit.hp < unit.mhp * unit.retreat_threshold:
                if not unit.retreating:
                    game.retreat_unit(unit)
            elif not unit.retreating:
                available.append(unit)

        threatened = []
        for base in own_bases:
            distances = [_distance(base, unit) for unit in enemies]
            intruders = [distance for distance in distances if distance <= 100]
            if intruders:
                threatened.append((base.kind != "hq", min(intruders), base.id, len(intruders), base))
        for _, _, _, count, base in sorted(threatened):
            defenders = sorted(available, key=lambda unit: _distance(unit, base))
            assigned = 0
            for unit in defenders:
                if game.move_unit(unit, base.cell):
                    available.remove(unit)
                    assigned += 1
                    if assigned >= min(count, self.group_size):
                        break

        # Give each group a shared objective; nearby divisions travel together.
        active_attackers = set()
        while len(available) >= self.group_size or (
            available and any(unit.id in self.previous_attackers for unit in available)
        ):
            lead = available.pop(0)
            nearby = sorted(available, key=lambda unit: _distance(lead, unit))
            group = [lead, *nearby[:self.group_size - 1]]
            for unit in group[1:]:
                available.remove(unit)
            objectives = sorted([*enemy_bases, *enemies], key=lambda target: _distance(lead, target))
            for target in objectives:
                if game.move_unit(lead, target.cell):
                    active_attackers.add(lead.id)
                    for unit in group[1:]:
                        if game.move_unit(unit, target.cell):
                            active_attackers.add(unit.id)
                    break

        for unit in available:
            unit.path.clear()
            unit.destination = None
        self.previous_attackers = active_attackers
