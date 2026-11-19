import { hexAt, hexCenter, makeGrid, WORLD_SIZE, R } from "./hex.js";

export const terrainColors = [0x293e48, 0xa2a187, 0x67795b, 0x88847d, 0x678b98, 0xa99b8b, 0x788574, 0xb1a07c, 0x8e8d73];
const sideColors = [0, 0x4b9fe1, 0xc95850];
const weatherColors = [0, 0x3983bd, 0xe6f2fa, 0xc8cfba];
const weatherMarks = ["", "╱╱", "✦", "≋"];
const baseEchelon = { hq: "K", corps: "J", div: "I" };
const panKeys = new Set(["KeyW", "KeyA", "KeyS", "KeyD", "ArrowUp", "ArrowLeft", "ArrowDown", "ArrowRight"]);
const TERRITORY_INTERVAL = 140;
const INTERPOLATION_MS = 250;
const CHUNK_SIZE = 12;
const clamp = (value, low, high) => Math.max(low, Math.min(high, value));

function areaOf(a, b) {
  return { left: Math.min(a.x, b.x), right: Math.max(a.x, b.x), top: Math.min(a.y, b.y), bottom: Math.max(a.y, b.y) };
}

export function createMap(host, init, options) {
  const P = globalThis.PIXI;
  const app = new P.Application({
    backgroundColor: 0x1c2b32, antialias: true,
    resolution: Math.min(devicePixelRatio || 1, 2), autoDensity: true,
  });
  const canvas = app.view;
  canvas.setAttribute("aria-label", "작전 지도");
  canvas.tabIndex = 0;
  host.append(canvas);

  const world = new P.Container();
  const territoryLayer = new P.Container();
  const frontLayer = new P.Container();
  const weatherLayer = new P.Graphics();
  const tacticalLines = new P.Graphics();
  const weatherLabels = new P.Container();
  const cityLabels = new P.Container();
  const baseLayer = new P.Container();
  const unitLayer = new P.Container();
  const box = new P.Graphics();
  app.stage.addChild(world, weatherLabels, cityLabels, tacticalLines, baseLayer, unitLayer, box);

  const { cols, rows, terrain, you } = init;
  const owner = Uint8Array.from(init.owner);
  const grid = makeGrid(cols, rows);
  const mapWidth = cols * 20 + 10;
  const mapHeight = (rows - 1) * 1.5 * R + 2 * R;
  const terrainGraphics = new P.Graphics();
  terrainGraphics.lineStyle(0.65, 0x263637, 0.3);
  for (const hex of grid) {
    terrainGraphics.beginFill(terrainColors[terrain[hex.index]] ?? terrainColors[1]).drawPolygon(hex.polygon).endFill();
  }
  const groundTexture = P.RenderTexture.create({ width: Math.ceil(mapWidth), height: Math.ceil(mapHeight) });
  app.renderer.render(terrainGraphics, { renderTexture: groundTexture });
  terrainGraphics.destroy();
  world.addChild(new P.Sprite(groundTexture), territoryLayer, weatherLayer, frontLayer);

  const chunksAcross = Math.ceil(cols / CHUNK_SIZE);
  const chunks = new Map();
  const dirtyChunks = new Set();
  let lastTerritoryPaint = 0;
  for (const hex of grid) {
    const key = Math.floor(hex.row / CHUNK_SIZE) * chunksAcross + Math.floor(hex.col / CHUNK_SIZE);
    hex.chunk = key;
    if (!chunks.has(key)) {
      const fill = new P.Graphics();
      const front = new P.Graphics();
      territoryLayer.addChild(fill);
      frontLayer.addChild(front);
      chunks.set(key, { fill, front, hexes: [] });
      dirtyChunks.add(key);
    }
    chunks.get(key).hexes.push(hex);
  }

  function paintTerritory(now) {
    for (const key of dirtyChunks) {
      const { fill, front, hexes } = chunks.get(key);
      fill.clear();
      front.clear();
      front.lineStyle(4, 0x422c28, 0.93);
      for (const { index, polygon, neighbors } of hexes) {
        const side = owner[index];
        if (!side) continue;
        fill.beginFill(sideColors[side], 0.25).drawPolygon(polygon).endFill();
        neighbors.forEach((neighbor, k) => {
          if (neighbor <= index || !owner[neighbor] || owner[neighbor] === side) return;
          const next = (k + 1) % 6;
          front.moveTo(polygon[k * 2], polygon[k * 2 + 1]).lineTo(polygon[next * 2], polygon[next * 2 + 1]);
        });
      }
    }
    dirtyChunks.clear();
    lastTerritoryPaint = now;
  }
  paintTerritory(performance.now());

  const labelStyle = new P.TextStyle({
    fontFamily: '"Malgun Gothic", sans-serif', fontSize: 12, fontWeight: "bold",
    fill: 0xf4f1e3, stroke: 0x283432, strokeThickness: 3,
  });
  const cities = init.cities.map(({ name, col, row }) => {
    const label = new P.Text(name, labelStyle);
    label.anchor.set(0.5, 0);
    cityLabels.addChild(label);
    const hasBase = init.bases.some((base) => base.col === col && base.row === row);
    return { ...hexCenter(col, row), label, offset: hasBase ? 18 : 3 };
  });
  let weather = [];
  let regions = [];

  function paintWeather(next) {
    weather = [...next];
    weatherLayer.clear();
    weatherLabels.removeChildren().forEach((label) => label.destroy());
    const count = init.weather_cells;
    const size = WORLD_SIZE / count;
    regions = weather.flatMap((id, index) => {
      const x = (index % count) * size;
      const y = Math.floor(index / count) * size;
      if (!id) return [];
      weatherLayer.lineStyle(1, weatherColors[id], 0.28);
      weatherLayer.beginFill(weatherColors[id], 0.13).drawRect(x, y, size, size).endFill();
      const label = new P.Text(`${weatherMarks[id]}  ${options.weatherNames.get(id) ?? ""}`, labelStyle);
      label.alpha = 0.86;
      weatherLabels.addChild(label);
      return [{ x, y, label }];
    });
    sceneDirty = true;
  }

  const textures = new Map();
  const bases = new Map();
  const units = new Map();
  let selected = new Set();
  let active = true;
  let sceneDirty = true;

  function symbolTexture(sidc) {
    if (!textures.has(sidc)) {
      const symbol = new globalThis.ms.Symbol(sidc, { size: 56 });
      textures.set(sidc, P.Texture.from(symbol.asCanvas()));
    }
    return textures.get(sidc);
  }

  function updateMarker(marker, record) {
    const affiliation = record.side === 1 ? "F" : "H";
    const sidc = record.kind
      ? `S${affiliation}GPU-----A${baseEchelon[record.kind]}---`
      : `S${affiliation}GP${record.fn}-I---`;
    if (sidc !== marker.sidc) {
      marker.sprite.texture = symbolTexture(sidc);
      const size = record.kind === "hq" ? 39 : record.kind ? 33 : 35;
      const { width, height } = marker.sprite.texture.orig;
      marker.sprite.scale.set(size / Math.max(width, height));
      marker.sidc = sidc;
    }
    if (record.hp !== marker.record?.hp || record.mhp !== marker.record?.mhp || record.side !== marker.record?.side) {
      marker.health.clear().beginFill(0x1c2829).drawRect(-17, 11, 34, 4).endFill();
      marker.health.beginFill(sideColors[record.side]).drawRect(-16, 12, 32 * clamp(record.hp / record.mhp, 0, 1), 2).endFill();
    }
    marker.record = record;
  }

  function addMarker(record, layer) {
    const container = new P.Container();
    const sprite = new P.Sprite();
    sprite.anchor.set(0.5, 0.55);
    sprite.y = -4;
    const health = new P.Graphics();
    const highlight = new P.Graphics();
    highlight.lineStyle(1.6, 0xffdf8b, 1).beginFill(0xffdf8b, 0.12).drawRoundedRect(-21, -25, 42, 44, 3).endFill();
    highlight.visible = false;
    container.addChild(highlight, sprite, health);
    layer.addChild(container);
    const marker = { container, sprite, health, highlight, x: record.x, y: record.y, fromX: record.x, fromY: record.y, since: 0 };
    updateMarker(marker, record);
    return marker;
  }

  for (const base of init.bases) bases.set(base.id, addMarker(base, baseLayer));
  paintWeather(init.weather);

  const camera = { x: 0, y: 0, zoom: 1, minZoom: 1 };
  let viewport = { left: 0, top: 0, width: 1, height: 1 };
  let pointer = null;
  const keys = new Set();
  const listeners = new AbortController();
  const { signal } = listeners;

  function screenPoint(x, y) {
    return { x: x * camera.zoom + camera.x, y: y * camera.zoom + camera.y };
  }

  function constrainCamera() {
    const margin = 34;
    const minX = viewport.left + viewport.width - mapWidth * camera.zoom - margin;
    const minY = viewport.top + viewport.height - mapHeight * camera.zoom - margin;
    const maxX = viewport.left + margin;
    const maxY = viewport.top + margin;
    camera.x = minX > maxX ? (minX + maxX) / 2 : clamp(camera.x, minX, maxX);
    camera.y = minY > maxY ? (minY + maxY) / 2 : clamp(camera.y, minY, maxY);
    sceneDirty = true;
  }

  function resize(initial = false) {
    const wx = (viewport.left + viewport.width / 2 - camera.x) / camera.zoom;
    const wy = (viewport.top + viewport.height / 2 - camera.y) / camera.zoom;
    const { clientWidth: width, clientHeight: height } = host;
    app.renderer.resize(width, height);
    const sides = Math.min(274, width * 0.22);
    viewport = { left: sides, top: 90, width: width - 2 * sides, height: Math.max(1, height - 145) };
    const fit = Math.min(viewport.width / mapWidth, viewport.height / mapHeight);
    camera.minZoom = fit * 0.8;
    camera.zoom = initial ? fit : Math.max(camera.minZoom, camera.zoom);
    camera.x = viewport.left + viewport.width / 2 - (initial ? mapWidth / 2 : wx) * camera.zoom;
    camera.y = viewport.top + viewport.height / 2 - (initial ? mapHeight / 2 : wy) * camera.zoom;
    constrainCamera();
  }
  resize(true);
  const observer = new ResizeObserver(() => resize());
  observer.observe(host);

  function positionMarker(marker, x, y) {
    const point = screenPoint(x, y);
    marker.container.position.set(point.x, point.y);
  }

  function setSelection(next) {
    selected = next;
    for (const [id, marker] of units) marker.highlight.visible = selected.has(id);
    options.onSelection(selected);
    sceneDirty = true;
  }

  function ownIds() {
    return [...selected].filter((id) => units.get(id)?.record.side === you);
  }

  function pointFromEvent(event) {
    const bounds = canvas.getBoundingClientRect();
    return { x: event.clientX - bounds.left, y: event.clientY - bounds.top };
  }

  function clearPointer() {
    pointer = null;
    canvas.classList.remove("panning");
    box.clear();
  }

  canvas.addEventListener("pointerdown", (event) => {
    if (!active || pointer || (event.button !== 0 && event.button !== 2)) return;
    const start = pointFromEvent(event);
    pointer = { id: event.pointerId, button: event.button, start, last: start, dragged: false };
    canvas.focus({ preventScroll: true });
    canvas.setPointerCapture(event.pointerId);
  }, { signal });

  canvas.addEventListener("pointermove", (event) => {
    if (pointer?.id !== event.pointerId) return;
    const point = pointFromEvent(event);
    const crossedThreshold = Math.hypot(point.x - pointer.start.x, point.y - pointer.start.y) >= 5;
    if (!pointer.dragged && !crossedThreshold) return;
    pointer.dragged = true;
    if (pointer.button === 2) {
      camera.x += point.x - pointer.last.x;
      camera.y += point.y - pointer.last.y;
      canvas.classList.add("panning");
      constrainCamera();
    } else {
      const { left, top, right, bottom } = areaOf(pointer.start, point);
      box.clear().lineStyle(1, 0xffdf8b, 0.95).beginFill(0xffdf8b, 0.15)
        .drawRect(left, top, right - left, bottom - top).endFill();
    }
    pointer.last = point;
  }, { signal });

  canvas.addEventListener("pointerup", (event) => {
    if (pointer?.id !== event.pointerId || event.button !== pointer.button) return;
    const point = pointFromEvent(event);
    const { button, dragged, start } = pointer;
    clearPointer();
    if (button === 2) {
      if (dragged) return;
      const { col, row } = hexAt((point.x - camera.x) / camera.zoom, (point.y - camera.y) / camera.zoom);
      const ids = ownIds();
      if (ids.length && col >= 0 && col < cols && row >= 0 && row < rows && terrain[row * cols + col] !== 0) {
        options.send({ t: "move", ids, col, row });
      }
      return;
    }
    const next = event.shiftKey ? new Set(selected) : new Set();
    if (dragged) {
      const { left, top, right, bottom } = areaOf(start, point);
      for (const [id, marker] of units) {
        const { x, y } = marker.container.position;
        if (marker.record.side === you && x >= left && x <= right && y >= top && y <= bottom) next.add(id);
      }
    } else {
      const ordered = [...units].reverse();
      const hit = ordered.find(([, marker]) => {
        const { x, y } = marker.container.position;
        return marker.record.side === you && Math.abs(point.x - x) <= 20 && point.y >= y - 24 && point.y <= y + 16;
      });
      if (hit) next.add(hit[0]);
    }
    setSelection(next);
  }, { signal });

  canvas.addEventListener("contextmenu", (event) => event.preventDefault(), { signal });
  canvas.addEventListener("pointercancel", clearPointer, { signal });
  canvas.addEventListener("lostpointercapture", clearPointer, { signal });
  canvas.addEventListener("wheel", (event) => {
    event.preventDefault();
    if (!active) return;
    const { x, y } = pointFromEvent(event);
    const delta = event.deltaMode === 1 ? event.deltaY * 16 : event.deltaY;
    const zoom = clamp(camera.zoom * Math.exp(-delta * 0.0017), camera.minZoom, 3);
    const ratio = zoom / camera.zoom;
    camera.x = x - (x - camera.x) * ratio;
    camera.y = y - (y - camera.y) * ratio;
    camera.zoom = zoom;
    constrainCamera();
  }, { passive: false, signal });

  window.addEventListener("keydown", (event) => {
    if (!active || event.ctrlKey || event.metaKey || event.altKey || event.target.closest?.("input, select, textarea, [contenteditable]")) return;
    if (panKeys.has(event.code)) {
      event.preventDefault();
      keys.add(event.code);
    } else if (!event.repeat && event.code === "KeyR") {
      const ids = ownIds();
      if (ids.length) options.send({ t: "retreat", ids });
    } else if (!event.repeat && /^(Digit|Numpad)[1-6]$/.test(event.code)) {
      options.onBuild(Number(event.code.at(-1)) - 1);
    }
  }, { signal });
  window.addEventListener("keyup", (event) => keys.delete(event.code), { signal });
  window.addEventListener("blur", () => keys.clear(), { signal });

  function interpolate(marker, now) {
    const t = clamp((now - marker.since) / INTERPOLATION_MS, 0, 1);
    marker.x = marker.fromX + (marker.record.x - marker.fromX) * t;
    marker.y = marker.fromY + (marker.record.y - marker.fromY) * t;
    return t < 1 && (marker.fromX !== marker.record.x || marker.fromY !== marker.record.y);
  }

  function drawTacticalLine(from, target, attack, side) {
    const to = screenPoint(...target);
    if (attack) {
      tacticalLines.lineStyle(1.4, side === you ? 0xffe4a1 : 0xff8d76, 0.76).moveTo(from.x, from.y).lineTo(to.x, to.y);
      return;
    }
    const dx = to.x - from.x;
    const dy = to.y - from.y;
    const length = Math.hypot(dx, dy);
    if (length < 12) return;
    tacticalLines.lineStyle(1.4, 0xc6edff, 0.75);
    for (let i = 0; i < length; i += 13) {
      const start = i / length;
      const end = Math.min(i + 7, length) / length;
      tacticalLines.moveTo(from.x + dx * start, from.y + dy * start).lineTo(from.x + dx * end, from.y + dy * end);
    }
    tacticalLines.drawCircle(to.x, to.y, 4);
  }

  function drawScene(now) {
    sceneDirty = false;
    world.position.set(camera.x, camera.y);
    world.scale.set(camera.zoom);
    for (const city of cities) {
      const { x, y } = screenPoint(city.x, city.y);
      city.label.position.set(x, y + city.offset);
    }
    for (const region of regions) {
      const { x, y } = screenPoint(region.x, region.y);
      region.label.position.set(x + 6, y + 5);
    }
    for (const marker of bases.values()) positionMarker(marker, marker.x, marker.y);
    tacticalLines.clear();
    for (const marker of units.values()) {
      if (interpolate(marker, now)) sceneDirty = true;
      positionMarker(marker, marker.x, marker.y);
      const { tgt, dest, side } = marker.record;
      if (dest && side === you) drawTacticalLine(marker.container.position, dest, false, side);
      if (tgt) drawTacticalLine(marker.container.position, tgt, true, side);
    }
  }

  function tick() {
    const now = performance.now();
    const horizontal = Number(keys.has("KeyA") || keys.has("ArrowLeft")) - Number(keys.has("KeyD") || keys.has("ArrowRight"));
    const vertical = Number(keys.has("KeyW") || keys.has("ArrowUp")) - Number(keys.has("KeyS") || keys.has("ArrowDown"));
    if (horizontal || vertical) {
      const distance = Math.min(app.ticker.deltaMS, 40) * 0.55 / Math.hypot(horizontal, vertical);
      camera.x += horizontal * distance;
      camera.y += vertical * distance;
      constrainCamera();
    }
    if (dirtyChunks.size && now - lastTerritoryPaint >= TERRITORY_INTERVAL) paintTerritory(now);
    if (sceneDirty) drawScene(now);
  }
  app.ticker.maxFPS = 60;
  app.ticker.add(tick);
  drawScene(performance.now());

  return {
    update(state) {
      const now = performance.now();
      for (const [index, side] of state.own) {
        if (!grid[index] || owner[index] === side) continue;
        owner[index] = side;
        const hex = grid[index];
        dirtyChunks.add(hex.chunk);
        for (const neighbor of hex.neighbors) if (neighbor >= 0) dirtyChunks.add(grid[neighbor].chunk);
      }
      if (state.weather?.some((id, index) => id !== weather[index])) paintWeather(state.weather);
      for (const patch of state.bases) {
        const marker = bases.get(patch.id);
        if (marker) updateMarker(marker, { ...marker.record, ...patch });
      }
      const live = new Set();
      for (const record of state.units) {
        live.add(record.id);
        const marker = units.get(record.id);
        if (!marker) {
          units.set(record.id, addMarker(record, unitLayer));
          continue;
        }
        interpolate(marker, now);
        marker.fromX = marker.x;
        marker.fromY = marker.y;
        marker.since = now;
        updateMarker(marker, record);
      }
      for (const [id, marker] of units) {
        if (live.has(id)) continue;
        marker.container.destroy({ children: true });
        units.delete(id);
      }
      const remaining = new Set(ownIds());
      if (remaining.size !== selected.size) setSelection(remaining);
      sceneDirty = true;
    },
    setActive(enabled) {
      active = enabled;
      if (!active) {
        keys.clear();
        clearPointer();
      }
    },
    destroy() {
      listeners.abort();
      observer.disconnect();
      app.destroy(true, { children: true });
      groundTexture.destroy(true);
      textures.forEach((texture) => texture.destroy(true));
    },
  };
}
