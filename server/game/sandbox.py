"""샌드박스 시작 메시지(맵 GeoJSON + Lua 스크립트)로 한 판의 설정을 만든다."""

from dataclasses import dataclass

from . import data, geomap, hexmap, scripting
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


def build(map_json, script, user):
    """map_json: GeoJSON dict 또는 None(기본 맵). script: Lua 코드 또는 None.

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

        for i, battalions in enumerate(ai_templates):
            error = data.validate_template(battalions, catalog, catalog)
            if error:
                raise ValueError(f"적군 편제 {i + 1}번: {error}")


    # 4. 레벨: 보유 대대는 계정 레벨, 나머지는 1
    levels = {}

    for key in catalog:
        levels[key] = user["unlocks"].get(key, 1)

    return SandboxSetup(Scenario(map_data, catalog, ai_templates), templates, levels)
