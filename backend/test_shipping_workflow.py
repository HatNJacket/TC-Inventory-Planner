"""Regression tests use temporary data only; no live Shopify/database calls."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import shipping_users as users
from app import shipping_v57_service as service
from app import main
from fastapi.testclient import TestClient


class ShippingWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.registry = Path(self.folder.name) / 'registry.json'
        self.registry.write_text(json.dumps({'records': [
            {'id': str(i), 'sku': f'SKU-{i:03}', 'product_name': f'Product {i}', 'part': ''}
            for i in range(125)
        ]}), encoding='utf-8')
        self.patch = patch.object(service, 'REGISTRY_PATH', self.registry)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        service._cached_registry.cache_clear()

    def test_default_and_explicit_limits_never_exceed_fifty(self):
        for kwargs in ({}, {'limit': 500}):
            result = service.registry_payload(**kwargs)
            self.assertEqual(len(result['records']), 50)
            self.assertEqual(result['count'], 125)
            self.assertTrue(result['truncated'])

    def test_search_finds_records_beyond_first_page(self):
        result = service.registry_payload('SKU-124')
        self.assertEqual(result['records'][0]['sku'], 'SKU-124')
        self.assertEqual(result['count'], 1)
        self.assertFalse(result['truncated'])

    def test_registry_cache_reused_and_invalidated_by_file_change(self):
        service.registry_payload()
        service.registry_payload('Product')
        self.assertEqual(service._cached_registry.cache_info().misses, 1)
        self.assertEqual(service._cached_registry.cache_info().hits, 1)
        self.registry.write_text(json.dumps({'records': [{'sku': 'NEW', 'product_name': 'Changed'}]}), encoding='utf-8')
        self.assertEqual(service.registry_payload()['records'][0]['sku'], 'NEW')

    def test_profile_creation_duplicate_and_resolution(self):
        with patch.object(users, 'USERS_PATH', Path(self.folder.name) / 'users.json'):
            self.assertEqual(users.load_users(), [])
            user = users.save_user({'name': 'Warehouse Tester', 'initials': 'wt'})
            self.assertEqual(user['initials'], 'WT')
            self.assertEqual(users.resolve_user(user['id']), user)
            self.assertIsNone(users.resolve_user('missing'))
            self.assertEqual(users.save_user({'name': 'warehouse tester'})['id'], user['id'])
            self.assertEqual(len(users.load_users()), 1)
            with self.assertRaises(ValueError):
                users.save_user({'name': ' '})

    def test_shipping_api_attributes_writes_to_selected_profile(self):
        main.app.dependency_overrides[main.verify_token] = lambda: 'test-token'
        self.addCleanup(main.app.dependency_overrides.clear)
        # No TestClient context manager: do not run the live app lifespan.
        client = TestClient(main.app)
        self.addCleanup(client.close)
        with patch.object(users, 'USERS_PATH', Path(self.folder.name) / 'users.json'):
            response = client.post('/api/shipping/users', json={'name': 'Packer'})
            self.assertEqual(response.status_code, 200)
            profile = response.json()['user']
            self.assertEqual(len(client.get('/api/shipping/users').json()['users']), 1)
            self.assertEqual(client.post('/api/shipping/cartons/stock', json={'changes': []}).status_code, 400)
            headers = {'X-Shipping-User': profile['id']}
            with patch.object(main, 'set_shipping_carton_stock_bulk', return_value={}) as save:
                self.assertEqual(client.post('/api/shipping/cartons/stock', headers=headers, json={'changes': []}).status_code, 200)
                save.assert_called_once_with([], user_name='Packer')
            with patch.object(main, 'shipping_save_observation', return_value={}) as save:
                self.assertEqual(client.post('/api/shipping/packing-history/observations', headers=headers, json={}).status_code, 200)
                save.assert_called_once_with({}, packed_by='Packer')
            with patch.object(main, 'shipping_save_registry_record', return_value={}) as save:
                self.assertEqual(client.post('/api/shipping/package-database', headers=headers, json={'record': {'sku': 'NEW'}}).status_code, 200)
                save.assert_called_once_with({'sku': 'NEW'}, measured_by_default='Packer')
            self.assertEqual(len(client.get('/api/shipping/package-database').json()['records']), 50)
            self.assertEqual(client.get('/api/shipping/package-database?limit=500').status_code, 422)


if __name__ == '__main__':
    unittest.main()
