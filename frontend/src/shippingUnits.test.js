import test from 'node:test';
import assert from 'node:assert/strict';
import { dimensionToInches, dimensionForInput, packageDimensionsText } from './shippingUnits.js';

test('centimetres convert to canonical inches, inches remain unchanged', () => {
  assert.equal(dimensionToInches('25.4', 'cm'), 10);
  assert.equal(dimensionToInches('10', 'in'), 10);
  assert.equal(dimensionForInput(10, 'cm'), '25.4');
  assert.equal(dimensionForInput(10, 'in'), '10');
});
test('blank dimensions stay blank and do not become zero', () => {
  for (const value of ['', null, undefined]) {
    assert.equal(dimensionToInches(value, 'cm'), '');
    assert.equal(dimensionForInput(value, 'cm'), '');
  }
});
test('display conversion never mutates stored precision', () => {
  const dims = [7.677165354330708, 4, 2];
  const original = [...dims];
  assert.equal(packageDimensionsText(dims, 'cm'), '19.5 × 10.16 × 5.08 cm');
  for (let i = 0; i < 20; i++) dims.forEach(v => { dimensionForInput(v, 'cm'); dimensionForInput(v, 'in'); });
  assert.deepEqual(dims, original);
  assert.equal(packageDimensionsText(['', 1, 2], 'cm'), '—');
});
