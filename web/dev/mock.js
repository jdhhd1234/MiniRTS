import { createBattle } from "../js/battle.js";
import { COLS, ROWS, hexAt, hexCenter } from "../js/hex.js";

const root = document.querySelector("#app");
const toEntries = (names) => names.map((name, id) => ({ id, name }));
const data = {
  battalions: [
    { id: "inf", name: "보병대대" }, { id: "tank", name: "전차대대" }, { id: "arty", name: "포병대대" },
  ],
  terrain: toEntries(["바다", "평지", "숲", "산", "강", "도시", "늪", "사막", "고원"]),
  weather: toEntries(["맑음", "비", "눈", "안개"]),
  stages: { 1: { name: "모의 작전" } },
  difficulty: { normal: { name: "중" } },
};
const templates = [
  { name: "보병사단", battalions: ["inf", "inf", "arty"], cost: 75 },
  { name: "기갑사단", battalions: ["tank", "tank", "inf"], cost: 120 },
];
const bases = [
  { side: 1, kind: "hq", name: "파리", col: 13, row: 31 },
  { side: 1, kind: "corps", name: "브뤼셀", col: 21, row: 20 },
  { side: 1, kind: "div", name: "리옹", col: 19, row: 53 },
  { side: 2, kind: "hq", name: "베를린", col: 48, row: 13 },
  { side: 2, kind: "corps", name: "뮌헨", col: 43, row: 40 },
  { side: 2, kind: "div", name: "프라하", col: 52, row: 30 },
].map((base, id) => {
  const hp = { hq: 600, corps: 400, div: 250 }[base.kind];
  return { ...base, id, ...hexCenter(base.col, base.row), hp, mhp: hp };
});
const cities = [...bases, { name: "스트라스부르", col: 27, row: 40 }, { name: "암스테르담", col: 20, row: 11 }]
  .map(({ name, col, row }) => ({ name, col, row }));
const allLandTypes = [1, 1, 1, 1, 1, 2, 2, 3, 4, 6, 7, 8];
const front = (row) => 30 + Math.round(2.5 * Math.sin(row / 8));

