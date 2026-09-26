import json
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from app import main, shipping_optimizer as stock
from app.shipping_stock_actions import confirm_usage
from app.shipping_intelligence import shipment_snapshot, shipment_history, suggestions
import test_shipping_stock as fixtures


class IntelligenceTests(unittest.TestCase):
    setUp = fixtures.StockTests.setUp
    save = fixtures.StockTests.save

    def payload(self, size=4):
        return {'shipment_reference':'#REAL-1', 'revision':stock.get_carton_catalog()['revision'],
                'real_shipment':True, 'source':'order',
                'packages':[{'package_type':'warehouse_carton','dimensions_in':[10,10,10]}],
                'packing_result':{'status':'ok','carton':[10,10,10],'recommendation_tier':'best_fit_in_stock',
                    'placements':[{'dimensions':[size]*3,'position':[0,0,0],'sku':'TEST', 'verification_status':'Verified — Warehouse'}]}}

    def seed(self, count, benefiting=None, **snapshot_fields):
        inventory = stock._ensure_inventory()
        records = {}
        for i in range(count):
            snapshot = shipment_snapshot(self.payload(4 if benefiting is None or i < benefiting else 9.6))
            snapshot.update(snapshot_fields)
            records[str(i)]={'reference':f'#{i}', 'recorded_at':'2026-01-01T12:00:00', 'user_name':'Test',
                            'changes':[{'dimensions':[10,10,10],'quantity':1}], 'packing':snapshot}
        inventory['shipments']=records
        stock._write_inventory(inventory)

    def test_threshold_and_geometry_proven_candidate(self):
        self.seed(99)
        self.assertEqual(suggestions()['suggestions'], [])
        self.seed(100)
        result=suggestions()
        self.assertEqual(result['eligible_count'],100)
        candidate=result['suggestions'][0]
        self.assertEqual(candidate['dimensions_in'],[5,5,5])
        self.assertEqual(candidate['shipment_count'],100)
        self.assertEqual(candidate['avg_empty_percent_before'],93.6)
        self.assertEqual(candidate['avg_empty_percent_after'],48.8)
        self.assertEqual(len(candidate['affected_shipments']),100)

    def test_minimum_ten_distinct_benefiting_orders(self):
        self.seed(100,benefiting=9)
        self.assertEqual(suggestions()['suggestions'],[])
        self.seed(100,benefiting=10)
        self.assertEqual(suggestions()['suggestions'][0]['shipment_count'],10)

    def test_existing_catalog_size_and_stockout_are_not_new_opportunities(self):
        self.seed(100,stockout_substitution=True)
        self.assertEqual(suggestions()['stockout_count'],100)
        self.assertEqual(suggestions()['suggestions'],[])
        self.seed(100)
        stock.CARTON_CATALOG_PATH.write_text(json.dumps({'unit':'inches','cartons':[[10,10,10],[5,5,5]]}))
        self.assertEqual(suggestions()['suggestions'],[])

    def test_legacy_custom_and_tests_excluded(self):
        for update in ({'source':'custom'},{'real_shipment':False},{'real_shipment':'true'}):
            self.assertFalse(shipment_snapshot({**self.payload(),**update})['eligible'])
        self.seed(1)
        inventory=stock._ensure_inventory()
        inventory['shipments']['legacy']={'reference':'old','recorded_at':'2025-01-01','packing':None}
        stock._write_inventory(inventory)
        self.assertEqual(suggestions()['eligible_count'],1)
        self.assertEqual(suggestions()['excluded_count'],1)
        self.assertEqual(shipment_history(limit=1)['count'],2)
        self.assertEqual(len(shipment_history(limit=1)['shipments']),1)

    def test_invalid_or_unverified_layout_is_not_evidence(self):
        for field,value in [('position',[8,0,0]),('dimensions',[float('nan'),4,4]),('verification_status','Shopify — Unverified')]:
            payload=self.payload()
            payload['packing_result']['placements'][0][field]=value
            self.assertFalse(shipment_snapshot(payload)['eligible'])
        payload=self.payload()
        payload['packing_result']['placements']*=2
        self.assertFalse(shipment_snapshot(payload)['eligible'])

    def test_history_and_stock_saved_once_together(self):
        self.save(8)
        payload=self.payload()
        confirm_usage(payload,'Packer')
        confirm_usage(payload,'Packer')
        self.assertEqual(stock.get_carton_catalog()['inventory'][0]['quantity'],7)
        history=shipment_history()
        self.assertEqual(history['count'],1)
        self.assertTrue(history['shipments'][0]['packing']['eligible'])
        self.assertEqual(history['shipments'][0]['user_name'],'Packer')
        before=stock.CARTON_INVENTORY_PATH.read_bytes()
        payload=self.payload();payload['shipment_reference']='#OTHER'
        with patch.object(stock,'_write_inventory',side_effect=OSError('test failure')):
            with self.assertRaises(OSError):confirm_usage(payload,'Packer')
        self.assertEqual(stock.CARTON_INVENTORY_PATH.read_bytes(),before)

    def test_bad_history_does_not_prevent_stock_confirmation(self):
        self.save(8)
        payload=self.payload();payload['packing_result']=None
        confirm_usage(payload,'Packer')
        self.assertEqual(stock.get_carton_catalog()['inventory'][0]['quantity'],7)
        self.assertFalse(shipment_history()['shipments'][0]['packing']['eligible'])

    def test_real_planner_output_is_accepted_without_recalculating(self):
        from app.shipping_v57_service import plan_payload
        self.save(8)
        plan=plan_payload({'order_reference':'#REAL-1','items':[{'sku':'TEST','dimensions_in':[4,4,4],
            'quantity':1,'verification_status':'Verified — Warehouse','shipping_behavior':'standard','weight_kg':1}]})
        payload=self.payload()
        payload['packages']=plan['shipping_summary']['packages']
        payload['packing_result']=plan['loose_result']
        confirm_usage(payload,'Packer')
        self.assertTrue(shipment_history()['shipments'][0]['packing']['eligible'])

    def test_analysis_window_is_bounded(self):
        self.seed(1002)
        self.assertEqual(suggestions()['eligible_count'],1000)
        self.assertEqual(suggestions()['total_recorded'],1002)

    def test_authenticated_read_endpoints_and_pagination_validation(self):
        self.seed(100)
        original=main.app.dependency_overrides.copy()
        self.addCleanup(lambda:setattr(main.app,'dependency_overrides',original))
        main.app.dependency_overrides[main.verify_token]=lambda:'test'
        client=TestClient(main.app)
        self.assertEqual(client.get('/api/shipping/shipment-history?offset=50').json()['offset'],50)
        self.assertEqual(client.get('/api/shipping/shipment-history?limit=100').status_code,422)
        self.assertEqual(client.get('/api/shipping/box-suggestions').json()['suggestions'][0]['shipment_count'],100)


if __name__=='__main__': unittest.main()
