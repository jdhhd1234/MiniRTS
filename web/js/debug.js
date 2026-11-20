// 샌드박스 개발자 디버그 창: AI 의사결정, 서버 틱 프로파일, 클라이언트 프레임.
import { escapeHtml as esc, number, percent } from "./ui.js";

const sideNames = { 1: "청군 AI", 2: "적군 AI" };
const goalNames = { attack: "공격", defend: "방어" };

export function createDebugPanel(frame, { send, init }) {
  const panel = document.createElement("aside");
  panel.className = "debug-panel";
  panel.hidden = true;
  panel.setAttribute("aria-label", "개발자 디버그");
  panel.innerHTML = `
    <header><strong>개발자 디버그</strong>
      <div role="tablist">${[["ai", "AI 의사결정"], ["profile", "프로파일링"], ["client", "클라이언트"]].map(([key, label], index) =>
        `<button role="tab" data-tab="${key}" aria-selected="${index === 0}">${label}</button>`).join("")}</div>
      <button class="quiet" data-close aria-label="디버그 창 닫기">×</button></header>
    <div class="debug-body" role="tabpanel"><p>서버 보고를 기다리는 중… (1초마다 갱신)</p></div>`;
  frame.append(panel);

  const body = panel.querySelector(".debug-body");
  const bases = new Map(init.bases.map(({ id, name }) => [id, name]));
  let tab = "ai";
  let report;
  let units = new Map();
  const client = { frames: 0, fps: 0, since: performance.now(), states: [], raf: 0 };

  function label(key) {
    const [kind, what, id] = key.split(":");
    if (kind === "defend") return `${bases.get(Number(what)) ?? what} 기지 방어`;
    if (what === "base") return `${bases.get(Number(id)) ?? id} 기지 공격`;
    return `${units.get(Number(id)) ?? `사단 #${id}`} 공격`;
  }

  function bar(ratio, warn = false) {
    const width = Math.max(0, Math.min(1, ratio)) * 100;
    return `<span class="debug-bar${warn ? " warn" : ""}"><i style="width:${width}%"></i></span>`;
  }

  function scoredRows(entries, nameOf, withReason = false) {
    if (!entries.length) return "<p>없음</p>";
    return `<table class="debug-table"><thead><tr><th>선택지</th><th>점수</th>${withReason ? "<th>결과 · 사유</th>" : ""}<th>고려 사항</th></tr></thead><tbody>
      ${entries.map((entry, index) => `<tr${(withReason ? entry.chosen : index === 0) ? ' class="chosen"' : ""}><td>${esc(nameOf(entry.name))}</td>
        <td>${bar(entry.score)} ${entry.score.toFixed(3)}</td>
        ${withReason ? `<td>${entry.chosen ? "선택" : "미선택"} · ${esc(entry.reason)}</td>` : ""}
        <td>${Object.entries(entry.details).map(([key, value]) =>
          `<span class="chip${value === 0 ? " off" : ""}">${esc(key)} ${value.toFixed(2)}</span>`).join("")}</td></tr>`).join("")}
    </tbody></table>`;
  }

  function squadStatus(squad, strength, brain) {
    if (strength === null) return "";
    if (strength >= brain.overkill) return " · 여유 (예비대를 넘는 사단만 추가)";
    if (strength >= 1) return " · 충족";
    if (squad.patience_left > 0) return ` · 증원 대기 ${number(squad.patience_left)}초`;
    if (strength >= brain.min_commit) return " · 대기 끝, 부족해도 진격";
    return " · 포기 예정";
  }

  function squadCard(squad, brain) {
    const strength = squad.required ? squad.power / squad.required : null;
    return `<article class="debug-squad">
      <div><strong>분대 ${squad.id} · ${goalNames[squad.goal]}</strong> <span>${esc(label(squad.target))}</span></div>
      <p>점수 ${squad.score.toFixed(3)} · 편성 ${number(squad.age)}초 · 사단 ${squad.units.length}개 · 전력 ${number(squad.power)}${
        squad.required !== null ? ` / 필요 ${number(squad.required)} (${percent(strength)}) ${bar(strength / 2, strength < 1)}${squadStatus(squad, strength, brain)}` : ""}</p>
      <p class="facts">${Object.entries(squad.facts).map(([key, value]) =>
        `<span class="chip${value ? "" : " off"}">${esc(key)}</span>`).join("")}</p>
      <p><b>GOAP</b> ${squad.plan.length ? squad.plan.map(esc).join(" → ") : "계획 없음 (목표에 닿는 행동 순서가 없음)"}</p>
      <p><b>HTN</b> ${squad.trace.length ? squad.trace.map(esc).join(" · ") : "—"}</p>
      <p class="muted">${squad.units.map(({ name }) => esc(name)).join(", ")}</p>
    </article>`;
  }

  function aiTab() {
    return report.ai.map((brain) => `<section class="debug-section">
      <h3>${sideNames[brain.side]} <small>생각 ${number(brain.think_ms)}ms · 주기 ${brain.think_interval}초 · 다음 ${number(brain.next_think)}초</small></h3>
      <p>단계별(ms): ${Object.entries(brain.phases).map(([key, value]) => `${esc(key)} ${value}`).join(" · ")}</p>
      <p>자원 ${number(brain.resources)} · 전력 ${number(brain.own_power)} 대 적 ${number(brain.enemy_power)} · 가용 사단 ${brain.free_units} · 위협 기지 ${brain.threats}</p>
      <p>성향: ${Object.entries(brain.profile).map(([key, value]) => `${key} ${value}`).join(" · ")}</p>
      <h4>Utility · 목표 순위 (상위 10)</h4>${scoredRows(brain.goals, label, true)}
      ${brain.shelved.length ? `<p>보류 목표: ${brain.shelved.map((item) => `${esc(label(item.target))} (${esc(item.reason)}, ${number(item.left)}초)`).join(" · ")}</p>` : ""}
      <h4>Utility · 생산 순위</h4>${scoredRows(brain.builds, (name) => name)}
      <h4>분대 (GOAP 계획 · HTN 분해)</h4>${brain.squads.length ? brain.squads.map((squad) => squadCard(squad, brain)).join("") : "<p>분대 없음</p>"}
    </section>`).join("");
  }

  function profileTab() {
    const { profile, loop, path_cache: cache, counts } = report;
    const over = profile.tick_max_ms > profile.budget_ms;
    const lookups = cache.hits + cache.misses;
    return `<section class="debug-section">
      <h3>서버 틱 <small>최근 1초 · ${profile.ticks}틱 · ${report.speed}×</small></h3>
      <p>평균 ${profile.tick_avg_ms}ms · 최대 ${profile.tick_max_ms}ms · 예산 ${profile.budget_ms}ms/틱 ${bar(profile.tick_max_ms / profile.budget_ms, over)}</p>
      <p>루프 지연 ${loop.lag_ms ?? 0}ms · 따라잡기 포기 ${loop.lag_resets ?? 0}회 · 받은 명령 ${loop.commands ?? 0}개</p>
      <table class="debug-table"><thead><tr><th>구간</th><th>틱 대비</th><th>합계</th><th>평균</th><th>최대</th><th>호출</th></tr></thead><tbody>
        ${profile.sections.map((section) => {
          const nested = section.name.includes("/");
          return `<tr class="${nested ? "nested" : ""}"><td>${esc(section.name)}</td><td>${bar(section.share)} ${percent(section.share)}</td>
            <td>${section.total_ms}ms</td><td>${section.avg_ms}ms</td><td>${section.max_ms}ms</td><td>${number(section.calls)}</td></tr>`;
        }).join("")}
      </tbody></table>
      <p class="muted">들여쓴 구간(ai1/…, ai2/…, lua/…)은 상위 구간(ai1, ai2, combat)에 포함된 세부 시간입니다.</p>
      <h3>경로 캐시 · 개체</h3>
      <p>적중 ${number(cache.hits)} / ${number(lookups)} (${lookups ? percent(cache.hits / lookups) : "—"}) · 크기 ${number(cache.size)} / ${number(cache.max)}</p>
      <p>사단 청군 ${counts.units_1} · 적군 ${counts.units_2} · 생산 대기 ${counts.queue_1} / ${counts.queue_2}</p>
    </section>`;
  }

  function clientTab() {
    const gaps = [];
    for (let i = 1; i < client.states.length; i++) gaps.push(client.states[i] - client.states[i - 1]);
    const average = gaps.length ? gaps.reduce((sum, value) => sum + value, 0) / gaps.length : 0;
    return `<section class="debug-section"><h3>클라이언트</h3>
      <p>렌더링 ${number(client.fps)} FPS · 상태 수신 간격 평균 ${number(average)}ms (목표 250ms) · 최대 ${number(gaps.length ? Math.max(...gaps) : 0)}ms</p>
      <p>지도 사단 ${number(units.size)}개</p></section>`;
  }

  function render() {
    if (panel.hidden) return;
    if (tab === "client") body.innerHTML = clientTab();
    else if (report) body.innerHTML = tab === "ai" ? aiTab() : profileTab();
  }

  function frameLoop(now) {
    client.frames++;
    if (now - client.since >= 1000) {
      client.fps = client.frames * 1000 / (now - client.since);
      client.frames = 0;
      client.since = now;
      if (tab === "client") render();
    }
    client.raf = requestAnimationFrame(frameLoop);
  }

  function toggle(open = panel.hidden) {
    panel.hidden = !open;
    send({ t: "debug", on: open });
    cancelAnimationFrame(client.raf);
    if (open) {
      client.since = performance.now();
      client.frames = 0;
      client.raf = requestAnimationFrame(frameLoop);
      render();
    }
  }

  panel.querySelector("[data-close]").onclick = () => toggle(false);
  panel.querySelectorAll("[data-tab]").forEach((button) => {
    button.onclick = () => {
      tab = button.dataset.tab;
      panel.querySelectorAll("[data-tab]").forEach((other) => other.setAttribute("aria-selected", String(other === button)));
      render();
    };
  });
  const keys = new AbortController();
  window.addEventListener("keydown", (event) => {
    if (event.key === "F2") {
      event.preventDefault();
      toggle();
    }
  }, { signal: keys.signal });

  return {
    toggle,
    receive(message) {
      report = message;
      render();
    },
    state(state) {
      units = new Map(state.units.map(({ id, name }) => [id, name]));
      client.states.push(performance.now());
      if (client.states.length > 20) client.states.shift();
    },
    destroy() {
      keys.abort();
      cancelAnimationFrame(client.raf);
      panel.remove();
    },
  };
}
