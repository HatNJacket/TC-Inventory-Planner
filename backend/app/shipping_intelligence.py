"""Conservative, arrangement-proven space opportunities, not price predictions."""
import math
from . import shipping_optimizer as stock
from .shipping_registry import VERIFIED_STATUSES

MIN_SHIPMENTS = 100
MIN_BENEFIT = 10
WINDOW = 1000
CLEARANCE_IN = 0.5  # Total added on each axis, before rounding up to an inch.


def _vector(value, positive=True):
    if not isinstance(value, list) or len(value) != 3:
        raise ValueError('Missing three-dimensional measurements.')
    result = [stock._safe_float(v) for v in value]
    if any(v is None or (v <= 0 if positive else v < 0) for v in result):
        raise ValueError('Invalid dimensions or positions.')
    return result


def shipment_snapshot(payload):
    record = {'eligible': False, 'source': 'custom' if payload.get('source') == 'custom' else 'order',
              'exclusion_reason': 'Not explicitly confirmed as a real order shipment.'}
    if payload.get('real_shipment') is not True or payload.get('source') != 'order':
        return record
    try:
        packages = [p for p in payload['packages'] if p.get('package_type') == 'warehouse_carton']
        if len(packages) != 1:
            raise ValueError('Analysis currently needs exactly one warehouse carton per shipment.')
        carton = sorted(_vector(packages[0].get('dimensions_in')))
        layout = payload.get('packing_result')
        if not isinstance(layout, dict) or layout.get('status') != 'ok':
            raise ValueError('A successful packing layout is required.')
        if stock._carton_key(_vector(layout.get('carton'))) != stock._carton_key(carton):
            raise ValueError('Packing layout does not match the box used.')
        raw_items = layout.get('placements')
        if not isinstance(raw_items, list) or not 1 <= len(raw_items) <= 200:
            raise ValueError('Missing or oversized item layout.')
        items = []
        for raw in raw_items:
            if not isinstance(raw, dict) or raw.get('verification_status') not in VERIFIED_STATUSES:
                raise ValueError('Item measurements must be verified before analysis.')
            dims, pos = _vector(raw.get('dimensions')), _vector(raw.get('position'), False)
            if any(pos[a] + dims[a] > carton[a] + 1e-5 for a in range(3)):
                raise ValueError('An item lies outside the confirmed box.')
            for previous in items:
                if all(pos[a] < previous['position'][a] + previous['dimensions'][a] - 1e-5 and
                       previous['position'][a] < pos[a] + dims[a] - 1e-5 for a in range(3)):
                    raise ValueError('Packing layout contains overlapping items.')
            items.append({'dimensions': dims, 'position': pos, 'sku': str(raw.get('sku') or '')[:100]})
        bounds = sorted(max(i['position'][a] + i['dimensions'][a] for i in items) -
                        min(i['position'][a] for i in items) for a in range(3))
        item_volume = sum(math.prod(i['dimensions']) for i in items)
        volume = math.prod(carton)
        record.update(eligible=True, exclusion_reason=None, carton=carton, items=items, bounds=bounds,
                      item_volume_in3=item_volume, empty_volume_in3=max(0, volume-item_volume),
                      empty_percent=round(100*(volume-item_volume)/volume, 1),
                      stockout_substitution=layout.get('recommendation_tier') == 'next_best_available')
    except (ValueError, TypeError, KeyError) as exc:
        record['exclusion_reason'] = str(exc)
    return record


def _history():
    with stock.INVENTORY_LOCK:
        records = list(stock._ensure_inventory().get('shipments', {}).values())
    return sorted(records, key=lambda r: r.get('recorded_at', ''), reverse=True)


def shipment_history(offset=0, limit=50):
    records = _history()
    return {'count': len(records), 'offset': offset, 'limit': limit,
            'shipments': records[offset:offset+limit]}


def suggestions():
    records = _history()
    eligible = [r for r in records if (r.get('packing') or {}).get('eligible')][:WINDOW]
    sample = [r for r in eligible if not r['packing'].get('stockout_substitution')]
    catalog = [sorted(c) for c in stock.load_catalog()]
    catalog_keys = {stock._carton_key(c) for c in catalog}
    result = {'eligible_count':len(eligible), 'total_recorded':len(records),
              'minimum_shipments':MIN_SHIPMENTS, 'minimum_benefit':MIN_BENEFIT, 'window_limit':WINDOW,
              'excluded_count':sum(not (r.get('packing') or {}).get('eligible') for r in records),
              'stockout_count':len(eligible)-len(sample), 'clearance_in':CLEARANCE_IN,
              'period_start':eligible[-1]['recorded_at'] if eligible else None,
              'period_end':eligible[0]['recorded_at'] if eligible else None, 'suggestions':[]}
    if len(eligible) < MIN_SHIPMENTS:
        return result
    prepared = []
    for record in sample:
        p = record['packing']
        needed = [v+CLEARANCE_IN for v in p['bounds']]
        existing_volume = min((math.prod(c) for c in catalog if all(needed[a] <= c[a] for a in range(3))), default=math.inf)
        prepared.append((record, needed, existing_volume))
    candidates = {tuple(math.ceil(v+CLEARANCE_IN) for v in r['packing']['bounds']) for r in sample}
    for candidate in candidates:
        if stock._carton_key(candidate) in catalog_keys:
            continue
        candidate_volume = math.prod(candidate)
        benefits = []
        for record, needed, existing_volume in prepared:
            p = record['packing']
            if any(needed[a] > candidate[a] for a in range(3)) or candidate_volume >= math.prod(p['carton']):
                continue
            # Do not propose a new size when a current catalog carton proves at least as efficient.
            if existing_volume <= candidate_volume:
                continue
            saved = math.prod(p['carton']) - candidate_volume
            benefits.append({'reference':record['reference'], 'recorded_at':record['recorded_at'],
                             'current_carton':p['carton'], 'empty_percent_before':p['empty_percent'],
                             'empty_percent_after':round(100*(candidate_volume-p['item_volume_in3'])/candidate_volume,1),
                             'empty_volume_reduction_l':round(saved*0.016387064,2)})
        if len(benefits) >= MIN_BENEFIT:
            result['suggestions'].append({'dimensions_in':list(candidate), 'shipment_count':len(benefits),
                'share_percent':round(100*len(benefits)/len(eligible),1),
                'empty_volume_reduction_l':round(sum(b['empty_volume_reduction_l'] for b in benefits),2),
                'avg_empty_percent_before':round(sum(b['empty_percent_before'] for b in benefits)/len(benefits),1),
                'avg_empty_percent_after':round(sum(b['empty_percent_after'] for b in benefits)/len(benefits),1),
                'affected_shipments':benefits})
    result['suggestions'].sort(key=lambda s:(-s['empty_volume_reduction_l'],-s['shipment_count'],s['dimensions_in']))
    result['suggestions'] = result['suggestions'][:10]
    return result
