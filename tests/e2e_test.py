"""MinRTS 통합 테스트 — 실행 중인 서버에 실제 HTTP/WebSocket으로 접속해 mvp_contract.md 형식을 검증한다.

사용법:
    .venv/Scripts/python -m uvicorn server.main:app --port 8000   (다른 터미널)
    .venv/Scripts/python tests/e2e_test.py [--url http://127.0.0.1:8000] [--seconds 20]
"""
import argparse
import asyncio
import json
import random
import string
import sys
import urllib.error
import urllib.request

import websockets

FAILS = []


def check(cond, msg):
    if not cond:
        FAILS.append(msg)
        print("  FAIL:", msg)
    return cond


def http(base, method, path, body=None, token=None):
    req = urllib.request.Request(base + path, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    data = json.dumps(body).encode() if body is not None else None
    try:
        with urllib.request.urlopen(req, data, timeout=10) as r:
            raw = r.read()
            try:
                return r.status, json.loads(raw or b"null")
            except ValueError:
                return r.status, raw.decode(errors="replace")
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, raw.decode(errors="replace")


def check_user(u, where):
    if not check(isinstance(u, dict), f"{where}: user 객체 아님"):
        return
    for k in ("username", "points", "max_stage", "unlocks", "templates"):
        check(k in u, f"{where}: user.{k} 없음")
    if isinstance(u.get("unlocks"), dict):
        check(len(u["unlocks"]) >= 6, f"{where}: 기본 해금 6종 미만")


def test_rest(base):
    print("[REST]")
    st, page = http(base, "GET", "/")
    check(st == 200, f"GET / -> {st} (web/index.html 정적 서빙)")

    st, d = http(base, "GET", "/api/data")
    check(st == 200, f"GET /api/data -> {st}")
    if st == 200:
        for k in ("battalions", "effects", "terrain", "weather", "stages", "difficulty", "rules"):
            check(k in d, f"/api/data.{k} 없음")
        b = d.get("battalions")
        check(isinstance(b, list) and len(b) == 20, "battalions는 20개 배열이어야 함")
        if isinstance(b, list) and b:
            check("id" in b[0], "battalions 항목에 id 없음")

    name = "e2e_" + "".join(random.choices(string.ascii_lowercase, k=8))
    st, r = http(base, "POST", "/api/register", {"username": name, "password": "pw123456"})
    check(st == 200 and "token" in r, f"register -> {st} {r}")
    st, r2 = http(base, "POST", "/api/register", {"username": name, "password": "pw123456"})
    check(400 <= st < 500, f"중복 가입은 4xx여야 함 -> {st}")
    st, r = http(base, "POST", "/api/login", {"username": name, "password": "pw123456"})
    check(st == 200 and "token" in r, f"login -> {st}")
    st, bad = http(base, "POST", "/api/login", {"username": name, "password": "wrong"})
    check(400 <= st < 500, f"잘못된 비밀번호는 4xx -> {st}")
    token = r.get("token") if isinstance(r, dict) else None
    if not token:
        return None, None

    st, me = http(base, "GET", "/api/me", token=token)
    check(st == 200, f"GET /api/me -> {st}")
    user = me.get("user") if isinstance(me, dict) else None
    check_user(user, "/api/me")
    check(user and user.get("points") == 300, "신규 계정 points는 300")
    st, _ = http(base, "GET", "/api/me")
    check(st in (401, 403), f"토큰 없는 /api/me는 401/403 -> {st}")

    # 템플릿 검증
    st, _ = http(base, "PUT", "/api/templates", {"templates": [{"name": "x", "battalions": ["tank"] * 3}]}, token)
    check(400 <= st < 500, f"같은 대대 3개 템플릿은 거부돼야 함 -> {st}")
    st, _ = http(base, "PUT", "/api/templates", {"templates": [{"name": "x", "battalions": ["heli"]}]}, token)
    check(400 <= st < 500, f"미해금 대대 템플릿은 거부돼야 함 -> {st}")
    tpl = [{"name": "시험사단", "battalions": ["tank", "tank", "mech", "arty"]},
           {"name": "보병", "battalions": ["inf"]}]
    st, r = http(base, "PUT", "/api/templates", {"templates": tpl}, token)
    check(st == 200, f"정상 템플릿 저장 -> {st} {r}")

    # 가챠: 300포인트 → 3회 성공, 4회째 실패
    for i in range(3):
        st, r = http(base, "POST", "/api/gacha/pull", token=token)
        ok = check(st == 200 and "result" in r, f"gacha #{i + 1} -> {st} {r}")
        if ok:
            res = r["result"]
            check(res.get("kind") in ("unlock", "upgrade"), f"gacha kind 이상: {res}")
            check_user(r.get("user"), "gacha")
    st, r = http(base, "POST", "/api/gacha/pull", token=token)
    check(st == 400, f"포인트 부족 가챠는 400 -> {st}")
    return token, user


async def test_ws(base, token, seconds):
    print("[WebSocket]")
    ws_url = base.replace("http", "ws", 1) + f"/ws/game?token={token}"
    async with websockets.connect(ws_url, max_size=2 ** 24) as ws:
        await ws.send(json.dumps({"t": "start", "stage": 3, "difficulty": "hard"}))
        first = json.loads(await asyncio.wait_for(ws.recv(), 5))
        check(first.get("t") == "error", f"max_stage 초과 스테이지는 error여야 함 -> {first.get('t')}")

    async with websockets.connect(ws_url, max_size=2 ** 24) as ws:
        await ws.send(json.dumps({"t": "start", "stage": 1, "difficulty": "easy"}))
        init = json.loads(await asyncio.wait_for(ws.recv(), 10))
        if not check(init.get("t") == "init", f"첫 메시지는 init -> {init.get('t')}"):
            return
        cols, rows = init.get("cols"), init.get("rows")
        check((cols, rows) == (60, 69), f"cols/rows = {cols},{rows}")
        n = 60 * 69
        check(len(init.get("terrain", [])) == n, "terrain 길이")
        check(len(init.get("owner", [])) == n, "owner 길이")
        terr = init.get("terrain", [])
        check(len(set(terr)) >= 7, f"지형 종류가 너무 적음: {sorted(set(terr))}")
        check(0 in terr, "바다(0) 헥스 없음")
        bases = init.get("bases", [])
        check(len(bases) == 14, f"기지 14개여야 함 -> {len(bases)}")
        for b in bases[:1]:
            for k in ("id", "side", "kind", "name", "x", "y", "col", "row", "hp", "mhp"):
                check(k in b, f"base.{k} 없음")
        check(len(init.get("weather", [])) == 16, "weather 16칸")
        check(init.get("you") == 1, "you == 1")
        tpls = init.get("templates", [])
        check(len(tpls) >= 1 and "cost" in tpls[0], "init.templates에 cost 필요")

        hq = next((b for b in bases if b["side"] == 2 and b["kind"] == "hq"), None)
        stats = dict(states=0, moved=False, engaged=False, own_changes=0, events=0)
        my_ids, sent_move, sent_build = [], False, False
        first_pos = {}
        loop = asyncio.get_event_loop()
        end = loop.time() + seconds
        while loop.time() < end:
            try:
                m = json.loads(await asyncio.wait_for(ws.recv(), 3))
            except asyncio.TimeoutError:
                check(False, "3초간 메시지 없음")
                break
            t = m.get("t")
            if t == "state":
                stats["states"] += 1
                if stats["states"] == 1:
                    for k in ("time", "res", "income", "build_speed", "queue", "units", "bases", "own", "terr"):
                        check(k in m, f"state.{k} 없음")
                    for u in m.get("units", [])[:1]:
                        for k in ("id", "side", "name", "x", "y", "hp", "mhp", "st", "tgt", "dest", "b", "fn", "supply"):
                            check(k in u, f"unit.{k} 없음")
                stats["own_changes"] += len(m.get("own") or [])
                for u in m.get("units", []):
                    check(u.get("st") in ("idle", "move", "engaged", "retreat"), f"unit.st 이상: {u.get('st')}")
                    if u["side"] == 1:
                        if u["id"] not in first_pos:
                            first_pos[u["id"]] = (u["x"], u["y"])
                        elif (u["x"], u["y"]) != first_pos[u["id"]]:
                            stats["moved"] = True
                    if u["side"] == 2:
                        check(u.get("dest") is None, "적 유닛 dest는 null이어야 함")
                    if u.get("st") == "engaged":
                        stats["engaged"] = True
                my_ids = [u["id"] for u in m.get("units", []) if u["side"] == 1]
                if my_ids and not sent_move and hq:
                    await ws.send(json.dumps({"t": "move", "ids": my_ids, "col": hq["col"], "row": hq["row"]}))
                    sent_move = True
                if not sent_build and m.get("res", 0) > 0:
                    await ws.send(json.dumps({"t": "build", "template": 0}))
                    sent_build = True
            elif t == "event":
                stats["events"] += 1
            elif t == "end":
                break
            elif t == "error":
                check(False, f"서버 error: {m.get('msg')}")

        check(stats["states"] >= seconds * 3, f"state 전송 빈도 낮음: {stats['states']}회/{seconds}초 (4Hz 기대)")
        check(bool(my_ids), "플레이어 시작 사단 없음")
        check(stats["moved"], "이동 명령 후 유닛이 움직이지 않음")
        print(f"  states={stats['states']} engaged_seen={stats['engaged']} own_changes={stats['own_changes']} events={stats['events']}")

        await ws.send(json.dumps({"t": "surrender"}))
        while True:
            m = json.loads(await asyncio.wait_for(ws.recv(), 5))
            if m.get("t") == "end":
                check(m.get("winner") == 2 and m.get("reason") == "surrender", f"항복 결과 이상: {m}")
                check_user(m.get("user"), "end")
                break


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    ap.add_argument("--seconds", type=int, default=20)
    a = ap.parse_args()
    token, _ = test_rest(a.url)
    if token:
        asyncio.run(test_ws(a.url, token, a.seconds))
    print()
    if FAILS:
        print(f"FAILED ({len(FAILS)})")
        for f in FAILS:
            print(" -", f)
        sys.exit(1)
    print("ALL PASSED")


if __name__ == "__main__":
    main()
