# MinRTS

웹 기반 2D 실시간 군사전략 게임. 유럽 헥스 지도 위에서 사단을 편성·운용해 적 본부를 함락시키거나, 20분 뒤 더 넓은 영토를 차지하면 승리한다.

- 기획: [`Planner/main_plan_spec.md`](Planner/main_plan_spec.md)
- 구현 계약(API·프로토콜·좌표계): [`Planner/mvp_contract.md`](Planner/mvp_contract.md)

## 실행

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m uvicorn server.main:app --port 8000 --reload
```

브라우저에서 http://127.0.0.1:8000 접속 → 회원가입 → 로비에서 편제 → 출격.

## 테스트

```powershell
# 서버 없이 시뮬레이션만 빠르게 검사
.venv\Scripts\python server\tests\smoke_test.py

# 실행 중인 서버에 접속해 REST/WebSocket 계약 검사
.venv\Scripts\python tests\e2e_test.py --seconds 20

# 설계서 8.2 체크리스트 (서버 불필요)
.venv\Scripts\python -m unittest tests.test_checklist -v

# 장비·구성품 도감 데이터 재생성 (설계서 수정 후)
.venv\Scripts\python server\tools\build_codex.py
```

## 조작

| 입력 | 동작 |
|---|---|
| 좌클릭 / Shift+좌클릭 | 사단 선택 / 추가 선택 |
| 좌드래그 | 박스 선택 |
| 우클릭 | 이동 명령 |
| 우드래그, WASD, 방향키 | 지도 이동 |
| 휠 | 확대/축소 |
| R | 선택 사단 후퇴 |
| 1~6 | 템플릿으로 사단 생산 |

## 구조

```
server/            Python (FastAPI)
  main.py          REST API + WebSocket + 정적 파일 서빙
  game/data.py     대대·지형·날씨·기지·스테이지 수치
  game/hexmap.py   유럽 헥스맵 생성, A* 경로
  game/sim.py      게임 시뮬레이션 (서버 권한)
  game/ai.py       AI (상/중/하)
web/               HTML/CSS/JS (PixiJS, milsymbol)
tests/e2e_test.py  통합 테스트
data/minrts.db     SQLite (자동 생성)
```
