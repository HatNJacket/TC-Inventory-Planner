"""Custom shipment and sign-in responsiveness regressions; no live services."""
import asyncio
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from app import main, shipping_registry, shipping_users


class CustomShipmentTests(unittest.TestCase):
    def setUp(self):
        main.app.dependency_overrides[main.verify_token] = lambda: 'test'
        self.addCleanup(main.app.dependency_overrides.clear)
        self.client = TestClient(main.app)  # No lifespan / live startup.
        self.addCleanup(self.client.close)
        records = [
            {'id':'one','sku':'MULTI','product_name':'Two-piece product','part':'Main','dimensions_in':[8,6,4], 'packages_per_unit':1,'shipping_behavior':'carrier_ready','verification_status':'Verified — Warehouse','weight_kg':2},
            {'id':'two','sku':'MULTI','product_name':'Two-piece product','part':'Accessory','dimensions_in':[2,2,1], 'packages_per_unit':2,'verification_status':'Verified — Warehouse','weight_kg':0.1},
        ]
        mock = patch.object(shipping_registry, '_load_payload', return_value={'records':records})
        mock.start(); self.addCleanup(mock.stop)
        shipping_registry._index.cache_clear()
        self.addCleanup(shipping_registry._index.cache_clear)

    def test_expands_all_parts_and_copies_for_requested_units(self):
        with patch.object(main.shopify_client,'fetch_order_for_shipping') as shopify:
            response = self.client.post('/api/shipping/custom-shipment', json={'reference':'Bench test','items':[{'sku':'multi','quantity':2}]})
            self.assertEqual(response.status_code,200)
            data=response.json()
            self.assertEqual(data['source'],'custom')
            self.assertEqual(data['name'],'Bench test')
            self.assertEqual(len(data['physical_packages']),6)
            self.assertEqual(sum(p['weight_kg'] for p in data['physical_packages']),4.4)
            self.assertEqual(sum(p['shipping_behavior']=='carrier_ready' for p in data['physical_packages']),2)
            self.assertTrue(data['packing_readiness']['ready_for_verified_packing'])
            shopify.assert_not_called()

    def test_rejects_missing_skus_and_invalid_quantities(self):
        for items in ([],[{'sku':'MISSING','quantity':1}],[{'sku':'MULTI','quantity':0}],
                      [{'sku':'MULTI','quantity':1.5}],[{'sku':'MULTI','quantity':True}],
                      [{'sku':'MULTI','quantity':101}],[{'sku':'MULTI','quantity':60}]*2):
            with self.subTest(items=items):
                self.assertEqual(self.client.post('/api/shipping/custom-shipment',json={'items':items}).status_code,400)


class ProfileResponsivenessTests(unittest.IsolatedAsyncioTestCase):
    async def test_profiles_respond_while_initial_database_reads_are_waiting(self):
        main.app.dependency_overrides[main.verify_token] = lambda: 'test'
        self.addCleanup(main.app.dependency_overrides.clear)
        with tempfile.TemporaryDirectory() as directory, patch.object(shipping_users,'USERS_PATH',Path(directory)/'users.json'), patch.object(main,'_enrich_with_waiters'), patch.object(main,'_enrich_with_sales'):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),base_url='http://test') as client:
                for url, method, result in [('/api/config/vendors','get_distinct_vendors',[]),('/api/replenishment','get_cached_replenishment',{'items':[]})]:
                    started=threading.Event(); release=threading.Event()
                    def slow_read(*args,**kwargs):
                        started.set(); release.wait(3); return result
                    with patch.object(main.db,method,side_effect=slow_read):
                        task=asyncio.create_task(client.get(url))
                        try:
                            self.assertTrue(await asyncio.to_thread(started.wait,2))
                            response=await asyncio.wait_for(client.get('/api/shipping/users'),1)
                            self.assertEqual(response.status_code,200)
                            self.assertFalse(task.done(),f'{url} blocked the profile request')
                        finally:
                            release.set()
                            await task


if __name__=='__main__':
    unittest.main()
