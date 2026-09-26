"""Bundle regressions using temporary mappings and measurements; no live services."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import shipping_bundles as bundles, shipping_registry as registry
from app.shipping_custom import custom_shipment
from app.shipping_optimizer import build_packing_plan

PARENT = 'ALP-T-3NM/3.5NM-SET'
A = 'ALP-T-3NM-Ha/OIII'
B = 'ALP-T-3.5NM-SII/Hb'


class BundleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'bundles.json'
        self.mapping = [{'sku': PARENT, 'components': [{'sku': A, 'quantity': 1}, {'sku': B, 'quantity': 1}]}]
        self.write_mapping()
        patcher = patch.object(bundles, 'BUNDLE_PATH', self.path)
        patcher.start(); self.addCleanup(patcher.stop)
        self.records = [{'id': sku, 'sku': sku, 'dimensions_in': [3, 3, 1],
                         'weight_kg': weight, 'verification_status': 'Verified — Warehouse'}
                        for sku, weight in [(A, .2), (B, .3), (PARENT, 99)]]
        patcher = patch.object(registry, '_load_payload', return_value={'records': self.records})
        patcher.start(); self.addCleanup(patcher.stop)
        registry._index.cache_clear(); self.addCleanup(registry._index.cache_clear)

    def write_mapping(self):
        self.path.write_text(json.dumps({'bundles': self.mapping}), encoding='utf-8')

    def expand(self, **line):
        return registry.expand_order({'name': '#50995', 'line_items': [
            {'id': 'line1', 'sku': PARENT, 'quantity': 1, **line}]})

    def test_two_units_expand_four_packages_without_parent_weight(self):
        result = self.expand(quantity=2)
        packages = result['physical_packages']
        self.assertEqual(len(packages), 4)
        self.assertEqual(sum(p['weight_kg'] for p in packages), 1)
        self.assertEqual(len({p['package_instance_id'] for p in packages}), 4)
        self.assertEqual({p['sku'] for p in packages}, {A, B})
        self.assertTrue(all(p['bundle_sku'] == PARENT and p['line_item_id'] == 'line1' for p in packages))
        self.assertEqual(result['line_items'][0]['sku'], PARENT)
        self.assertTrue(result['packing_readiness']['ready_for_verified_packing'])

    def test_remaining_quantity_and_nonshipping_lines(self):
        self.assertEqual(len(self.expand(quantity=5, pack_quantity=1)['physical_packages']), 2)
        self.assertEqual(self.expand(pack_quantity=0)['physical_packages'], [])
        self.assertEqual(self.expand(requires_shipping=False)['physical_packages'], [])

    def test_case_insensitive_and_parent_registry_not_required(self):
        self.records.pop()
        result = custom_shipment({'items': [{'sku': PARENT.lower(), 'quantity': 2}]})
        self.assertEqual(len(result['physical_packages']), 4)

    def test_missing_component_does_not_fall_back_to_parent(self):
        self.records.pop(1)
        result = self.expand()
        self.assertFalse(result['packing_readiness']['ready_for_verified_packing'])
        self.assertEqual(result['packing_readiness']['unresolved'][0]['sku'], B)
        self.assertEqual(len(result['physical_packages']), 1)
        with self.assertRaisesRegex(ValueError, 'Resolve bundle'):
            build_packing_plan(result)

    def test_missing_or_invalid_weight_blocks_planning(self):
        for weight in (None, 0, -1, float('nan'), float('inf')):
            self.records[0]['weight_kg'] = weight
            result = self.expand()
            self.assertFalse(result['packing_readiness']['ready_for_verified_packing'])
            self.assertIn('positive stored package weight', result['packing_readiness']['unresolved'][0]['reason'])

    def test_invalid_dimensions_are_reported_not_crashed_or_silently_used(self):
        for dims in ([0, 2, 3], ['bad', 2, 3], [float('nan'), 2, 3], [1, 2], 'bad'):
            self.records[0]['dimensions_in'] = dims
            result = self.expand()
            self.assertFalse(result['packing_readiness']['ready_for_verified_packing'])
            self.assertEqual(len(result['physical_packages']), 1)

    def test_component_multiplicity_and_individual_purchase_are_additive(self):
        self.mapping[0]['components'][0]['quantity'] = 2
        self.write_mapping()
        self.records[0]['packages_per_unit'] = 2
        result = registry.expand_order({'line_items': [
            {'id': 'bundle', 'sku': PARENT, 'quantity': 2},
            {'id': 'separate', 'sku': A, 'quantity': 1}]})
        self.assertEqual(len(result['physical_packages']), 12)
        self.assertEqual(len({p['package_instance_id'] for p in result['physical_packages']}), 12)

    def test_unmapped_product_unchanged_and_provisional_propagated(self):
        self.assertEqual(len(self.expand(sku=A)['physical_packages']), 1)
        self.records[0]['verification_status'] = 'Shopify — Unverified'
        result = self.expand()
        self.assertEqual(result['packing_readiness']['provisional_skus'], [A])
        self.assertFalse(result['packing_readiness']['ready_for_verified_packing'])

    def test_invalid_mapping_fails_closed(self):
        for component in ({'sku': PARENT, 'quantity': 1}, {'sku': A, 'quantity': 0},
                          {'sku': A, 'quantity': 1.5}, {'sku': A, 'quantity': True}):
            self.mapping[0]['components'] = [component]
            self.write_mapping()
            with self.assertRaises(ValueError):
                self.expand()


if __name__ == '__main__':
    unittest.main()
