/*  TELCAN — DROP the deprecated Shopify mirror  (STEP 2 of 2: PERMANENT)
    ---------------------------------------------------------------------
    ONLY run this after telcan_mirror_deprecate.sql has been in place for
    ~2 weeks with no complaints. THIS IS NOT REVERSIBLE -- once dropped,
    the only recovery is an Azure SQL point-in-time restore (Basic tier
    retains 7 days).

    Take a safety copy first if you want one, e.g. via
    "Export data-tier application (.bacpac)" in SSMS / the Azure portal.

    Reclaims ~60,000 rows across 12 tables on a 2 GB Basic database.
*/

-- 1) The dead ETL procedures that populated the mirror. They only touch the
--    mirror tables, so they are useless once those are gone.
DROP PROCEDURE IF EXISTS usp_ETL_Shopify_Products_Full;
DROP PROCEDURE IF EXISTS usp_ETL_Shopify_Variants_Full;
DROP PROCEDURE IF EXISTS usp_ETL_Shopify_Images_Full;
DROP PROCEDURE IF EXISTS usp_ETL_Shopify_Inventory_Full;
DROP PROCEDURE IF EXISTS usp_Update_Shopify_Inventory_HS_Codes;

-- 2) The mirror tables themselves.
DROP TABLE IF EXISTS zz_deprecated_Stg_Shopify_Products_Load;
DROP TABLE IF EXISTS zz_deprecated_Stg_Shopify_Variants_Load;
DROP TABLE IF EXISTS zz_deprecated_Stg_Shopify_Images_Load;
DROP TABLE IF EXISTS zz_deprecated_Stg_Shopify_Inventory_Load;
DROP TABLE IF EXISTS zz_deprecated_Stg_Shopify_Products;
DROP TABLE IF EXISTS zz_deprecated_Stg_Shopify_Variants;
DROP TABLE IF EXISTS zz_deprecated_Stg_Shopify_Images;
DROP TABLE IF EXISTS zz_deprecated_Stg_Shopify_Inventory;
DROP TABLE IF EXISTS zz_deprecated_Shopify_Products;
DROP TABLE IF EXISTS zz_deprecated_Shopify_Variants;
DROP TABLE IF EXISTS zz_deprecated_Shopify_Images;
DROP TABLE IF EXISTS zz_deprecated_Shopify_Inventory;
DROP TABLE IF EXISTS zz_deprecated_Error_Variants;

-- 3) Reclaim space and confirm.
DBCC SHRINKDATABASE (TELCAN, 10);

SELECT name FROM sys.tables WHERE name LIKE 'zz_deprecated_%';  -- expect 0 rows
