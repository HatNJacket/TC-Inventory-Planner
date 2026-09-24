import { useState, useEffect } from 'react';
import * as api from './api';

// The staff roster is shared with the returns and inventory-verification
// apps and edited on the Settings page. It changes a few times a year, so a
// module-level cache keeps every dropdown on one fetch per page load rather
// than one per row.

const FALLBACK = ['Matt', 'Clay', 'Steve', 'Nick', 'Alex'];

let cache = null;        // last known roster
let inflight = null;     // de-dupes concurrent first loads
const listeners = new Set();

function publish(names) {
  cache = names;
  listeners.forEach(fn => fn(names));
}

export function loadStaffRoster({ force = false } = {}) {
  if (cache && !force) return Promise.resolve(cache);
  if (inflight && !force) return inflight;
  inflight = api.getStaff()
    .then(d => {
      const names = Array.isArray(d?.staff) ? d.staff.filter(Boolean) : [];
      publish(names.length ? names : FALLBACK);
      return cache;
    })
    .catch(err => {
      console.warn('Staff roster unavailable, using defaults:', err);
      publish(cache || FALLBACK);
      return cache;
    })
    .finally(() => { inflight = null; });
  return inflight;
}

// Call after saving so open dropdowns pick the new names up immediately.
export function refreshStaffRoster() {
  return loadStaffRoster({ force: true });
}

export function useStaffRoster() {
  const [names, setNames] = useState(cache || FALLBACK);
  useEffect(() => {
    listeners.add(setNames);
    loadStaffRoster();
    return () => listeners.delete(setNames);
  }, []);
  return names;
}
