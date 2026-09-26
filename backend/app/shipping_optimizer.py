"""TC Shipping Planner Phase 2A packing engine.

Ports the proven V5.7 rectangular 3D carton solver into TC Inventory Planner.
Phase 2A covers:
- standard/factory-carton split
- 3D warehouse-carton optimization
- carton on-hand counts
- next-best-available carton selection when the ideal carton is out of stock
- final package/weight summary

Accessory-carrier learning is intentionally left for the next Phase 2 step.
"""
from __future__ import annotations

import json
import math
import hashlib
from threading import RLock
from dataclasses import asdict, dataclass
from itertools import permutations
from pathlib import Path
from typing import Any, Iterable, Sequence

from .shipping_registry import PROVISIONAL_STATUS, VERIFIED_STATUSES

TOLERANCE = 1e-6
APP_DATA_DIR = Path(__file__).resolve().parent / "data"
CARTON_CATALOG_PATH = APP_DATA_DIR / "shipping_carton_catalog.json"
CARTON_INVENTORY_PATH = APP_DATA_DIR / "shipping_carton_inventory.json"
FACTORY_BEHAVIORS = {"carrier_ready", "accessory_carrier", "must_ship_alone"}
INVENTORY_LOCK = RLock()


class InventoryConflict(ValueError):
    pass


def inventory_revision(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True)
class Package:
    dimensions: tuple[float, float, float]
    sku: str | None = None
    name: str | None = None
    part: str | None = None
    verification_status: str | None = None


@dataclass
class OptimizationResult:
    status: str
    carton: tuple[float, float, float] | None
    empty_space_percent: int | None
    placements: list[dict[str, Any]]
    confidence: str
    provisional_skus: list[str]
    message: str

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if self.carton is not None:
            data["carton"] = list(self.carton)
        return data


def _safe_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def _safe_stock(value: Any) -> int | None:
    if value is None or str(value).strip() == "":
        return None
    if isinstance(value, bool):
        raise ValueError("Carton stock must be a whole number, not true/false.")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Carton stock must be a whole number or blank") from exc
    if not math.isfinite(number) or number < 0 or not number.is_integer():
        raise ValueError("Carton stock must be a whole number of 0 or more")
    return int(number)


def _carton_key(dimensions: Sequence[float]) -> str:
    dims = tuple(sorted(round(float(v), 6) for v in dimensions))

    def fmt(value: float) -> str:
        if math.isclose(value, round(value), abs_tol=1e-9):
            return str(int(round(value)))
        return f"{value:.6f}".rstrip("0").rstrip(".")

    return "x".join(fmt(v) for v in dims)


def volume(dimensions: Sequence[float]) -> float:
    return math.prod(dimensions)


def unique_orientations(dimensions: Sequence[float]) -> list[tuple[float, float, float]]:
    return sorted(set(permutations(tuple(float(v) for v in dimensions), 3)))


def fits_individually(item: Sequence[float], carton: Sequence[float]) -> bool:
    return all(i <= c + TOLERANCE for i, c in zip(sorted(item), sorted(carton)))


def load_catalog() -> list[tuple[float, float, float]]:
    payload = json.loads(CARTON_CATALOG_PATH.read_text(encoding="utf-8"))
    if payload.get("unit") != "inches":
        raise ValueError("Shipping carton catalog must use inches")
    cartons = [tuple(float(v) for v in dims) for dims in payload.get("cartons", [])]
    if any(len(c) != 3 or any(v <= 0 for v in c) for c in cartons):
        raise ValueError("Shipping carton catalog contains invalid dimensions")
    return cartons  # type: ignore[return-value]


def _ensure_inventory() -> dict[str, Any]:
    APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not CARTON_INVENTORY_PATH.exists():
        CARTON_INVENTORY_PATH.write_text(
            json.dumps({"version": 1, "stock": {}, "adjustments": []}, indent=2) + "\n",
            encoding="utf-8",
        )
    payload = json.loads(CARTON_INVENTORY_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("stock", {}), dict):
        raise ValueError("Shipping carton inventory is malformed")
    payload.setdefault("version", 1)
    payload.setdefault("stock", {})
    payload.setdefault("adjustments", [])
    payload.setdefault("reorder", {})
    return payload


