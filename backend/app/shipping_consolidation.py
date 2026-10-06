"""Stock-aware, bounded set-partition search over proven 3D carton fits.

Minimize parcel count, then total outer volume. Solver timeouts are not no-fit
proofs: report best-found rather than claiming a global optimum.
"""
from time import monotonic
from itertools import combinations
from . import shipping_optimizer as so


def plan_shipments(loose, fixed, packed_inside, *, budget=8.0):
    deadline = monotonic() + budget
    units, separate = [], []
    assigned = set()
    for row in fixed:
        host = row['registry_id']
        accessories = [p for p in packed_inside if p['host_registry_id'] == host] if host not in assigned else []
        assigned.add(host)
        contents = [{'sku': row['sku'], 'name': row['name'], 'part': row['part'], 'quantity': 1}]
        weights = [row['weight_kg']]
        for item in accessories:
            contents.append({k: item[k] for k in ('sku', 'name', 'part', 'quantity')})
            weights.append(None if item['weight_kg_each'] is None else item['weight_kg_each'] * item['quantity'])
        unit = dict(row, contents=contents, total_weight=None if None in weights else sum(weights),
                    stamp=bool(accessories), own_box=True)
        (separate if row['shipping_behavior'] == 'must_ship_alone' else units).append(unit)
    for row in loose:
        units.append(dict(row, contents=[{'sku': row['sku'], 'name': row['name'], 'part': row['part'], 'quantity': 1}],
                          total_weight=row['weight_kg'], stamp=False, own_box=False))

    def factory(unit):
        weight = unit['total_weight']
        return {'package_type': 'factory_carton', 'label': unit['sku'], 'dimensions_in': unit['dimensions_in'],
                'calculated_weight_kg': weight, 'weight_complete': weight is not None,
                'weight_note': 'Stored factory-package and packed accessory weights.' if weight is not None else 'Missing stored package weight.',
                'verification_status': unit['verification_status'], 'contents': unit['contents'],
                'stamp_accessories_inside': unit['stamp']}

    catalog = sorted(set(so.load_catalog()), key=lambda c: (so.volume(c), c))
    inventory = so._ensure_inventory()
    stock = [so._stock_for(c, inventory) for c in catalog]
    n = len(units)
    # (mask, carton index or -1 for own box, layout, volume)
    candidates = []
    exhaustive = n <= 9
    timed_out = False
    packages = [so.Package(tuple(u['dimensions_in']), sku=u['sku'], name=u['name'], part=u['part'],
                           verification_status=u['verification_status']) for u in units]
    if so._confidence(packages)[0] == 'blocked':
        raise ValueError('Package records need review before packing.')

    def add_candidate(indices):
        nonlocal timed_out
        mask = sum(1 << i for i in indices)
        if len(indices) == 1 and units[indices[0]]['own_box']:
            candidates.append((mask, -1, None, so.volume(units[indices[0]]['dimensions_in'])))
            return  # Never wrap one already shippable box just to make another parcel.
        subset = [packages[i] for i in indices]
        for ci, carton in enumerate(catalog):
            if stock[ci] == 0:
                continue
            # Fast geometric rejection costs no solver budget.
            if sum(so.volume(p.dimensions) for p in subset) > so.volume(carton) + so.TOLERANCE:
                continue
            if any(not so.fits_individually(p.dimensions, carton) for p in subset):
                continue
            remaining = deadline - monotonic()
            if remaining <= 0 and len(indices) > 1:
                timed_out = True
                return
            fit = so.optimize_packages(subset, catalog=[carton], time_limit=max(.01, min(.4, remaining)))
            if fit.status == 'unresolved':
                timed_out = True
            if fit.status != 'ok':
                continue
            layout = fit.to_dict()
            layout.update(recommendation_tier='fewest_packages', selected_carton_stock=stock[ci],
                          stock_constraints_present=any(s == 0 for s in stock[:ci]),
                          stock_warning='Carton stock has not been counted.' if stock[ci] is None else '')
            candidates.append((mask, ci, layout, so.volume(carton)))
            # With unlimited/untracked stock, larger cartons for this same subset
            # cannot improve either objective. Finite stock needs alternatives.
            if stock[ci] is None or stock[ci] >= n:
                break

    # Safe fallback candidates first, then large groups to find one-parcel plans early.
    for i in range(n):
        add_candidate((i,))
    if n > 1:
        add_candidate(tuple(range(n)))
    if exhaustive:
        for size in range(n - 1, 1, -1):
            if monotonic() >= deadline:
                timed_out = True
                break
            for group in combinations(range(n), size):
                add_candidate(group)
                if monotonic() >= deadline:
                    timed_out = True
                    break
    else:
        # Large orders use bounded best-found grouping; no optimality claim.
        order = sorted(range(n), key=lambda i: so.volume(units[i]['dimensions_in']), reverse=True)
        for start in range(n):
            if monotonic() >= deadline:
                timed_out = True
                break
            for size in range(min(9, n-start), 1, -1):
                add_candidate(tuple(order[start:start+size]))
                if monotonic() >= deadline:
                    timed_out = True
                    break

    by_item = [[] for _ in units]
    for candidate in candidates:
        for i in range(n):
            if candidate[0] & (1 << i):
                by_item[i].append(candidate)
    for options in by_item:
        options.sort(key=lambda c: (-c[0].bit_count(), c[3], c[1]))
    full = (1 << n) - 1
    best, best_score = None, (float('inf'), float('inf'))
    used, seen = {}, {}
    # Separate short search budget so geometric fitting cannot starve assembly.
    search_deadline = monotonic() + 2.0

    def search(mask, chosen, total_volume):
        nonlocal best, best_score, timed_out
        if mask == full:
            score = (len(chosen), total_volume)
            if score < best_score:
                best, best_score = list(chosen), score
            return
        if monotonic() >= search_deadline:
            timed_out = True
            return
        if len(chosen) >= best_score[0]:
            return
        state = (mask, tuple(sorted(used.items())))
        score = (len(chosen), total_volume)
        if seen.get(state, (float('inf'), float('inf'))) <= score:
            return
        seen[state] = score
        i = next(i for i in range(n) if not mask & (1 << i))
        for candidate in by_item[i]:
            subset, ci, _, volume = candidate
            if subset & mask or (ci >= 0 and stock[ci] is not None and used.get(ci, 0) >= stock[ci]):
                continue
            if ci >= 0:
                used[ci] = used.get(ci, 0) + 1
            search(mask | subset, chosen + [candidate], total_volume + volume)
            if ci >= 0:
                used[ci] -= 1
                if not used[ci]: del used[ci]
    search(0, [], 0)
    output = [factory(u) for u in separate]
    layouts = []
    if best is not None:
        for mask, ci, layout, _ in best:
            members = [units[i] for i in range(n) if mask & (1 << i)]
            if ci == -1:
                output.append(factory(members[0]))
                continue
            weights = [u['total_weight'] for u in members]
            weight = None if None in weights else round(sum(weights), 4)
            output.append({'package_type': 'warehouse_carton', 'label': 'Combined warehouse carton',
                           'dimensions_in': list(catalog[ci]), 'calculated_weight_kg': weight,
                           'weight_complete': weight is not None,
                           'weight_note': 'Stored content weights; outer-carton tare not included. Confirm final weight.',
                           'verification_status': layout['confidence'], 'carton_recommendation_tier': 'fewest_packages',
                           'carton_stock_on_hand': stock[ci], 'stock_warning': layout.get('stock_warning', ''),
                           'contents': [c for u in members for c in u['contents']],
                           'stamp_accessories_inside': any(u['stamp'] for u in members), 'packing_layout': layout})
            layouts.append(layout)
    for i, package in enumerate(output, 1):
        package['package_number'] = i
    complete = best is not None
    return {'status': 'ok' if complete else 'unresolved' if timed_out else 'no_stock_fit',
            'packages': output, 'layouts': layouts,
            'search_complete': exhaustive and not timed_out,
            'message': ('Fewest packages among proven fits; smaller total outer volume breaks ties.' if exhaustive and not timed_out else
                        'Best plan found within the search limit; a smaller shipment count may be possible.') if complete else
                       'No complete stock-aware plan was found. Check carton stock, sizes, or retry; do not ship this partial plan.'}
