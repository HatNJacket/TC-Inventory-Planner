import test from 'node:test';
import assert from 'node:assert/strict';
import { boxRecommendationsCsv } from './boxRecommendationsCsv.mjs';

const analysis = {eligible_count:120,minimum_shipments:100,period_start:'2026-01-01T12:00:00',period_end:'2026-02-01T12:00:00',suggestions:[{
  dimensions_in:[5,10,12],shipment_count:18,share_percent:15,empty_volume_reduction_l:123.45,
  avg_empty_percent_before:58,avg_empty_percent_after:22,
}]};

test('exports ranked dimensions, benefit metrics, date range and limitations',()=>{
  const csv=boxRecommendationsCsv(analysis);
  assert.ok(csv.startsWith('\uFEFF"Rank"'));
  assert.ok(csv.includes('"1","5","10","12","12.7","25.4","30.48","18","15","123.45","58","22","120","2026-01-01T12:00:00","2026-02-01T12:00:00"'));
  assert.ok(csv.includes('Suggestions overlap; do not add their savings together.'));
  assert.equal(csv.split('\r\n').length,3);
});
test('does not export absent or below-threshold suggestions',()=>{
  assert.equal(boxRecommendationsCsv(null),'');
  assert.equal(boxRecommendationsCsv({...analysis,suggestions:[]}),'');
  assert.equal(boxRecommendationsCsv({...analysis,eligible_count:99}),'');
});
test('escapes quotes and neutralizes formula-like text',()=>{
  const csv=boxRecommendationsCsv({...analysis,period_start:'=HYPERLINK("test")'});
  assert.ok(csv.includes('"\'=HYPERLINK(""test"")"'));
});
