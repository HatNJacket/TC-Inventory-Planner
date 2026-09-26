"""Explicit, separately packaged bundle mappings; never infer bundles from SKU names.

Mappings are separate from package measurements so registry edits cannot erase them.
Only direct physical-component mappings are supported; nested bundles fail closed.
"""
import json
import hashlib
import math
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock

from .shopify_client import normalize_sku

BUNDLE_PATH = Path(__file__).resolve().parent / 'data' / 'shipping_bundle_registry.json'
_lock = RLock()


class BundleConflict(ValueError):
    pass


def sku_key(sku):
    return normalize_sku(str(sku or '')).strip().casefold()


def load_bundles():
    with _lock:
        seed = _seed()
        changes = _overrides()
        for key, value in changes.items():
            if value is None:
                seed.pop(key, None)
            else:
                seed[key] = value
        return _validate(list(seed.values()))


def _seed():
    with BUNDLE_PATH.open(encoding='utf-8') as handle:
        payload = json.load(handle)
    bundles = payload.get('bundles') if isinstance(payload, dict) else None
    return _validate(bundles)


def _validate(bundles):
    if not isinstance(bundles, list):
        raise ValueError('Bundle registry must contain a bundles list.')
    result = {}
    for bundle in bundles:
        if not isinstance(bundle, dict) or not isinstance(bundle.get('sku'), str) or not sku_key(bundle.get('sku')) or len(bundle['sku']) > 100:
            raise ValueError('Every bundle needs a SKU.')
        key = sku_key(bundle['sku'])
        if key in result:
            raise ValueError('Duplicate bundle SKU in bundle registry.')
        components = bundle.get('components')
        if not isinstance(components, list) or not 1 <= len(components) <= 50:
            raise ValueError(f'{bundle["sku"]}: add between 1 and 50 components.')
        seen = set()
        for component in components:
            if not isinstance(component, dict) or not isinstance(component.get('sku'), str) or not sku_key(component.get('sku')) or len(component['sku']) > 100:
                raise ValueError('Every bundle component needs a SKU.')
            component_key = sku_key(component['sku'])
            quantity = component.get('quantity')
            if type(quantity) is not int or not 1 <= quantity <= 100:
                raise ValueError('Bundle component quantities must be whole numbers from 1 to 100.')
            if component_key in seen:
                raise ValueError('Duplicate component SKU; use its quantity instead.')
            seen.add(component_key)
        result[key] = bundle
    for bundle in result.values():
        if any(sku_key(c['sku']) in result for c in bundle['components']):
            raise ValueError('Nested or circular bundles are not supported; list physical component SKUs directly.')
    return result


def bundle_for_sku(sku):
    return load_bundles().get(sku_key(sku))


def _state_path():
    return BUNDLE_PATH.with_name('shipping_bundle_overrides.json')


def _overrides():
    path = _state_path()
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding='utf-8'))
    changes = payload.get('overrides')
    if not isinstance(changes, dict):
        raise ValueError('Saved bundle mappings are malformed.')
    return changes


def _revision():
    # Includes tombstones and audit metadata so stale saves cannot restore removed mappings.
    data = {'seed': _seed(), 'overrides': _overrides()}
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def _write(changes):
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='bundle-', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump({'version': 1, 'overrides': changes}, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def component_warnings(bundle):
    from .shipping_registry import lookup_sku, VERIFIED_STATUSES
    warnings = []
    for component in bundle['components']:
        sku = component['sku']
        rows = lookup_sku(sku)
        if not rows:
            warnings.append(f'{sku}: not in Package Database; add its package data before planning.')
        for row in rows:
            def positive(value):
                try:
                    return math.isfinite(float(value)) and float(value) > 0
                except (TypeError, ValueError):
                    return False
            dims = row.get('dimensions_in')
            if not isinstance(dims, list) or len(dims) != 3 or not all(positive(v) for v in dims):
                warnings.append(f'{sku}: needs three positive package dimensions.')
            if not positive(row.get('weight_kg')):
                warnings.append(f'{sku}: needs a positive package weight.')
            if row.get('verification_status') not in VERIFIED_STATUSES:
                warnings.append(f'{sku}: package measurements are not verified.')
    return list(dict.fromkeys(warnings))


def bundle_list(query='', offset=0, limit=50):
    with _lock:
        rows = sorted(load_bundles().values(), key=lambda b: sku_key(b['sku']))
        q = sku_key(query)
        rows = [b for b in rows if not q or q in sku_key(b['sku'])
                or any(q in sku_key(c['sku']) for c in b['components'])]
        return {'bundles': [{**b, 'warnings': component_warnings(b)} for b in rows[offset:offset+limit]],
                'count': len(rows), 'revision': _revision()}


def save_bundle(payload, user):
    raw = payload.get('bundle')
    if not isinstance(raw, dict):
        raise ValueError('Enter a bundle SKU and components.')
    _validate([raw])
    bundle = {'sku': normalize_sku(raw['sku']).strip(),
              'components': [{'sku': normalize_sku(c['sku']).strip(), 'quantity': c['quantity']}
                             for c in raw['components']],
              'updated_by': user, 'updated_at': datetime.now(timezone.utc).isoformat()}
    with _lock:
        if payload.get('revision') != _revision():
            raise BundleConflict('Bundle mappings changed. Refresh the list, review your draft, and save again.')
        current = load_bundles()
        key = sku_key(bundle['sku'])
        original = payload.get('original_sku')
        if original is None and key in current:
            raise ValueError('That bundle already exists. Select Edit on its mapping instead.')
        if original is not None and (sku_key(original) != key or key not in current):
            raise ValueError('Bundle SKU cannot be renamed. Remove the old mapping and create the corrected SKU.')
        current[key] = bundle
        _validate(list(current.values()))
        changes = _overrides()
        changes[key] = bundle
        _write(changes)
        return {'bundle': {**bundle, 'warnings': component_warnings(bundle)}, 'revision': _revision()}


def delete_bundle(payload, user):
    with _lock:
        if payload.get('revision') != _revision():
            raise BundleConflict('Bundle mappings changed. Refresh the list before removing a mapping.')
        key = sku_key(payload.get('sku'))
        if key not in load_bundles():
            raise ValueError('Bundle mapping was not found.')
        changes = _overrides()
        changes[key] = None  # Tombstone also hides bundled defaults after a restart.
        _write(changes)
        return {'revision': _revision()}
