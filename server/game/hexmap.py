"""Odd-r pointy hex geometry and the deterministic MinRTS playfield."""

from dataclasses import dataclass, field
from functools import cache, lru_cache
from heapq import heappop, heappush
from math import ceil, cos, dist, floor, radians, sin, sqrt
from random import Random

from .data import BASES, TERRAIN, TERRAIN_BY_ID

COLS, ROWS = 60, 69
WIDTH = HEIGHT = 1200
R = 20 / sqrt(3)
DIRECTIONS = ((1, 0), (0, 1), (-1, 1), (-1, 0), (0, -1), (1, -1))
SEA = TERRAIN["sea"].id


def offset_to_axial(col, row):
    return col - (row - (row & 1)) // 2, row


def axial_to_offset(q, r):
    return q + (r - (r & 1)) // 2, r


def center(col, row):
    return 20 * (col + 0.5 * (row & 1)) + 10, R + row * 1.5 * R


def vertices(col, row):
    x, y = center(col, row)
    points = []

    for i in range(6):
        angle = radians(60 * i - 30)
        points.append((x + R * cos(angle), y + R * sin(angle)))

    return tuple(points)


def pixel_to_hex(x, y):
    px, py = x - 10, y - R
    qf = (sqrt(3) / 3 * px - py / 3) / R
    rf = (2 / 3 * py) / R
    sf = -qf - rf
    q = floor(qf + 0.5)
    r = floor(rf + 0.5)
    s = floor(sf + 0.5)
    dq, dr, ds = abs(q - qf), abs(r - rf), abs(s - sf)
    if dq > dr and dq > ds:
        q = -r - s
    elif dr > ds:
        r = -q - s
    return axial_to_offset(q, r)


def index(col, row):
    return row * COLS + col if 0 <= col < COLS and 0 <= row < ROWS else None


def lonlat_to_world(lon, lat):
    return lon * 72.5, (54.5 - lat) * 111


def _neighbors(q, r):
    cells = []

    for dq, dr in DIRECTIONS:
        col, row = axial_to_offset(q + dq, r + dr)
        cells.append(index(col, row))

    return tuple(cells)


def _build_grid():
    offsets = []
    centers = []
    axial = []
    neighbors = []

    for i in range(COLS * ROWS):
        col, row = i % COLS, i // COLS
        q, r = offset_to_axial(col, row)

        offsets.append((col, row))
        centers.append(center(col, row))
        axial.append((q, r))
        # Neighbor slots retain their direction at the edge of the map.
        neighbors.append(_neighbors(q, r))

    return tuple(offsets), tuple(centers), tuple(axial), tuple(neighbors)


OFFSETS, CENTERS, AXIAL, NEIGHBORS = _build_grid()


def hex_distance(a, b):
    aq, ar = AXIAL[a]
    bq, br = AXIAL[b]
    dq = aq - bq
    dr = ar - br
    return max(abs(dq), abs(dr), abs(dq + dr))


# Hand-simplified coastlines at game scale, with lon/lat control points.
SEA_MASKS = {
    "North Sea": [(0,54.55),(8.8,54.55),(8.7,54.05),(8.0,53.85),
                  (7.15,53.55),(6.0,53.43),(5.1,53.1),(4.55,52.8),
                  (4.3,52.2),(3.4,51.45),(2.55,51.1),(1.6,50.95),
                  (1.35,51.15),(0.95,51.35),(1.25,51.9),(1.75,52.5),
                  (1.5,53.0),(0.2,53.12),(0,53.55)],
    "Channel": [(0,49.33),(0.25,49.45),(0.14,49.75),(1.05,49.95),
                (1.58,50.32),(1.58,50.9),(1.32,51.15),(0.93,51.02),
                (0.14,50.75),(0,50.78)],
    "Baltic": [(9.85,54.55),(16.57,54.55),(16.57,54.52),(15.55,54.19),
               (14.25,53.91),(13.9,54.13),(13.39,54.51),(12.43,54.47),
               (11.65,54.02),(11.42,54.01),(11.0,53.96),(10.92,54.4),
               (10.18,54.37)],
    "Ligurian": [(4.10,43.66),(9.85,43.66),(9.95,44.04),(9.23,44.30),
                 (8.93,44.41),(8.48,44.29),(8.17,43.91),(7.4,43.72),
                 (6.64,43.38),(5.4,43.29),(4.8,43.34)],
    "Adriatic": [(12.28,45.47),(12.64,45.64),(13.56,45.75),(13.77,45.65),
                 (13.51,45.48),(13.56,45.18),(13.94,44.81),(14.49,45.1),
                 (14.98,44.69),(15.35,44.21),(15.86,43.73),(16.42,43.50),
                 (16.57,43.45),(16.57,43.65),(12.88,43.65),(12.54,44.08),
                 (12.28,44.49),(12.4,45.15)],
}

