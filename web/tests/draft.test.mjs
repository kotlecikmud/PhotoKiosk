import { test } from 'node:test';
import assert from 'node:assert/strict';
import { OrderDraft } from '../static/js/draft.js';

const catalog = {
  sizes: [{ name: '9x13' }, { name: '10x15' }, { name: '15x21' }],
  finishes: ['GLOSSY', 'MATTE'],
  prices: { '9x13|glossy': 1.0, '10x15|glossy': 1.1 },
};
const photos = [{ id: 'a', name: 'a.jpg' }, { id: 'b', name: 'b.jpg' }];
const mk = () => { const d = new OrderDraft(catalog); d.setPhotos(photos); return d; };

test('increment auto-selects and decrement auto-deselects', () => {
  const d = mk();
  assert.ok(d.increment('a', 0));
  assert.ok(d.selected.has('a')); assert.equal(d.printsFor('a'), 1);
  d.decrement('a', 0);
  assert.ok(!d.selected.has('a'));
});

test('select sets the first card to one', () => {
  const d = mk(); d.select('b');
  assert.equal(d.cards('b')[0].count, 1); assert.equal(d.totalPrints(), 1);
});

test('add size everywhere adds a zero-count card to every photo', () => {
  const d = mk(); d.addSizeEverywhere('15x21');
  for (const pid of ['a', 'b']) assert.ok(d.cards(pid).some((c) => c.size_name === '15x21' && c.count === 0));
});

test('remove card only affects one photo and fixes the active card', () => {
  const d = mk(); d.addSizeEverywhere('15x21');
  d.setActiveCard('a', 1); d.removeCard('a', 0);
  assert.equal(d.activeCardIdx('a'), 0);
  assert.equal(d.cards('a').length, 2); assert.equal(d.cards('b').length, 3);
});

test('removing every card leaves a safe empty state', () => {
  const d = mk(); d.removeCard('a', 0); d.removeCard('a', 0);
  assert.deepEqual(d.cards('a'), []); assert.equal(d.activeCardIdx('a'), 0);
});

test('buildItems prices lines and normalises finish', () => {
  const d = mk(); d.increment('a', 0); d.increment('a', 0); d.increment('b', 1);
  const items = d.buildItems(['a', 'b']);
  assert.deepEqual(items.map((i) => i.qty), [2, 1]);
  assert.equal(items[0].unit_price, 1.0); assert.equal(items[0].subtotal, 2.0);
  assert.equal(items[0].finish, 'GLOSSY'); assert.equal(items[0].filename, 'a.jpg');
});

test('select all is scoped to the ids it is given (category filter)', () => {
  const d = mk();
  d.selectMany(['a']);
  assert.ok(d.allSelected(['a'])); assert.ok(!d.allSelected(['a', 'b']));
  d.deselectMany(['a']);
  assert.equal(d.totalPrints(), 0);
});

test('clearCard removes one summary line and deselects when nothing is left', () => {
  const d = mk(); d.increment('a', 0); d.increment('a', 1);
  d.clearCard('a', 0); assert.ok(d.selected.has('a'));
  d.clearCard('a', 1); assert.ok(!d.selected.has('a'));
});

test('selections of vanished photos are dropped; draft survives a JSON round-trip', () => {
  const d = mk(); d.increment('a', 0); d.increment('b', 1); d.setCrop('b', 'fit_in');
  const restored = new OrderDraft(catalog, JSON.parse(JSON.stringify(d.toJSON())));
  restored.setPhotos([photos[1]]);                  // photo "a" is gone
  assert.deepEqual([...restored.selected], ['b']);
  assert.equal(restored.buildItems()[0].crop, 'fit_in');
  assert.equal(restored.buildItems()[0].filename, 'b.jpg');
});

test('stale persisted sizes that were deleted in the admin are ignored', () => {
  const d = new OrderDraft(catalog, { sessionCardSizes: ['9x13', 'gone'] });
  assert.deepEqual(d.sessionCardSizes, ['9x13']);
});

test('admin removes a size / finish mid-order: draft drops it, keeps the rest', () => {
  const d = mk();
  d.increment('a', 0); d.increment('a', 1); d.setPaper('a', 1, 'MATTE'); d.increment('b', 0);
  d.updateCatalog({ sizes: [{ name: '10x15' }, { name: '15x21' }], finishes: ['GLOSSY'], prices: { '10x15|glossy': 2 } });
  assert.deepEqual(d.cards('a').map((c) => c.size_name), ['10x15']);       // 9x13 is gone
  assert.equal(d.cards('a')[0].paper_type, 'GLOSSY');                       // MATTE no longer exists
  assert.ok(d.selected.has('a')); assert.equal(d.price('10x15', 'GLOSSY'), 2);
  assert.ok(!d.selected.has('b'));                                          // its only card vanished -> deselected
  assert.deepEqual(d.sessionCardSizes, ['10x15']);
  assert.equal(d.activeCardIdx('a'), 0);
});
