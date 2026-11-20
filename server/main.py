"""MinRTS REST API, authoritative WebSocket matches, and frontend hosting."""

import asyncio
from contextlib import asynccontextmanager, suppress
import json
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .db import AccountError, Database, Session
from .game import data, sandbox
from .game.sim import Game, STATE_HZ, TICK_HZ

WEB_DIR = Path(__file__).resolve().parents[1] / "web"
CODEX_PATH = Path(__file__).with_name("game") / "codex.json"
db = Database()


@asynccontextmanager
async def lifespan(app):
    with CODEX_PATH.open(encoding="utf-8") as codex_file:
        app.state.codex = json.load(codex_file)
    await run_in_threadpool(db.initialize)
    yield


app = FastAPI(title="MinRTS", lifespan=lifespan)

# uvicorn server.main:app --port 8000 --reload

@app.exception_handler(AccountError)
async def account_error(request, exc):
    return JSONResponse({"detail": str(exc)}, status_code=exc.status)


@app.exception_handler(404)
async def not_found(request, exc):
    return JSONResponse({"detail": "요청한 경로를 찾을 수 없습니다."}, status_code=404)


@app.exception_handler(405)
async def method_not_allowed(request, exc):
    return JSONResponse({"detail": "지원하지 않는 요청 방식입니다."}, status_code=405,
                        headers=exc.headers)


async def object_body(request: Request):
    try:
        body = await request.json()
    except (ValueError, UnicodeError) as exc:
        raise HTTPException(400, "JSON 요청 형식이 올바르지 않습니다.") from exc
    if not isinstance(body, dict):
        raise HTTPException(400, "JSON 객체를 보내 주세요.")
    return body


def current_account(authorization: Annotated[str | None, Header()] = None):
    scheme, _, token = (authorization or "").partition(" ")
    return db.authenticate(token if scheme.lower() == "bearer" else None)


Body = Annotated[dict, Depends(object_body)]
Account = Annotated[Session, Depends(current_account)]


@app.post("/api/register")
def register(body: Body):
    return db.register(body.get("username"), body.get("password"))


@app.post("/api/login")
def login(body: Body):
    return db.login(body.get("username"), body.get("password"))


@app.get("/api/me")
def me(account: Account):
    return {"user": account.user}


@app.put("/api/templates")
def templates(body: Body, account: Account):
    return {"user": db.save_templates(account.user_id, body.get("templates"))}


@app.post("/api/gacha/pull")
def pull(account: Account):
    return db.pull(account.user_id)


@app.get("/api/data")
def game_data():

    # 1. 대대 목록 (id 포함)
    battalions = []

    for key, battalion in data.BATTALIONS.items():
        record = data.to_json(battalion)
        record["id"] = key
        battalions.append(record)


    # 2. 이름별 테이블을 dict로 변환
    tables = {"terrain": data.TERRAIN, "weather": data.WEATHER,
              "stages": data.STAGES, "difficulty": data.DIFFICULTY}
    converted = {}

    for table_name, table in tables.items():
        converted[table_name] = {}

        for key, record in table.items():
            converted[table_name][key] = data.to_json(record)


    # 3. 최종 응답
    return {
        "battalions": battalions,
        "effects": data.EFFECT_DESC,
        "terrain": converted["terrain"],
        "weather": converted["weather"],
        "stages": converted["stages"],
        "difficulty": converted["difficulty"],
        "rules": {
            "max_battalions": data.MAX_BATTALIONS, "max_duplicate": data.MAX_DUPLICATE,
            "max_templates": data.MAX_TEMPLATES, "gacha_cost": data.GACHA_COST,
            "gacha_unlock_chance": data.GACHA_UNLOCK_CHANCE,
            "gacha_rarity_weight": data.GACHA_RARITY_WEIGHT, "max_level": data.MAX_LEVEL,
            "level_bonus": data.LEVEL_BONUS, "match_seconds": data.MATCH_SECONDS,
            "speeds": list(data.SPEEDS),
        },
    }


@app.get("/api/codex")
def codex(request: Request):
    return request.app.state.codex


async def _receive(socket):
    packet = await socket.receive()
    if packet["type"] == "websocket.disconnect":
        raise WebSocketDisconnect(packet.get("code", 1000))
    try:
        message = json.loads(packet.get("text") or "")
    except ValueError as exc:
        raise ValueError("JSON 명령 형식이 올바르지 않습니다.") from exc
    if not isinstance(message, dict):
        raise ValueError("JSON 명령 객체를 보내 주세요.")
    return message


async def _error(socket, message):
    await socket.send_json({"t": "error", "msg": str(message)})


MAX_LAG = 0.5   # 시뮬레이션이 이만큼(초) 밀리면 따라잡기를 포기하고 시계를 다시 맞춘다.


