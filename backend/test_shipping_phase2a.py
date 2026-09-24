"""Focused tests for the Shipping Phase 2A packing engine."""
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from app import shipping_optimizer as so


class ShippingPhase2ATests(unittest.TestCase):
    def setUp(self):
        self.temp_inventory = Path(__file__).with_name(".shipping_phase2a_test_inventory.json")
        if self.temp_inventory.exists():
            self.temp_inventory.unlink()
        self.inventory_patch = patch.object(so, "CARTON_INVENTORY_PATH", self.temp_inventory)
        self.inventory_patch.start()

    def tearDown(self):
        self.inventory_patch.stop()
        if self.temp_inventory.exists():
            self.temp_inventory.unlink()

    def test_known_six_item_case_uses_15x15x4(self):
        rows = []
        def add(sku, dims, qty):
            for _ in range(qty):
                rows.append({
                    "sku": sku, "product_name": sku, "part": "Primary package",
                    "dimensions_in": dims, "verification_status": "Verified — Warehouse",
                    "weight_kg": 0.1, "shipping_behavior": "standard",
                })
        add("3361", [7.6772, 4.0157, 3.1496], 3)
        add("3362", [5.5118, 3.3465, 3.1496], 1)
        add("2MD-DTL", [6.6929, 4.3307, 3.1496], 1)
        add("71237", [6.4961, 3.7402, 2.6378], 1)
        plan = so.build_packing_plan({"name": "#TEST", "physical_packages": rows, "packing_readiness": {"unresolved": []}})
        self.assertEqual(plan["status"], "ok")
        self.assertEqual(plan["loose_result"]["carton"], [15.0, 15.0, 4.0])
        self.assertEqual(plan["loose_result"]["empty_space_percent"], 44)

    def test_zero_stock_forces_next_best(self):
        row = {
            "sku": "TEST", "product_name": "Test", "part": "Primary package",
            "dimensions_in": [7.5, 5.5, 3.0], "verification_status": "Verified — Warehouse",
            "weight_kg": 0.2, "shipping_behavior": "standard",
        }
        order = {"name": "#TEST", "physical_packages": [row], "packing_readiness": {"unresolved": []}}
        first = so.build_packing_plan(order)
        ideal = first["loose_result"]["carton"]
        so.set_carton_stock_bulk([{"dimensions": ideal, "quantity": 0}], "Test User")
        second = so.build_packing_plan(order)
        self.assertEqual(second["loose_result"]["recommendation_tier"], "next_best_available")
        self.assertEqual(second["loose_result"]["ideal_carton"], ideal)
        self.assertNotEqual(second["loose_result"]["carton"], ideal)

    def test_blank_stock_remains_eligible_but_untracked(self):
        row = {
            "sku": "TEST", "product_name": "Test", "part": "Primary package",
            "dimensions_in": [5, 5, 2], "verification_status": "Verified — Warehouse",
            "weight_kg": 0.2, "shipping_behavior": "standard",
        }
        plan = so.build_packing_plan({"name": "#TEST", "physical_packages": [row], "packing_readiness": {"unresolved": []}})
        self.assertEqual(plan["loose_result"]["recommendation_tier"], "best_fit_stock_untracked")


if __name__ == "__main__":
    unittest.main()
