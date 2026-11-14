import { test } from 'node:test';
import assert from 'node:assert/strict';
import { format } from '../static/js/i18n.js';

test('formats simple and python-style placeholders', () => {
  assert.equal(format('Found {count}', { count: 3 }), 'Found 3');
  assert.equal(format('Time: {elapsed:.1f}s', { elapsed: 2 }), 'Time: 2.0s');
  assert.equal(format('{price:.2f} PLN', { price: 1.5 }), '1.50 PLN');
  assert.equal(format('keep {unknown}', {}), 'keep {unknown}');
});