async def _play(socket, game):
    loop = asyncio.get_running_loop()
    start = loop.time()
    state_number = 1

    # 배속이 바뀌면 그 시점을 기준으로 틱 시계를 다시 잡는다.
    clock = start
    clock_ticks = game.ticks
    clock_speed = game.speed
    next_debug = start + 1
    game.loop_stats = {"lag_resets": 0, "commands": 0}

    receiver = asyncio.create_task(_receive(socket))
    try:
        while game.winner is None:
            now = loop.time()

            lag = now - (clock + (game.ticks - clock_ticks + 1) / (TICK_HZ * clock_speed))

            if game.speed != clock_speed or lag > MAX_LAG:
                if lag > MAX_LAG:
                    game.loop_stats["lag_resets"] += 1
                clock, clock_ticks, clock_speed = now, game.ticks, game.speed

            game.loop_stats["lag_ms"] = round(max(0.0, lag) * 1000, 1)

            if game.profiler is not None and now >= next_debug:
                next_debug = now + 1
                await socket.send_json(game.debug_message())

            next_tick = clock + (game.ticks - clock_ticks + 1) / (TICK_HZ * clock_speed)
            next_state = start + state_number / STATE_HZ

            if now >= next_tick and next_tick <= next_state:
                game.tick()
                for event in game.pop_events():
                    await socket.send_json(event)
            elif now >= next_state:
                await socket.send_json(game.state_message())
                state_number += 1
            elif not receiver.done():
                await asyncio.wait((receiver,), timeout=min(next_tick, next_state) - now)

            if receiver.done() and game.winner is None:
                try:
                    game.loop_stats["commands"] += 1
                    game.command(receiver.result())
                except ValueError as exc:
                    await _error(socket, exc)
                receiver = asyncio.create_task(_receive(socket))
                await asyncio.sleep(0)
    finally:
        receiver.cancel()
        with suppress(asyncio.CancelledError, WebSocketDisconnect, ValueError):
            await receiver


def _speed(start):
    speed = start.get("speed", 1)

    if speed not in data.SPEEDS or isinstance(speed, bool):
        raise ValueError("배속이 올바르지 않습니다.")

    return speed


def _difficulty(start):
    difficulty = start.get("difficulty")

    if not isinstance(difficulty, str) or difficulty not in data.DIFFICULTY:
        raise ValueError("난이도가 올바르지 않습니다.")

    return difficulty


def _create_game(start, user_id, user):
    """시작 메시지로 Game을 만든다 (Lua 실행·맵 변환이 있어 스레드풀에서 호출). 오류는 ValueError."""

    mode = start.get("mode", "campaign")
    difficulty = _difficulty(start)
    speed = _speed(start)

    if mode == "sandbox":
        setup = sandbox.build(start.get("map"), start.get("script"), user, start.get("bots") is True)
        return Game(None, difficulty, setup.templates, setup.levels, speed=speed,
                    scenario=setup.scenario)

    if mode != "campaign":
        raise ValueError("알 수 없는 게임 모드입니다.")

    stage = start.get("stage")

    if type(stage) is not int or stage not in data.STAGES or stage > user["max_stage"]:
        raise ValueError("선택할 수 없는 스테이지입니다.")

    return Game(stage, difficulty, user["templates"], user["unlocks"], speed=speed,
                on_reward=lambda points: db.add_points(user_id, points))


@app.websocket("/ws/game")
async def websocket_game(socket: WebSocket):
    await socket.accept()
    try:
        try:
            session = await run_in_threadpool(db.authenticate, socket.query_params.get("token"))
            user_id, user = session.user_id, session.user
            start = await _receive(socket)
            if start.get("t") != "start":
                raise ValueError("첫 메시지는 게임 시작 명령이어야 합니다.")
            game = await run_in_threadpool(_create_game, start, user_id, user)
        except ValueError as exc:
            await _error(socket, exc)
            await socket.close(code=1008)
            return

        await socket.send_json(game.init_message())
        try:
            await _play(socket, game)
        finally:
            # Record wins even if the client disconnects during the last event.
            # 샌드박스는 스테이지 진행을 기록하지 않는다.
            if game.winner is not None and not game.sandbox:
                user = await run_in_threadpool(db.finish_match, user_id, game.stage, game.winner)
        await socket.send_json(game.state_message())
        await socket.send_json(game.end_message(user))
        await socket.close()
    except (WebSocketDisconnect, OSError):
        pass


class FrontendFiles(StaticFiles):
    async def get_response(self, path, scope):
        if not WEB_DIR.is_dir():
            raise HTTPException(404, "웹 화면을 찾을 수 없습니다.")
        return await super().get_response(path, scope)

    async def check_config(self):
        if WEB_DIR.is_dir():
            await super().check_config()


app.mount("/", FrontendFiles(directory=WEB_DIR, html=True, check_dir=False), name="web")
