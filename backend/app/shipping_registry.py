"""Shipping package registry helpers for TC Inventory Planner.

Phase 1 is intentionally read-only: it loads the packaged dimension registry seed,
looks up Shopify line-item SKUs, and expands multi-package products into the
physical packages that the packing engine will consume in the next phase.
"""
from __future__ import annotations

import json
from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, List

from .shopify_client import normalize_sku

REGISTRY_PATH = Path(__file__).resolve().parent / "data" / "shipping_package_registry.json"
VERIFIED_STATUSES = {"Verified — Warehouse", "Confirmed — Vendor/Label"}
PROVISIONAL_STATUS = "Shopify — Unverified"


def _sku_key(value: str) -> str:
    return normalize_sku(value or "").strip().casefold()


@lru_cache(maxsize=1)
def _load_payload() -> Dict[str, Any]:
    if not REGISTRY_PATH.exists():
        return {"version": 2, "records": []}
    with REGISTRY_PATH.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict) or not isinstance(payload.get("records"), list):
        raise ValueError("Shipping package registry seed is malformed")
    return payload


@lru_cache(maxsize=1)
def _index() -> Dict[str, List[Dict[str, Any]]]:
    out: Dict[str, List[Dict[str, Any]]] = {}
    for raw in _load_payload().get("records", []):
        if not isinstance(raw, dict):
            continue
        sku = str(raw.get("sku") or "").strip()
        if not sku:
            continue
        out.setdefault(_sku_key(sku), []).append(raw)
    for rows in out.values():
        rows.sort(key=lambda r: (str(r.get("part") or "").casefold(), str(r.get("id") or "")))
    return out


def reload_registry() -> None:
    """Clear in-process caches after replacing the bundled seed."""
    _load_payload.cache_clear()
    _index.cache_clear()


def registry_status() -> Dict[str, Any]:
    records = _load_payload().get("records", [])
    statuses = Counter(str(r.get("verification_status") or "Unknown") for r in records)
    unique_skus = {_sku_key(str(r.get("sku") or "")) for r in records if str(r.get("sku") or "").strip()}
    return {
        "source": REGISTRY_PATH.name,
        "records": len(records),
        "unique_skus": len(unique_skus),
        "verified_records": sum(statuses.get(s, 0) for s in VERIFIED_STATUSES),
        "provisional_records": statuses.get(PROVISIONAL_STATUS, 0),
        "statuses": dict(statuses),
    }


def lookup_sku(sku: str) -> List[Dict[str, Any]]:
    rows = _index().get(_sku_key(sku), [])
    return [dict(row) for row in rows]


def lookup_many(skus: Iterable[str]) -> Dict[str, List[Dict[str, Any]]]:
    return {sku: lookup_sku(sku) for sku in skus}


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any):
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def expand_order(order: Dict[str, Any]) -> Dict[str, Any]:
    """Attach registry matches and expand order lines into physical packages.

    This does NOT choose cartons yet. It is the integration boundary between a
    Shopify order and the existing packing engine.
    """
    physical_packages: List[Dict[str, Any]] = []
    enriched_items: List[Dict[str, Any]] = []
    unresolved: List[Dict[str, Any]] = []
    provisional_skus = set()
    verified_skus = set()

    for line in order.get("line_items", []) or []:
        if line.get("requires_shipping") is False:
            continue

        sku = str(line.get("sku") or "").strip()
        qty = _safe_int(line.get("pack_quantity"), _safe_int(line.get("quantity"), 0))
        if qty <= 0:
            continue

        rows = lookup_sku(sku) if sku else []
        item = dict(line)
        item["pack_quantity"] = qty
        item["registry_records"] = rows

        if not sku:
            item["registry_state"] = "missing_sku"
            unresolved.append({
                "line_item_id": line.get("id"),
                "sku": "",
                "product": line.get("title") or "Unnamed line item",
                "reason": "Shopify line item has no SKU",
            })
            enriched_items.append(item)
            continue

        if not rows:
            item["registry_state"] = "not_found"
            unresolved.append({
                "line_item_id": line.get("id"),
                "sku": sku,
                "product": line.get("title") or sku,
                "reason": "SKU is not in the package dimension registry",
            })
            enriched_items.append(item)
            continue

        statuses = {str(row.get("verification_status") or "") for row in rows}
        if statuses and statuses.issubset(VERIFIED_STATUSES):
            item["registry_state"] = "verified"
            verified_skus.add(sku)
        elif PROVISIONAL_STATUS in statuses:
            item["registry_state"] = "provisional"
            provisional_skus.add(sku)
        else:
            item["registry_state"] = "review"

        for unit_index in range(1, qty + 1):
            for row in rows:
                packages_per_unit = max(1, _safe_int(row.get("packages_per_unit"), 1))
                dims = row.get("dimensions_in") or []
                if len(dims) != 3:
                    unresolved.append({
                        "line_item_id": line.get("id"),
                        "sku": sku,
                        "part": row.get("part") or "Primary package",
                        "reason": "Registry row does not have three usable dimensions",
                    })
                    continue
                for copy_index in range(1, packages_per_unit + 1):
                    physical_packages.append({
                        "package_instance_id": f"{line.get('id') or sku}:{unit_index}:{row.get('id') or row.get('part') or 'package'}:{copy_index}",
                        "line_item_id": line.get("id"),
                        "sku": sku,
                        "product_name": row.get("product_name") or line.get("title") or sku,
                        "part": row.get("part") or "Primary package",
                        "ordered_unit": unit_index,
                        "package_copy": copy_index,
                        "dimensions_in": [round(float(v), 4) for v in dims],
                        "verification_status": row.get("verification_status") or "Unknown",
                        "weight_kg": _safe_float(row.get("weight_kg")),
                        "shipping_behavior": row.get("shipping_behavior") or "standard",
                        "carrier_notes": row.get("carrier_notes") or "",
                        "registry_id": row.get("id"),
                    })

        enriched_items.append(item)

    return {
        **order,
        "line_items": enriched_items,
        "physical_packages": physical_packages,
        "packing_readiness": {
            "physical_package_count": len(physical_packages),
            "unresolved_count": len(unresolved),
            "unresolved": unresolved,
            "provisional_skus": sorted(provisional_skus),
            "verified_skus": sorted(verified_skus),
            "ready_for_verified_packing": not unresolved and not provisional_skus,
        },
        "registry": registry_status(),
    }
