"""Focused tests for the V5.7 migration layer."""
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from app import shipping_optimizer as so
from app import shipping_v57_service as svc


class ShippingV57MigrationTests(unittest.TestCase):
    def setUp(self):
        self.history = Path(__file__).with_name('.shipping_v57_test_history.json')
        self.inventory = Path(__file__).with_name('.shipping_v57_test_inventory.json')
        for p in (self.history, self.inventory):
            if p.exists(): p.unlink()
        self.p1 = patch.object(svc, 'PACKING_HISTORY_PATH', self.history); self.p1.start()
        self.p2 = patch.object(so, 'CARTON_INVENTORY_PATH', self.inventory); self.p2.start()

    def tearDown(self):
        self.p1.stop(); self.p2.stop()
        for p in (self.history, self.inventory):
            if p.exists(): p.unlink()

    def _accessory(self, sku='ACC', dims=None):
        return {
            'sku': sku, 'product_name': sku, 'part': 'Primary package', 'registry_id': sku + '-id',
            'dimensions_in': dims or [4, 3, 2], 'verification_status': 'Verified — Warehouse',
            'weight_kg': 0.2, 'shipping_behavior': 'standard', 'quantity': 1,
        }

    def test_packed_inside_is_removed_from_loose_solver(self):
        host = {
            'sku': 'HOST', 'product_name': 'Host', 'part': 'Main', 'registry_id': 'host-id',
            'dimensions_in': [20, 10, 8], 'verification_status': 'Verified — Warehouse',
            'weight_kg': 4.0, 'shipping_behavior': 'accessory_carrier', 'quantity': 1,
        }
        acc = self._accessory(); acc.update({'packed_inside_count': 1, 'packed_into': 'host-id'})
        plan = svc.plan_payload({'order_reference': '#1', 'items': [host, acc]})
        self.assertEqual(plan['status'], 'ok')
        self.assertEqual(plan['loose_item_count'], 0)
        self.assertEqual(plan['shipping_summary']['package_count'], 1)
        self.assertTrue(plan['shipping_summary']['packages'][0]['stamp_accessories_inside'])

    def test_three_clean_failures_auto_reject(self):
        for i in range(3):
            svc.save_packing_observation({
                'host_registry_id': 'host-id', 'host_sku': 'HOST', 'host_part': 'Main', 'order_reference': f'#{i}',
                'results': [{'sku': 'ACC', 'part': 'Primary package', 'product_name': 'ACC', 'registry_id': 'acc-id',
                             'verification_status': 'Verified — Warehouse', 'dimensions_in': [4,3,2], 'fit_qty': 0, 'failed_qty': 1}],
            }, packed_by='Tester')
        summary = svc.packing_history_summary('host-id')
        self.assertTrue(summary['accessories'][0]['auto_reject'])

    def test_dimensional_inference_requires_slot_coverage(self):
        observed = {
            'version': 1,
            'observations': [{
                'id':'obs1','recorded_at':'2026-09-24T12:00:00','host_registry_id':'host-id','host_sku':'HOST','host_part':'Main',
                'results': [
                    {'sku':'BIG','part':'','registry_id':'big','verification_status':'Verified — Warehouse','dimensions_in':[7,5,3],'fit_qty':1,'failed_qty':0},
                    {'sku':'SMALL','part':'','registry_id':'small','verification_status':'Verified — Warehouse','dimensions_in':[4,4,2],'fit_qty':1,'failed_qty':0},
                ]
            }]
        }
        self.history.write_text(json.dumps(observed), encoding='utf-8')
        with patch.object(svc, 'load_registry', return_value=[]):
            combo = svc.packing_combination_summary('host-id', [
                {'sku':'X','part':'','quantity':1,'dimensions_in':[6,4,2],'verification_status':'Verified — Warehouse','registry_id':'x'},
                {'sku':'Y','part':'','quantity':1,'dimensions_in':[3,3,2],'verification_status':'Verified — Warehouse','registry_id':'y'},
            ])
            self.assertEqual(combo['combination_status'], 'dimensionally_inferred')
            too_many = svc.packing_combination_summary('host-id', [
                {'sku':'X','part':'','quantity':2,'dimensions_in':[6,4,2],'verification_status':'Verified — Warehouse','registry_id':'x'},
                {'sku':'Y','part':'','quantity':1,'dimensions_in':[3,3,2],'verification_status':'Verified — Warehouse','registry_id':'y'},
            ])
            self.assertNotEqual(too_many['combination_status'], 'dimensionally_inferred')


if __name__ == '__main__':
    unittest.main()
