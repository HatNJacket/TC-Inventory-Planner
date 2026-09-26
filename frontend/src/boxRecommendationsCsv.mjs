const note = 'Internal dimensions; verify supplier availability and physical fit. Space reduction is not measured filler usage or cost savings. Suggestions overlap; do not add their savings together.';
function csvCell(value) {
  let text = String(value ?? '');
  if (/^[\s]*[=+@-]/.test(text)) text = "'" + text;
  return `"${text.replaceAll('"', '""')}"`;
}

export function boxRecommendationsCsv(analysis) {
  if (!analysis?.suggestions?.length || analysis.eligible_count < analysis.minimum_shipments) return '';
  const rows = [[
    'Rank','Dimension 1 (in)','Dimension 2 (in)','Dimension 3 (in)',
    'Dimension 1 (cm)','Dimension 2 (cm)','Dimension 3 (cm)',
    'Benefiting shipments','Share of eligible shipments (%)','Empty volume reduction (L)',
    'Average unused space before (%)','Average unused space after (%)',
    'Eligible shipments analyzed','Period start','Period end','Notes',
  ], ...analysis.suggestions.map((s,index)=>[
    index+1,...s.dimensions_in,...s.dimensions_in.map(v=>Number((v*2.54).toFixed(2))),
    s.shipment_count,s.share_percent,s.empty_volume_reduction_l,
    s.avg_empty_percent_before,s.avg_empty_percent_after,
    analysis.eligible_count,analysis.period_start,analysis.period_end,note,
  ])];
  return '\uFEFF' + rows.map(row=>row.map(csvCell).join(',')).join('\r\n') + '\r\n';
}
