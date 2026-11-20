"""샌드박스 Lua 스크립트: 고유 대대 정의와 특수능력 훅.

lupa(LuaRuntime)로 실행하되 공개 서버에서도 안전하도록 격리한다.
- 스크립트는 화이트리스트 환경(env)에서만 실행된다. os, io, require, load, debug,
  pcall, coroutine, setmetatable, python 등은 보이지 않는다.
- 명령 수 한도(debug.sethook)로 무한 루프를 끊고, max_memory로 메모리를 제한한다.
- 실행 시간이 명령 수와 무관하게 커질 수 있는 문자열 패턴 함수(find/match/gsub/gmatch/rep)는
  문자열 메서드로도 접근되므로 원본 string 테이블에서 지운다.
- 텍스트 코드만 받는다(바이트코드 금지).

스크립트 형식은 Planner/sandbox/README.md 참고.
"""

from dataclasses import dataclass
from math import isfinite
import re

from lupa import LuaError, LuaRuntime, lua_type

from . import data

MAX_SCRIPT_CHARS = 64_000
MAX_MEMORY = 32 * 1024 * 1024
LOAD_BUDGET = 5_000_000      # 스크립트 최상위 실행 명령 수 한도
CALL_BUDGET = 100_000        # 훅 1회 호출 명령 수 한도
MAX_CUSTOM_BATTALIONS = 20

BATTALION_ID = re.compile(r"^[a-z][a-z0-9_]{0,23}$")
CLASS_ID = re.compile(r"^[a-z][a-z0-9_]{0,15}$")
SYMBOL_CODE = re.compile(r"^[A-Z\-]{6}$")

# (필드, 최소, 최대)
NUMBER_FIELDS = (
    ("atk", 0, 500),
    ("dfn", 0, 500),
    ("spd", 0.1, 20),
    ("rng", 1, 200),
    ("hp", 1, 5000),
    ("cost", 1, 1000),
)
HOOK_NAMES = ("on_attack", "on_damaged", "on_tick")

SETUP = r"""
local sethook, pcall, error, load, pack = debug.sethook, pcall, error, load, table.pack
local budget = 0

local function guard()
  budget = budget - 1000
  if budget < 0 then
    error("명령 수 한도를 넘었습니다 (무한 루프?)", 0)
  end
end

for _, name in ipairs({"rep", "find", "match", "gmatch", "gsub", "dump", "pack", "unpack", "packsize"}) do
  string[name] = nil
end

local env = {
  math = {
    abs = math.abs, ceil = math.ceil, floor = math.floor, max = math.max, min = math.min,
    sqrt = math.sqrt, exp = math.exp, log = math.log, sin = math.sin, cos = math.cos,
    tan = math.tan, fmod = math.fmod, modf = math.modf, tointeger = math.tointeger,
    type = math.type, huge = math.huge, pi = math.pi,
    maxinteger = math.maxinteger, mininteger = math.mininteger,
  },
  string = {
    format = string.format, sub = string.sub, len = string.len, upper = string.upper,
    lower = string.lower, byte = string.byte, char = string.char, reverse = string.reverse,
  },
  table = {
    insert = table.insert, remove = table.remove, concat = table.concat,
    sort = table.sort, unpack = table.unpack,
  },
  pairs = pairs, ipairs = ipairs, next = next, select = select, type = type,
  tostring = tostring, tonumber = tonumber, error = error, assert = assert,
  rawequal = rawequal, rawlen = rawlen, rawget = rawget,
}

local function compile(code)
  return load(code, "=script", "t", env)
end

local function run(limit, fn, ...)
  budget = limit
  sethook(guard, "", 1000)
  local result = pack(pcall(fn, ...))
  sethook()
  if not result[1] then
    error(tostring(result[2]), 0)
  end
  return result[2]
end

for _, name in ipairs({"os", "io", "package", "require", "dofile", "loadfile", "load",
                       "debug", "coroutine", "python", "collectgarbage"}) do
  _G[name] = nil
end

return compile, run
"""


class ScriptError(ValueError):
    pass


def _deny_attributes(obj, name, is_setting):
    raise AttributeError("Lua에서 Python 객체에 접근할 수 없습니다.")


