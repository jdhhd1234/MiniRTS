"""MinRTS 정적 게임 데이터: 대대, 지형, 날씨, 기지, 스테이지.

단위: 거리 km, 속도 km/초(실시간), 시간 초.
"""

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Battalion:
    name: str
    cls: str
    atk: float
    dfn: float
    spd: float
    rng: float
    hp: float
    cost: int
    rarity: str
    fn: str
    effect: str | None = None
    move: dict | None = None     # 샌드박스 Lua 대대: 지형별 이동 배율 덮어쓰기 (예: {"sea": 1.0})
    hooks: object = None         # 샌드박스 Lua 대대: scripting.Hooks


@dataclass(frozen=True)
class Terrain:
    id: int
    name: str
    move: float
    dfn: float


@dataclass(frozen=True)
class Weather:
    id: int
    name: str
    move: float
    rng: float
    atk: float
    var: float


@dataclass(frozen=True)
class BaseKind:
    name: str
    hp: int
    dfn: float
    atk: float
    income: float
    penalty: float


@dataclass(frozen=True)
class Stage:
    name: str
    player_start: int
    ai_start: int
    ai_income: float
    ai_templates: list


@dataclass(frozen=True)
class Difficulty:
    name: str
    income: float
    think: float
    group: int


def to_json(record):
    """dataclass를 API용 dict로 변환. 값이 None인 필드(선택 효과)는 뺀다."""

    result = {}

    for key, value in asdict(record).items():
        if value is not None:
            result[key] = value

    return result


# ---------------------------------------------------------------- 대대 (20종)
# cls: inf / mech / armor / arty / air / aa / at / support
# rarity: base(기본 해금) / common / rare / epic
# fn: NATO APP-6 (2525C) 기능 코드 6자리 — milsymbol에 그대로 전달
# effect: 지원부대 특수효과 키
BATTALIONS = {
    "inf":   Battalion(name="보병대대",       cls="inf",     atk=10, dfn=12, spd=1.2, rng=20, hp=100, cost=20, rarity="base",   fn="UCI---"),
    "mech":  Battalion(name="기계화보병대대", cls="mech",    atk=14, dfn=14, spd=2.0, rng=20, hp=110, cost=35, rarity="base",   fn="UCII--"),
    "tank":  Battalion(name="전차대대",       cls="armor",   atk=22, dfn=16, spd=2.2, rng=20, hp=120, cost=50, rarity="base",   fn="UCA---"),
    "arty":  Battalion(name="포병대대",       cls="arty",    atk=16, dfn=5,  spd=1.2, rng=40, hp=70,  cost=35, rarity="base",   fn="UCF---"),
    "eng":   Battalion(name="공병대대",       cls="support", atk=6,  dfn=10, spd=1.4, rng=20, hp=80,  cost=25, rarity="base",   fn="UCE---", effect="engineer"),
    "recon": Battalion(name="정찰대대",       cls="support", atk=8,  dfn=6,  spd=3.0, rng=30, hp=60,  cost=25, rarity="base",   fn="UCR---", effect="recon"),
    "spg":   Battalion(name="자주포대대",     cls="arty",    atk=20, dfn=7,  spd=2.0, rng=45, hp=80,  cost=50, rarity="common", fn="UCF---"),
    "mlrs":  Battalion(name="다연장로켓대대", cls="arty",    atk=28, dfn=4,  spd=1.6, rng=60, hp=60,  cost=65, rarity="rare",   fn="UCFR--"),
    "aa":    Battalion(name="방공대대",       cls="aa",      atk=8,  dfn=8,  spd=1.8, rng=30, hp=70,  cost=35, rarity="common", fn="UCD---"),
    "at":    Battalion(name="대전차대대",     cls="at",      atk=14, dfn=10, spd=1.6, rng=25, hp=80,  cost=35, rarity="common", fn="UCAA--"),
    "heli":  Battalion(name="공격헬기대대",   cls="air",     atk=26, dfn=6,  spd=4.0, rng=30, hp=60,  cost=70, rarity="epic",   fn="UCVRA-"),
    "sig":   Battalion(name="통신대대",       cls="support", atk=2,  dfn=6,  spd=1.6, rng=20, hp=50,  cost=25, rarity="common", fn="UUS---", effect="signals"),
    "med":   Battalion(name="의무대대",       cls="support", atk=0,  dfn=5,  spd=1.6, rng=20, hp=50,  cost=25, rarity="common", fn="USM---", effect="medical"),
    "trans": Battalion(name="수송대대",       cls="support", atk=0,  dfn=5,  spd=2.4, rng=20, hp=50,  cost=20, rarity="common", fn="UST---", effect="transport"),
    "maint": Battalion(name="정비대대",       cls="support", atk=0,  dfn=6,  spd=1.8, rng=20, hp=50,  cost=25, rarity="common", fn="USX---", effect="maintenance"),
    "cbrn":  Battalion(name="화생방대대",     cls="support", atk=2,  dfn=6,  spd=1.6, rng=20, hp=50,  cost=25, rarity="rare",   fn="UUA---", effect="cbrn"),
    "abn":   Battalion(name="공수대대",       cls="inf",     atk=15, dfn=12, spd=1.6, rng=20, hp=90,  cost=45, rarity="rare",   fn="UCI---"),
    "mtn":   Battalion(name="산악대대",       cls="inf",     atk=12, dfn=14, spd=1.4, rng=20, hp=100, cost=35, rarity="common", fn="UCI---", effect="mountain"),
    "mp":    Battalion(name="헌병대대",       cls="support", atk=4,  dfn=8,  spd=1.6, rng=20, hp=60,  cost=20, rarity="common", fn="UUL---", effect="police"),
    "ew":    Battalion(name="전자전대대",     cls="support", atk=2,  dfn=6,  spd=1.6, rng=30, hp=50,  cost=40, rarity="rare",   fn="UUM---", effect="ew"),
}

