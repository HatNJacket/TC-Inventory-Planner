"""Explicit, separately packaged bundle mappings; never infer bundles from SKU names.

Mappings are separate from package measurements so registry edits cannot erase them.
Only direct physical-component mappings are supported; nested bundles fail closed.
"""
import json
from pathlib import Path

from .shopify_client import normalize_sku

BUNDLE_PATH = Path(__file__).resolve().parent / 'data' / 'shipping_bundle_registry.json'


def sku_key(sku):
    return normalize_sku(str(sku or '')).strip().casefold()


def load_bundles():
    with BUNDLE_PATH.open(encoding='utf-8') as handle:
        payload = json.load(handle)
    bundles = payload.get('bundles') if isinstance(payload, dict) else None
    if not isinstance(bundles, list):
        raise ValueError('Bundle registry must contain a bundles list.')
    result = {}
    for bundle in bundles:
        if not isinstance(bundle, dict) or not sku_key(bundle.get('sku')):
            raise ValueError('Every bundle needs a SKU.')
        key = sku_key(bundle['sku'])
        if key in result:
            raise ValueError('Duplicate bundle SKU in bundle registry.')
        components = bundle.get('components')
        if not isinstance(components, list) or not 1 <= len(components) <= 50:
            raise ValueError(f'{bundle["sku"]}: add between 1 and 50 components.')
        seen = set()
        for component in components:
            if not isinstance(component, dict) or not sku_key(component.get('sku')):
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
