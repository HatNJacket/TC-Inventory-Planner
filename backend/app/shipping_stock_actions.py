"""Explicit physical counts and shipment usage; planning never mutates stock."""
from . import shipping_optimizer as stock
from .shipping_intelligence import shipment_snapshot


def apply_stocktake(payload, user):
    if not payload.get("revision"):
        raise ValueError("Load current stock before saving a count.")
    changes = payload.get("changes")
    if not isinstance(changes, list) or any(not isinstance(c, dict) or c.get("quantity") is None for c in changes):
        raise ValueError("Enter actual counts for the sizes checked.")
    return stock.set_carton_stock_bulk(changes, user, mode="stocktake", expected_revision=payload["revision"])


def confirm_usage(payload, user):
    if not payload.get("revision"):
        raise ValueError("Review current stock before confirming boxes used.")
    packages = payload.get("packages")
    if not isinstance(packages, list) or not packages or len(packages) > 500:
        raise ValueError("Provide between 1 and 500 shipping packages.")
    counts = {}
    for package in packages:
        if not isinstance(package, dict) or package.get("package_type") not in {"warehouse_carton", "factory_carton"}:
            raise ValueError("Invalid shipping package type.")
        if package["package_type"] == "factory_carton":
            continue
        dims = package.get("dimensions_in")
        if not isinstance(dims, list) or len(dims) != 3 or any(stock._safe_float(v) is None or stock._safe_float(v) <= 0 for v in dims):
            raise ValueError("Each warehouse carton needs three positive dimensions in inches.")
        key = stock._carton_key(dims)
        counts.setdefault(key, {"dimensions": sorted(float(v) for v in dims), "quantity": 0})["quantity"] += 1
    if not counts:
        raise ValueError("This shipment uses no warehouse boxes.")
    return stock.set_carton_stock_bulk(
        [counts[key] for key in sorted(counts)], user, mode="consume",
        expected_revision=payload["revision"], shipment_reference=payload.get("shipment_reference"),
        shipment_data=shipment_snapshot(payload),
    )
