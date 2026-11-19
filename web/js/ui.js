export const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
})[char]);

export const number = (value) => value.toLocaleString("ko-KR", { maximumFractionDigits: 2 });
export const percent = (ratio) => `${number(ratio * 100)}%`;
export const rarityNames = { base: "기본", common: "일반", rare: "희귀", epic: "특급" };
export const statLabels = { atk: "공격력", dfn: "방어력", hp: "체력", spd: "이동속도", rng: "사거리", cost: "생산 비용" };
const statUnits = { spd: " km/초", rng: " km" };

export function leveledStats(battalion, level, rules) {
  const bonus = 1 + (level - 1) * rules.level_bonus;
  return {
    atk: battalion.atk * bonus, dfn: battalion.dfn * bonus, hp: battalion.hp * bonus,
    spd: battalion.spd, rng: battalion.rng, cost: battalion.cost,
  };
}

export function divisionStats(ids, byId, unlocks, rules) {
  return ids.map((id) => leveledStats(byId.get(id), unlocks[id], rules)).reduce((sum, stats) => ({
    atk: sum.atk + stats.atk, dfn: sum.dfn + stats.dfn, hp: sum.hp + stats.hp,
    spd: Math.min(sum.spd, stats.spd), rng: Math.max(sum.rng, stats.rng), cost: sum.cost + stats.cost,
  }), { atk: 0, dfn: 0, hp: 0, spd: Infinity, rng: 0, cost: 0 });
}

export function statList(stats) {
  return `<dl class="stat-list">${Object.entries(stats).map(([key, value]) =>
    `<div><dt>${statLabels[key]}</dt><dd>${number(value)}${statUnits[key] ?? ""}</dd></div>`).join("")}</dl>`;
}