def _write_inventory(payload: dict[str, Any]) -> None:
    payload["version"] = 1
    payload["adjustments"] = list(payload.get("adjustments", []))[-500:]
    temp = CARTON_INVENTORY_PATH.with_suffix(".tmp")
    temp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temp.replace(CARTON_INVENTORY_PATH)


def get_carton_catalog() -> dict[str, Any]:
    cartons = load_catalog()
    payload = _ensure_inventory()
    stock = payload.get("stock", {})
    rows = []
    for carton in cartons:
        quantity = _safe_stock(stock.get(_carton_key(carton)))
        policy = payload.get("reorder", {}).get(_carton_key(carton), {})
        minimum = policy.get("minimum")
        target = policy.get("target")
        low_stock = minimum is not None and quantity is not None and quantity <= minimum
        rows.append({
            "dimensions": list(carton),
            "key": _carton_key(carton),
            "quantity": quantity,
            "last_counted": payload.get("last_counted", {}).get(_carton_key(carton)),
            "tracked": quantity is not None,
            "in_stock": quantity is None or quantity > 0,
            "minimum": minimum,
            "target": target,
            "low_stock": low_stock,
            "order_quantity": max(0, (target or 0) - quantity) if low_stock else 0,
        })
    return {
        "revision": inventory_revision(payload),
        "unit": "inches",
        "count": len(rows),
        "tracked_count": sum(1 for row in rows if row["tracked"]),
        "uncounted_count": sum(1 for row in rows if not row["tracked"]),
        "out_of_stock_count": sum(1 for row in rows if row["quantity"] == 0),
        "inventory": rows,
        "shopping_list": sorted([row for row in rows if row["low_stock"]], key=lambda row: (row["quantity"] != 0, row["key"])),
        "low_stock_count": sum(1 for row in rows if row["low_stock"]),
        "needs_count_count": sum(1 for row in rows if row["minimum"] is not None and row["quantity"] is None),
    }


