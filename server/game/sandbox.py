"""샌드박스 시작 메시지(맵 GeoJSON + Lua 스크립트)로 한 판의 설정을 만든다."""

from dataclasses import dataclass

from . import data, geomap, hexmap, scripting
from .ai import AIProfile
from .sim import Scenario


@dataclass
class SandboxSetup:
    scenario: Scenario
    templates: list     # 청군 편제 [{"name", "battalions"}]
    levels: dict        # 대대 id -> 레벨 (샌드박스는 모든 대대 사용 가능)


def _check_templates(templates, catalog, where):
    for i, template in enumerate(templates):
        error = data.validate_template(template["battalions"], catalog, catalog)

        if error:
            raise ValueError(f"{where} {i + 1}번 ({template['name']}): {error}")


def _start_indices(start, templates, where):
    """Lua 시작 사단(수 또는 편제 이름 목록)을 편제 번호 목록으로."""

    if not templates:
        raise ValueError(f"{where}: 편제가 없어 시작 사단을 만들 수 없습니다.")

    indices = []

    if isinstance(start, int):
        for i in range(start):
            indices.append(i % len(templates))
        return indices

    names = []

    for template in templates:
        names.append(template["name"])

    for name in start:
        if name not in names:
            raise ValueError(f"{where}: 편제 이름 {name!r}이 없습니다 ({', '.join(names)}).")
        indices.append(names.index(name))

    return indices


def build(map_json, script, user, bots=False):
    """map_json: GeoJSON dict 또는 None(기본 맵). script: Lua 코드 또는 None. bots: 봇전투.

    오류는 ValueError (geomap.MapError, scripting.ScriptError 포함).
    """

    # 1. 맵
    map_data = hexmap.default_map() if map_json is None else geomap.load(map_json)


    # 2. 대대 목록: 기본 20종 + Lua 대대
    catalog = dict(data.BATTALIONS)
    result = None

    if isinstance(script, str) and script.strip():
        result = scripting.load(script)
        catalog.update(result.battalions)
    elif script is not None and not isinstance(script, str):
        raise ValueError("Lua 스크립트는 문자열이어야 합니다.")


    # 3. 편제: 스크립트에 templates가 있으면 그것, 없으면 저장된 편제
    templates = user["templates"]

    if result is not None and result.templates:
        templates = result.templates

    _check_templates(templates, catalog, "청군 편제")

    ai_templates = None

    if result is not None and result.enemy_templates:
        ai_templates = result.enemy_templates
        _check_templates(ai_templates, catalog, "적군 편제")


    # 4. 시작 사단 (편제 번호)과 AI 성향
    start = {}
    profiles = {}

    if result is not None:
        enemy = ai_templates

        if enemy is None:
            enemy = []
            for battalions in data.SANDBOX_STAGE.ai_templates:
                enemy.append({"name": "사단", "battalions": battalions})

        for side, value in result.start.items():
            side_templates = templates if side == 1 else enemy
            where = "player_start" if side == 1 else "enemy_start"
            start[side] = _start_indices(value, side_templates, where)

        for side, weights in result.ai_profiles.items():
            profiles[side] = AIProfile(**weights)


    # 5. 레벨: 보유 대대는 계정 레벨, 나머지는 1
    levels = {}

    for key in catalog:
        levels[key] = user["unlocks"].get(key, 1)

    scenario = Scenario(map_data, catalog, ai_templates, start, profiles, bool(bots))
    return SandboxSetup(scenario, templates, levels)
