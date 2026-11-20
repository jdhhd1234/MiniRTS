"""샌드박스 맵: QGIS에서 내보낸 GeoJSON(EPSG:4326, WGS84)을 헥스 전장으로 변환.

규칙 (자세한 예시는 Planner/sandbox/README.md):
- 모든 피처의 경계 상자가 전장이 된다. 가로·세로 최대 1200km. 경계 상자 밖은 바다.
- 경계 상자 안의 기본 지형은 평지.
- Polygon/MultiPolygon + terrain 속성: 셀 중심이 안에 들어가는 헥스를 그 지형으로 칠한다.
  파일 순서대로 칠하므로 뒤 피처가 앞 피처를 덮는다.
- LineString/MultiLineString: terrain 속성(기본 river)으로 선이 지나는 헥스를 칠한다. 바다는 건너뛴다.
- Point + base 속성(hq/corps/div) + side(1=청군, 2=적군) + name: 기지. 진영마다 본부 1개 필수.
- Point + name 속성(base 없음): 도시.
"""

from math import cos, floor, radians

from . import data
from .hexmap import (CENTERS, COLS, HEIGHT, R, ROWS, SEA, WIDTH, BaseSpot, MapData, index,
                     pixel_to_hex, trace_line)

MAX_EXTENT_KM = 1200
MAX_FEATURES = 5000
MAX_POINTS = 200_000
MAX_BASES = 40
KM_PER_DEG_LAT = 110.574
KM_PER_DEG_LON = 111.320

POLYGONS = ("Polygon", "MultiPolygon")
LINES = ("LineString", "MultiLineString")
POINTS = ("Point", "MultiPoint")


class MapError(ValueError):
    pass


def _terrain_names():
    names = {}

    for key, terrain in data.TERRAIN.items():
        names[key] = key
        names[terrain.name] = key

    return names


TERRAIN_NAMES = _terrain_names()


def _property(properties, name):
    """QGIS 필드 이름 대소문자 차이를 허용한다."""

    for key, value in properties.items():
        if isinstance(key, str) and key.lower() == name:
            return value

    return None


def _position(value, where):
    if (not isinstance(value, list) or len(value) < 2
            or not isinstance(value[0], (int, float)) or not isinstance(value[1], (int, float))
            or isinstance(value[0], bool) or isinstance(value[1], bool)):
        raise MapError(f"{where}: 좌표는 [경도, 위도] 숫자 배열이어야 합니다.")

    lon, lat = float(value[0]), float(value[1])

    if not -180 <= lon <= 180 or not -90 <= lat <= 90:
        raise MapError(f"{where}: 경위도 범위를 벗어났습니다. QGIS에서 EPSG:4326(WGS84)으로 내보내 주세요.")

    return lon, lat


def _positions(value, where):
    if not isinstance(value, list):
        raise MapError(f"{where}: 좌표 배열이어야 합니다.")

    points = []

    for i, position in enumerate(value):
        points.append(_position(position, f"{where}[{i}]"))

    return points


def _parts(geometry, where):
    """도형을 (종류, 부분 목록)으로 정리. 폴리곤 부분 = 링 목록, 선 부분 = 점 목록, 점 부분 = 점."""

    kind = geometry.get("type")
    coordinates = geometry.get("coordinates")

    if kind == "Point":
        return "point", [_position(coordinates, where)]

    if kind == "MultiPoint":
        return "point", _positions(coordinates, where)

    if kind == "LineString":
        return "line", [_positions(coordinates, where)]

    if kind == "MultiLineString":
        if not isinstance(coordinates, list):
            raise MapError(f"{where}: 좌표 배열이어야 합니다.")
        lines = []
        for i, line in enumerate(coordinates):
            lines.append(_positions(line, f"{where}[{i}]"))
        return "line", lines

    if kind == "Polygon":
        polygons = [coordinates]
    elif kind == "MultiPolygon":
        polygons = coordinates
    else:
        raise MapError(f"{where}: 지원하지 않는 도형 {kind!r} (Point/LineString/Polygon과 Multi* 형만 지원).")

    if not isinstance(polygons, list):
        raise MapError(f"{where}: 좌표 배열이어야 합니다.")

    parts = []

    for i, polygon in enumerate(polygons):
        if not isinstance(polygon, list) or not polygon:
            raise MapError(f"{where}[{i}]: 링 배열이어야 합니다.")

        rings = []
        for j, ring in enumerate(polygon):
            rings.append(_positions(ring, f"{where}[{i}][{j}]"))
        parts.append(rings)

    return "polygon", parts


