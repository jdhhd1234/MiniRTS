import { createBattle } from "./battle.js";
import { renderCodex } from "./codex.js";
import { escapeHtml as esc, number, percent, rarityNames, divisionStats, statList } from "./ui.js";

const root = document.querySelector("#app");
const sessionKey = "minrts-token";
let token = localStorage.getItem(sessionKey);
let data;
let byId;
let user;
let page = "sortie";
let stage = 1;
let difficulty = "normal";
let draft = [];
let activeTemplate = 0;
let busy = false;
let result;
let codexRequest;

async function api(path, method = "GET", body) {
  const headers = {};
  if (token) headers.Authorization = `Bearer ${token}`;
  if (body !== undefined) headers["Content-Type"] = "application/json";
  let response;
  try {
    response = await fetch(`/api/${path}`, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  } catch {
    throw new Error("서버에 연결할 수 없습니다. 연결을 확인해 주세요.");
  }
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw Object.assign(new Error(typeof payload.detail === "string" ? payload.detail : "요청을 처리할 수 없습니다."), {
      status: response.status,
    });
  }
  return payload;
}

function session(nextToken) {
  token = nextToken;
  if (token) localStorage.setItem(sessionKey, token);
  else localStorage.removeItem(sessionKey);
}

function authScreen(register = false, message = "") {
  root.innerHTML = `
    <section class="auth-layout">
      <div class="auth-intro"><div class="brand large">Min<span>RTS</span></div><h1>작전 지휘소</h1>
        <p>사단을 편성하고, 전선을 지휘하고, 적 본부를 점령하세요.</p>
      </div>
      <section class="auth-panel panel">
        <h2>${register ? "계정 등록" : "지휘관 로그인"}</h2>
        <form id="auth-form">
          <label>아이디<input required name="username" autocomplete="username"></label>
          <label>비밀번호<input required type="password" name="password" autocomplete="${register ? "new-password" : "current-password"}"></label>
          <p class="message" role="status">${esc(message)}</p>
          <button type="submit" class="primary">${register ? "등록" : "로그인"}</button>
          <button type="button" id="auth-switch" class="quiet">${register ? "로그인으로 돌아가기" : "새 계정 등록"}</button>
        </form>
      </section>
    </section>`;
  const form = root.querySelector("form");
  root.querySelector("#auth-switch").onclick = () => authScreen(!register);
  form.onsubmit = async (event) => {
    event.preventDefault();
    const credentials = Object.fromEntries(new FormData(form));
    const buttons = [...form.querySelectorAll("button")];
    buttons.forEach((button) => { button.disabled = true; });
    const status = form.querySelector(".message");
    status.textContent = "연결 중…";
    try {
      await loadData();
      const response = await api(register ? "register" : "login", "POST", credentials);
      user = response.user;
      session(response.token);
      stage = 1;
      difficulty = "normal";
      openPage("sortie");
    } catch (error) {
      status.textContent = error.message;
      buttons.forEach((button) => { button.disabled = false; });
    }
  };
}

async function loadData() {
  if (data) return;
  data = await api("data");
  byId = new Map(data.battalions.map((battalion) => [battalion.id, battalion]));
}

const nav = { sortie: "출격", templates: "편제 편집기", gacha: "가챠", codex: "대대 도감" };

function openPage(nextPage) {
  page = nextPage;
  if (page === "templates") {
    draft = user.templates.map(({ name, battalions }) => ({ name, battalions: [...battalions] }));
    activeTemplate = 0;
  }
  if (page === "gacha") result = null;
  lobby();
}

