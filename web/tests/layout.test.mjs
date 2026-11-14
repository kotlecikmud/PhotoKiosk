import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { computeJustifiedLayout, rowsInRange } from '../static/js/layout.js';

const cases = JSON.parse(readFileSync(new URL('./fixtures/layout_cases.json', import.meta.url)));

test('matches the Python reference implementation exactly', () => {
  assert.ok(cases.length >= 19);
  for (const c of cases) {
    const lay = computeJustifiedLayout(c.aspects, c.width, c.density, c.gap);
    assert.deepEqual(lay.tiles.map((t) => [t.slot, t.x, t.y, t.w, t.h]), c.tiles, `tiles of ${c.name}`);
    assert.deepEqual(lay.rows.map((r) => [r.y, r.h, r.first, r.last]), c.rows, `rows of ${c.name}`);
    assert.equal(lay.totalHeight, c.total_height, `height of ${c.name}`);
  }
});

test('rows are flush and keep aspect ratio', () => {
  const aspects = Array.from({ length: 30 }, (_, i) => [1.5, 0.667, 1.5, 1.0, 1.78, 0.75, 1.5, 1.5, 0.667, 1.33][i % 10]);
  const lay = computeJustifiedLayout(aspects, 900, 4, 4);
  for (const row of lay.rows.slice(0, -1)) {
    const tiles = lay.tiles.slice(row.first, row.last);
    assert.equal(tiles[0].x, 0);
    assert.equal(tiles.at(-1).x + tiles.at(-1).w, 900);
  }
  for (const t of lay.tiles) assert.ok(Math.abs(t.w / t.h - aspects[t.slot]) / aspects[t.slot] < 0.06 || t.w <= 3);
});

test('density changes photos per row; last row is not stretched', () => {
  const per = [4, 8, 16].map((d) => { const l = computeJustifiedLayout(Array(64).fill(1.5), 1000, d); return l.rows[0].last - l.rows[0].first; });
  assert.ok(per[0] < per[1] && per[1] < per[2]);
  const lay = computeJustifiedLayout(Array(5).fill(1.5), 800, 4);
  assert.ok(lay.rows.at(-1).h <= lay.rows[0].h + 1);
});

test('rowsInRange finds visible rows', () => {
  const lay = computeJustifiedLayout(Array(20).fill(1.5), 800, 4);
  assert.deepEqual(rowsInRange(lay, 0, lay.rows[0].h).map((v, i) => i === 0 ? v : v >= 1), [0, true]);
  const [f, l] = rowsInRange(lay, lay.rows[2].y + 1, lay.rows[3].y + 1);
  assert.equal(f, 2); assert.ok(l >= 4);
  assert.deepEqual(rowsInRange({ rows: [] }, 0, 10), [0, 0]);
});