def _inside(x, y, ring):
    inside = False

    for (ax, ay), (bx, by) in zip(ring, ring[1:] + ring[:1]):
        if (ay > y) != (by > y) and x < ax + (y - ay) * (bx - ax) / (by - ay):
            inside = not inside

    return inside


def _cells_in_box(left, top, right, bottom):
    """월드 좌표 상자와 겹칠 수 있는 셀 번호 (행·열 범위로 좁힌다)."""

    first_row = max(0, floor((top - R) / (1.5 * R)) - 1)
    last_row = min(ROWS - 1, floor((bottom - R) / (1.5 * R)) + 1)
    first_col = max(0, floor((left - 20) / 20))
    last_col = min(COLS - 1, floor(right / 20) + 1)
    cells = []

    for row in range(first_row, last_row + 1):
        for col in range(first_col, last_col + 1):
            cells.append(row * COLS + col)

    return cells


class _Projection:
    """경계 상자 기준 등장방형 투영(km), 전장 중앙에 배치."""

    def __init__(self, west, south, east, north):
        self.west = west
        self.north = north
        self.x_scale = KM_PER_DEG_LON * cos(radians((south + north) / 2))
        width = (east - west) * self.x_scale
        height = (north - south) * KM_PER_DEG_LAT

        if width > MAX_EXTENT_KM or height > MAX_EXTENT_KM:
            raise MapError(f"맵 범위가 {width:.0f}km × {height:.0f}km입니다. "
                           f"가로·세로 최대 {MAX_EXTENT_KM}km까지 지원합니다.")

        self.ox = (WIDTH - width) / 2
        self.oy = (HEIGHT - height) / 2
        self.box = (self.ox, self.oy, self.ox + width, self.oy + height)

    def __call__(self, lon, lat):
        return (self.ox + (lon - self.west) * self.x_scale,
                self.oy + (self.north - lat) * KM_PER_DEG_LAT)


def load(collection):
    """GeoJSON FeatureCollection(dict) -> MapData. 오류는 MapError."""

    if not isinstance(collection, dict) or collection.get("type") != "FeatureCollection":
        raise MapError("GeoJSON FeatureCollection이어야 합니다.")

    crs = collection.get("crs")

    if isinstance(crs, dict):
        crs_name = str((crs.get("properties") or {}).get("name", ""))
        if "4326" not in crs_name and "CRS84" not in crs_name:
            raise MapError(f"좌표계 {crs_name}는 지원하지 않습니다. QGIS에서 EPSG:4326(WGS84)으로 내보내 주세요.")

    features = collection.get("features")

    if not isinstance(features, list) or not features:
        raise MapError("features가 비어 있습니다.")
    if len(features) > MAX_FEATURES:
        raise MapError(f"피처는 최대 {MAX_FEATURES}개까지입니다.")


    # 1. 피처 정리와 경계 상자
    shapes = []
    point_count = 0
    west, south, east, north = 180.0, 90.0, -180.0, -90.0

    for i, feature in enumerate(features):
        where = f"features[{i}]"

        if not isinstance(feature, dict) or not isinstance(feature.get("geometry"), dict):
            raise MapError(f"{where}: geometry가 있는 Feature여야 합니다.")

        properties = feature.get("properties") or {}

        if not isinstance(properties, dict):
            raise MapError(f"{where}.properties: 객체여야 합니다.")

        kind, parts = _parts(feature["geometry"], f"{where}.geometry")
        shapes.append((where, kind, parts, properties))

        for lon, lat in _flatten(kind, parts):
            point_count += 1
            west, east = min(west, lon), max(east, lon)
            south, north = min(south, lat), max(north, lat)

        if point_count > MAX_POINTS:
            raise MapError(f"좌표는 최대 {MAX_POINTS:,}개까지입니다. QGIS에서 도형을 단순화해 주세요.")

    project = _Projection(west, south, east, north)


    # 2. 기본 지형: 경계 상자 안은 평지, 밖은 바다
    left, top, right, bottom = project.box
    plain = data.TERRAIN["plain"].id
    terrain = []

    for x, y in CENTERS:
        terrain.append(plain if left <= x <= right and top <= y <= bottom else SEA)


    # 3. 면 → 선 → 점 순서로 적용
    for where, kind, parts, properties in shapes:
        if kind == "polygon":
            _paint_polygons(terrain, parts, _terrain_of(properties, where, None), project, CENTERS)

    for where, kind, parts, properties in shapes:
        if kind == "line":
            _paint_lines(terrain, parts, _terrain_of(properties, where, "river"), project)

    cities = []
    bases = []
    used_cells = set()

    for where, kind, parts, properties in shapes:
        if kind != "point":
            continue

        for lon, lat in parts:
            _place_point(where, properties, project(lon, lat), terrain, cities, bases, used_cells)


    # 4. 기지 검사
    if len(bases) > MAX_BASES:
        raise MapError(f"기지는 최대 {MAX_BASES}개까지입니다.")

    for side in (1, 2):
        hq_count = 0
        for base in bases:
            if base.side == side and base.kind == "hq":
                hq_count += 1
        if hq_count != 1:
            raise MapError(f"진영 {side}의 본부(base=hq)가 정확히 1개 있어야 합니다 (현재 {hq_count}개).")

    name = collection.get("name")
    name = name.strip()[:40] if isinstance(name, str) and name.strip() else "사용자 맵"
    return MapData(tuple(terrain), tuple(cities), tuple(bases), name)