# Region center and east/west, north/south radii in degrees.
RELIEF = (
    ("Alps", "mountain", [(6.5,45.65,0.94,0.83),(7.7,46.15,1.24,0.7),
                         (9.3,46.53,1.42,0.55),(11.1,46.85,1.48,0.65),
                         (13.1,47.12,1.62,0.64),(14.5,47.5,1.3,0.49)]),
    ("Jura", "highland", [(5.75,46.25,.51,.38),(6.58,46.69,.56,.36),
                         (7.03,47.18,.61,.30)]),
    ("Massif Central", "highland", [(3.12,45.36,1.25,.88),(3.93,44.71,.91,.65)]),
    ("Vosges", "forest", [(7.02,48.03,.42,.79)]),
    ("Black Forest", "forest", [(8.24,48.15,.48,.77)]),
    ("Bohemian Forest", "highland", [(13.27,49.02,.91,.39),(14.1,48.67,.69,.36)]),
    ("Ore Mountains", "highland", [(12.79,50.52,.88,.26)]),
    ("Sudetes", "mountain", [(15.53,50.72,1.12,.37)]),
    ("Ardennes", "forest", [(5.24,49.97,1.00,.47)]),
    ("Po wetlands", "swamp", [(12.27,44.91,.39,.27)]),
    ("Bourtanger Moor", "swamp", [(7.10,52.71,.44,.29)]),
    ("Spreewald", "swamp", [(14.00,51.89,.42,.23)]),
    ("Brenne", "swamp", [(1.18,46.77,.37,.24)]),
    ("Lüneburg Heath", "desert", [(9.92,53.15,.52,.33)]),
)

RIVERS = {
    "Rhine": [(9.53,47.56),(8.62,47.58),(7.59,47.56),(7.74,48.58),
              (8.38,49.00),(8.47,49.48),(8.27,50.0),(7.59,50.35),
              (6.96,50.94),(6.75,51.46),(6.15,51.86),(5.11,51.97),(4.0,51.96)],
    "Elbe": [(15.53,50.77),(15.77,50.21),(14.99,50.17),(14.47,50.35),
             (14.21,50.78),(13.74,51.06),(13.11,51.50),(12.65,51.87),
             (11.63,52.13),(11.83,52.94),(10.61,53.38),(9.99,53.55),(8.71,53.88)],
    "Danube": [(8.50,47.95),(9.20,48.08),(9.99,48.40),(10.88,48.74),
               (11.42,48.76),(12.10,49.02),(12.57,48.88),(13.46,48.57),
               (14.28,48.31),(15.52,48.40),(16.37,48.22),(16.65,48.17)],
    "Seine": [(4.71,47.45),(4.42,47.86),(4.08,48.30),(3.50,48.52),
              (2.67,48.54),(2.35,48.86),(1.72,49.03),(1.10,49.44),(0.12,49.48)],
    "Rhône": [(8.38,46.58),(7.36,46.24),(6.86,46.43),(6.15,46.20),
              (5.81,45.77),(5.12,45.85),(4.83,45.76),(4.89,44.93),
              (4.81,43.95),(4.64,43.68),(4.85,43.33)],
    "Loire": [(4.22,44.85),(3.89,45.04),(4.08,45.96),(3.96,46.49),
              (3.16,46.96),(2.05,47.69),(1.91,47.90),(1.33,47.59),
              (0.69,47.39),(-0.12,47.34)],
    "Oder": [(17.5,49.61),(18.28,49.84),(17.03,51.11),(16.42,51.59),
             (15.53,51.95),(14.75,52.07),(14.56,52.34),(14.13,52.85),
             (14.45,53.24),(14.55,53.47),(14.30,53.83)],
    "Main": [(11.28,50.10),(10.98,50.02),(10.24,50.05),(10.16,49.72),
             (9.93,49.80),(9.58,49.76),(9.26,50.00),(8.68,50.11),(8.27,50.0)],
    "Moselle": [(6.83,47.91),(6.44,48.17),(6.17,48.67),(6.05,48.89),
                (6.18,49.12),(6.37,49.47),(6.64,49.76),(7.06,49.91),(7.59,50.35)],
    "Weser": [(9.65,51.42),(9.46,51.82),(9.14,52.06),(8.92,52.29),
              (9.21,52.64),(9.14,52.96),(8.80,53.08),(8.58,53.54)],
    "Po": [(7.08,44.70),(7.68,45.06),(8.10,45.17),(9.16,45.14),
           (9.69,45.05),(10.02,45.13),(10.74,45.04),(11.61,44.96),(12.49,44.96)],
    "Meuse": [(5.63,47.98),(5.57,48.44),(5.38,49.16),(4.94,49.70),
              (4.91,50.27),(4.87,50.47),(5.57,50.63),(5.69,50.85),
              (6.02,51.37),(5.87,51.74),(5.15,51.82)],
}

