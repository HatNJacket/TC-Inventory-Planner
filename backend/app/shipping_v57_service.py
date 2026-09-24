"""V5.7 warehouse packing intelligence adapted for TC Inventory Planner.

This module ports the stateful parts of the standalone Shipping Tools V5.7.1
prototype into the Planner backend while reusing the Planner's package registry,
carton inventory, authenticated user identity, and Phase 2A 3D optimizer.
"""
from __future__ import annotations

import json
import math
import uuid
from functools import lru_cache
from datetime import date, datetime
from pathlib import Path
from typing import Any

from . import shipping_registry
from .shipping_optimizer import Package, _optimize_stock_aware
from .shipping_registry import PROVISIONAL_STATUS, VERIFIED_STATUSES

DATA_DIR = Path(__file__).resolve().parent / "data"
REGISTRY_PATH = DATA_DIR / "shipping_package_registry.json"
PACKING_HISTORY_PATH = DATA_DIR / "shipping_packing_history.json"

ALLOWED_VERIFICATION_STATUSES = VERIFIED_STATUSES | {PROVISIONAL_STATUS}
SHIPPING_BEHAVIORS = {"standard", "carrier_ready", "accessory_carrier", "must_ship_alone"}
ACCESSORY_AUTO_REJECT_FAILURES = 3
COMBINATION_STRONG_CONFIRM_SUCCESSES = 3


def _safe_number(value: object, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a number") from exc
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"{label} must be greater than 0")
    return number


def _safe_optional_number(value: object, label: str) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    return _safe_number(value, label)


def _safe_positive_int(value: object, label: str, *, default: int = 1) -> int:
    if value is None or str(value).strip() == "":
        return default
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a whole number") from exc
    if not math.isfinite(number) or number < 1 or not number.is_integer():
        raise ValueError(f"{label} must be a whole number of 1 or more")
    return int(number)


def _clean_text(value: object, label: str, *, required: bool = False, max_length: int = 250) -> str:
    text = str(value or "").strip()
    if required and not text:
        raise ValueError(f"{label} is required")
    if len(text) > max_length:
        raise ValueError(f"{label} is too long")
    return text


def _valid_dimensions_list(value: object) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return None
    try:
        dims = [float(v) for v in value]
    except (TypeError, ValueError):
        return None
    if any(not math.isfinite(v) or v <= 0 for v in dims):
        return None
    return dims


def load_registry() -> list[dict[str, Any]]:
    if not REGISTRY_PATH.exists():
        return []
    stat = REGISTRY_PATH.stat()
    return list(_cached_registry(str(REGISTRY_PATH), stat.st_mtime_ns, stat.st_size))


@lru_cache(maxsize=2)
def _cached_registry(path: str, modified: int, size: int) -> tuple:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("records"), list):
        raise ValueError("Package registry is malformed")
    rows = list(payload["records"])
    rows.sort(key=lambda r: (str(r.get("sku", "")).lower(), str(r.get("part", "")).lower()))
    return tuple(rows)


