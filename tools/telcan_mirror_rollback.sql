/*  TELCAN — ROLLBACK the mirror deprecation
    ----------------------------------------
    Run this if anything broke after telcan_mirror_deprecate.sql.
    Restores the original table names. No data was ever deleted by the
    deprecate step, so this is a complete, lossless undo.
*/

EXEC sp_rename 'zz_deprecated_Shopify_Products',           'Shopify_Products';
EXEC sp_rename 'zz_deprecated_Shopify_Variants',           'Shopify_Variants';
EXEC sp_rename 'zz_deprecated_Shopify_Images',             'Shopify_Images';
EXEC sp_rename 'zz_deprecated_Shopify_Inventory',          'Shopify_Inventory';

EXEC sp_rename 'zz_deprecated_Stg_Shopify_Products',       'Stg_Shopify_Products';
EXEC sp_rename 'zz_deprecated_Stg_Shopify_Products_Load',  'Stg_Shopify_Products_Load';
EXEC sp_rename 'zz_deprecated_Stg_Shopify_Variants',       'Stg_Shopify_Variants';
EXEC sp_rename 'zz_deprecated_Stg_Shopify_Variants_Load',  'Stg_Shopify_Variants_Load';
EXEC sp_rename 'zz_deprecated_Stg_Shopify_Images',         'Stg_Shopify_Images';
EXEC sp_rename 'zz_deprecated_Stg_Shopify_Images_Load',    'Stg_Shopify_Images_Load';
EXEC sp_rename 'zz_deprecated_Stg_Shopify_Inventory',      'Stg_Shopify_Inventory';
EXEC sp_rename 'zz_deprecated_Stg_Shopify_Inventory_Load', 'Stg_Shopify_Inventory_Load';
EXEC sp_rename 'zz_deprecated_Error_Variants',             'Error_Variants';

SELECT name FROM sys.tables WHERE name LIKE 'Shopify_%' OR name LIKE 'Stg_Shopify%'
   OR name = 'Error_Variants' ORDER BY name;