class Sandbox:
    """격리된 Lua 런타임 하나. 한 판(Game)에 하나씩 만든다."""

    def __init__(self):
        self.lua = LuaRuntime(register_eval=False, register_builtins=False,
                              unpack_returned_tuples=False, max_memory=MAX_MEMORY,
                              attribute_filter=_deny_attributes)
        self._compile, self._run = self.lua.execute(SETUP)

    def execute(self, code):
        compiled = self._compile(code)

        if isinstance(compiled, tuple):
            # load 실패: (nil, 오류 메시지)
            raise ScriptError(f"Lua 문법 오류: {compiled[1]}")

        try:
            return self._run(LOAD_BUDGET, compiled)
        except LuaError as exc:
            raise ScriptError(f"Lua 실행 오류: {exc}") from exc

    def call(self, function, context):
        table = self.lua.table_from(context, recursive=True)

        try:
            return self._run(CALL_BUDGET, function, table)
        except LuaError as exc:
            raise ScriptError(str(exc)) from exc


class Hooks:
    """Lua 대대 하나의 특수능력 훅. 실행 오류가 나면 그 대대의 훅은 꺼진다."""

    def __init__(self, sandbox, key, functions):
        self.sandbox = sandbox
        self.key = key
        self.functions = functions      # 훅 이름 -> Lua 함수
        self.disabled = False

    def has(self, name):
        return not self.disabled and name in self.functions

    def number(self, name, context, low, high):
        """훅을 호출해 숫자를 받는다. nil이면 None. 범위를 벗어나면 잘라낸다."""

        try:
            value = self.sandbox.call(self.functions[name], context)
        except ScriptError as exc:
            self.disabled = True
            raise ScriptError(f"{self.key}.{name}: {exc}") from exc

        if value is None:
            return None

        if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
            self.disabled = True
            raise ScriptError(f"{self.key}.{name}: 숫자 또는 nil을 반환해야 합니다.")

        return min(high, max(low, float(value)))


@dataclass
class ScriptResult:
    battalions: dict              # id -> data.Battalion
    templates: list | None        # [{"name", "battalions"}], 없으면 None
    enemy_templates: list | None  # [[대대 id, ...]], 없으면 None
    sandbox: Sandbox


def _is_table(value):
    return value is not None and lua_type(value) == "table"


def _string_list(value, where):
    if not _is_table(value):
        raise ScriptError(f"{where}: 문자열 배열이어야 합니다.")

    items = []

    for i in range(1, len(value) + 1):
        item = value[i]
        if not isinstance(item, str):
            raise ScriptError(f"{where}[{i}]: 문자열이어야 합니다.")
        items.append(item)

    return items