function lobby(message = "") {
  root.innerHTML = `
    <header class="lobby-header">
      <div class="brand">Min<span>RTS</span></div>
      <nav aria-label="지휘소 메뉴">${Object.entries(nav).map(([key, label]) =>
        `<button data-page="${key}" ${key === page ? 'aria-current="page"' : ""} ${busy ? "disabled" : ""}>${label}</button>`).join("")}</nav>
      <div class="account"><span>${esc(user.username)}</span><b>${number(user.points)} 포인트</b>
        <button id="logout" class="quiet" ${busy ? "disabled" : ""}>로그아웃</button></div>
    </header>
    <section class="lobby-content" id="page-content"></section>`;
  root.querySelectorAll("[data-page]").forEach((button) => {
    button.onclick = () => { if (button.dataset.page !== page) openPage(button.dataset.page); };
  });
  root.querySelector("#logout").onclick = () => { session(null); user = null; authScreen(); };
  const content = root.querySelector("#page-content");
  ({ sortie: sortiePage, templates: templatePage, gacha: gachaPage, codex: codexPage })[page](content, message);
}

function sortiePage(content, message) {
  const selected = data.stages[stage];
  content.innerHTML = `
    <div class="page-title"><h1>작전 출격</h1><p>스테이지와 AI 난이도를 선택하세요.</p></div>
    <div class="sortie-layout">
      <div class="stage-list" aria-label="스테이지">
        ${Object.entries(data.stages).map(([id, entry]) => {
          const locked = Number(id) > user.max_stage;
          return `<button class="stage-card panel" data-stage="${Number(id)}" aria-pressed="${Number(id) === stage}" ${locked ? "disabled" : ""}>
            <span>${esc(entry.name)}</span><small>${locked ? "이전 스테이지 승리 후 해금" : Number(id) === stage ? "선택됨" : "출격 가능"}</small>
          </button>`;
        }).join("")}
      </div>
      <section class="sortie-settings panel">
        <h2>${esc(selected.name)}</h2>
        <p>적 본부를 점령하세요. 제한 시간 종료 시 영토가 더 넓은 진영이 승리합니다.</p>
        <fieldset><legend>AI 난이도</legend><div class="difficulty">
          ${["easy", "normal", "hard"].map((key) => `<button data-difficulty="${key}" aria-pressed="${difficulty === key}">${esc(data.difficulty[key].name)}</button>`).join("")}
        </div></fieldset>
        <p class="message" role="status">${esc(message)}</p>
        <button id="sortie" class="primary">출격</button>
      </section>
    </div>`;
  content.querySelectorAll("[data-stage]").forEach((button) => {
    button.onclick = () => { stage = Number(button.dataset.stage); sortiePage(content, ""); };
  });
  content.querySelectorAll("[data-difficulty]").forEach((button) => {
    button.onclick = () => { difficulty = button.dataset.difficulty; sortiePage(content, ""); };
  });
  content.querySelector("#sortie").onclick = startBattle;
}

