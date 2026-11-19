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

from .db import AccountError, Database
from .game import data
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
Account = Annotated[tuple, Depends(current_account)]


@app.post("/api/register")
def register(body: Body):
    return db.register(body.get("username"), body.get("password"))


@app.post("/api/login")
def login(body: Body):
    return db.login(body.get("username"), body.get("password"))


@app.get("/api/me")
def me(account: Account):
    return {"user": account[1]}


@app.put("/api/templates")
def templates(body: Body, account: Account):
    return {"user": db.save_templates(account[0], body.get("templates"))}


@app.post("/api/gacha/pull")
def pull(account: Account):
    return db.pull(account[0])


@app.get("/api/data")
def game_data():
    return {
        "battalions": [dict(stats, id=key) for key, stats in data.BATTALIONS.items()],
        "effects": data.EFFECT_DESC, "terrain": data.TERRAIN, "weather": data.WEATHER,
        "stages": data.STAGES, "difficulty": data.DIFFICULTY,
        "rules": {
            "max_battalions": data.MAX_BATTALIONS, "max_duplicate": data.MAX_DUPLICATE,
            "max_templates": data.MAX_TEMPLATES, "gacha_cost": data.GACHA_COST,
            "gacha_unlock_chance": data.GACHA_UNLOCK_CHANCE,
            "gacha_rarity_weight": data.GACHA_RARITY_WEIGHT, "max_level": data.MAX_LEVEL,
            "level_bonus": data.LEVEL_BONUS, "match_seconds": data.MATCH_SECONDS,
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


async def _play(socket, game):
    loop = asyncio.get_running_loop()
    start = loop.time()
    state_number = 1
    receiver = asyncio.create_task(_receive(socket))
    try:
        while game.winner is None:
            now = loop.time()
            next_tick = start + (game.ticks + 1) / TICK_HZ
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
                    game.command(receiver.result())
                except ValueError as exc:
                    await _error(socket, exc)
                receiver = asyncio.create_task(_receive(socket))
                await asyncio.sleep(0)
    finally:
        receiver.cancel()
        with suppress(asyncio.CancelledError, WebSocketDisconnect, ValueError):
            await receiver


@app.websocket("/ws/game")
async def websocket_game(socket: WebSocket):
    await socket.accept()
    try:
        try:
            user_id, user = await run_in_threadpool(db.authenticate, socket.query_params.get("token"))
            start = await _receive(socket)
            if start.get("t") != "start":
                raise ValueError("첫 메시지는 게임 시작 명령이어야 합니다.")
            stage, difficulty = start.get("stage"), start.get("difficulty")
            if type(stage) is not int or stage not in data.STAGES or stage > user["max_stage"]:
                raise ValueError("선택할 수 없는 스테이지입니다.")
            if not isinstance(difficulty, str) or difficulty not in data.DIFFICULTY:
                raise ValueError("난이도가 올바르지 않습니다.")
        except ValueError as exc:
            await _error(socket, exc)
            await socket.close(code=1008)
            return

        game = await run_in_threadpool(
            Game, stage, difficulty, user["templates"], user["unlocks"],
            on_reward=lambda points: db.add_points(user_id, points),
        )
        await socket.send_json(game.init_message())
        try:
            await _play(socket, game)
        finally:
            # Record wins even if the client disconnects during the last event.
            if game.winner is not None:
                user = await run_in_threadpool(db.finish_match, user_id, stage, game.winner)
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