def _flatten(kind, parts):
    if kind == "point":
        return parts

    points = []

    for part in parts:
        if kind == "line":
            points.extend(part)
        else:
            for ring in part:
                points.extend(ring)

    return points


def _terrain_of(properties, where, default):
    value = _property(properties, "terrain")

    if value is None:
        if default is None:
            raise MapError(f"{where}: 면 피처에는 terrain 속성이 필요합니다 ({', '.join(data.TERRAIN)}).")
        value = default

    if value not in TERRAIN_NAMES:
        raise MapError(f"{where}: 알 수 없는 terrain {value!r} ({', '.join(data.TERRAIN)}).")

    return data.TERRAIN[TERRAIN_NAMES[value]].id


def _paint_polygons(terrain, parts, terrain_id, project, centers):
    for rings in parts:
        projected = []

        for ring in rings:
            points = []
            for lon, lat in ring:
                points.append(project(lon, lat))
            projected.append(points)

        outer, holes = projected[0], projected[1:]

        if len(outer) < 3:
            continue

        xs, ys = [], []
        for x, y in outer:
            xs.append(x)
            ys.append(y)

        for cell in _cells_in_box(min(xs), min(ys), max(xs), max(ys)):
            x, y = centers[cell]

            if not _inside(x, y, outer):
                continue

            in_hole = False
            for hole in holes:
                if _inside(x, y, hole):
                    in_hole = True
                    break

            if not in_hole:
                terrain[cell] = terrain_id


def _paint_lines(terrain, parts, terrain_id, project):
    for line in parts:
        points = []

        for lon, lat in line:
            points.append(project(lon, lat))

        for cell in trace_line(points):
            if cell is not None and (terrain[cell] != SEA or terrain_id == SEA):
                terrain[cell] = terrain_id


def _place_point(where, properties, xy, terrain, cities, bases, used_cells):
    col, row = pixel_to_hex(*xy)
    cell = index(col, row)
    name = _property(properties, "name")
    name = str(name).strip()[:30] if name is not None and str(name).strip() else None
    base_kind = _property(properties, "base")

    if cell is None:
        raise MapError(f"{where}: 점이 전장 밖에 있습니다.")

    if base_kind is None:
        if name is not None:
            terrain[cell] = data.TERRAIN["city"].id
            cities.append((name, col, row))
        return

    if base_kind not in data.BASE_KINDS:
        raise MapError(f"{where}: base는 {', '.join(data.BASE_KINDS)} 중 하나여야 합니다.")

    side = _property(properties, "side")

    if isinstance(side, str) and side.strip().isdigit():
        side = int(side)
    if side not in (1, 2):
        raise MapError(f"{where}: side는 1(청군) 또는 2(적군)여야 합니다.")
    if terrain[cell] == SEA:
        raise MapError(f"{where}: 기지가 바다 위에 있습니다.")
    if cell in used_cells:
        raise MapError(f"{where}: 같은 헥스에 기지가 두 개 있습니다.")

    used_cells.add(cell)
    name = name or f"{data.BASE_KINDS[base_kind].name} {len(bases) + 1}"
    terrain[cell] = data.TERRAIN["city"].id
    cities.append((name, col, row))
    bases.append(BaseSpot(side, base_kind, name, cell))
