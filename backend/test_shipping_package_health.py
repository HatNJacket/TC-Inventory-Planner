"""Package health and verification workflows isolated from live measurements."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from app import main, shipping_registry as registry, shipping_v57_service as svc, shipping_bundles as bundles
from app import shipping_package_health as health


class PackageHealthTests(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup)
        self.root=Path(folder.name)
        for module,name,value in [(svc,'REGISTRY_PATH',self.root/'registry.json'),(registry,'REGISTRY_PATH',self.root/'registry.json'),(bundles,'BUNDLE_PATH',self.root/'bundles.json')]:
            p=patch.object(module,name,value);p.start();self.addCleanup(p.stop)
        bundles.BUNDLE_PATH.write_text(json.dumps({'bundles':[{'sku':'BUNDLE','components':[{'sku':'A','quantity':1}]}]}))
        records=[self.record('A'), self.record('B',weight_kg=None), self.record('C',dimensions_in=[0,2,3]),
                 self.record('D',verification_status='Shopify — Unverified'),self.record('E',needs_review=True,verification_status='Needs review'),self.record('BUNDLE')]
        records += [self.record(f'ROW-{i}') for i in range(60)]
        svc._write_registry(records)
        self.addCleanup(registry.reload_registry);self.addCleanup(svc._cached_registry.cache_clear)
        main.app.dependency_overrides[main.verify_token]=lambda:'test'
        main.app.dependency_overrides[main.current_shipping_user]=lambda:'Packer'
        self.addCleanup(main.app.dependency_overrides.clear)
        self.client=TestClient(main.app);self.addCleanup(self.client.close)

    def record(self,sku,**kwargs):
        return {'id':sku,'sku':sku,'product_name':sku,'dimensions_in':[4,3,2],'weight_kg':1,
                'verification_status':'Verified — Warehouse','last_verified':'2020-01-01','measured_by':'Original',
                'part':'','packages_per_unit':1,'shipping_behavior':'standard',**kwargs}

    def get(self,sku):
        return next(r for r in self.client.get('/api/shipping/package-database',params={'q':sku}).json()['records'] if r['id']==sku)

    def save(self,record):
        return self.client.post('/api/shipping/package-database',json={'record':record})

    def review(self,record,action='verify',**extra):
        return self.client.post(f'/api/shipping/package-database/{record["id"]}/review',json={
            'action':action,'revision':record['_revision'],'physically_checked':True,**extra})

    def test_counts_whole_database_filters_and_pagination(self):
        data=self.client.get('/api/shipping/package-database').json()
        self.assertEqual(len(data['records']),50)
        self.assertEqual(data['health']['total'],65)
        self.assertEqual(data['health']['verified'],62)
        self.assertEqual(data['health']['missing_weights'],1)
        self.assertEqual(data['health']['missing_dimensions'],1)
        self.assertEqual(data['health']['needs_review'],1)
        self.assertEqual(data['health']['ready_bundles'],1)
        missing=self.client.get('/api/shipping/package-database?health_filter=missing_weights').json()
        self.assertEqual([r['sku'] for r in missing['records']],['B'])
        self.assertEqual(missing['health'],data['health'])
        self.assertEqual(len(self.client.get('/api/shipping/package-database?offset=50').json()['records']),16)
        self.assertEqual(self.client.get('/api/shipping/package-database?health_filter=bad').status_code,422)

    def test_digital_save_health_filter_and_no_verification(self):
        record=self.get('C')
        record.update(shipping_behavior='digital',dimensions_in=None,weight_kg=None)
        response=self.save(record)
        self.assertEqual(response.status_code,200,response.text)
        saved=response.json()['record']
        self.assertIsNone(saved['dimensions_in'])
        data=self.client.get('/api/shipping/package-database?health_filter=digital').json()
        self.assertEqual([r['sku'] for r in data['records']],['C'])
        self.assertEqual(data['health']['digital'],1)
        self.assertEqual(data['health']['total'],64)
        self.assertEqual(data['health']['registry_records'],66)
        self.assertEqual(data['health']['missing_dimensions'],0)
        self.assertEqual(data['health']['missing_weights'],1)
        self.assertEqual(data['health']['verified_percent'],round(62/64*100,1))
        self.assertEqual(self.review(saved).status_code,400)
        saved['shipping_behavior']='standard'
        self.assertEqual(self.save(saved).status_code,400)
        saved.update(dimensions_in=[4,3,2],weight_kg=1)
        physical=self.save(saved).json()['record']
        self.assertEqual(physical['verification_status'],'Shopify — Unverified')
        self.assertEqual(svc.registry_payload()['health']['digital'],0)

    def test_digital_retains_measurements_and_history_but_blocks_stale_plans(self):
        record=self.get('A');record['shipping_behavior']='digital'
        saved=self.save(record).json()['record']
        self.assertEqual(saved['dimensions_in'],[4,3,2])
        self.assertEqual(saved['weight_kg'],1)
        self.assertEqual(saved['change_history'][-1]['before']['shipping_behavior'],'standard')
        with self.assertRaisesRegex(ValueError,'digital products'):
            svc.plan_payload({'items':[{'registry_id':'A','dimensions_in':[4,3,2]}]})
        with self.assertRaisesRegex(ValueError,'Digital products'):
            svc.plan_payload({'items':[{'shipping_behavior':'digital'}]})

    def test_digital_expansion_mixed_orders_and_bundles(self):
        record=self.get('A');record.update(shipping_behavior='digital',dimensions_in=None,weight_kg=None)
        self.assertEqual(self.save(record).status_code,200)
        expanded=registry.expand_order({'line_items':[{'sku':'A','quantity':2},{'sku':'D','quantity':1}]})
        self.assertEqual([p['sku'] for p in expanded['physical_packages']],['D'])
        self.assertEqual(expanded['line_items'][0]['registry_state'],'digital')
        self.assertEqual(expanded['packing_readiness']['unresolved_count'],0)
        self.assertEqual(expanded['packing_readiness']['provisional_skus'],['D'])
        bundled=registry.expand_order({'line_items':[{'sku':'BUNDLE','quantity':1}]})
        self.assertEqual(bundled['physical_packages'],[])
        self.assertEqual(bundled['packing_readiness']['unresolved_count'],0)
        self.assertEqual(svc.registry_payload()['health']['ready_bundles'],1)
        self.assertEqual(bundles.component_warnings(bundles.load_bundles()['bundle']),[])
        from app.shipping_custom import custom_shipment
        custom=custom_shipment({'items':[{'sku':'A','quantity':1}]})
        self.assertEqual(custom['physical_packages'],[])
        self.assertEqual(custom['packing_readiness']['unresolved_count'],0)

    def test_digital_part_does_not_hide_physical_part_of_same_sku(self):
        records=svc.load_registry()
        records.append(self.record('A',id='A-license',part='License',shipping_behavior='digital',dimensions_in=None,weight_kg=None))
        svc._write_registry(records)
        expanded=registry.expand_order({'line_items':[{'sku':'A','quantity':1}]})
        self.assertEqual(len(expanded['physical_packages']),1)
        self.assertEqual(expanded['packing_readiness']['unresolved_count'],0)
        self.assertEqual(health.health([records[-1]],{})['missing_weights'],0)

    def test_save_is_not_verification_and_dimensions_change_resets(self):
        new=self.record('NEW');new.pop('id')
        response=self.save(new);self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['record']['verification_status'],'Shopify — Unverified')
        record=self.get('A');record['dimensions_in']=[4.1,3,2]
        result=self.save(record).json()['record']
        self.assertEqual(result['verification_status'],'Shopify — Unverified')
        self.assertEqual(result['change_history'][-1]['before']['dimensions_in'],[4,3,2])
        self.assertEqual(result['change_history'][-1]['by'],'Packer')
        self.assertEqual(result['last_verified'],'2020-01-01')

    def test_metadata_edits_keep_verification_and_no_expiry(self):
        record=self.get('A');record['notes']='Shelf A'
        result=self.save(record).json()['record']
        self.assertEqual(result['verification_status'],'Verified — Warehouse')
        self.assertEqual(result['last_verified'],'2020-01-01')

    def test_weights_required_and_explicit_confirmation(self):
        record=self.get('B')
        self.assertEqual(self.review(record).status_code,400)
        record['weight_kg']=.5
        result=self.save(record).json()['record']
        self.assertEqual(result['verification_status'],'Shopify — Unverified')
        self.assertEqual(self.review(result,physically_checked=False).status_code,400)
        verified=self.review(result).json()['record']
        self.assertEqual(verified['verification_status'],'Verified — Warehouse')
        self.assertEqual(verified['measured_by'],'Packer')

    def test_suspicious_unit_changes_need_explanation(self):
        record=self.get('A');record['dimensions_in']=[v*2.54 for v in record['dimensions_in']]
        result=self.save(record).json()['record']
        self.assertTrue(result['needs_review'])
        self.assertTrue(result['review_warnings'])
        self.assertEqual(self.review(result).status_code,400)
        verified=self.review(result,reason='Remeasured retail packaging in cm; confirmed converted values.').json()['record']
        self.assertFalse(verified['needs_review'])
        self.assertEqual(verified['review_warnings'],[])

    def test_flag_blocks_loaded_and_future_plans_and_bundle_readiness(self):
        record=self.get('A')
        response=self.review(record,action='flag',reason='Retail packaging differs from measurements')
        self.assertEqual(response.status_code,200)
        expanded=registry.expand_order({'line_items':[{'sku':'A','quantity':1}]})
        self.assertFalse(expanded['packing_readiness']['ready_for_verified_packing'])
        with self.assertRaisesRegex(ValueError,'needs review'):
            svc.plan_payload({'items':[{'registry_id':'A'}]})
        self.assertEqual(svc.registry_payload()['health']['ready_bundles'],0)
        self.assertEqual(self.review(response.json()['record']).status_code,400)

    def test_stale_save_and_verification_rejected(self):
        record=self.get('A');old=dict(record)
        record['weight_kg']=1.1
        self.assertEqual(self.save(record).status_code,200)
        self.assertEqual(self.save(old).status_code,400)
        self.assertEqual(self.review(old).status_code,400)
        with self.assertRaisesRegex(ValueError,'changed'):
            svc.plan_payload({'items':[{'registry_id':'A','registry_revision':old['_revision']}]})

    def test_restore_values_does_not_restore_verification(self):
        record=self.get('A');record['weight_kg']=1.1
        result=self.save(record).json()['record']
        previous=result['change_history'][-1]['before']
        previous['_revision']=result['_revision']
        restored=self.save(previous).json()['record']
        self.assertEqual(restored['weight_kg'],1)
        self.assertEqual(restored['verification_status'],'Shopify — Unverified')
        self.assertEqual(len(restored['change_history']),2)

    def test_flag_cannot_be_cleared_by_normal_save(self):
        record=self.review(self.get('A'),action='flag',reason='Check sizing').json()['record']
        record.update(needs_review=False,verification_status='Verified — Warehouse',measured_by='Forged')
        result=self.save(record).json()['record']
        self.assertTrue(result['needs_review'])
        self.assertEqual(result['verification_status'],'Needs review')
        self.assertEqual(result['measured_by'],'Original')

    def test_invalid_input_and_missing_identity(self):
        record=self.get('A');record['dimensions_in']=[True,2,3]
        self.assertEqual(self.save(record).status_code,400)
        del main.app.dependency_overrides[main.current_shipping_user]
        self.assertEqual(self.review(self.get('A')).status_code,400)


if __name__=='__main__':unittest.main()
