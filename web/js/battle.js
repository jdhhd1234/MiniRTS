import { createMap, terrainColors } from "./map.js";
import { escapeHtml as esc, number, percent } from "./ui.js";

const statusNames = { idle: "대기", move: "이동", engaged: "교전", retreat: "후퇴" };
const endReasons = { hq: "본부 함락", time: "제한 시간 종료", surrender: "항복" };

function clock(seconds) {
  const left = Math.max(0, Math.ceil(seconds));
  return `${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}`;
}

export function createBattle(root, options) {
  const { data, stage, difficulty, send, leave } = options;
  const battalions = new Map(data.battalions.map(({ id, name }) => [id, name]));
  const weatherNames = new Map(Object.values(data.weather).map(({ id, name }) => [id, name]));
  const frame = document.createElement("section");
  frame.className = "battle";
  frame.innerHTML = `
    <div class="map-host"></div>
    <header class="battle-top">
      <div class="battle-heading"><div class="brand">Min<span>RTS</span></div><div id="mission-title">${esc(data.stages[stage]?.name)} · 난이도 ${esc(data.difficulty[difficulty]?.name)}</div></div>
      <div class="battle-metrics">
        <div><span>남은 시간</span><strong id="time-left">—</strong></div>
        <div><span>자원 / 수입</span><strong id="resources">—</strong></div>
        <div><span>생산속도</span><strong id="build-speed">—</strong></div>
      </div>
      <button id="surrender" class="danger" disabled>항복</button>
    </header>
    <aside class="battle-left">
      <section class="battle-panel"><h2>사단 생산</h2><div id="production"><p>작전 정보를 기다리는 중…</p></div>
        <h3>생산 대기열</h3><div id="queue"><p>대기 중인 사단이 없습니다.</p></div></section>
      <section class="battle-panel events"><h2>작전 기록</h2><ol id="events" aria-live="polite"></ol></section>
    </aside>
    <aside class="battle-right">
      <section class="battle-panel"><h2>영토 점유</h2><div class="territory-bar" id="territory-bar"><span id="blue-share"></span><span id="red-share"></span></div>
        <div class="territory-count"><span id="blue-count">청군 —</span><span id="red-count">적군 —</span></div></section>
      <section class="battle-panel selection"><h2>선택 사단</h2><div id="selection"><p>지도에서 청군 사단을 선택하세요.</p></div></section>
      <details class="battle-panel legend"><summary>지도 범례</summary>
        <div class="terrain-key">${Object.values(data.terrain).map(({ id, name }) =>
          `<span><i style="background:#${terrainColors[id].toString(16).padStart(6, "0")}"></i>${esc(name)}</span>`).join("")}</div>
        <p>실선 · 교전　점선 · 이동 목표</p>
      </details>
    </aside>
    <p class="control-help">좌클릭 선택 · Shift 추가 · 좌드래그 범위 선택 · 우클릭 이동 · 우드래그 / WASD / 방향키 지도 이동 · 휠 확대 · R 후퇴 · 1–6 생산</p>
    <div class="battle-overlay" id="battle-overlay" role="status"><div class="panel overlay-panel"><h1>작전 연결 중…</h1></div></div>`;
  root.replaceChildren(frame);

  const get = (id) => frame.querySelector(`#${id}`);
  const overlay = get("battle-overlay");
  const surrender = get("surrender");
  let map;
  let init;
  let state;
  let finished = false;
  let surrenderRequested = false;
  let selected = new Set();
  let queueKey = "";
  let selectionKey = "";
  let productionButtons = [];
  let productionStatus;

  function display(text, title, showButton = true) {
    overlay.hidden = false;
    overlay.innerHTML = `<div class="panel overlay-panel"><h1>${esc(title)}</h1>
      ${text ? `<p>${esc(text)}</p>` : ""}
      ${showButton ? '<button class="primary" id="leave-battle">로비로 돌아가기</button>' : ""}</div>`;
    const button = get("leave-battle");
    if (button) {
      button.onclick = leave;
      button.focus();
    }
  }

  function eventLine(msg, kind = "info") {
    const log = get("events");
    const item = document.createElement("li");
    item.className = ["info", "good", "bad"].includes(kind) ? kind : "info";
    const time = document.createElement("time");
    time.textContent = clock(state?.time ?? 0);
    const message = document.createElement("span");
    message.textContent = msg;
    item.append(time, message);
    log.prepend(item);
    if (log.children.length > 40) log.lastElementChild.remove();
  }

  function build(template) {
    if (finished || surrenderRequested || !init?.templates[template]) return;
    if (state && state.res < init.templates[template].cost) {
      eventLine("사단을 생산할 자원이 부족합니다.", "bad");
      return;
    }
    send({ t: "build", template });
  }

  function showSelected() {
    const records = (state?.units ?? []).filter(({ id }) => selected.has(id));
    const key = JSON.stringify(records.map(({ id, hp, mhp, supply, st }) => [id, Math.ceil(hp), mhp, Math.round(supply * 100), st]));
    if (key === selectionKey) return;
    selectionKey = key;
    get("selection").innerHTML = records.length ? `
      <p class="selection-count">${records.length}개 사단 선택</p>
      ${records.map(({ name, st, hp, mhp, supply, b }) => `<article class="selected-unit">
        <div><strong>${esc(name)}</strong><small>${statusNames[st] ?? ""}</small></div>
        <p>${b.map((id) => esc(battalions.get(id) ?? id)).join(" · ")}</p>
        <p>체력 ${number(Math.max(0, Math.ceil(hp)))} / ${number(mhp)} · 보급 ${Math.round(supply * 100)}%</p>
      </article>`).join("")}` : "<p>지도에서 청군 사단을 선택하세요.</p>";
  }

  function showTerritory([blue, red]) {
    const total = blue + red;
    const ratio = total ? blue / total : 0;
    get("blue-share").style.width = percent(ratio);
    get("red-share").style.width = percent(total ? 1 - ratio : 0);
    get("blue-count").textContent = `청군 ${number(blue)} · ${percent(ratio)}`;
    get("red-count").textContent = `적군 ${number(red)} · ${percent(total ? 1 - ratio : 0)}`;
  }

  function showQueue(queue) {
    const key = JSON.stringify(queue.map(({ template, name, progress }) => [template, name, Math.floor(progress * 100)]));
    if (key === queueKey) return;
    queueKey = key;
    get("queue").innerHTML = queue.length ? `<ol class="queue-list">${queue.map(({ name, progress }, index) =>
      `<li><span>${esc(name)}</span><small>${index ? "대기" : `${Math.floor(progress * 100)}%`}</small>
        ${index ? "" : `<progress max="1" value="${progress}" aria-label="${esc(name)} 생산 진행률"></progress>`}</li>`).join("")}</ol>`
      : "<p>대기 중인 사단이 없습니다.</p>";
  }

  function updateProduction() {
    const status = init.templates.map(({ cost }) => finished || surrenderRequested || (state !== undefined && state.res < cost));
    const key = status.join(",");
    if (productionStatus === key) return;
    productionStatus = key;
    productionButtons.forEach((button, index) => { button.disabled = status[index]; });
  }

  function start(message) {
    init = message;
    map = createMap(frame.querySelector(".map-host"), message, {
      send: (command) => { if (!finished && !surrenderRequested) send(command); },
      onBuild: build,
      onSelection: (next) => { selected = next; showSelected(); },
      weatherNames,
    });
    const title = data.stages[init.stage]?.name;
    get("mission-title").textContent = `${title ?? ""} · 난이도 ${data.difficulty[init.difficulty]?.name ?? ""}`;
    get("time-left").textContent = clock(init.match_seconds);
    get("production").innerHTML = init.templates.length ? init.templates.map(({ name, cost }, index) =>
      `<button class="build-button" data-build="${index}"><kbd>${index + 1}</kbd><span>${esc(name)}</span><small>${number(cost)}</small></button>`).join("")
      : "<p>로비에서 사단 편제를 저장하세요.</p>";
    productionButtons = [...frame.querySelectorAll("[data-build]")];
    productionButtons.forEach((button, index) => { button.onclick = () => build(index); });
    showTerritory(init.owner.reduce((counts, side) => {
      if (side === 1 || side === 2) counts[side - 1]++;
      return counts;
    }, [0, 0]));
    overlay.hidden = true;
    surrender.disabled = false;
    eventLine("작전을 시작했습니다.");
  }

  function finish() {
    finished = true;
    map?.setActive(false);
    surrender.disabled = true;
    if (init) updateProduction();
  }

  surrender.onclick = () => {
    if (!init || finished || surrenderRequested || !window.confirm("항복하면 패배로 처리됩니다. 항복하시겠습니까?")) return;
    surrenderRequested = true;
    surrender.disabled = true;
    updateProduction();
    send({ t: "surrender" });
  };

  return {
    get ready() { return Boolean(init); },
    receive(message) {
      if (finished) return;
      switch (message.t) {
        case "init":
          if (!init) start(message);
          break;
        case "state":
          if (!map) break;
          state = message;
          map.update(state);
          get("time-left").textContent = clock(init.match_seconds - state.time);
          get("resources").textContent = `${number(Math.floor(state.res))} / +${number(state.income)}초당`;
          get("build-speed").textContent = `${number(state.build_speed)}×`;
          showTerritory(state.terr);
          showQueue(state.queue);
          showSelected();
          updateProduction();
          break;
        case "event":
          eventLine(message.msg, message.kind);
          break;
        case "error":
          eventLine(message.msg, "bad");
          if (surrenderRequested) {
            surrenderRequested = false;
            surrender.disabled = false;
            updateProduction();
          }
          break;
        case "end":
          finish();
          display(`${endReasons[message.reason] ?? ""} · ${number(message.points_earned)} 포인트 획득`,
            message.winner === (init?.you ?? 1) ? "작전 승리" : "작전 패배");
          break;
      }
    },
    disconnect(message) {
      if (finished) return;
      finish();
      display(message, "작전 연결 종료");
    },
    destroy() {
      map?.destroy();
      frame.remove();
    },
  };
}