def set_carton_stock_bulk(changes: list[dict[str, Any]], user_name: str = "", *,
                          expected_revision=None, mode="count", request_id=None,
                          shipment_reference=None) -> dict[str, Any]:
    """Validate the entire batch before writing; receipts add to known counts."""
    if mode not in {"count", "receive", "stocktake", "consume"}:
        raise ValueError("Choose a valid stock action.")
    if not isinstance(changes, list) or not changes or len(changes) > 500:
        raise ValueError("Provide between 1 and 500 carton changes.")
    with INVENTORY_LOCK:
        payload = _ensure_inventory()
        shipment_key = None
        if mode == "consume":
            if not isinstance(shipment_reference, str) or not shipment_reference.strip() or len(shipment_reference) > 150:
                raise ValueError("Enter a unique shipment reference (up to 150 characters).")
            shipment_reference = shipment_reference.strip()
            shipment_key = shipment_reference.casefold()
            previous = payload.get("shipments", {}).get(shipment_key)
            if previous:
                if previous["changes"] != changes:
                    raise InventoryConflict("Boxes were already recorded for this shipment. Review stock before making corrections.")
                return get_carton_catalog()
        if request_id and request_id in payload.get("applied_imports", []):
            return get_carton_catalog()
        if expected_revision is not None and expected_revision != inventory_revision(payload):
            raise InventoryConflict("Carton stock changed since loading. Refresh or preview the import again.")
        catalog_by_key = {_carton_key(c): c for c in load_catalog()}
        stock = payload.setdefault("stock", {})
        policies = payload.setdefault("reorder", {})
        adjustments = payload.setdefault("adjustments", [])
        from datetime import datetime
        recorded_at = datetime.now().isoformat(timespec="seconds")
        seen = set()
        for raw in changes:
            if not isinstance(raw, dict):
                raise ValueError("Each carton change must be an object.")
            dims = raw.get("dimensions")
            if not isinstance(dims, list) or len(dims) != 3:
                raise ValueError("Each carton stock change needs dimensions [L, W, H].")
            if any(_safe_float(value) is None or _safe_float(value) <= 0 for value in dims):
                raise ValueError("Carton dimensions must be positive finite numbers.")
            key = _carton_key(dims)
            if key not in catalog_by_key:
                raise ValueError(f"Carton {key} is not in the catalog.")
            if key in seen:
                raise ValueError(f"Carton {key} appears more than once.")
            seen.add(key)
            old = _safe_stock(stock.get(key))
            quantity = _safe_stock(raw.get("quantity", old))
            if mode == "receive":
                if old is None:
                    raise ValueError(f"Count carton {key} before adding a delivery; current stock is unknown.")
                if quantity is None:
                    raise ValueError("Delivery quantity cannot be blank.")
                quantity += old
            if mode == "stocktake" and quantity is None:
                raise ValueError("Enter an actual count, including zero for an empty shelf.")
            if mode == "consume":
                if quantity is None or quantity <= 0:
                    raise ValueError("Boxes used must be a positive whole number.")
                if old is None:
                    raise ValueError(f"Count carton {key} before recording boxes used; current stock is unknown.")
                if quantity > old:
                    raise ValueError(f"Not enough stock for carton {key}: {old} on hand. Check the physical count first.")
                quantity = old - quantity
            previous_policy = policies.get(key, {})
            minimum = _safe_stock(raw.get("minimum", previous_policy.get("minimum")))
            target = _safe_stock(raw.get("target", previous_policy.get("target")))
            if (minimum is None) != (target is None) or (minimum is not None and target <= minimum):
                raise ValueError(f"Carton {key}: set both minimum and target, with target greater than minimum, or leave both blank.")
            policy = {"minimum": minimum, "target": target}
            counted = mode in {"count", "stocktake"} and "quantity" in raw and quantity is not None
            if counted:
                payload.setdefault("last_counted", {})[key] = {"recorded_at": recorded_at, "user_name": user_name or "Unknown"}
            if not counted and quantity == old and policy == {"minimum": previous_policy.get("minimum"), "target": previous_policy.get("target")}:
                continue
            stock[key] = quantity
            policies[key] = policy
            adjustments.append({
                "recorded_at": recorded_at, "carton_key": key,
                "dimensions": list(catalog_by_key[key]),
                "old_quantity": old, "new_quantity": quantity,
                "old_reorder": previous_policy, "new_reorder": policy,
                "change_type": {"receive": "delivery_import", "stocktake": "stocktake", "consume": "shipment"}.get(mode, "count_import" if request_id else "manual_count_bulk"),
                "shipment_reference": shipment_reference,
                "user_name": user_name or "Unknown",
            })
        if request_id:
            payload["applied_imports"] = (payload.get("applied_imports", []) + [request_id])[-100:]
        if shipment_key:
            payload.setdefault("shipments", {})[shipment_key] = {
                "reference": shipment_reference, "changes": changes,
                "recorded_at": recorded_at, "user_name": user_name or "Unknown",
            }
        _write_inventory(payload)
        return get_carton_catalog()


