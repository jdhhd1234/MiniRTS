export const WORLD_SIZE = 1200;
export const R = 20 / Math.sqrt(3);
export const COLS = 60;
export const ROWS = 69;
const directions = [[1, 0], [0, 1], [-1, 1], [-1, 0], [0, -1], [1, -1]];
const corners = Array.from({ length: 6 }, (_, i) => {
  const angle = (60 * i - 30) * Math.PI / 180;
  return [R * Math.cos(angle), R * Math.sin(angle)];
});

export function hexCenter(col, row) {
  return { x: 20 * (col + 0.5 * (row & 1)) + 10, y: R + row * 1.5 * R };
}

export function hexAt(x, y) {
  const px = x - 10;
  const py = y - R;
  const qf = (Math.sqrt(3) / 3 * px - py / 3) / R;
  const rf = (2 / 3 * py) / R;
  const sf = -qf - rf;
  let q = Math.round(qf);
  let r = Math.round(rf);
  const dq = Math.abs(q - qf);
  const dr = Math.abs(r - rf);
  const ds = Math.abs(Math.round(sf) - sf);
  if (dq > dr && dq > ds) q = -r - Math.round(sf);
  else if (dr > ds) r = -q - Math.round(sf);
  return { col: q + (r - (r & 1)) / 2, row: r };
}

export function makeGrid(cols, rows) {
  return Array.from({ length: cols * rows }, (_, index) => {
    const col = index % cols;
    const row = Math.floor(index / cols);
    const { x, y } = hexCenter(col, row);
    const q = col - (row - (row & 1)) / 2;
    const neighbors = directions.map(([dq, dr]) => {
      const nr = row + dr;
      const nc = q + dq + (nr - (nr & 1)) / 2;
      return nc >= 0 && nc < cols && nr >= 0 && nr < rows ? nr * cols + nc : -1;
    });
    return { index, col, row, neighbors, polygon: corners.flatMap(([dx, dy]) => [x + dx, y + dy]) };
  });
}
