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

    def make_link(self, sku='OPENBOX', source='A'):
        raw=self.record(sku,dimensions_in=None,weight_kg=None,packaging_source_sku=source,packaging_confirmed=True)
        raw.pop('id')
        response=self.save(raw)
        self.assertEqual(response.status_code,200,response.text)
        return response.json()['record']

    def test_link_health_identity_and_live_source_updates(self):
        link=self.make_link()
        data=self.client.get('/api/shipping/package-database?health_filter=linked').json()
        self.assertEqual([r['sku'] for r in data['records']],['OPENBOX'])
        self.assertEqual(data['health']['total'],65)
        self.assertEqual(data['health']['linked'],1)
        self.assertEqual(data['health']['missing_dimensions'],1)
        expanded=registry.expand_order({'line_items':[{'sku':'OPENBOX','quantity':2},{'sku':'A','quantity':1}]})
        packages=expanded['physical_packages']
        self.assertEqual([p['sku'] for p in packages],['OPENBOX','OPENBOX','A'])
        self.assertEqual(packages[0]['registry_id'],'A')
        self.assertEqual(packages[0]['dimensions_in'],[4,3,2])
        with patch('app.shipping_consolidation.plan_shipments',return_value={'packages':[],'layouts':[],'status':'ok','search_complete':True,'message':''}) as planner:
            svc.plan_payload({'items':[{**packages[0],'quantity':1}]})
            self.assertEqual(planner.call_args.args[0][0]['sku'],'OPENBOX')
        source=self.get('A');source['dimensions_in']=[4.1,3,2]
        self.assertEqual(self.save(source).status_code,200)
        fresh=registry.expand_order({'line_items':[{'sku':'OPENBOX','quantity':1}]})
        self.assertEqual(fresh['physical_packages'][0]['dimensions_in'],[4.1,3,2])
        with self.assertRaisesRegex(ValueError,'link changed'):
            svc.plan_payload({'items':[packages[0]]})
        self.assertEqual(self.review(link).status_code,400)

    def test_link_validation_and_source_protection(self):
        link=self.make_link()
        for source in ['OPENBOX','MISSING','BUNDLE']:
            raw={**link,'packaging_source_sku':source,'packaging_confirmed':True}
            self.assertEqual(self.save(raw).status_code,400)
        self.assertEqual(self.save({**link,'packaging_confirmed':False}).status_code,400)
        source=self.get('A');source['shipping_behavior']='digital'
        self.assertEqual(self.save(source).status_code,400)
        source=self.get('A');source['sku']='RENAMED'
        self.assertEqual(self.save(source).status_code,400)
        with self.assertRaises(ValueError): svc.delete_registry_record('A')
        raw=self.record('OTHER',packaging_source_sku='OPENBOX',packaging_confirmed=True);raw.pop('id')
        self.assertEqual(self.save(raw).status_code,400)

    def test_link_multiple_parts_and_new_part_invalidates_plan(self):
        link=self.make_link()
        old=registry.expand_order({'line_items':[{'sku':'OPENBOX','quantity':1}]})['physical_packages'][0]
        records=svc.load_registry()
        records.append(self.record('A',id='A-second',part='Tripod',packages_per_unit=2))
        svc._write_registry(records)
        fresh=registry.expand_order({'line_items':[{'sku':'OPENBOX','quantity':2}]})
        self.assertEqual(len(fresh['physical_packages']),6)
        self.assertTrue(all(p['sku']=='OPENBOX' for p in fresh['physical_packages']))
        with self.assertRaisesRegex(ValueError,'link changed'):svc.plan_payload({'items':[old]})

    def test_convert_existing_record_and_unlink(self):
        record=self.get('C');record.update(packaging_source_sku='A',packaging_confirmed=True)
        saved=self.save(record).json()['record']
        self.assertEqual(saved['change_history'][-1]['before']['dimensions_in'],[0,2,3])
        saved.update(packaging_source_sku='',dimensions_in=[4,3,2])
        result=self.save(saved)
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(result.json()['record']['verification_status'],'Shopify — Unverified')
        self.assertEqual(svc.registry_payload()['health']['linked'],0)

    def test_linked_bundle_component_and_missing_source_fail_closed(self):
        self.make_link()
        bundle={'sku':'KIT','components':[{'sku':'OPENBOX','quantity':1}]}
        self.assertEqual(bundles.component_warnings(bundle),[])
        svc._write_registry([r for r in svc.load_registry() if r['id']!='A'])
        expanded=registry.expand_order({'line_items':[{'sku':'OPENBOX','quantity':1}]})
        self.assertEqual(expanded['physical_packages'],[])
        self.assertEqual(expanded['packing_readiness']['unresolved_count'],1)

    def test_source_review_and_unlink_invalidate_loaded_alias(self):
        link=self.make_link()
        package=registry.expand_order({'line_items':[{'sku':'OPENBOX','quantity':1}]})['physical_packages'][0]
        self.review(self.get('A'),action='flag',reason='Packaging changed')
        expanded=registry.expand_order({'line_items':[{'sku':'OPENBOX','quantity':1}]})
        self.assertGreater(expanded['packing_readiness']['unresolved_count'],0)
        with self.assertRaises(ValueError):svc.plan_payload({'items':[package]})
        link.update(packaging_source_sku='',dimensions_in=[4,3,2],weight_kg=1)
        self.assertEqual(self.save(link).status_code,200)
        with self.assertRaisesRegex(ValueError,'link removed'):svc.plan_payload({'items':[package]})

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