def solve_packing(
    items: Sequence[tuple[float, float, float]],
    carton_dimensions: Sequence[float],
    time_limit: float = 15.0,
) -> tuple[bool | None, list[tuple[tuple[float, float, float], tuple[float, float, float]]]]:
    carton = tuple(sorted(float(v) for v in carton_dimensions))
    if sum(volume(item) for item in items) > volume(carton) + TOLERANCE:
        return False, []
    if any(not fits_individually(item, carton) for item in items):
        return False, []

    if len(items) == 1:
        orientation = next(
            o for o in unique_orientations(items[0])
            if all(o[a] <= carton[a] + TOLERANCE for a in range(3))
        )
        return True, [((0.0, 0.0, 0.0), orientation)]

    try:
        import numpy as np
        from scipy.optimize import Bounds, LinearConstraint, milp
        from scipy.sparse import coo_array
    except ImportError as exc:
        raise RuntimeError("Multi-box optimization requires scipy. Install backend requirements again.") from exc

    ordered_items = sorted(
        (tuple(float(v) for v in item) for item in items),
        key=lambda item: (volume(item), max(item), sum(item)),
        reverse=True,
    )
    orientations = [unique_orientations(item) for item in ordered_items]
    item_count = len(ordered_items)
    next_variable = 0
    position_variables: list[tuple[int, int, int]] = []
    for _ in ordered_items:
        position_variables.append((next_variable, next_variable + 1, next_variable + 2))
        next_variable += 3
    orientation_variables: list[list[int]] = []
    for item_orientations in orientations:
        indices = list(range(next_variable, next_variable + len(item_orientations)))
        orientation_variables.append(indices)
        next_variable += len(item_orientations)
    separation_variables: dict[tuple[int, int], list[int]] = {}
    for first in range(item_count):
        for second in range(first + 1, item_count):
            indices = list(range(next_variable, next_variable + 6))
            separation_variables[(first, second)] = indices
            next_variable += 6

    variable_count = next_variable
    lower_bounds = np.zeros(variable_count)
    upper_bounds = np.empty(variable_count)
    integrality = np.zeros(variable_count, dtype=int)
    for i in range(item_count):
        for axis, variable in enumerate(position_variables[i]):
            upper_bounds[variable] = carton[axis]
        for variable in orientation_variables[i]:
            upper_bounds[variable] = 1.0
            integrality[variable] = 1
    for variables in separation_variables.values():
        for variable in variables:
            upper_bounds[variable] = 1.0
            integrality[variable] = 1

    row_indices: list[int] = []
    column_indices: list[int] = []
    coefficients: list[float] = []
    constraint_lower: list[float] = []
    constraint_upper: list[float] = []

    def add_constraint(values: Iterable[tuple[int, float]], lower: float, upper: float) -> None:
        row = len(constraint_lower)
        for column, coefficient in values:
            if coefficient:
                row_indices.append(row)
                column_indices.append(column)
                coefficients.append(float(coefficient))
        constraint_lower.append(lower)
        constraint_upper.append(upper)

    for variables in orientation_variables:
        add_constraint(((variable, 1.0) for variable in variables), 1.0, 1.0)
    for item_index in range(item_count):
        for axis in range(3):
            values = [(position_variables[item_index][axis], 1.0)]
            values.extend(
                (orientation_variables[item_index][orientation_index], orientation[axis])
                for orientation_index, orientation in enumerate(orientations[item_index])
            )
            add_constraint(values, -np.inf, carton[axis])
    for (first, second), variables in separation_variables.items():
        add_constraint(((variable, 1.0) for variable in variables), 1.0, np.inf)
        for axis in range(3):
            first_before_second = variables[axis * 2]
            second_before_first = variables[axis * 2 + 1]
            values = [
                (position_variables[first][axis], 1.0),
                (position_variables[second][axis], -1.0),
                (first_before_second, carton[axis]),
            ]
            values.extend(
                (orientation_variables[first][orientation_index], orientation[axis])
                for orientation_index, orientation in enumerate(orientations[first])
            )
            add_constraint(values, -np.inf, carton[axis])
            values = [
                (position_variables[second][axis], 1.0),
                (position_variables[first][axis], -1.0),
                (second_before_first, carton[axis]),
            ]
            values.extend(
                (orientation_variables[second][orientation_index], orientation[axis])
                for orientation_index, orientation in enumerate(orientations[second])
            )
            add_constraint(values, -np.inf, carton[axis])
    for i in range(item_count - 1):
        if tuple(sorted(ordered_items[i])) == tuple(sorted(ordered_items[i + 1])):
            add_constraint(
                ((position_variables[i][0], 1.0), (position_variables[i + 1][0], -1.0)),
                -np.inf,
                0.0,
            )

    matrix = coo_array(
        (coefficients, (row_indices, column_indices)),
        shape=(len(constraint_lower), variable_count),
    ).tocsc()
    constraints = LinearConstraint(matrix, np.asarray(constraint_lower), np.asarray(constraint_upper))
    result = milp(
        c=np.zeros(variable_count),
        integrality=integrality,
        bounds=Bounds(lower_bounds, upper_bounds),
        constraints=constraints,
        options={"time_limit": float(time_limit), "presolve": True},
    )
    if result.x is None:
        if result.status == 1:
            return None, []
        return False, []

    placements = []
    solution = result.x
    for i in range(item_count):
        pos = tuple(max(0.0, float(solution[v])) for v in position_variables[i])
        selected = max(
            range(len(orientation_variables[i])),
            key=lambda j: solution[orientation_variables[i][j]],
        )
        dims = orientations[i][selected]
        placements.append((pos, dims))
    return True, placements


def _confidence(packages: Sequence[Package]) -> tuple[str, list[str]]:
    provisional = sorted({p.sku for p in packages if p.verification_status == PROVISIONAL_STATUS and p.sku})
    unsupported = [
        p for p in packages
        if p.verification_status not in VERIFIED_STATUSES | {PROVISIONAL_STATUS}
    ]
    if unsupported:
        return "blocked", provisional
    return ("provisional" if provisional else "verified"), provisional


