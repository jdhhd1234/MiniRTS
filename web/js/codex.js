import { escapeHtml as esc, rarityNames, leveledStats, statList } from "./ui.js";

const tabs = [
  { id: "battalions", label: "대대" },
  { id: "equipment", label: "장비" },
  { id: "parts", label: "구성품" },
];

function selectFilter(key, label, options) {
  return `<label>${label}<select data-filter="${key}"><option value="">전체</option>
    ${options.map(([id, name]) => `<option value="${esc(id)}">${esc(name)}</option>`).join("")}</select></label>`;
}

function searchFilter(placeholder) {
  return `<label class="codex-search">검색<input data-filter="query" type="search" placeholder="${placeholder}"></label>`;
}

const normalize = (text) => text.normalize("NFKC").toLocaleLowerCase("ko-KR");

function matches(query, ...fields) {
  const terms = normalize(query).trim().split(/\s+/).filter(Boolean);
  if (!terms.length) return true;
  const haystack = normalize(fields.join(" "));
  return terms.every((term) => haystack.includes(term));
}

export function renderCodex(content, { data, byId, unlocks, loadCatalog }) {
  let activeTab = "battalions";
  let catalog;
  let failed = false;
  const equipmentFilters = { category: "", battalion: "", query: "" };
  const partFilters = { category: "", tag: "", query: "" };
  const expanded = new Set();
  const equipmentByBattalion = new Map();

  content.innerHTML = `
    <div class="page-title"><h1>대대 도감</h1></div>
    <div class="codex-tabs" role="tablist" aria-label="도감 분류">
      ${tabs.map(({ id, label }) => `<button type="button" id="codex-tab-${id}" role="tab" data-codex-tab="${id}"
        aria-controls="codex-panel-${id}">${label} <span data-tab-count></span></button>`).join("")}
    </div>
    ${tabs.map(({ id }) => `<section id="codex-panel-${id}" role="tabpanel" aria-labelledby="codex-tab-${id}"></section>`).join("")}`;

  const tabList = content.querySelector(".codex-tabs");
  const buttons = [...tabList.querySelectorAll("button")];
  const panels = new Map(tabs.map(({ id }) => [id, content.querySelector(`#codex-panel-${id}`)]));

  function showTab(next, focus = false) {
    if (activeTab === "battalions") {
      expanded.clear();
      panels.get("battalions").querySelectorAll("details[open]").forEach(({ dataset }) => expanded.add(dataset.detail));
    }
    activeTab = next;
    const totals = {
      battalions: data.battalions.length, equipment: catalog?.equipment.length ?? 70, parts: catalog?.parts.length ?? 110,
    };
    for (const button of buttons) {
      const id = button.dataset.codexTab;
      const current = id === activeTab;
      button.setAttribute("aria-selected", String(current));
      button.tabIndex = current ? 0 : -1;
      button.querySelector("[data-tab-count]").textContent = totals[id];
      panels.get(id).hidden = !current;
      if (current && focus) button.focus();
    }
    const panel = panels.get(activeTab);
    if (activeTab === "battalions") showBattalions(panel);
    else if (!catalog) {
      panel.innerHTML = failed
        ? '<div class="panel codex-notice"><p role="status">장비·구성품 도감을 불러올 수 없습니다.</p><button type="button">다시 시도</button></div>'
        : '<p class="panel codex-notice" role="status">도감 자료를 불러오는 중…</p>';
      if (failed) panel.querySelector("button").onclick = fetchCatalog;
    } else if (activeTab === "equipment") showEquipment(panel);
    else showParts(panel);
  }

  function showBattalions(panel) {
    panel.innerHTML = `
      <p class="codex-summary">${Object.keys(unlocks).length} / ${data.battalions.length}종 해금</p>
      <div class="codex-grid">${data.battalions.map((battalion) => {
        const level = unlocks[battalion.id];
        const effect = data.effects[battalion.effect];
        const equipment = equipmentByBattalion.get(battalion.id) ?? [];
        const equipmentList = equipment.length
          ? `<ul>${equipment.map(({ id, name }) => `<li><button type="button" class="quiet" data-linked-battalion="${esc(battalion.id)}">
              <span class="codex-id">${esc(id)}</span> ${esc(name)}</button></li>`).join("")}</ul>`
          : `<p>${catalog ? "연결된 장비가 없습니다." : failed ? "장비 목록을 불러올 수 없습니다." : "장비 목록을 불러오는 중…"}</p>`;
        return `<article class="panel codex-card ${level ? "" : "locked"}"><div class="codex-meta">
          <span class="rarity ${esc(battalion.rarity)}">${rarityNames[battalion.rarity]}</span>
          <span>${level ? `레벨 ${level} / ${data.rules.max_level}` : "잠김"}</span></div>
          <h2>${esc(battalion.name)}</h2>${statList(leveledStats(battalion, level ?? 1, data.rules))}
          <p>${effect ? esc(effect) : "특수 효과 없음"}</p>
          <details class="codex-equipment-links" data-detail="${esc(battalion.id)}" ${expanded.has(battalion.id) ? "open" : ""}>
            <summary>대표 장비${catalog ? ` <small>${equipment.length}종</small>` : ""}</summary>
            ${equipmentList}
            ${failed ? '<button type="button" class="quiet" data-codex-retry>다시 시도</button>' : ""}
          </details></article>`;
      }).join("")}</div>`;
    for (const button of panel.querySelectorAll("[data-linked-battalion]")) {
      button.onclick = () => {
        Object.assign(equipmentFilters, { category: "", battalion: button.dataset.linkedBattalion, query: "" });
        showTab("equipment", true);
      };
    }
    for (const button of panel.querySelectorAll("[data-codex-retry]")) button.onclick = fetchCatalog;
  }

  function showFilters(panel, markup, filters, renderResults) {
    panel.innerHTML = `<div class="panel codex-filters">${markup}</div>
      <p class="codex-summary" role="status"></p><div class="codex-results"></div>`;
    const summary = panel.querySelector(".codex-summary");
    const results = panel.querySelector(".codex-results");
    const redraw = () => renderResults(summary, results);
    for (const input of panel.querySelectorAll("[data-filter]")) {
      const key = input.dataset.filter;
      input.value = filters[key];
      input.addEventListener(key === "query" ? "input" : "change", () => {
        filters[key] = input.value;
        redraw();
      });
    }
    redraw();
  }

  function showEquipment(panel) {
    const categories = new Map(catalog.categories.filter(({ id }) => id !== "S").map(({ id, name }) => [id, name]));
    const categoryOptions = [...categories].map(([id, name]) => [id, `${id} · ${name}`]);
    const battalionOptions = data.battalions.map(({ id, name }) => [id, name]);
    showFilters(panel, selectFilter("category", "분류", categoryOptions)
      + selectFilter("battalion", "연결 대대", battalionOptions) + searchFilter("장비 이름, ID, 역할, 구성품"),
    equipmentFilters, (summary, results) => {
      const { category, battalion, query } = equipmentFilters;
      const equipment = catalog.equipment.filter((item) => (!category || item.category === category)
        && (!battalion || item.battalion === battalion)
        && matches(query, item.id, item.name, item.role, categories.get(item.category), byId.get(item.battalion).name,
          item.axis, item.scope, ...item.parts));
      summary.textContent = `${equipment.length} / ${catalog.equipment.length}종 장비`;
      results.innerHTML = equipment.length
        ? `<div class="codex-grid">${equipment.map((item) => equipmentCard(item, categories.get(item.category))).join("")}</div>`
        : '<p class="panel codex-notice">조건에 맞는 장비가 없습니다.</p>';
    });
  }

  function equipmentCard(item, category) {
    const battalion = byId.get(item.battalion);
    const unlocked = Object.hasOwn(unlocks, item.battalion);
    return `<article class="panel codex-card equipment-card">
      <div class="codex-meta"><span class="codex-id">${esc(item.id)}</span><span>${esc(category)}</span></div>
      <h2>${esc(item.name)}</h2>
      <dl class="equipment-fields">
        <div><dt>역할</dt><dd>${esc(item.role)}</dd></div>
        <div><dt>연결 대대</dt><dd>${esc(battalion.name)} <span class="codex-unlock ${unlocked ? "unlocked" : ""}">${unlocked ? "해금됨" : "잠김"}</span></dd></div>
        <div><dt>주요 구성품</dt><dd>${item.parts.map(esc).join(" · ")}</dd></div>
        <div><dt>비교 축</dt><dd>${esc(item.axis)}</dd></div>
        <div><dt>적용 범위</dt><dd>${esc(item.scope)}</dd></div>
      </dl>
      <div class="equipment-reference"><h3>연결 대대 Lv.1 참조</h3>${statList(leveledStats(battalion, 1, data.rules))}</div>
    </article>`;
  }

  function showParts(panel) {
    const categories = new Map(catalog.categories.map(({ id, name }) => [id, name]));
    const categoryOptions = [...categories].map(([id, name]) => [id, `${id} · ${name}`]);
    showFilters(panel, selectFilter("category", "분류", categoryOptions)
      + selectFilter("tag", "소재 태그", catalog.tags.map((tag) => [tag, tag])) + searchFilter("구성품 이름, ID, 설명"),
    partFilters, (summary, results) => {
      const { category, tag, query } = partFilters;
      const parts = catalog.parts.filter((item) => (!category || item.category === category)
        && (!tag || item.tag === tag)
        && matches(query, item.id, item.name, item.tag, item.field, item.desc, categories.get(item.category)));
      summary.textContent = `${parts.length} / ${catalog.parts.length}종 구성품`;
      results.innerHTML = parts.length ? `
        <div class="panel codex-table-scroll"><table class="codex-table" aria-label="구성품 목록">
          <thead><tr><th scope="col">ID</th><th scope="col">이름</th><th scope="col">소재 태그</th><th scope="col">비교 필드</th><th scope="col">설명</th></tr></thead>
          <tbody>${parts.map(({ id, name, tag, field, desc }) => `<tr>
            <td class="codex-id">${esc(id)}</td><th scope="row">${esc(name)}</th>
            <td>${esc(tag)}</td><td>${esc(field)}</td><td>${esc(desc)}</td></tr>`).join("")}</tbody>
        </table></div>` : '<p class="panel codex-notice">조건에 맞는 구성품이 없습니다.</p>';
    });
  }

  async function fetchCatalog() {
    failed = false;
    showTab(activeTab);
    try {
      catalog = await loadCatalog();
      for (const item of catalog.equipment) {
        if (!equipmentByBattalion.has(item.battalion)) equipmentByBattalion.set(item.battalion, []);
        equipmentByBattalion.get(item.battalion).push(item);
      }
    } catch {
      failed = true;
    }
    if (content.isConnected) showTab(activeTab);
  }

  buttons.forEach((button) => { button.onclick = () => showTab(button.dataset.codexTab); });
  tabList.onkeydown = (event) => {
    const index = buttons.indexOf(event.target);
    if (index < 0) return;
    let next;
    if (event.key === "ArrowRight") next = (index + 1) % buttons.length;
    else if (event.key === "ArrowLeft") next = (index + buttons.length - 1) % buttons.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = buttons.length - 1;
    else return;
    event.preventDefault();
    showTab(buttons[next].dataset.codexTab, true);
  };
  fetchCatalog();
}
