export const CM_PER_INCH = 2.54;

// Keep storage in inches; only editable/displayed values change unit.
export function dimensionToInches(value, unit) {
  if (value === '' || value == null) return '';
  const number = Number(value);
  return unit === 'cm' ? number / CM_PER_INCH : number;
}

export function dimensionForInput(inches, unit) {
  if (inches === '' || inches == null) return '';
  const number = Number(inches) * (unit === 'cm' ? CM_PER_INCH : 1);
  return Number.isFinite(number) ? String(Number(number.toFixed(6))) : '';
}

export function packageDimensionsText(dimensions, unit) {
  if (!Array.isArray(dimensions) || dimensions.length !== 3 || dimensions.some(v => v === '' || v == null || !Number.isFinite(Number(v)))) return '—';
  return dimensions.map(v => String(Number((Number(v) * (unit === 'cm' ? CM_PER_INCH : 1)).toFixed(2)))).join(' × ') + ` ${unit}`;
}