CITIES = []

for _side, _kind, _name, _lon, _lat in BASES:
    CITIES.append((_name, _lon, _lat))

CITIES += [
    ("런던",-.127,51.51),("캔터베리",1.08,51.28),("도버",1.31,51.13),
    ("릴",3.06,50.63),("루앙",1.10,49.44),("오를레앙",1.91,47.90),
    ("투르",.69,47.39),("클레르몽페랑",3.09,45.78),("그르노블",5.72,45.19),
    ("스트라스부르",7.75,48.58),("낭시",6.18,48.69),("룩셈부르크",6.13,49.61),
    ("로테르담",4.48,51.92),("안트베르펜",4.40,51.22),("리에주",5.57,50.63),
    ("쾰른",6.96,50.94),("뒤셀도르프",6.77,51.23),("도르트문트",7.47,51.51),
    ("하노버",9.73,52.38),("브레멘",8.80,53.08),("킬",10.12,54.32),
    ("로스토크",12.13,54.09),("마그데부르크",11.63,52.13),("드레스덴",13.74,51.06),
    ("슈투트가르트",9.18,48.78),("아우크스부르크",10.90,48.37),("울름",9.99,48.40),
    ("바젤",7.59,47.56),("제네바",6.15,46.20),("베른",7.45,46.95),("취리히",8.54,47.38),
    ("인스브루크",11.40,47.27),("잘츠부르크",13.04,47.80),("린츠",14.28,48.31),
    ("빈",16.37,48.21),("플젠",13.38,49.75),("브르노",16.61,49.20),
    ("슈체친",14.55,53.43),("밀라노",9.19,45.46),("토리노",7.68,45.07),
    ("제노바",8.93,44.41),("베로나",10.99,45.44),("베네치아",12.32,45.44),
    ("볼로냐",11.34,44.49),("트리에스테",13.77,45.65),("류블랴나",14.51,46.06),
    ("자그레브",15.98,45.81),
]


def trace_line(points):
    """월드 좌표(km) 폴리라인이 지나는 셀 목록. 구간을 3km 간격으로 샘플링해 끊김이 없다.

    맵 밖 지점은 None으로 들어간다.
    """

    cells = []

    for (ax, ay), (bx, by) in zip(points, points[1:]):
        steps = max(1, ceil(dist((ax, ay), (bx, by)) / 3))

        for step in range(steps + 1):
            u = step / steps
            cells.append(index(*pixel_to_hex(ax + u * (bx - ax), ay + u * (by - ay))))

    return cells


def _inside(lon, lat, polygon):
    inside = False
    for (ax, ay), (bx, by) in zip(polygon, polygon[1:] + polygon[:1]):
        if (ay > lat) != (by > lat) and lon < ax + (lat - ay) * (bx - ax) / (by - ay):
            inside = not inside
    return inside


def _in_sea(lon, lat):
    for polygon in SEA_MASKS.values():
        if _inside(lon, lat, polygon):
            return True

    return False


def _in_regions(lon, lat, regions):
    for a, b, rx, ry in regions:
        if ((lon - a) / rx) ** 2 + ((lat - b) / ry) ** 2 <= 1:
            return True

    return False