EFFECT_DESC = {
    "engineer": "지형 이동 패널티 절반",
    "recon": "사단 공격력 +10%",
    "signals": "사단 공격력·방어력 +10%",
    "medical": "교전 중이 아닐 때 체력 회복 +1%/초",
    "transport": "보급 거리 1.5배",
    "maintenance": "교전 중이 아닐 때 체력 회복 +0.5%/초",
    "cbrn": "날씨 패널티 절반",
    "mountain": "산·고원에서 공격력 +30%, 이동 패널티 없음",
    "police": "자동 후퇴 기준 30% → 20%",
    "ew": "교전 중인 적 공격력 -10%",
}

BASE_UNLOCKED = []

for key, battalion in BATTALIONS.items():
    if battalion.rarity == "base":
        BASE_UNLOCKED.append(key)

# 상성: (공격측 cls, 방어측 cls) -> 배율
COUNTERS = {
    ("at", "armor"): 1.6, ("at", "mech"): 1.3,
    ("armor", "inf"): 1.3, ("armor", "arty"): 1.3, ("armor", "support"): 1.2,
    ("air", "armor"): 1.4, ("air", "mech"): 1.3,
    ("aa", "air"): 2.0,
    ("arty", "inf"): 1.2,
    ("inf", "at"): 1.2,
    ("mech", "inf"): 1.1,
}

MAX_BATTALIONS = 4
MAX_DUPLICATE = 2
MAX_TEMPLATES = 6
MAX_LEVEL = 10
LEVEL_BONUS = 0.05  # 레벨당 공격·방어·체력 +5%

# ---------------------------------------------------------------- 지형
# move: 이동속도 배율, dfn: 방어 배율. sea = 통행 불가
TERRAIN = {
    "sea":      Terrain(id=0, name="바다", move=0.0, dfn=1.0),
    "plain":    Terrain(id=1, name="평지", move=1.0, dfn=1.0),
    "forest":   Terrain(id=2, name="숲",   move=0.7, dfn=1.25),
    "mountain": Terrain(id=3, name="산",   move=0.4, dfn=1.6),
    "river":    Terrain(id=4, name="강",   move=0.5, dfn=1.2),
    "city":     Terrain(id=5, name="도시", move=0.8, dfn=1.5),
    "swamp":    Terrain(id=6, name="늪",   move=0.4, dfn=1.2),
    "desert":   Terrain(id=7, name="사막", move=0.8, dfn=0.9),
    "highland": Terrain(id=8, name="고원", move=0.7, dfn=1.3),
}
TERRAIN_BY_ID = {}

for key, terrain in TERRAIN.items():
    TERRAIN_BY_ID[terrain.id] = key

# 전차·기계화는 숲/산/늪/도시에서 공격력 감소
ARMOR_TERRAIN_PENALTY = {"forest": 0.7, "mountain": 0.6, "swamp": 0.6, "city": 0.7}

# ---------------------------------------------------------------- 날씨
# move: 이동 배율, rng: 사거리 배율, var: 전투 랜덤 폭(±)
WEATHER = {
    "clear": Weather(id=0, name="맑음", move=1.0, rng=1.0, atk=1.0, var=0.05),
    "rain":  Weather(id=1, name="비",   move=0.7, rng=1.0, atk=0.95, var=0.10),
    "snow":  Weather(id=2, name="눈",   move=0.6, rng=1.0, atk=0.9, var=0.10),
    "fog":   Weather(id=3, name="안개", move=0.9, rng=0.5, atk=0.95, var=0.15),
}
WEATHER_BY_ID = {}

for key, weather in WEATHER.items():
    WEATHER_BY_ID[weather.id] = key
WEATHER_CELLS = 4            # 맵을 4x4 지역으로 나눔
WEATHER_CHANGE = (60, 120)   # 지역별 날씨 변경 주기(초)

# ---------------------------------------------------------------- 기지
BASE_KINDS = {
    "hq":    BaseKind(name="본부",         hp=600, dfn=40, atk=15, income=1.0,  penalty=0.0),
    "corps": BaseKind(name="군단작전기지", hp=400, dfn=30, atk=12, income=0.5,  penalty=0.50),
    "div":   BaseKind(name="사단작전기지", hp=250, dfn=25, atk=10, income=0.25, penalty=0.15),
}