function templatePage(content, message) {
  const rules = data.rules;
  const current = draft[activeTemplate];
  const unlocked = data.battalions.filter(({ id }) => Object.hasOwn(user.unlocks, id));
  content.innerHTML = `
    <div class="page-title"><h1>편제 편집기</h1><p>사단당 대대 1~${rules.max_battalions}개 · 같은 대대 최대 ${rules.max_duplicate}개</p></div>
    <div class="editor-layout">
      <section class="panel editor-sidebar"><h2>사단 편제 <small>${draft.length} / ${rules.max_templates}</small></h2>
        ${draft.map((entry, index) => `<button class="template-tab" aria-pressed="${index === activeTemplate}" data-template="${index}" ${busy ? "disabled" : ""}>${index + 1}. ${esc(entry.name) || "이름 없는 편제"}</button>`).join("")}
        <button id="add-template" ${busy || draft.length >= rules.max_templates ? "disabled" : ""}>편제 추가</button>
      </section>
      <section class="panel editor-detail">
        ${current ? `
          <label for="template-name">편제 이름</label><input id="template-name" required value="${esc(current.name)}" ${busy ? "disabled" : ""}>
          <h2>대대 구성 <small>${current.battalions.length} / ${rules.max_battalions}</small></h2>
          <div class="battalion-slots">${current.battalions.map((id, index) => `<div class="slot"><span>${esc(byId.get(id).name)}</span>
            <button data-remove="${index}" aria-label="${esc(byId.get(id).name)} 제거" ${busy ? "disabled" : ""}>제거</button></div>`).join("")}</div>
          <div class="add-battalion"><label for="battalion-choice">해금된 대대</label><select id="battalion-choice" ${busy ? "disabled" : ""}>
            ${unlocked.filter(({ id }) => current.battalions.filter((item) => item === id).length < rules.max_duplicate)
              .map(({ id, name }) => `<option value="${esc(id)}">${esc(name)} · 레벨 ${user.unlocks[id]}</option>`).join("")}
          </select><button id="add-battalion" ${busy || current.battalions.length >= rules.max_battalions ? "disabled" : ""}>대대 추가</button></div>
          <h2>사단 능력치 미리보기</h2>
          ${current.battalions.length ? statList(divisionStats(current.battalions, byId, user.unlocks, rules)) : "<p>대대를 추가하면 능력치가 표시됩니다.</p>"}
          <button id="remove-template" class="quiet danger" ${busy ? "disabled" : ""}>이 편제 삭제</button>
        ` : "<p>편제를 추가해 사단을 구성하세요.</p>"}
      </section>
    </div>
    <div class="page-actions"><p class="message" role="status">${esc(message)}</p><button id="save-templates" class="primary" ${busy ? "disabled" : ""}>편제 저장</button></div>`;
  const redraw = () => templatePage(content, "");
  content.querySelectorAll("[data-template]").forEach((button) => {
    button.onclick = () => { activeTemplate = Number(button.dataset.template); redraw(); };
  });
  content.querySelector("#add-template").onclick = () => {
    draft.push({ name: `새 사단 ${draft.length + 1}`, battalions: [unlocked[0].id] });
    activeTemplate = draft.length - 1;
    redraw();
  };
  if (current) {
    content.querySelector("#template-name").oninput = (event) => {
      current.name = event.target.value;
      content.querySelector(`[data-template="${activeTemplate}"]`).textContent = `${activeTemplate + 1}. ${current.name || "이름 없는 편제"}`;
    };
    content.querySelectorAll("[data-remove]").forEach((button) => {
      button.onclick = () => { current.battalions.splice(Number(button.dataset.remove), 1); redraw(); };
    });
    const choice = content.querySelector("#battalion-choice");
    const add = content.querySelector("#add-battalion");
    if (!choice.value) add.disabled = true;
    add.onclick = () => { current.battalions.push(choice.value); redraw(); };
    content.querySelector("#remove-template").onclick = () => {
      draft.splice(activeTemplate, 1);
      activeTemplate = Math.max(0, Math.min(activeTemplate, draft.length - 1));
      redraw();
    };
  }
  content.querySelector("#save-templates").onclick = async () => {
    const invalid = draft.findIndex(({ name, battalions }) => !name.trim() || !battalions.length);
    if (invalid >= 0) {
      activeTemplate = invalid;
      templatePage(content, "각 편제에 이름과 대대를 하나 이상 지정하세요.");
      return;
    }
    busy = true;
    lobby("편제 저장 중…");
    let status = "편제를 저장했습니다.";
    try {
      ({ user } = await api("templates", "PUT", { templates: draft.map(({ name, battalions }) => ({ name: name.trim(), battalions })) }));
      draft = user.templates.map(({ name, battalions }) => ({ name, battalions: [...battalions] }));
    } catch (error) {
      status = error.message;
    } finally {
      busy = false;
      lobby(status);
    }
  };
}

