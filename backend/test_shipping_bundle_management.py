"""Bundle editor persistence and authenticated endpoints, without app lifespan."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from app import main, shipping_bundles as bundles, shipping_registry as registry


class BundleManagementTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / 'shipping_bundle_registry.json'
        self.seed = {'bundles': [{'sku':'SET', 'components':[{'sku':'A','quantity':1}]}]}
        path.write_text(json.dumps(self.seed), encoding='utf-8')
        self.seed_path = path
        self.seed_bytes = path.read_bytes()
        patcher = patch.object(bundles, 'BUNDLE_PATH', path)
        patcher.start(); self.addCleanup(patcher.stop)
        patcher = patch.object(registry, '_load_payload', return_value={'records':[
            {'sku':'A','id':'a','dimensions_in':[2,2,1], 'weight_kg':.2,'verification_status':'Verified — Warehouse'},
            {'sku':'B','id':'b','dimensions_in':[3,3,1], 'weight_kg':None,'verification_status':'Shopify — Unverified'}]})
        patcher.start(); self.addCleanup(patcher.stop)
        registry._index.cache_clear(); self.addCleanup(registry._index.cache_clear)
        main.app.dependency_overrides[main.verify_token] = lambda: 'test'
        main.app.dependency_overrides[main.current_shipping_user] = lambda: 'Warehouse tester'
        self.addCleanup(main.app.dependency_overrides.clear)
        self.client = TestClient(main.app)
        self.addCleanup(self.client.close)

    def listing(self, **params):
        response = self.client.get('/api/shipping/bundles', params=params)
        self.assertEqual(response.status_code, 200)
        return response.json()

    def save(self, sku='NEW', components=None, **extra):
        payload = {'bundle': {'sku':sku,'components':components if components is not None else [{'sku':'A','quantity':2}]},
                   'revision':self.listing()['revision'], **extra}
        return self.client.post('/api/shipping/bundles',json=payload)

    def test_create_edit_and_reload_preserve_seed_and_expansion(self):
        response = self.save()
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['bundle']['updated_by'],'Warehouse tester')
        self.assertEqual(bundles.load_bundles()['new']['components'][0]['quantity'],2)
        response = self.save(components=[{'sku':'A','quantity':3}],original_sku='NEW')
        self.assertEqual(response.status_code,200)
        expanded = registry.expand_order({'line_items':[{'sku':'NEW','quantity':2}]})
        self.assertEqual(len(expanded['physical_packages']),6)
        self.assertEqual(self.seed_path.read_bytes(),self.seed_bytes)
        self.assertEqual(len(self.listing()['bundles']),2)

    def test_missing_data_allowed_with_explicit_warnings(self):
        response = self.save(components=[{'sku':'B','quantity':1},{'sku':'MISSING','quantity':1}])
        self.assertEqual(response.status_code,200)
        warnings = ' '.join(response.json()['bundle']['warnings'])
        self.assertIn('positive package weight',warnings)
        self.assertIn('not verified',warnings)
        self.assertIn('not in Package Database',warnings)
        expanded = registry.expand_order({'line_items':[{'sku':'NEW','quantity':1}]})
        self.assertFalse(expanded['packing_readiness']['ready_for_verified_packing'])

    def test_delete_default_persists_without_touching_package_data(self):
        response = self.client.request('DELETE','/api/shipping/bundles',json={'sku':'SET','revision':self.listing()['revision']})
        self.assertEqual(response.status_code,200)
        self.assertEqual(self.listing()['count'],0)
        self.assertNotIn('set',bundles.load_bundles())
        self.assertTrue(registry.lookup_sku('A'))
        self.assertEqual(self.seed_path.read_bytes(),self.seed_bytes)
        self.assertEqual(self.save('SET').status_code,200)

    def test_stale_save_and_delete_rejected(self):
        stale = self.listing()['revision']
        self.assertEqual(self.save().status_code,200)
        self.assertEqual(self.save('OTHER',revision=stale).status_code,409)
        response=self.client.request('DELETE','/api/shipping/bundles',json={'sku':'SET','revision':stale})
        self.assertEqual(response.status_code,409)
        self.assertIn('set',bundles.load_bundles())

    def test_duplicate_nested_self_reference_and_invalid_quantities(self):
        self.assertEqual(self.save('set').status_code,400)
        for components in ([],[{'sku':'SET','quantity':1}],[{'sku':'NEW','quantity':1}],
                           [{'sku':'A','quantity':1},{'sku':'a','quantity':2}]):
            self.assertEqual(self.save(components=components).status_code,400)
        for quantity in (True,0,-1,1.5,101,'2'):
            self.assertEqual(self.save(components=[{'sku':'A','quantity':quantity}]).status_code,400)
        self.assertEqual(self.save('A').status_code,400)  # existing SET would become nested
        self.assertEqual(self.listing()['count'],1)

    def test_search_pagination_and_parent_rename_rejected(self):
        self.assertEqual(self.save('SECOND').status_code,200)
        self.assertEqual(self.listing(q='a')['count'],2)
        page=self.listing(limit=1,offset=1)
        self.assertEqual(len(page['bundles']),1)
        self.assertEqual(self.listing(q='SECOND')['count'],1)
        self.assertEqual(self.save('RENAMED',original_sku='SET').status_code,400)
        self.assertEqual(self.client.get('/api/shipping/bundles?limit=51').status_code,422)

    def test_missing_revision_or_identity_cannot_write(self):
        self.assertEqual(self.save(revision=None).status_code,409)
        del main.app.dependency_overrides[main.current_shipping_user]
        self.assertEqual(self.save().status_code,400)
        main.app.dependency_overrides.clear()
        self.assertIn(self.client.get('/api/shipping/bundles').status_code,(401,403))


if __name__ == '__main__':
    unittest.main()