def _write_registry(records: list[dict[str, Any]]) -> None:
    temp = REGISTRY_PATH.with_suffix(".tmp")
    temp.write_text(json.dumps({"version": 2, "records": records}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temp.replace(REGISTRY_PATH)
    _cached_registry.cache_clear()
    shipping_registry.reload_registry()


def registry_payload(query: str = "", *, limit: int = 50) -> dict[str, Any]:
    records = load_registry()
    q = query.strip().lower()
    if q:
        records = [
            r for r in records
            if q in str(r.get("sku", "")).lower()
            or q in str(r.get("product_name", "")).lower()
            or q in str(r.get("part", "")).lower()
        ]
    total = len(records)
    limit = max(1, min(int(limit), 50))
    return {"status": "ok", "count": total, "records": records[:limit], "truncated": total > limit}


def normalize_registry_record(raw: dict[str, Any], *, existing_id: str | None = None, measured_by_default: str = "") -> dict[str, Any]:
    sku = _clean_text(raw.get("sku"), "SKU", required=True, max_length=100)
    product_name = _clean_text(raw.get("product_name"), "Product name", required=True, max_length=250)
    part = _clean_text(raw.get("part"), "Part", max_length=120)
    status = _clean_text(raw.get("verification_status"), "Verification status", required=True, max_length=80)
    if status not in ALLOWED_VERIFICATION_STATUSES:
        raise ValueError("Unsupported verification status")
    dims = _valid_dimensions_list(raw.get("dimensions_in"))
    if not dims:
        raise ValueError("dimensions_in must be three positive numbers")
    behavior = _clean_text(raw.get("shipping_behavior") or "standard", "Shipping behavior", required=True, max_length=40)
    if behavior not in SHIPPING_BEHAVIORS:
        raise ValueError("Unsupported shipping behavior")
    last_verified = _clean_text(raw.get("last_verified"), "Last verified", max_length=20)
    if status in VERIFIED_STATUSES and not last_verified:
        last_verified = date.today().isoformat()
    measured_by = _clean_text(raw.get("measured_by") or measured_by_default, "Measured by", max_length=120)
    return {
        "id": existing_id or str(raw.get("id") or uuid.uuid4()),
        "sku": sku,
        "product_name": product_name,
        "part": part,
        "dimensions_in": dims,
        "verification_status": status,
        "last_verified": last_verified,
        "notes": _clean_text(raw.get("notes"), "Notes", max_length=1200),
        "packages_per_unit": _safe_positive_int(raw.get("packages_per_unit"), "Copies per unit", default=1),
        "source_parts_per_unit": _safe_positive_int(raw.get("source_parts_per_unit"), "Source parts per unit", default=1),
        "weight_kg": _safe_optional_number(raw.get("weight_kg"), "Weight"),
        "measured_by": measured_by,
        "source_import": _clean_text(raw.get("source_import"), "Source import", max_length=250),
        "shipping_behavior": behavior,
        "stamp_accessories_inside": behavior == "accessory_carrier",
        "carrier_notes": _clean_text(raw.get("carrier_notes"), "Carrier notes", max_length=1200),
    }


def save_registry_record(raw: dict[str, Any], *, measured_by_default: str = "") -> dict[str, Any]:
    record_id = _clean_text(raw.get("id"), "Record id", max_length=100)
    records = load_registry()
    existing_index = next((i for i, r in enumerate(records) if str(r.get("id")) == record_id), None) if record_id else None
    clean = normalize_registry_record(raw, existing_id=record_id or None, measured_by_default=measured_by_default)
    same_sku = [r for r in records if str(r.get("sku", "")).strip().lower() == clean["sku"].lower() and r.get("id") != clean["id"]]
    if same_sku:
        if not clean["part"] or any(not str(r.get("part", "")).strip() for r in same_sku):
            raise ValueError("A SKU with multiple physical packages needs a part name on every package record")
        if any(str(r.get("part", "")).strip().lower() == clean["part"].lower() for r in same_sku):
            raise ValueError("That SKU already has a package record with the same part name")
    if existing_index is None:
        records.append(clean)
    else:
        records[existing_index] = clean
    _write_registry(records)
    return {"status": "ok", "record": clean}


def delete_registry_record(record_id: str) -> dict[str, Any]:
    record_id = _clean_text(record_id, "Record id", required=True, max_length=100)
    records = load_registry()
    kept = [r for r in records if str(r.get("id")) != record_id]
    if len(kept) == len(records):
        raise ValueError("Package record was not found")
    _write_registry(kept)
    return {"status": "ok", "deleted": record_id}


def _ensure_history() -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not PACKING_HISTORY_PATH.exists():
        PACKING_HISTORY_PATH.write_text(json.dumps({"version": 1, "observations": []}, indent=2) + "\n", encoding="utf-8")
    return PACKING_HISTORY_PATH


def load_packing_history() -> list[dict[str, Any]]:
    target = _ensure_history()
    payload = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("observations"), list):
        raise ValueError("Packing history is malformed")
    return list(payload["observations"])


def _write_packing_history(observations: list[dict[str, Any]]) -> None:
    target = _ensure_history()
    temp = target.with_suffix(".tmp")
    temp.write_text(json.dumps({"version": 1, "observations": observations}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temp.replace(target)


def save_packing_observation(payload: dict[str, Any], *, packed_by: str) -> dict[str, Any]:
    host_registry_id = _clean_text(payload.get("host_registry_id"), "Host package id", required=True, max_length=100)
    host_sku = _clean_text(payload.get("host_sku"), "Host SKU", required=True, max_length=100)
    host_part = _clean_text(payload.get("host_part"), "Host part", max_length=120)
    order_reference = _clean_text(payload.get("order_reference"), "Order reference", max_length=80)
    notes = _clean_text(payload.get("notes"), "Observation notes", max_length=1200)
    raw_results = payload.get("results")
    if not isinstance(raw_results, list) or not raw_results:
        raise ValueError("Record at least one accessory result")
    results = []
    for raw in raw_results:
        if not isinstance(raw, dict):
            raise ValueError("Accessory result must be an object")
        sku = _clean_text(raw.get("sku"), "Accessory SKU", required=True, max_length=100)
        fit_qty = int(raw.get("fit_qty") or 0)
        failed_qty = int(raw.get("failed_qty") or 0)
        if fit_qty < 0 or failed_qty < 0 or fit_qty + failed_qty < 1:
            raise ValueError("Each recorded accessory needs at least one fit or did-not-fit quantity")
        verification_status = _clean_text(raw.get("verification_status"), "Accessory verification status", max_length=80)
        if fit_qty and verification_status not in VERIFIED_STATUSES:
            raise ValueError(f"{sku} must be measured and verified before it can be recorded inside an accessory carrier")
        results.append({
            "sku": sku,
            "part": _clean_text(raw.get("part"), "Accessory part", max_length=120),
            "product_name": _clean_text(raw.get("product_name"), "Accessory product", max_length=250),
            "registry_id": _clean_text(raw.get("registry_id"), "Accessory package id", max_length=100),
            "verification_status": verification_status,
            "dimensions_in": _valid_dimensions_list(raw.get("dimensions_in")),
            "fit_qty": fit_qty,
            "failed_qty": failed_qty,
        })
    observation = {
        "id": str(uuid.uuid4()),
        "recorded_at": datetime.now().isoformat(timespec="seconds"),
        "order_reference": order_reference,
        "packed_by": packed_by or "Unknown",
        "notes": notes,
        "host_registry_id": host_registry_id,
        "host_sku": host_sku,
        "host_part": host_part,
        "stamp_required": True,
        "results": results,
    }
    observations = load_packing_history()
    observations.append(observation)
    _write_packing_history(observations)
    return {"status": "ok", "observation": observation, "count": len(observations)}


def packing_history_payload(limit: int = 200) -> dict[str, Any]:
    observations = load_packing_history()
    observations.sort(key=lambda item: str(item.get("recorded_at", "")), reverse=True)
    limit = max(1, min(int(limit), 500))
    return {"status": "ok", "count": len(observations), "observations": observations[:limit]}


def _accessory_key(sku: object, part: object) -> tuple[str, str]:
    return (str(sku or "").strip().lower(), str(part or "").strip().lower())


def _result_dimensions(result: dict[str, Any], registry_records: list[dict[str, Any]]) -> list[float] | None:
    direct = _valid_dimensions_list(result.get("dimensions_in"))
    if direct:
        return direct
    registry_id = str(result.get("registry_id") or "").strip()
    if registry_id:
        match = next((r for r in registry_records if str(r.get("id") or "") == registry_id), None)
        if match:
            dims = _valid_dimensions_list(match.get("dimensions_in"))
            if dims:
                return dims
    key = _accessory_key(result.get("sku"), result.get("part"))
    matches = [r for r in registry_records if _accessory_key(r.get("sku"), r.get("part")) == key]
    if len(matches) == 1:
        return _valid_dimensions_list(matches[0].get("dimensions_in"))
    return None


def _dims_dominate(larger: object, smaller: object) -> bool:
    a = _valid_dimensions_list(larger)
    b = _valid_dimensions_list(smaller)
    if not a or not b:
        return False
    return all(s <= l + 1e-6 for s, l in zip(sorted(b), sorted(a)))


def _expand_dimension_slots(items: list[dict[str, Any]], registry_records: list[dict[str, Any]], *, require_verified: bool = False) -> list[dict[str, Any]] | None:
    slots = []
    for item in items:
        if require_verified and str(item.get("verification_status") or "") not in VERIFIED_STATUSES:
            return None
        dims = _valid_dimensions_list(item.get("dimensions_in")) or _result_dimensions(item, registry_records)
        if not dims:
            return None
        qty = int(item.get("quantity") or item.get("fit_qty") or 0)
        for _ in range(max(0, qty)):
            slots.append({"sku": str(item.get("sku") or ""), "part": str(item.get("part") or ""), "dimensions_in": dims})
    return slots


def _dimension_slots_cover(observed_slots: list[dict[str, Any]], target_slots: list[dict[str, Any]]) -> tuple[bool, list[dict[str, Any]]]:
    if not target_slots or len(target_slots) > len(observed_slots):
        return False, []
    candidates = []
    for target in target_slots:
        matches = [i for i, observed in enumerate(observed_slots) if _dims_dominate(observed.get("dimensions_in"), target.get("dimensions_in"))]
        if not matches:
            return False, []
        candidates.append(matches)
    order = sorted(range(len(target_slots)), key=lambda i: len(candidates[i]))
    used: set[int] = set()
    assignment: dict[int, int] = {}
    def search(pos: int) -> bool:
        if pos >= len(order):
            return True
        target_index = order[pos]
        for observed_index in candidates[target_index]:
            if observed_index in used:
                continue
            used.add(observed_index)
            assignment[target_index] = observed_index
            if search(pos + 1):
                return True
            used.remove(observed_index)
            assignment.pop(target_index, None)
        return False
    if not search(0):
        return False, []
    proof = []
    for target_index, observed_index in sorted(assignment.items()):
        target = target_slots[target_index]
        observed = observed_slots[observed_index]
        proof.append({
            "target_sku": target.get("sku"), "target_part": target.get("part"), "target_dimensions_in": target.get("dimensions_in"),
            "proven_sku": observed.get("sku"), "proven_part": observed.get("part"), "proven_dimensions_in": observed.get("dimensions_in"),
        })
    return True, proof


def packing_history_summary(host_registry_id: str) -> dict[str, Any]:
    host_registry_id = _clean_text(host_registry_id, "Host package id", required=True, max_length=100)
    observations = [o for o in load_packing_history() if o.get("host_registry_id") == host_registry_id]
    registry_records = load_registry()
    by_accessory: dict[tuple[str, str], dict[str, Any]] = {}
    inference_envelopes = []
    for observation in observations:
        for result in observation.get("results", []):
            key = _accessory_key(result.get("sku"), result.get("part"))
            entry = by_accessory.setdefault(key, {
                "sku": result.get("sku", ""), "part": result.get("part", ""), "product_name": result.get("product_name", ""),
                "fit_attempts": 0, "failed_attempts": 0, "fit_qty": 0, "failed_qty": 0,
            })
            fit_qty = int(result.get("fit_qty") or 0)
            failed_qty = int(result.get("failed_qty") or 0)
            if fit_qty:
                entry["fit_attempts"] += 1; entry["fit_qty"] += fit_qty
                dims = _result_dimensions(result, registry_records)
                if dims:
                    inference_envelopes.append({"sku": result.get("sku", ""), "part": result.get("part", ""), "dimensions_in": dims})
            if failed_qty:
                entry["failed_attempts"] += 1; entry["failed_qty"] += failed_qty
    accessories = list(by_accessory.values())
    for entry in accessories:
        entry["auto_reject"] = entry["failed_attempts"] >= ACCESSORY_AUTO_REJECT_FAILURES and entry["fit_attempts"] == 0
    return {
        "status": "ok", "host_registry_id": host_registry_id, "observation_count": len(observations),
        "auto_reject_failure_threshold": ACCESSORY_AUTO_REJECT_FAILURES, "accessories": accessories,
        "inference_envelopes": inference_envelopes,
    }


def _fit_combo_from_results(results: list[dict[str, Any]]) -> dict[tuple[str, str], int]:
    combo: dict[tuple[str, str], int] = {}
    for result in results:
        qty = int(result.get("fit_qty") or 0)
        if qty > 0:
            key = _accessory_key(result.get("sku"), result.get("part"))
            combo[key] = combo.get(key, 0) + qty
    return combo


def _combo_covers(observed: dict[tuple[str, str], int], target: dict[tuple[str, str], int]) -> bool:
    return bool(target) and all(observed.get(key, 0) >= qty for key, qty in target.items())


def packing_combination_summary(host_registry_id: str, raw_accessories: object) -> dict[str, Any]:
    host_registry_id = _clean_text(host_registry_id, "Host package id", required=True, max_length=100)
    if not isinstance(raw_accessories, list) or not raw_accessories:
        return {"status": "ok", "host_registry_id": host_registry_id, "combination_status": "none", "exact_success_count": 0,
                "covering_success_count": 0, "dimensionally_inferred_count": 0, "all_items_individually_seen_fit": False,
                "strongly_confirmed": False, "threshold": COMBINATION_STRONG_CONFIRM_SUCCESSES}
    target: dict[tuple[str, str], int] = {}
    display = []
    target_items = []
    for raw in raw_accessories:
        if not isinstance(raw, dict):
            raise ValueError("Combination accessory must be an object")
        sku = _clean_text(raw.get("sku"), "Accessory SKU", required=True, max_length=100)
        part = _clean_text(raw.get("part"), "Accessory part", max_length=120)
        qty = _safe_positive_int(raw.get("quantity"), "Accessory quantity", default=1)
        key = _accessory_key(sku, part)
        target[key] = target.get(key, 0) + qty
        target_items.append({
            "sku": sku, "part": part, "quantity": qty, "dimensions_in": _valid_dimensions_list(raw.get("dimensions_in")),
            "verification_status": _clean_text(raw.get("verification_status"), "Verification status", max_length=80),
            "registry_id": _clean_text(raw.get("registry_id"), "Registry id", max_length=100),
        })
        display.append({"sku": sku, "part": part, "quantity": qty})
    observations = [o for o in load_packing_history() if o.get("host_registry_id") == host_registry_id]
    registry_records = load_registry()
    exact_success_count = 0
    covering_success_count = 0
    individual_fit_keys: set[tuple[str, str]] = set()
    individual_failed_keys: set[tuple[str, str]] = set()
    successful_combos = []
    dimensional_proofs = []
    target_slots = _expand_dimension_slots(target_items, registry_records, require_verified=True)
    for observation in observations:
        results = observation.get("results", [])
        for result in results:
            if int(result.get("fit_qty") or 0) > 0: individual_fit_keys.add(_accessory_key(result.get("sku"), result.get("part")))
            if int(result.get("failed_qty") or 0) > 0: individual_failed_keys.add(_accessory_key(result.get("sku"), result.get("part")))
        if not results or any(int(result.get("failed_qty") or 0) > 0 for result in results):
            continue
        observed = _fit_combo_from_results(results)
        if not observed: continue
        successful_combos.append(observed)
        if observed == target: exact_success_count += 1
        if _combo_covers(observed, target): covering_success_count += 1
        if target_slots:
            observed_items = []
            for result in results:
                fit_qty = int(result.get("fit_qty") or 0)
                if fit_qty <= 0: continue
                observed_items.append({**result, "quantity": fit_qty, "dimensions_in": _result_dimensions(result, registry_records)})
            observed_slots = _expand_dimension_slots(observed_items, registry_records, require_verified=True)
            if observed_slots:
                inferred, proof = _dimension_slots_cover(observed_slots, target_slots)
                if inferred:
                    dimensional_proofs.append({"observation_id": observation.get("id"), "recorded_at": observation.get("recorded_at"), "proof": proof})
    all_individual = all(key in individual_fit_keys for key in target)
    inference_blocked_by_failure = any(key in individual_failed_keys for key in target)
    if exact_success_count: status = "exact_confirmed"
    elif covering_success_count: status = "subset_supported"
    elif dimensional_proofs and not inference_blocked_by_failure: status = "dimensionally_inferred"
    elif all_individual: status = "individual_only"
    else: status = "unproven"
    return {
        "status": "ok", "host_registry_id": host_registry_id, "combination_status": status, "target": display,
        "exact_success_count": exact_success_count, "covering_success_count": covering_success_count,
        "dimensionally_inferred_count": len(dimensional_proofs) if not inference_blocked_by_failure else 0,
        "dimensional_inference": dimensional_proofs[0] if dimensional_proofs and not inference_blocked_by_failure else None,
        "dimensional_inference_blocked_by_failure": inference_blocked_by_failure,
        "all_items_individually_seen_fit": all_individual,
        "strongly_confirmed": exact_success_count >= COMBINATION_STRONG_CONFIRM_SUCCESSES,
        "threshold": COMBINATION_STRONG_CONFIRM_SUCCESSES,
        "successful_combination_observations": len(successful_combos),
    }


def plan_payload(payload: dict[str, Any]) -> dict[str, Any]:
    raw_items = payload.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        raise ValueError("Add at least one package before building a packing plan")
    fixed_packages: list[dict[str, Any]] = []
    loose_packages: list[Package] = []
    loose_rows: list[dict[str, Any]] = []
    packed_inside: list[dict[str, Any]] = []

    for raw in raw_items:
        if not isinstance(raw, dict):
            raise ValueError("Packing item must be an object")
        dims = _valid_dimensions_list(raw.get("dimensions_in"))
        if not dims:
            raise ValueError(f"{raw.get('sku') or 'Package'} needs three positive dimensions")
        quantity = _safe_positive_int(raw.get("quantity"), "Quantity", default=1)
        packed_count = int(raw.get("packed_inside_count") or 0)
        if packed_count < 0 or packed_count > quantity:
            raise ValueError("Packed-inside quantity cannot exceed package quantity")
        behavior = str(raw.get("shipping_behavior") or "standard")
        if behavior not in SHIPPING_BEHAVIORS:
            behavior = "standard"
        row = {
            "sku": str(raw.get("sku") or ""), "name": str(raw.get("product_name") or raw.get("name") or ""),
            "part": str(raw.get("part") or ""), "registry_id": str(raw.get("registry_id") or ""),
            "dimensions_in": dims, "verification_status": str(raw.get("verification_status") or ""),
            "weight_kg": _safe_optional_number(raw.get("weight_kg"), "Weight"), "shipping_behavior": behavior,
            "packed_inside_count": packed_count, "packed_into": str(raw.get("packed_into") or ""), "quantity": quantity,
        }
        if packed_count:
            packed_inside.append({
                "sku": row["sku"], "name": row["name"], "part": row["part"], "quantity": packed_count,
                "host_registry_id": row["packed_into"], "weight_kg_each": row["weight_kg"],
                "verification_status": row["verification_status"],
            })
        # Factory-carton behaviours always contribute their own shipping package(s).
        if behavior in {"carrier_ready", "accessory_carrier", "must_ship_alone"}:
            for _ in range(quantity):
                fixed_packages.append(dict(row))
            continue
        remaining = quantity - packed_count
        for _ in range(remaining):
            loose_packages.append(Package(tuple(dims), sku=row["sku"] or None, name=row["name"] or None,
                                          part=row["part"] or None, verification_status=row["verification_status"] or None))
            loose_rows.append(row)

    loose_result = _optimize_stock_aware(loose_packages) if loose_packages else None
    status = "ok"
    if loose_packages and (not loose_result or loose_result.get("status") != "ok"):
        status = str((loose_result or {}).get("status") or "blocked")

    packed_by_host: dict[str, list[dict[str, Any]]] = {}
    for packed in packed_inside:
        packed_by_host.setdefault(str(packed.get("host_registry_id") or ""), []).append(packed)
    assigned_hosts: set[str] = set()
    shipping_packages = []
    for fixed in fixed_packages:
        host_id = str(fixed.get("registry_id") or "")
        accessories = []
        if host_id and host_id not in assigned_hosts:
            accessories = packed_by_host.get(host_id, [])
            assigned_hosts.add(host_id)
        missing = []
        calculated = fixed.get("weight_kg")
        if calculated is None:
            missing.append(fixed.get("sku") or fixed.get("name") or "carrier package")
        else:
            calculated = float(calculated)
        contents = [{"sku": fixed.get("sku"), "name": fixed.get("name"), "part": fixed.get("part"), "quantity": 1}]
        for accessory in accessories:
            contents.append({"sku": accessory.get("sku"), "name": accessory.get("name"), "part": accessory.get("part"), "quantity": accessory.get("quantity", 1)})
            each = accessory.get("weight_kg_each")
            if each is None: missing.append(accessory.get("sku") or accessory.get("name") or "accessory")
            elif calculated is not None: calculated += float(each) * int(accessory.get("quantity") or 1)
        shipping_packages.append({
            "package_type": "factory_carton", "label": f"{fixed.get('sku') or fixed.get('name') or 'Carrier package'}" + (f" — {fixed.get('part')}" if fixed.get("part") else ""),
            "dimensions_in": fixed.get("dimensions_in"), "calculated_weight_kg": round(calculated, 4) if calculated is not None and not missing else None,
            "weight_complete": not missing, "weight_note": "Factory/package weight plus packed accessory weights." if not missing else "Missing stored weight for: " + ", ".join(dict.fromkeys(missing)),
            "verification_status": fixed.get("verification_status"), "contents": contents,
            "stamp_accessories_inside": bool(accessories and fixed.get("shipping_behavior") == "accessory_carrier"),
        })
    if loose_packages and loose_result and loose_result.get("status") == "ok":
        missing = []
        total_weight = 0.0
        grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
        for row in loose_rows:
            key = (row["sku"], row["name"], row["part"])
            grouped.setdefault(key, {"sku": row["sku"], "name": row["name"], "part": row["part"], "quantity": 0})["quantity"] += 1
            if row["weight_kg"] is None: missing.append(row["sku"] or row["name"] or "loose item")
            else: total_weight += float(row["weight_kg"])
        shipping_packages.append({
            "package_type": "warehouse_carton", "label": "Warehouse carton for remaining loose items",
            "dimensions_in": loose_result.get("carton"), "calculated_weight_kg": round(total_weight, 4) if not missing else None,
            "weight_complete": not missing, "weight_note": "Known loose-item weights only; outer-carton tare is not included. Confirm final weight on the scale before ShipStation." if not missing else "Missing stored weight for: " + ", ".join(dict.fromkeys(missing)) + ". Confirm final package weight on the scale before ShipStation.",
            "verification_status": loose_result.get("confidence"), "carton_recommendation_tier": loose_result.get("recommendation_tier"),
            "carton_stock_on_hand": loose_result.get("selected_carton_stock"), "ideal_carton": loose_result.get("ideal_carton"),
            "contents": list(grouped.values()), "stamp_accessories_inside": False,
        })
    for i, package in enumerate(shipping_packages, 1): package["package_number"] = i
    return {
        "status": status, "order_reference": _clean_text(payload.get("order_reference"), "Order reference", max_length=80),
        "fixed_packages": fixed_packages, "fixed_package_count": len(fixed_packages), "packed_inside": packed_inside,
        "loose_result": loose_result, "loose_item_count": len(loose_packages),
        "total_shipping_packages": len(shipping_packages) if status == "ok" else None,
        "shipping_summary": {
            "complete": status == "ok", "package_count": len(shipping_packages), "packages": shipping_packages,
            "all_weights_complete": bool(shipping_packages) and all(p.get("weight_complete") for p in shipping_packages),
            "requires_scale_confirmation": any(p.get("package_type") == "warehouse_carton" or not p.get("weight_complete") for p in shipping_packages),
        },
        "message": "Packing plan created." if status == "ok" else "A partial packing plan was created; remaining loose items need review.",
        "accessory_learning_enabled": True,
    }
