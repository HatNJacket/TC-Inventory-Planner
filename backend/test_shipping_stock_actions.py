"""Count and shipment usage regressions; all inventory lives in temporary files."""
import unittest
from concurrent.futures import ThreadPoolExecutor
from fastapi.testclient import TestClient
from app import main, shipping_optimizer as stock
from app.shipping_stock_actions import apply_stocktake, confirm_usage
import test_shipping_stock as fixtures


class StockActionTests(unittest.TestCase):
    setUp = fixtures.StockTests.setUp
    save = fixtures.StockTests.save

    def usage(self, reference="#1001", quantity=1, dims=None):
        return {"shipment_reference": reference, "revision": stock.get_carton_catalog()["revision"],
                "packages": [{"package_type": "warehouse_carton", "dimensions_in": dims or self.dim}] * quantity}

    def test_stocktake_records_matching_count_and_leaves_unchecked_alone(self):
        self.save(8, minimum=10, target=40)
        stock.set_carton_stock_bulk([{"dimensions": [20,10,5], "quantity": 3}])
        result = apply_stocktake({"revision": stock.get_carton_catalog()["revision"], "changes": [
            {"dimensions": self.dim, "quantity": 8}]}, "Counter")
        self.assertEqual(result["inventory"][0]["last_counted"]["user_name"], "Counter")
        self.assertEqual(result["inventory"][1]["quantity"], 3)
        self.assertEqual(result["shopping_list"][0]["order_quantity"], 32)
        self.assertEqual(stock._ensure_inventory()["adjustments"][-1]["change_type"], "stocktake")

    def test_stale_and_invalid_counts_are_atomic(self):
        result = self.save(8)
        request = {"revision": result["revision"], "changes": [{"dimensions": self.dim, "quantity": 2}]}
        self.save(7)
        with self.assertRaises(stock.InventoryConflict): apply_stocktake(request, "Counter")
        for quantity in (None, -1, 1.5, True, "", "bad"):
            request["revision"] = stock.get_carton_catalog()["revision"]
            request["changes"][0]["quantity"] = quantity
            before = stock.CARTON_INVENTORY_PATH.read_bytes()
            with self.assertRaises(ValueError): apply_stocktake(request, "Counter")
            self.assertEqual(stock.CARTON_INVENTORY_PATH.read_bytes(), before)

    def test_policy_only_save_preserves_stock_and_count_metadata(self):
        before = self.save(8)['inventory'][0]
        result = stock.set_carton_stock_bulk([
            {'dimensions': self.dim, 'minimum': 10, 'target': 40}], 'Settings editor')
        self.assertEqual(result['inventory'][0]['quantity'], 8)
        self.assertEqual(result['inventory'][0]['last_counted'], before['last_counted'])
        self.assertEqual(result['shopping_list'][0]['order_quantity'], 32)

    def test_usage_filters_factory_and_retries_after_reload(self):
        self.save(8, minimum=7, target=40)
        request = self.usage()
        request["packages"].append({"package_type": "factory_carton", "dimensions_in": self.dim})
        result = confirm_usage(request, "Packer")
        self.assertEqual(result["inventory"][0]["quantity"], 7)
        self.assertEqual(result["shopping_list"][0]["order_quantity"], 33)
        self.assertEqual(confirm_usage(request, "Packer")["inventory"][0]["quantity"], 7)
        self.assertEqual(confirm_usage(self.usage(" #1001 "), "Packer")["inventory"][0]["quantity"], 7)
        self.assertEqual(stock._ensure_inventory()["adjustments"][-1]["user_name"], "Packer")

    def test_changed_usage_for_same_reference_conflicts(self):
        self.save(8)
        confirm_usage(self.usage(), "Packer")
        with self.assertRaises(stock.InventoryConflict): confirm_usage(self.usage(quantity=2), "Packer")
        self.assertEqual(stock.get_carton_catalog()["inventory"][0]["quantity"], 7)

    def test_insufficient_unknown_or_invalid_usage_never_partially_deducts(self):
        self.save(2)
        requests = [self.usage(quantity=3), self.usage(reference=""), self.usage(dims=[99,99,99])]
        batch = self.usage()
        batch["packages"].append({"package_type": "warehouse_carton", "dimensions_in": [20,10,5]})
        requests.append(batch)
        for request in requests:
            before = stock.CARTON_INVENTORY_PATH.read_bytes()
            with self.assertRaises(ValueError): confirm_usage(request, "Packer")
            self.assertEqual(stock.CARTON_INVENTORY_PATH.read_bytes(), before)

    def test_concurrent_retry_only_deducts_once(self):
        self.save(2)
        request = self.usage()
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda _: confirm_usage(request, "Packer"), range(2)))
        self.assertEqual(stock.get_carton_catalog()["inventory"][0]["quantity"], 1)

    def test_stale_usage_and_rotated_sizes(self):
        stock.set_carton_stock_bulk([{"dimensions": [20,10,5], "quantity": 4}])
        request = self.usage(dims=[5,20,10])
        self.save(8)
        with self.assertRaises(stock.InventoryConflict): confirm_usage(request, "Packer")
        request["revision"] = stock.get_carton_catalog()["revision"]
        self.assertEqual(confirm_usage(request, "Packer")["inventory"][1]["quantity"], 3)
        request["packages"][0]["dimensions_in"] = [20,10,5]
        self.assertEqual(confirm_usage(request, "Packer")["inventory"][1]["quantity"], 3)

    def test_routes_use_authenticated_user(self):
        self.save(8)
        original = main.app.dependency_overrides.copy()
        self.addCleanup(lambda: setattr(main.app, "dependency_overrides", original))
        main.app.dependency_overrides[main.current_shipping_user] = lambda: "Signed-in Packer"
        client = TestClient(main.app)
        response = client.post('/api/shipping/cartons/usage', json=self.usage())
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(stock._ensure_inventory()["adjustments"][-1]["user_name"], "Signed-in Packer")
        response = client.post('/api/shipping/cartons/stocktake', json={
            "revision": response.json()["revision"], "changes": [{"dimensions": self.dim, "quantity": 0}]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["inventory"][0]["quantity"], 0)
        response = client.post('/api/shipping/cartons/usage', json=self.usage("#1002"))
        self.assertEqual(response.status_code, 400)


if __name__ == '__main__':
    unittest.main()