def optimize_packages(
    packages: Sequence[Package],
    *,
    catalog: Sequence[tuple[float, float, float]],
    time_limit: float = 15.0,
) -> OptimizationResult:
    if not packages:
        raise ValueError("At least one package is required")
    confidence, provisional = _confidence(packages)
    if confidence == "blocked":
        return OptimizationResult("blocked", None, None, [], confidence, provisional,
                                  "One or more package records need review before packing.")
    items = [p.dimensions for p in packages]
    candidates = sorted(enumerate(catalog), key=lambda x: (volume(x[1]), x[0]))
    unresolved_smaller = False
    for _, carton in candidates:
        status, placements = solve_packing(items, carton, time_limit)
        if status is None:
            unresolved_smaller = True
            continue
        if not status:
            continue
        if unresolved_smaller:
            return OptimizationResult("unresolved", None, None, [], confidence, provisional,
                                      "Could not prove the smallest fitting carton within the time limit.")
        empty = 100.0 * (volume(carton) - sum(volume(i) for i in items)) / volume(carton)
        rounded = int(math.floor(empty + 0.5))
        ordered_packages = sorted(
            enumerate(packages),
            key=lambda entry: (volume(items[entry[0]]), max(items[entry[0]]), sum(items[entry[0]])),
            reverse=True,
        )
        placement_data = []
        for (pos, dims), (original_index, package) in zip(placements, ordered_packages):
            placement_data.append({
                "position": list(pos), "dimensions": list(dims), "package_index": original_index,
                "sku": package.sku, "name": package.name, "part": package.part,
                "verification_status": package.verification_status,
            })
        prefix = "Provisional best box" if confidence == "provisional" else "Best box"
        dims_text = " × ".join(str(int(v)) if float(v).is_integer() else str(v) for v in carton)
        return OptimizationResult("ok", tuple(carton), rounded, placement_data, confidence, provisional,
                                  f"{prefix}: {dims_text} — approximately {rounded}% empty space.")
    if unresolved_smaller:
        return OptimizationResult("unresolved", None, None, [], confidence, provisional,
                                  "Could not determine whether a catalog carton fits within the time limit.")
    return OptimizationResult("no_fit", None, None, [], confidence, provisional,
                              "No catalog carton fits all of these product boxes together.")


def _stock_for(carton: Sequence[float], inventory: dict[str, Any]) -> int | None:
    return _safe_stock(inventory.get("stock", {}).get(_carton_key(carton)))


def _optimize_stock_aware(packages: Sequence[Package]) -> dict[str, Any]:
    catalog = load_catalog()
    inventory = _ensure_inventory()
    ideal = optimize_packages(packages, catalog=catalog)
    available = [c for c in catalog if _stock_for(c, inventory) != 0]
    selected = optimize_packages(packages, catalog=available) if available else None

    if selected is None:
        response = ideal.to_dict()
        if ideal.status == "ok":
            response.update({
                "status": "no_stock_fit", "carton": None, "placements": [], "empty_space_percent": None,
                "recommendation_tier": "no_stock_fit",
                "ideal_carton": list(ideal.carton) if ideal.carton else None,
                "ideal_carton_stock": 0,
                "selected_carton_stock": None,
                "message": "A catalog carton fits, but every eligible fitting carton is marked out of stock.",
            })
        return response

    response = selected.to_dict()
    response["recommendation_tier"] = "best_fit"
    response["ideal_carton"] = list(ideal.carton) if ideal.status == "ok" and ideal.carton else None
    response["ideal_carton_stock"] = _stock_for(ideal.carton, inventory) if ideal.status == "ok" and ideal.carton else None
    response["selected_carton_stock"] = _stock_for(selected.carton, inventory) if selected.status == "ok" and selected.carton else None
    if selected.status == "ok" and ideal.status == "ok" and selected.carton and ideal.carton:
        if _carton_key(selected.carton) != _carton_key(ideal.carton):
            response["recommendation_tier"] = "next_best_available"
            response["message"] = (
                "Next best available box selected because the smaller best-fit carton is out of stock."
            )
        elif response["selected_carton_stock"] is None:
            response["recommendation_tier"] = "best_fit_stock_untracked"
            response["stock_warning"] = "This carton has not been counted yet."
        else:
            response["recommendation_tier"] = "best_fit_in_stock"
    return response


