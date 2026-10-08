"""Package verification policy and read-only health metrics. No age-based expiry."""
import hashlib
import json
import math
from datetime import datetime, timezone
from functools import wraps
from threading import RLock

from .shipping_registry import VERIFIED_STATUSES, PROVISIONAL_STATUS

LOCK = RLock()
REVIEW = 'Needs review'
MEASURED_FIELDS = ('sku','part','dimensions_in','weight_kg','packages_per_unit','shipping_behavior')


def locked(fn):
    @wraps(fn)
    def call(*args, **kwargs):
        with LOCK:
            return fn(*args, **kwargs)
    return call


def revision(record):
    return hashlib.sha256(json.dumps({k:v for k,v in record.items() if k != '_revision'},sort_keys=True).encode()).hexdigest()


def positive(v):
    try:
        return not isinstance(v, bool) and math.isfinite(float(v)) and float(v) > 0
    except (TypeError, ValueError):
        return False


def dimensions_valid(record):
    dims=record.get('dimensions_in')
    return isinstance(dims, (list,tuple)) and len(dims)==3 and all(positive(v) for v in dims)


def categories(record):
    if record.get('shipping_behavior') == 'digital':
        return dict.fromkeys(('verified','unverified','missing_dimensions','missing_weights','needs_review','ready'), False) | {'digital': True}
    valid=dimensions_valid(record)
    review=bool(record.get('needs_review')) or record.get('verification_status')==REVIEW
    verified=valid and not review and record.get('verification_status') in VERIFIED_STATUSES
    return {'verified':verified, 'unverified':valid and not verified and not review,
            'missing_dimensions':not valid, 'missing_weights':not positive(record.get('weight_kg')),
            'needs_review':review, 'ready':verified and positive(record.get('weight_kg')), 'digital':False}


def health(records, bundles):
    from .shipping_bundles import sku_key
    components=[r for r in records if sku_key(r.get('sku')) not in bundles]
    physical=[r for r in components if r.get('shipping_behavior') != 'digital']
    counts={k:sum(categories(r)[k] for r in physical) for k in categories({})}
    by_sku={}
    counts['digital']=sum(categories(r)['digital'] for r in components)
    for r in components: by_sku.setdefault(sku_key(r.get('sku')),[]).append(r)
    ready_bundles=sum(all(by_sku.get(sku_key(c['sku'])) and all(categories(r)['ready'] or categories(r)['digital'] for r in by_sku[sku_key(c['sku'])])
                          for c in b['components']) for b in bundles.values())
    return {**counts,'total':len(physical),'registry_records':len(records),
            'verified_percent':round(100*counts['verified']/len(physical),1) if physical else 0,
            'ready_percent':round(100*counts['ready']/len(physical),1) if physical else 0,
            'bundles':len(bundles),'ready_bundles':ready_bundles}


def suspicious(clean, existing):
    if clean.get('shipping_behavior') == 'digital':
        return []
    warnings=[]
    dims=clean.get('dimensions_in') or []
    if dimensions_valid(clean) and (min(dims)<0.1 or max(dims)>100):
        warnings.append('Unusual package size; check centimetres versus inches.')
    if positive(clean.get('weight_kg')) and float(clean['weight_kg'])>100:
        warnings.append('Unusually high package weight; check kilograms versus other units.')
    if existing:
        old=existing.get('dimensions_in') or []
        if dimensions_valid(existing) and dimensions_valid(clean):
            ratios=[a/b for a,b in zip(sorted(dims),sorted(old))]
            if any(r>=2 or r<=0.5 for r in ratios):
                warnings.append('A dimension changed by at least a factor of two. Confirm the units and packaged measurement.')
        if positive(clean.get('weight_kg')) and positive(existing.get('weight_kg')):
            ratio=clean['weight_kg']/existing['weight_kg']
            if ratio>=2 or ratio<=0.5: warnings.append('Weight changed by at least a factor of two. Check the packaged weight and units.')
    return warnings


def snapshot(record):
    return {k:v for k,v in record.items() if k not in ('change_history','_revision')}


def audit(clean, existing, user, action, reason=''):
    clean['change_history']=list((existing or {}).get('change_history',[]))+[{
        'at':datetime.now(timezone.utc).isoformat(),'by':user or 'Unknown','action':action,'reason':reason,
        'before':snapshot(existing) if existing else None,'after':snapshot(clean)}]
    return clean


def prepare_save(raw, clean, existing, user):
    if existing and raw.get('_revision') != revision(existing):
        raise ValueError('This package changed since you opened it. Reload and select the record again before saving.')
    changed=existing is None or any(clean.get(k)!=existing.get(k) for k in MEASURED_FIELDS)
    warnings=suspicious(clean,existing)
    clean['needs_review']=bool((existing or {}).get('needs_review')) or (existing or {}).get('verification_status')==REVIEW or bool(warnings)
    clean['review_reason']=(existing or {}).get('review_reason','')
    clean['review_warnings']=list(dict.fromkeys(list((existing or {}).get('review_warnings',[]))+warnings))
    clean['verification_status']=REVIEW if clean['needs_review'] else PROVISIONAL_STATUS if changed else existing.get('verification_status',PROVISIONAL_STATUS)
    clean['last_verified']=(existing or {}).get('last_verified','')
    clean['measured_by']=(existing or {}).get('measured_by','')
    return audit(clean,existing,user,'created' if existing is None else 'edited',
                 'Package data changed; verification reset.' if changed else 'Metadata edited; verification retained.')