@cache
def generate_map():
    rng = Random(41069)

    # 1. 숲 분포용 노이즈 격자 (난수 순서 유지: x 바깥, y 안쪽)
    noise = {}

    for x in range(22):
        for y in range(25):
            noise[x, y] = rng.random()


    # 2. 셀별 기본 지형
    terrain = []

    for x, y in CENTERS:
        lon, lat = x / 72.5, 54.5 - y / 111

        if _in_sea(lon, lat):
            terrain.append(SEA)
            continue

        nx, ny = x / 60, y / 55
        i, j = floor(nx), floor(ny)
        u, v = nx - i, ny - j
        forest = ((noise[i, j] * (1-u) + noise[i+1, j] * u) * (1-v)
                  + (noise[i, j+1] * (1-u) + noise[i+1, j+1] * u) * v)
        kind = "forest" if forest + rng.uniform(-0.10, 0.10) > 0.60 else "plain"

        for _, relief, regions in RELIEF:
            if _in_regions(lon, lat, regions):
                kind = relief
                break

        terrain.append(TERRAIN[kind].id)


    # 3. 강
    for river in RIVERS.values():
        points = []

        for lon, lat in river:
            points.append(lonlat_to_world(lon, lat))

        for cell in trace_line(points):
            if cell is not None and terrain[cell] != SEA:
                terrain[cell] = TERRAIN["river"].id


    # 4. 도시
    cities = []

    for name, lon, lat in CITIES:
        col, row = pixel_to_hex(*lonlat_to_world(lon, lat))
        cell = index(col, row)
        if cell is not None and 0 <= lon * 72.5 < WIDTH:
            terrain[cell] = TERRAIN["city"].id
            cities.append((name, col, row))
    return tuple(terrain), tuple(cities)


def movement_factor(kind, engineer=False, mountain=False):
    if mountain and kind in ("mountain", "highland"):
        return 1.0
    speed = TERRAIN[kind].move
    return (1 + speed) / 2 if engineer and speed else speed


def move_profile(engineer=False, mountain=False):
    """지형 id 순서의 이동 배율 튜플. 0이면 통행 불가."""

    profile = []

    for ident in range(len(TERRAIN_BY_ID)):
        profile.append(movement_factor(TERRAIN_BY_ID[ident], engineer, mountain))

    return tuple(profile)


class Pathfinder:
    """한 맵의 지형에 대한 A* 경로 탐색. 결과는 (출발, 도착, 이동 배율)별로 캐시한다."""

    def __init__(self, terrain):
        self.terrain = terrain
        self.find = lru_cache(maxsize=4096)(self._find)

    def _find(self, start, goal, profile):
        """Return waypoint indices excluding start, or None if unreachable."""
        terrain = self.terrain

        for cell in (start, goal):
            if not 0 <= cell < len(terrain) or profile[terrain[cell]] <= 0:
                return None

        costs = []

        for factor in profile:
            costs.append(1 / factor if factor > 0 else None)

        frontier = [(hex_distance(start, goal), 0.0, start)]
        previous = {}
        best = {start: 0.0}
        while frontier:
            _, cost, cell = heappop(frontier)
            if cell == goal:
                path = []
                while cell != start:
                    path.append(cell)
                    cell = previous[cell]
                return tuple(reversed(path))
            if cost > best[cell]:
                continue
            for neighbor in NEIGHBORS[cell]:
                if neighbor is None or costs[terrain[neighbor]] is None:
                    continue
                # A center-to-center step spends half its distance in each tile.
                new_cost = cost + (costs[terrain[cell]] + costs[terrain[neighbor]]) / 2
                if new_cost < best.get(neighbor, float("inf")):
                    previous[neighbor] = cell
                    best[neighbor] = new_cost
                    heappush(frontier, (new_cost + hex_distance(neighbor, goal), new_cost, neighbor))
        return None


@dataclass(frozen=True)
class BaseSpot:
    side: int
    kind: str
    name: str
    cell: int


@dataclass
class MapData:
    """한 판의 전장: 지형, 도시 라벨, 기지 배치."""
    terrain: tuple
    cities: tuple        # (이름, col, row)
    bases: tuple         # BaseSpot
    name: str = "서유럽"
    pathfinder: Pathfinder = field(init=False, repr=False)

    def __post_init__(self):
        self.pathfinder = Pathfinder(self.terrain)


@cache
def default_map():
    terrain, cities = generate_map()
    bases = []

    for side, kind, name, lon, lat in BASES:
        cell = index(*pixel_to_hex(*lonlat_to_world(lon, lat)))
        bases.append(BaseSpot(side, kind, name, cell))

    return MapData(terrain, cities, tuple(bases))


def find_path(start, goal, engineer=False, mountain=False):
    """기본 맵 경로. Return waypoint indices excluding start, or None if unreachable."""
    return default_map().pathfinder.find(start, goal, move_profile(engineer, mountain))