def build_packing_plan(expanded_order: dict[str, Any]) -> dict[str, Any]:
    physical = list(expanded_order.get("physical_packages") or [])
    unresolved = list((expanded_order.get("packing_readiness") or {}).get("unresolved") or [])
    fixed = []
    loose = []
    for raw in physical:
        behavior = str(raw.get("shipping_behavior") or "standard")
        if behavior in FACTORY_BEHAVIORS:
            fixed.append(raw)
        else:
            loose.append(raw)

    loose_result = None
    if loose:
        packages = []
        for raw in loose:
            dims = raw.get("dimensions_in") or []
            if len(dims) != 3:
                continue
            packages.append(Package(
                tuple(float(v) for v in dims),
                sku=raw.get("sku"), name=raw.get("product_name"), part=raw.get("part"),
                verification_status=raw.get("verification_status"),
            ))
        if packages:
            loose_result = _optimize_stock_aware(packages)

    shipping_packages = []
    for raw in fixed:
        weight = _safe_float(raw.get("weight_kg"))
        shipping_packages.append({
            "package_type": "factory_carton",
            "label": f"{raw.get('sku') or raw.get('product_name') or 'Factory carton'}" + (f" — {raw.get('part')}" if raw.get("part") else ""),
            "dimensions_in": raw.get("dimensions_in"),
            "calculated_weight_kg": weight,
            "weight_complete": weight is not None,
            "weight_note": "Stored factory-package weight." if weight is not None else "Stored package weight is missing; confirm on scale.",
            "verification_status": raw.get("verification_status"),
            "contents": [{"sku": raw.get("sku"), "name": raw.get("product_name"), "part": raw.get("part"), "quantity": 1}],
            "stamp_accessories_inside": False,
        })

    if loose and loose_result and loose_result.get("status") == "ok":
        missing = []
        total_weight = 0.0
        contents = []
        for raw in loose:
            contents.append({"sku": raw.get("sku"), "name": raw.get("product_name"), "part": raw.get("part"), "quantity": 1})
            weight = _safe_float(raw.get("weight_kg"))
            if weight is None:
                missing.append(raw.get("sku") or raw.get("product_name") or "item")
            else:
                total_weight += weight
        shipping_packages.append({
            "package_type": "warehouse_carton",
            "label": "Warehouse carton for remaining loose items",
            "dimensions_in": loose_result.get("carton"),
            "calculated_weight_kg": None if missing else round(total_weight, 4),
            "weight_complete": not missing,
            "weight_note": (
                "Known item weights only; outer-carton tare is not included. Confirm final scale weight."
                if not missing else "Missing stored weight for: " + ", ".join(dict.fromkeys(missing)) + ". Confirm final scale weight."
            ),
            "verification_status": loose_result.get("confidence"),
            "carton_recommendation_tier": loose_result.get("recommendation_tier"),
            "carton_stock_on_hand": loose_result.get("selected_carton_stock"),
            "ideal_carton": loose_result.get("ideal_carton"),
            "empty_space_percent": loose_result.get("empty_space_percent"),
            "contents": contents,
            "stamp_accessories_inside": False,
        })

    for index, package in enumerate(shipping_packages, start=1):
        package["package_number"] = index

    if unresolved:
        status = "partial_unresolved"
    elif loose and not loose_result:
        status = "blocked"
    elif loose_result and loose_result.get("status") != "ok":
        status = str(loose_result.get("status"))
    else:
        status = "ok"

    return {
        "status": status,
        "order_reference": expanded_order.get("name") or expanded_order.get("order_number"),
        "fixed_package_count": len(fixed),
        "loose_item_count": len(loose),
        "loose_result": loose_result,
        "unresolved": unresolved,
        "shipping_summary": {
            "complete": status == "ok",
            "package_count": len(shipping_packages),
            "packages": shipping_packages,
            "all_weights_complete": bool(shipping_packages) and all(p.get("weight_complete") for p in shipping_packages),
            "requires_scale_confirmation": any(p.get("package_type") == "warehouse_carton" or not p.get("weight_complete") for p in shipping_packages),
        },
        "accessory_learning_enabled": False,
        "message": (
            "Packing plan created."
            if status == "ok" else
            "Packing plan needs review before shipment."
        ),
    }
