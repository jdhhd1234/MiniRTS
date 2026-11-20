# 샌드박스 모드

로비의 **샌드박스** 메뉴에서 직접 만든 맵(GeoJSON)과 Lua 고유 대대로 자유 전투를 한다.

- 포인트 보상과 스테이지 진행은 없다.
- 모든 대대를 쓸 수 있다. 보유한 대대는 계정 레벨을 따르고, 나머지는 레벨 1이다.
- 시간 배속(1·2·5·10·20×)을 전투 중에 바꿀 수 있다. 캠페인은 출격 전에 고른 배속으로 고정된다.
- 예제: [`example.geojson`](example.geojson) (두 섬의 해협), [`example.lua`](example.lua) (드래곤·마법사 대대)

## 1. 맵: QGIS GeoJSON

QGIS에서 레이어를 **GeoJSON, 좌표계 EPSG:4326(WGS84)** 으로 내보낸다.
레이어가 여러 개라면 하나로 병합(벡터 → 데이터 관리 도구 → 벡터 레이어 병합)한 뒤 내보낸다.

| 도형 | 속성 | 의미 |
|---|---|---|
| Polygon / MultiPolygon | `terrain` (필수) | 셀 중심이 안에 들어간 헥스를 그 지형으로 칠한다. 구멍(내부 링)은 제외한다. |
| LineString / MultiLineString | `terrain` (생략 시 `river`) | 선이 지나는 헥스를 칠한다. 바다 헥스는 건너뛴다(`terrain=sea`인 선은 예외). |
| Point | `base` = `hq`/`corps`/`div`, `side` = `1`(청군)/`2`(적군), `name` | 기지. 진영마다 `hq`가 정확히 1개 있어야 한다. 최대 40개. |
| Point | `name` (`base` 없음) | 도시(지형이 도시가 되고 이름이 표시됨) |
| Point | 속성 없음 | 범위 지정용. 맵 경계 상자에만 들어간다. |

- `terrain` 값: `sea` `plain` `forest` `mountain` `river` `city` `swamp` `desert` `highland`. 한글 이름(`바다` `평지` `숲` `산` `강` `도시` `늪` `사막` `고원`)도 받는다. 필드 이름의 대소문자는 구분하지 않는다.
- **범위**: 모든 피처의 경계 상자가 전장이 되고, 가로·세로 최대 **1200km**까지 지원한다. 경계 상자는 60×69 헥스(1헥스 = 20km) 전장의 가운데에 놓인다. 상자 안의 기본 지형은 평지, 상자 밖은 바다다.
- **적용 순서**: 면 → 선 → 점. 면끼리는 파일 순서대로 칠하므로 뒤 피처가 앞 피처를 덮는다. 바다(해협, 호수)는 `terrain=sea` 면으로 그린다.
- **제한**: 피처 5,000개, 좌표 200,000개. 넘으면 QGIS의 단순화(벡터 → 도형 도구 → 단순화)를 쓴다.
- **오류**: 범위 초과, 본부 누락, 바다 위 기지, EPSG:4326이 아닌 좌표계는 출격할 때 오류 메시지로 알려 준다.

## 2. Lua 고유 대대 (lupa)

스크립트는 테이블 하나를 `return` 한다. 모든 키는 생략할 수 있다.

```lua
return {
  battalions = {                 -- 고유 대대 (최대 20종)
    dragon = {                   -- id: 영문 소문자로 시작, a-z 0-9 _ 24자 이내, 기본 대대 id와 겹치면 안 됨
      name = "드래곤대대",        -- 1~30자
      cls = "air",               -- 병과. 상성표: inf mech armor arty air aa at support. 그 밖의 값(magic 등)은 상성 1.0
      atk = 40,                  -- 0~500
      dfn = 20,                  -- 0~500
      spd = 3.5,                 -- 0.1~20 (km/초)
      rng = 30,                  -- 1~200 (km)
      hp = 220,                  -- 1~5000
      cost = 120,                -- 1~1000
      fn = "UCVRA-",             -- 선택: NATO APP-6 기능 코드 6자리 (지도 심볼). 기본 UCI---
      effect = "recon",          -- 선택: 기본 지원 효과 키 하나 (engineer recon signals medical transport
                                 --       maintenance cbrn mountain police ew)
      move = { sea = 1.0 },      -- 선택: 지형별 이동 배율 덮어쓰기 (0~5, 0이면 통행 불가)
      on_attack = function(ctx) ... end,   -- 선택: 특수능력 훅 (아래)
      on_damaged = function(ctx) ... end,
      on_tick = function(ctx) ... end,
    },
  },
  templates = {                  -- 선택: 청군 편제 (최대 6개). 없으면 저장된 편제를 쓴다.
    { name = "비룡사단", battalions = { "dragon", "dragon" } },
  },
  enemy_templates = {            -- 선택: 적군 편제. 없으면 기본 적 편제
    { name = "적 기갑", battalions = { "tank", "tank", "mech", "aa" } },
  },
}
```

편제 규칙은 캠페인과 같다. 사단당 대대 1~4개, 같은 대대는 최대 2개다.

**이동**: 사단은 가장 느린 대대를 따른다. 바다를 건너려면 사단의 **모든** 대대가 `move.sea > 0` 이어야 한다.
예를 들어 드래곤 2개로 된 사단은 바다를 건너지만, 드래곤 1개와 기계화 1개로 된 사단은 건너지 못한다.

### 훅

훅은 사단에 그 대대가 있으면 한 번씩 호출된다. 같은 대대가 여러 개여도 한 번만 호출된다.
모든 훅의 `ctx`에는 `hp` `mhp` `supply`(0.5~1) `engaged` `side` `terrain`(사단이 있는 지형 키) `weather`(`clear`/`rain`/`snow`/`fog`) `time`(초)이 있다.

| 훅 | 추가 ctx | 반환 | 호출 시점 |
|---|---|---|---|
| `on_attack` | `damage`, `distance`, `target`(`"unit"`/`"base"`), `target_classes`(배열) | 새 피해량 (0 이상) | 사단이 공격할 때 (0.1초마다) |
| `on_damaged` | `damage` | 새 받는 피해량 (0 이상) | 피해를 받을 때 |
| `on_tick` | — | 초당 회복량(최대 체력 비율, 0~0.2) | 0.1초마다 |

- `nil`을 반환하면 값을 바꾸지 않는다.
- 숫자가 아닌 값을 반환하거나 실행 오류가 나면 그 대대의 훅이 꺼지고 작전 기록에 오류가 표시된다. 전투는 계속된다.
- 훅에는 난수가 없다. 같은 상황이면 같은 결과가 나온다.

### 격리 (공개 서버용)

스크립트는 서버에서 실행되므로 다음 제한이 있다.

- 쓸 수 있는 것: `math`(random 제외), `string`(`format` `sub` `len` `upper` `lower` `byte` `char` `reverse`), `table`(`insert` `remove` `concat` `sort` `unpack`), `pairs` `ipairs` `next` `select` `type` `tostring` `tonumber` `error` `assert` `rawequal` `rawlen` `rawget`
- 없는 것: `os` `io` `require` `load` `dofile` `debug` `coroutine` `pcall` `setmetatable` `collectgarbage` 문자열 패턴 함수(`find` `match` `gsub` `gmatch`)와 `rep` 바이트코드
- 한도: 스크립트 64,000자, 메모리 32MB, 최상위 실행 500만 명령, 훅 1회 호출 10만 명령. 넘으면 출격 오류가 나거나 훅이 꺼진다.