def _battalion(key, table, sandbox):
    where = f"battalions.{key}"

    if not BATTALION_ID.match(key):
        raise ScriptError(f"{where}: id는 영문 소문자로 시작하는 영문 소문자·숫자·_ 24자 이내여야 합니다.")
    if key in data.BATTALIONS:
        raise ScriptError(f"{where}: 기본 대대 id와 겹칩니다.")
    if not _is_table(table):
        raise ScriptError(f"{where}: 테이블이어야 합니다.")


    # 1. 이름·병과·심볼
    name = table["name"]

    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 30:
        raise ScriptError(f"{where}.name: 1~30자 문자열이어야 합니다.")

    cls = table["cls"]

    if not isinstance(cls, str) or not CLASS_ID.match(cls):
        raise ScriptError(f"{where}.cls: 병과는 영문 소문자 16자 이내여야 합니다 (예: inf, armor, air, magic).")

    fn = table["fn"] or "UCI---"

    if not isinstance(fn, str) or not SYMBOL_CODE.match(fn):
        raise ScriptError(f"{where}.fn: NATO 기능 코드 6자리여야 합니다 (예: UCI---).")


    # 2. 수치
    numbers = {}

    for field_name, low, high in NUMBER_FIELDS:
        value = table[field_name]

        if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
            raise ScriptError(f"{where}.{field_name}: {low}~{high} 사이 숫자여야 합니다.")

        numbers[field_name] = value


    # 3. 지원 효과 (기본 효과 키 중 하나)
    effect = table["effect"]

    if effect is not None and effect not in data.EFFECT_DESC:
        raise ScriptError(f"{where}.effect: 알 수 없는 효과입니다 ({', '.join(data.EFFECT_DESC)}).")


    # 4. 지형별 이동 배율 덮어쓰기 (예: move = { sea = 1.0 })
    move = None
    move_table = table["move"]

    if move_table is not None:
        if not _is_table(move_table):
            raise ScriptError(f"{where}.move: 테이블이어야 합니다.")

        move = {}

        for terrain, factor in move_table.items():
            if terrain not in data.TERRAIN:
                raise ScriptError(f"{where}.move.{terrain}: 알 수 없는 지형입니다.")
            if isinstance(factor, bool) or not isinstance(factor, (int, float)) or not 0 <= factor <= 5:
                raise ScriptError(f"{where}.move.{terrain}: 0~5 사이 숫자여야 합니다.")
            move[terrain] = float(factor)


    # 5. 특수능력 훅
    functions = {}

    for hook_name in HOOK_NAMES:
        function = table[hook_name]

        if function is None:
            continue
        if lua_type(function) != "function":
            raise ScriptError(f"{where}.{hook_name}: 함수여야 합니다.")

        functions[hook_name] = function

    hooks = Hooks(sandbox, key, functions) if functions else None

    return data.Battalion(name=name.strip(), cls=cls, rarity="custom", fn=fn, effect=effect,
                          move=move, hooks=hooks, **numbers)


def _templates(value, where):
    if not _is_table(value):
        raise ScriptError(f"{where}: 배열이어야 합니다.")

    templates = []

    for i in range(1, len(value) + 1):
        entry = value[i]

        if not _is_table(entry):
            raise ScriptError(f"{where}[{i}]: {{name=..., battalions={{...}}}} 테이블이어야 합니다.")

        name = entry["name"]

        if not isinstance(name, str) or not name.strip():
            raise ScriptError(f"{where}[{i}].name: 이름이 필요합니다.")

        battalions = _string_list(entry["battalions"], f"{where}[{i}].battalions")
        templates.append({"name": name.strip()[:30], "battalions": battalions})

    if len(templates) > data.MAX_TEMPLATES:
        raise ScriptError(f"{where}: 최대 {data.MAX_TEMPLATES}개까지입니다.")

    return templates


def load(code):
    """스크립트를 실행해 고유 대대·편제를 읽는다. 오류는 ScriptError."""

    if not isinstance(code, str):
        raise ScriptError("Lua 스크립트는 문자열이어야 합니다.")
    if len(code) > MAX_SCRIPT_CHARS:
        raise ScriptError(f"Lua 스크립트는 {MAX_SCRIPT_CHARS:,}자 이내여야 합니다.")

    sandbox = Sandbox()
    result = sandbox.execute(code)

    if not _is_table(result):
        raise ScriptError("스크립트는 { battalions = {...}, templates = {...} } 테이블을 return 해야 합니다.")


    # 1. 고유 대대
    battalions = {}
    table = result["battalions"]

    if table is not None:
        if not _is_table(table):
            raise ScriptError("battalions: 테이블이어야 합니다.")

        for key, entry in table.items():
            if not isinstance(key, str):
                raise ScriptError("battalions: 키는 대대 id 문자열이어야 합니다.")
            battalions[key] = _battalion(key, entry, sandbox)

            if len(battalions) > MAX_CUSTOM_BATTALIONS:
                raise ScriptError(f"battalions: 최대 {MAX_CUSTOM_BATTALIONS}종까지입니다.")


    # 2. 청군 편제와 적군 편제
    templates = None

    if result["templates"] is not None:
        templates = _templates(result["templates"], "templates")

    enemy_templates = None

    if result["enemy_templates"] is not None:
        enemy_templates = []

        for template in _templates(result["enemy_templates"], "enemy_templates"):
            enemy_templates.append(template["battalions"])

    return ScriptResult(battalions, templates, enemy_templates, sandbox)