function startMock() {
  const terrain = Array.from({ length: COLS * ROWS }, (_, index) => {
    const col = index % COLS;
    const row = Math.floor(index / COLS);
    if (col < 8 - row * 0.21 + 3 * Math.sin(row / 5)) return 0;
    if (col === 27 + Math.round(Math.sin(row / 7) * 2)) return 4;
    return allLandTypes[Math.floor(Math.random() * allLandTypes.length)];
  });
  for (const { col, row } of cities) terrain[row * COLS + col] = 5;
  const mockBases = bases.map((base) => ({ ...base }));
  const owner = terrain.map((type, index) => type === 0 ? 0 : index % COLS < front(Math.floor(index / COLS)) ? 1 : 2);
  const weather = [1, 0, 0, 3, 0, 2, 0, 0, 3, 0, 1, 0, 0, 0, 2, 0];
  const queue = [];
  const units = [];
  let elapsed = 0;
  let res = 300;
  const income = 6.2;
  let buildSpeed = 1;
  let productionProgress = 0;
  let tick = 0;
  const mockUser = {
    username: "모의 지휘관", points: 0, max_stage: 1, unlocks: { inf: 1, tank: 1, arty: 1 },
    templates: templates.map(({ name, battalions }) => ({ name, battalions })),
  };

  function unit(side, col, row, battalions, target) {
    const id = units.length + 1;
    const { x, y } = hexCenter(col, row);
    units.push({
      id, side, name: `제${id}${side === 1 ? "" : " 적군 "}사단`, x, y, hp: 300, mhp: 300,
      st: target ? "move" : "idle", tgt: null, dest: null, b: battalions,
      fn: battalions.includes("tank") ? "UCA---" : "UCI---", supply: 1, target,
    });
  }
  unit(1, 22, 27, templates[0].battalions, [650, 500]);
  unit(1, 24, 33, templates[1].battalions, [640, 535]);
  unit(1, 21, 43, templates[0].battalions, [600, 690]);
  unit(2, 35, 29, ["inf", "arty"], [635, 515]);
  unit(2, 37, 43, ["tank", "inf"], [620, 690]);

  const battle = createBattle(root, {
    data, stage: 1, difficulty: "normal",
    send(command) {
      if (command.t === "surrender") {
        clearInterval(timer);
        battle.receive({ t: "end", winner: 2, reason: "surrender", points_earned: 0, user: mockUser });
      } else if (command.t === "build") {
        const template = templates[command.template];
        if (res < template.cost) return;
        res -= template.cost;
        queue.push({ template: command.template, name: template.name, progress: 0 });
        battle.receive({ t: "event", msg: `${template.name} 생산 명령을 접수했습니다.`, kind: "info" });
      } else {
        for (const id of command.ids) {
          const record = units.find((entry) => entry.id === id);
          if (!record) continue;
          const retreat = command.t === "retreat";
          const { x, y } = retreat ? mockBases[0] : hexCenter(command.col, command.row);
          record.target = [x, y];
          record.st = retreat ? "retreat" : "move";
        }
      }
    },
    leave() { clearInterval(timer); battle.destroy(); showMockLobby(); },
  });

  battle.receive({
    t: "init", you: 1, cols: COLS, rows: ROWS, terrain, owner, bases: mockBases, cities,
    templates, weather, weather_cells: 4, stage: 1, difficulty: "normal", match_seconds: 1200,
  });

  function advance() {
    elapsed += 0.25;
    tick++;
    res += income / 4;
    const changes = new Map();
    for (const record of units) {
      if (record.target) {
        const [x, y] = record.target;
        const distance = Math.hypot(x - record.x, y - record.y);
        const step = distance ? Math.min(1, 2.2 / distance) : 1;
        record.x += (x - record.x) * step;
        record.y += (y - record.y) * step;
        if (step === 1) { record.target = null; record.st = "idle"; }
      }
      const enemy = units.find((other) => other.side !== record.side && Math.hypot(record.x - other.x, record.y - other.y) < 50);
      record.tgt = enemy ? [enemy.x, enemy.y] : null;
      if (enemy) record.hp = Math.max(45, record.hp - 0.4);
      if (record.st !== "retreat") record.st = enemy ? "engaged" : record.target ? "move" : "idle";
      record.dest = record.side === 1 ? record.target : null;
      record.supply = Math.max(0.5, 1 - Math.hypot(record.x - mockBases[0].x, record.y - mockBases[0].y) / 1300);
      const { col, row } = hexAt(record.x, record.y);
      const index = row * COLS + col;
      if (owner[index] && owner[index] !== record.side) {
        changes.set(index, record.side);
        owner[index] = record.side;
      }
    }
    if (queue.length) {
      productionProgress += 0.25 * buildSpeed / 15;
      queue[0].progress = Math.min(1, productionProgress);
      if (productionProgress >= 1) {
        const { template, name } = queue.shift();
        unit(1, mockBases[0].col + 2, mockBases[0].row + (units.length % 3) - 1, templates[template].battalions);
        productionProgress = 0;
        battle.receive({ t: "event", msg: `${name} 생산이 완료되었습니다.`, kind: "good" });
      }
    }
    const weatherChanged = tick % 80 === 0;
    if (weatherChanged) weather[1] = (weather[1] + 1) % data.weather.length;
    if (tick === 108) {
      mockBases[2].side = 2;
      mockBases[2].hp = 75;
      buildSpeed *= 0.85;
      battle.receive({ t: "event", msg: "리옹 사단작전기지가 함락되었습니다.", kind: "bad" });
    }
    const terr = owner.reduce((counts, side) => {
      if (side) counts[side - 1]++;
      return counts;
    }, [0, 0]);
    battle.receive({
      t: "state", time: elapsed, res, income, build_speed: buildSpeed, queue,
      units: units.map(({ target, ...record }) => record),
      bases: mockBases.map(({ id, side, hp, mhp }) => ({ id, side, hp, mhp })),
      own: [...changes], weather: weatherChanged ? weather : null, terr,
    });
    if (elapsed >= 1200) {
      const winner = terr[0] >= terr[1] ? 1 : 2;
      clearInterval(timer);
      battle.receive({ t: "end", winner, reason: "time", points_earned: 0, user: mockUser });
    }
  }

  const timer = setInterval(advance, 250);
  advance();
}

function showMockLobby() {
  root.innerHTML = '<section class="lobby-content"><div class="page-title"><h1>모의 작전</h1></div><button class="primary">다시 출격</button></section>';
  root.querySelector("button").onclick = startMock;
}

if (globalThis.PIXI && globalThis.ms) startMock();
else root.querySelector("p").textContent = "지도 라이브러리를 불러올 수 없습니다. CDN 연결을 확인해 주세요.";
