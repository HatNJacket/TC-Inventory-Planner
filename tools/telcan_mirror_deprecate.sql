/*  TELCAN — deprecate the stale Shopify mirror  (STEP 1 of 2: reversible)
    ---------------------------------------------------------------------
    Context
      The Shopify_* / Stg_Shopify_* tables are the remains of a Shopify->SQL
      ETL that last loaded on 2025-12-08. Verified 2026-08-07:
        * No application code queries them (tc-planner, Shopify-Azure,
          tc-dashboard all searched, case-sensitive, in SQL context).
        * No Data Factory / Logic App exists to repopulate them.
        * No foreign keys reference them.
        * TC-Planner uses `product_velocity_cache` instead, which is
          refreshed automatically (inventory every 30 min 12:00-23:59 UTC,
          full refresh daily 12:45 UTC).
      ~60,000 rows across 12 tables on a Basic-tier (2 GB) database.

    What this does
      Renames each table to zz_deprecated_<name>. Nothing is deleted, so if
      an unknown consumer (Power BI, Excel, an ad-hoc tool) was reading
      them, it fails loudly and immediately -- run the rollback script and
      you are back to the previous state in seconds.

    Leave running for ~2 weeks. If nothing complains, run
    telcan_mirror_drop.sql to reclaim the space.

    NOTE: sp_rename emits a caution message about breaking scripts. That is
    expected here -- breaking (detectably) is the entire point of step 1.
*/

EXEC sp_rename 'Shopify_Products',           'zz_deprecated_Shopify_Products';
EXEC sp_rename 'Shopify_Variants',           'zz_deprecated_Shopify_Variants';
EXEC sp_rename 'Shopify_Images',             'zz_deprecated_Shopify_Images';
EXEC sp_rename 'Shopify_Inventory',          'zz_deprecated_Shopify_Inventory';

EXEC sp_rename 'Stg_Shopify_Products',       'zz_deprecated_Stg_Shopify_Products';
EXEC sp_rename 'Stg_Shopify_Products_Load',  'zz_deprecated_Stg_Shopify_Products_Load';
EXEC sp_rename 'Stg_Shopify_Variants',       'zz_deprecated_Stg_Shopify_Variants';
EXEC sp_rename 'Stg_Shopify_Variants_Load',  'zz_deprecated_Stg_Shopify_Variants_Load';
EXEC sp_rename 'Stg_Shopify_Images',         'zz_deprecated_Stg_Shopify_Images';
EXEC sp_rename 'Stg_Shopify_Images_Load',    'zz_deprecated_Stg_Shopify_Images_Load';
EXEC sp_rename 'Stg_Shopify_Inventory',      'zz_deprecated_Stg_Shopify_Inventory';
EXEC sp_rename 'Stg_Shopify_Inventory_Load', 'zz_deprecated_Stg_Shopify_Inventory_Load';
EXEC sp_rename 'Error_Variants',             'zz_deprecated_Error_Variants';

-- Verify
SELECT name, create_date FROM sys.tables
WHERE name LIKE 'zz_deprecated_%' ORDER BY name;