function gachaPage(content, message) {
  const { gacha_cost: cost, gacha_unlock_chance: chance, gacha_rarity_weight: weights } = data.rules;
  const total = Object.values(weights).reduce((sum, value) => sum + value, 0);
  const battalion = result && byId.get(result.battalion);
  content.innerHTML = `
    <div class="page-title"><h1>가챠</h1><p>포인트로 새 대대를 해금하거나 보유 대대를 강화하세요.</p></div>
    <div class="gacha-layout">
      <section class="panel gacha-odds"><h2>기본 확률</h2>
        <dl><div><dt>대대 해금</dt><dd>${percent(chance)}</dd></div><div><dt>대대 강화</dt><dd>${percent(1 - chance)}</dd></div></dl>
        <h3>해금 등급 가중치</h3><dl>${Object.entries(weights).map(([key, value]) =>
          `<div><dt>${rarityNames[key]}</dt><dd>${percent(value / total)}</dd></div>`).join("")}</dl>
        <p>해금·강화 가능한 대대 보유 현황에 따라 실제 결과가 달라질 수 있습니다.</p>
      </section>
      <section class="panel gacha-action">
        <div class="gacha-result ${result ? "reveal" : ""}" aria-live="polite">
          ${result ? `<div class="rarity ${esc(battalion.rarity)}">${result.kind === "unlock" ? "신규 해금" : "강화 완료"} · ${rarityNames[battalion.rarity]}</div>
            <h2>${esc(battalion.name)}</h2><p>레벨 ${result.level}</p>` : `<p>${busy ? "결과 확인 중…" : "획득 결과"}</p>`}
        </div>
        <p class="message" role="status">${esc(message || (user.points < cost ? "포인트가 부족합니다." : ""))}</p>
        <button id="pull" class="primary" ${busy || user.points < cost ? "disabled" : ""}>1회 뽑기 · ${number(cost)} 포인트</button>
      </section>
    </div>`;
  content.querySelector("#pull").onclick = async () => {
    busy = true;
    result = null;
    lobby();
    let status = "";
    try {
      ({ result, user } = await api("gacha/pull", "POST"));
    } catch (error) {
      status = error.message;
    } finally {
      busy = false;
      lobby(status);
    }
  };
}

function codexPage(content) {
  renderCodex(content, { data, byId, unlocks: user.unlocks, loadCatalog });
}

function loadCatalog() {
  codexRequest ??= api("codex").catch((error) => {
    codexRequest = undefined;
    throw error;
  });
  return codexRequest;
}

function startBattle() {
  if (!globalThis.PIXI || !globalThis.ms) {
    lobby("지도 라이브러리를 불러올 수 없습니다. 연결을 확인하고 새로고침해 주세요.");
    return;
  }
  let socket;
  let ended = false;
  const battle = createBattle(root, {
    data, stage, difficulty,
    send(message) { if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify(message)); },
    leave: async () => {
      battle.destroy();
      socket?.close();
      openPage("sortie");
    },
  });
  const url = new URL("/ws/game", location.href);
  url.protocol = location.protocol === "https:" ? "wss:" : "ws:";
  url.searchParams.set("token", token);
  try {
    socket = new WebSocket(url);
  } catch {
    battle.disconnect("작전 서버에 연결할 수 없습니다.");
    return;
  }
  socket.onopen = () => socket.send(JSON.stringify({ t: "start", stage, difficulty }));
  socket.onmessage = ({ data: payload }) => {
    let message;
    try {
      message = JSON.parse(payload);
      if (message.t === "end") {
        user = message.user;
        ended = true;
      }
      if (message.t === "error" && !battle.ready) battle.disconnect(message.msg);
      else battle.receive(message);
    } catch (error) {
      console.error(error);
      battle.disconnect("작전 화면을 불러오는 중 오류가 발생했습니다.");
      socket.close();
    }
  };
  socket.onclose = () => { if (!ended) battle.disconnect("작전 서버와 연결이 끊겼습니다."); };
}

async function boot() {
  if (!token) {
    authScreen();
    return;
  }
  try {
    await loadData();
    ({ user } = await api("me"));
    openPage("sortie");
  } catch (error) {
    if (error.status === 401 || error.status === 403) session(null);
    authScreen(false, error.message);
  }
}

boot();
