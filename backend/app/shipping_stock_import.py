"""Preview and apply explicit CSV stock counts or deliveries. No fuzzy size matches."""
import csv
import io
import math
import uuid
from . import shipping_optimizer as stock


def preview_import(text, mode):
    if mode not in {'count', 'receive'}:
        raise ValueError('Choose count or receive mode.')
    if not isinstance(text, str) or len(text.encode('utf-8')) > 1_000_000:
        raise ValueError('Upload a CSV file smaller than 1 MB.')
    reader = csv.DictReader(io.StringIO(text.lstrip('\ufeff')), strict=True)
    required = {'length', 'width', 'height', 'unit', 'quantity'}
    if not reader.fieldnames:
        raise ValueError('The CSV is empty.')
    reader.fieldnames = [name.strip().lower() for name in reader.fieldnames]
    if len(set(reader.fieldnames)) != len(reader.fieldnames) or not required.issubset(reader.fieldnames):
        raise ValueError('Use unique headers: length,width,height,unit,quantity. Optional: minimum,target.')
    catalog = stock.get_carton_catalog()
    by_key = {row['key']: row for row in catalog['inventory']}
    rows, changes, errors, seen = [], [], [], set()
    try:
        for number, raw in enumerate(reader, 2):
            if number > 501:
                raise ValueError('Import at most 500 rows.')
            try:
                if None in raw:
                    raise ValueError('Too many columns; use the CSV template.')
                unit = str(raw.get('unit') or '').strip().lower()
                if unit not in {'cm', 'in', 'inches'}:
                    raise ValueError('Unit must be cm or in.')
                dims = [float(raw[name]) / (2.54 if unit == 'cm' else 1) for name in ('length','width','height')]
                if any(not math.isfinite(v) or v <= 0 for v in dims):
                    raise ValueError('Dimensions must be positive numbers.')
                key = stock._carton_key(dims)
                if key not in by_key:
                    raise ValueError(f'Box size is not in the catalog ({key} in). Check dimensions and units.')
                if key in seen:
                    raise ValueError('Duplicate box size in this import.')
                seen.add(key)
                current = by_key[key]
                quantity = stock._safe_stock(raw.get('quantity'))
                if quantity is None:
                    raise ValueError('Quantity is required; enter 0 explicitly for empty stock.')
                if mode == 'receive' and current['quantity'] is None:
                    raise ValueError(f'{key} in: current stock is unknown. Use Check shelf stock to record the boxes already on hand (0 if none), then preview this delivery again.')
                change = {'dimensions': current['dimensions'], 'quantity': quantity}
                for field in ('minimum', 'target'):
                    if str(raw.get(field) or '').strip():
                        change[field] = stock._safe_stock(raw[field])
                minimum = change.get('minimum', current['minimum'])
                target = change.get('target', current['target'])
                if (minimum is None) != (target is None) or (minimum is not None and target <= minimum):
                    raise ValueError('Set both minimum and target, with target greater than minimum.')
                rows.append({'row':number, 'key':key, 'dimensions':current['dimensions'], 'old_quantity':current['quantity'],
                             'new_quantity':quantity + current['quantity'] if mode == 'receive' else quantity,
                             'minimum':minimum, 'target':target})
                changes.append(change)
            except (ValueError, TypeError, KeyError) as exc:
                errors.append({'row':number, 'message':str(exc)})
    except csv.Error as exc:
        raise ValueError(f'Could not read CSV: {exc}') from exc
    if not rows and not errors:
        raise ValueError('The CSV contains no stock rows.')
    return {'rows':rows, 'errors':errors, 'changes':changes, 'revision':catalog['revision'], 'mode':mode, 'can_apply':bool(rows) and not errors}


def apply_import(payload, user_name):
    request_id = str(payload.get('request_id') or '')
    try:
        uuid.UUID(request_id)
    except ValueError as exc:
        raise ValueError('Preview the import before applying it.') from exc
    with stock.INVENTORY_LOCK:
        inventory = stock._ensure_inventory()
        if request_id in inventory.get('applied_imports', []):
            return stock.get_carton_catalog()
        if payload.get('revision') != stock.inventory_revision(inventory):
            raise stock.InventoryConflict('Stock changed since preview. Preview this file again before applying.')
        preview = preview_import(payload.get('csv'), payload.get('mode'))
        if not preview['can_apply']:
            raise ValueError('Fix all import errors before applying.')
        return stock.set_carton_stock_bulk(preview['changes'], user_name, expected_revision=payload['revision'],
                                          mode=payload['mode'], request_id=request_id)
