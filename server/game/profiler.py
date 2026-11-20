"""샌드박스 디버그 창용 프로파일러: 구간별 실행 시간을 1초 단위 창(window)으로 모은다.

Game.profiler가 None이면(디버그 창을 열지 않으면) 측정 코드는 아무것도 하지 않는다.
"""

from dataclasses import dataclass
from time import perf_counter


@dataclass
class Section:
    total: float = 0.0     # 초
    worst: float = 0.0     # 한 번 호출의 최대 시간(초)
    calls: int = 0

    def add(self, seconds):
        self.total += seconds
        self.worst = max(self.worst, seconds)
        self.calls += 1


class Profiler:
    def __init__(self):
        self.sections = {}
        self.ticks = 0
        self.tick_total = 0.0
        self.tick_worst = 0.0
        self._tick_start = 0.0
        self._last = 0.0

    def start_tick(self):
        self._tick_start = self._last = perf_counter()

    def lap(self, name):
        """지난 lap 이후 걸린 시간을 name 구간에 더한다."""
        now = perf_counter()
        self.add(name, now - self._last)
        self._last = now

    def end_tick(self):
        seconds = perf_counter() - self._tick_start
        self.ticks += 1
        self.tick_total += seconds
        self.tick_worst = max(self.tick_worst, seconds)

    def add(self, name, seconds):
        section = self.sections.get(name)

        if section is None:
            section = Section()
            self.sections[name] = section

        section.add(seconds)

    def report(self, budget_ms):
        """지금까지 모은 창을 dict로 만들고 비운다."""

        # 1. 구간별 통계 (총 시간이 큰 순서)
        sections = []
        tick_total = self.tick_total or 1e-12

        for name, section in sorted(self.sections.items(), key=lambda item: -item[1].total):
            sections.append({
                "name": name,
                "total_ms": round(section.total * 1000, 3),
                "avg_ms": round(section.total * 1000 / section.calls, 4),
                "max_ms": round(section.worst * 1000, 3),
                "calls": section.calls,
                "share": round(section.total / tick_total, 4),
            })


        # 2. 틱 전체
        ticks = self.ticks
        report = {
            "ticks": ticks,
            "tick_avg_ms": round(self.tick_total * 1000 / ticks, 4) if ticks else 0,
            "tick_max_ms": round(self.tick_worst * 1000, 3),
            "budget_ms": round(budget_ms, 2),
            "sections": sections,
        }


        # 3. 창 비우기
        self.sections = {}
        self.ticks = 0
        self.tick_total = 0.0
        self.tick_worst = 0.0
        return report
