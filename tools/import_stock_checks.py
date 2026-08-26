"""One-time import of the ops sheet's "Inventory Check" tab into TC-Planner.

Vendor names differ between the sheet and the planner, so the mapping is
explicit rather than fuzzy — a wrong guess would silently attach a stock
check to the wrong vendor.

Run with --apply to write; without it, it only reports.
"""
import os, sys, json, datetime
import httpx
from dotenv import load_dotenv

load_dotenv(r"C:\tc-planner\backend\.env")
BASE = "https://tc-planner-app.azurewebsites.net"
H = {"Authorization": "Bearer " + os.environ["TC_PLANNER_AUTH_TOKEN"]}
SHEET = "https://tc-dashboard-proxy.azurewebsites.net/api/sheets?range=inventory"

# sheet name -> planner vendor name
MAP = {
    "ADM":                         "ADM Accessories",
    "Astrel":                      "Astrel Instruments",
    "Baader":                      "Baader Planetarium",
    "Buckeye":                     "Buckeye Stargazer",
    "DwarfLAB":                    "DWARFLAB",   # planner stores it uppercase
    "Explore Hut":                 "ExploreHut",
    # NOT mapped to "Explore Scientific" — despite the near-identical names
    # these are two separate brands, each its own planner vendor.
    "Formac":                      "Formac Lorimer Books",
    "Generic / Telescopes Canada": "Telescopes Canada",
    "HeatIt":                      "Heat It",
    "Hubble":                      "Hubble Optics",
    "IDAS/Sightron":               "IDAS",
    "Ikarus":                      "Ikarus Technologies",
    "inventor.io":                 "inventr.io",
    "Kendrick":                    "Kendrick Astro Instruments",
    "Meade":                       "Meade Instruments",
    "Pegasus":                     "Pegasus Astro",
    "QHY":                         "QHYCCD",
    "Rigel":                       "Rigel Systems",
    "Sharpstar":                   "Sharpstar Optics",
}

# SeeStar is a ZWO sub-brand and its SKUs already sit under vendor "ZWO" in
# Shopify, which was counted 2026-06-29 — newer than the standalone SeeStar
# row — so merging it in must not drag ZWO's date backwards. Skipped for
# that reason, not because it has no home.
SKIP = {
    "SeeStar": "merges into ZWO, whose own count (2026-06-29) is newer",
}

# "Explore Science" and "Explore Scientific" are separate brands despite the
# similar names, and Vaonis is a real (rarely ordered) supplier. All three
# are planner vendors in their own right.
MAP_EXTRA_NOTE = "Explore Science / Explore Scientific / Vaonis are distinct vendors"


def parse_date(s):
    s = (s or "").strip()
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%Y-%m-%d", "%d %B %Y", "%m/%d/%Y"):
        try:
            return datetime.datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def main():
    apply = "--apply" in sys.argv
    with httpx.Client(timeout=120) as c:
        rows = c.get(SHEET).json()["values"][1:]
        planner = c.get(f"{BASE}/api/vendors", headers=H).json()
        planner = planner if isinstance(planner, list) else planner.get("vendors", [])
        known = {v["vendor"] for v in planner}

        todo, skipped, unresolved, bad_date = [], [], [], []
        for r in rows:
            if not r or not r[0].strip():
                continue
            name = r[0].strip()
            if name in SKIP:
                skipped.append((name, SKIP[name]))
                continue
            target = MAP.get(name, name)
            if target not in known:
                unresolved.append((name, target))
                continue
            iso = parse_date(r[1] if len(r) > 1 else "")
            if not iso:
                bad_date.append((name, r[1] if len(r) > 1 else ""))
                continue
            todo.append({
                "vendor": target,
                "date":   iso,
                "by":     (r[2].strip() if len(r) > 2 else "") or None,
                "notes":  (r[3].strip() if len(r) > 3 else "") or None,
            })

        print(f"to import : {len(todo)}")
        print(f"skipped   : {len(skipped)}")
        for n, why in skipped:
            print(f"    {n!r}: {why}")
        if unresolved:
            print(f"UNRESOLVED: {len(unresolved)}")
            for n, t in unresolved:
                print(f"    {n!r} -> {t!r} not in planner")
        if bad_date:
            print(f"BAD DATES : {len(bad_date)}")
            for n, d in bad_date:
                print(f"    {n!r}: {d!r}")

        if not apply:
            print("\n(dry run — pass --apply to write)")
            for t in todo[:5]:
                print("   ", t)
            return

        ok = fail = 0
        for t in todo:
            try:
                resp = c.post(f"{BASE}/api/vendors/{httpx.URL(t['vendor']).path.lstrip('/') or t['vendor']}/stock-check",
                              headers=H, json={"date": t["date"], "by": t["by"], "notes": t["notes"]})
                resp.raise_for_status()
                ok += 1
            except Exception as e:
                fail += 1
                print(f"  FAILED {t['vendor']}: {str(e)[:120]}")
        print(f"\nimported ok: {ok}   failed: {fail}")


main()