# (side, kind, 이름, 경도, 위도). side 1=플레이어(청군), 2=AI(적군)
BASES = [
    (1, "hq",    "파리",       2.35, 48.86),
    (1, "corps", "브뤼셀",     4.35, 50.85),
    (1, "corps", "리옹",       4.83, 45.76),
    (1, "div",   "암스테르담", 4.90, 52.37),
    (1, "div",   "랭스",       4.03, 49.26),
    (1, "div",   "메스",       6.18, 49.12),
    (1, "div",   "디종",       5.04, 47.32),
    (2, "hq",    "베를린",    13.40, 52.52),
    (2, "corps", "함부르크",   9.99, 53.55),
    (2, "corps", "뮌헨",      11.58, 48.14),
    (2, "div",   "프랑크푸르트", 8.68, 50.11),
    (2, "div",   "라이프치히", 12.37, 51.34),
    (2, "div",   "뉘른베르크", 11.08, 49.45),
    (2, "div",   "프라하",    14.42, 50.08),
]

# ---------------------------------------------------------------- 경제·전투 상수
MATCH_SECONDS = 20 * 60
SPEEDS = (1, 2, 5, 10, 20)   # 시간 배속. 캠페인은 출격 전에 고정, 샌드박스는 전투 중 변경 가능
START_RESOURCES = 300
BUILD_TIME = 15.0          # 사단 1개 생산 기본 시간(초)
INCOME_BASE = 1.0
INCOME_PER_HEX = 0.0015
ENGAGE_KM = 20.0           # 교전 반경
DAMAGE_K = 0.15
RETREAT_HP = 0.30
SUPPLY_FULL_KM = 250.0
SUPPLY_ZERO_KM = 650.0     # 이 거리에서 전투력 50%
SUPPLY_MIN = 0.5

# 가챠 재화 보상
REWARD_CORPS = 50
REWARD_HQ = 200

# ---------------------------------------------------------------- 스테이지 / 난이도
STAGES = {
    1: Stage(name="스테이지 1 — 국경 충돌", player_start=3, ai_start=2, ai_income=0.8,
            ai_templates=[["inf", "inf", "arty"], ["inf", "mech"]]),
    2: Stage(name="스테이지 2 — 라인강 공세", player_start=3, ai_start=4, ai_income=1.0,
            ai_templates=[["mech", "tank", "arty"], ["inf", "inf", "at", "arty"], ["inf", "mech", "eng"]]),
    3: Stage(name="스테이지 3 — 유럽 대전", player_start=3, ai_start=6, ai_income=1.2,
            ai_templates=[["tank", "tank", "mech", "spg"], ["mech", "at", "arty", "sig"],
                          ["inf", "inf", "mlrs", "aa"], ["tank", "mech", "heli", "recon"]]),
}

DIFFICULTY = {
    "easy":   Difficulty(name="하", income=0.7, think=8.0, group=1),
    "normal": Difficulty(name="중", income=1.0, think=5.0, group=2),
    "hard":   Difficulty(name="상", income=1.3, think=3.0, group=3),
}

# 샌드박스: 스테이지 진행·보상 없음. Lua 스크립트가 적 편제를 주지 않으면 이 편제를 쓴다.
SANDBOX_STAGE = Stage(name="샌드박스", player_start=3, ai_start=3, ai_income=1.0,
                      ai_templates=STAGES[2].ai_templates)

DEFAULT_TEMPLATES = [
    {"name": "보병사단", "battalions": ["inf", "inf", "arty", "eng"]},
    {"name": "기갑사단", "battalions": ["tank", "tank", "mech", "arty"]},
    {"name": "기계화사단", "battalions": ["mech", "mech", "tank", "recon"]},
]

# 가챠
GACHA_COST = 100
GACHA_UNLOCK_CHANCE = 0.4
GACHA_RARITY_WEIGHT = {"common": 60, "rare": 30, "epic": 10}


def validate_template(battalions, unlocked, catalog=None):
    """편제 규칙 검사. 문제 없으면 None, 있으면 오류 메시지.

    catalog: 사용할 대대 목록. 기본은 BATTALIONS, 샌드박스는 Lua 대대가 추가된 목록.
    """

    if catalog is None:
        catalog = BATTALIONS

    if not isinstance(battalions, list) or not 1 <= len(battalions) <= MAX_BATTALIONS:
        return f"대대는 1~{MAX_BATTALIONS}개여야 합니다."

    for key in battalions:
        if key not in catalog:
            return f"알 수 없는 대대: {key}"

        name = catalog[key].name

        if key not in unlocked:
            return f"해금되지 않은 대대: {name}"

        if battalions.count(key) > MAX_DUPLICATE:
            return f"같은 대대는 최대 {MAX_DUPLICATE}개까지: {name}"

    return None
