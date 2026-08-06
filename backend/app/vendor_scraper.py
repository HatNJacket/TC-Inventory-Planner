"""
Vendor Scraper — vendor-agnostic structured product page extraction.

Each vendor has a JSON scrape_config (stored in vendor_settings.scrape_config)
that defines how to locate content sections on their product pages:
  - description: marketing copy / overview
  - features: bullet-point feature list
  - specs: technical specifications table (key-value pairs)
  - documents: support documents / manuals / firmware links
  - images: product image URLs

The config uses CSS-like content markers (text patterns in the HTML) rather
than brittle CSS selectors, since most vendor sites are table-heavy legacy
platforms (Volusion, BigCommerce) where class names are meaningless.

Extraction strategy per vendor platform:
  - iOptron (Volusion): All tab content in initial HTML, table-based specs
  - Celestron (Shopify): Structured product JSON in page, accordion sections
  - ZWO (WooCommerce): Tab-based with woocommerce-Tabs, table specs
  - Sky-Watcher (custom): Product detail sections with specific div classes
  - Baader (custom CMS): German/English pages with spec tables
"""
import logging
import re
import json
from typing import Dict, List, Optional, Any
from urllib.parse import urljoin
from html import unescape

import httpx

logger = logging.getLogger(__name__)

# ─── DEFAULT SCRAPE CONFIGS ──────────────────────────────────────
# These are the starting configs for known vendors. Users can customise
# them via the UI; these serve as sensible defaults when a vendor is
# first set up.

DEFAULT_CONFIGS: Dict[str, Dict] = {
    "iOptron": {
        "platform": "volusion",
        "base_url": "https://www.ioptron.com",
        "url_pattern": "https://www.ioptron.com/product-p/{sku}.htm",
        "notes": "Volusion template uses specific div IDs for each content section. Tab headers are grouped together but content is in separate divs: ProductDetail_ProductDetails_div (description), ProductDetail_TechSpecs_div (specs), ProductDetail_ExtInfo_div (documents), ProductDetail_ProductDetails_div2 (features).",
        "sections": {
            "description": {
                "method": "between_markers",
                "start": "itemprop='description'>",
                "end": "</span>",
                "notes": "The product description is inside a <span itemprop='description'> tag. This marker is unique on the page and captures only the description text, ending at the closing </span>."
            },
            "features": {
                "method": "between_markers",
                "start": "ProductDetail_ProductDetails_div2",
                "end": "</table>",
                "extract_as": "bullet_list",
                "notes": "Features are in a separate div (ProductDetail_ProductDetails_div2) outside the main tab content area. The <ul><li> items are inside a table structure within this div."
            },
            "specs": {
                "method": "between_markers",
                "start": "ProductDetail_TechSpecs_div",
                "end": "</div>",
                "extract_as": "key_value_table",
                "notes": "Specs table is inside div#ProductDetail_TechSpecs_div (initially display:none for tab switching). The key_value_table parser extracts <tr><td>key</td><td>value</td></tr> rows."
            },
            "documents": {
                "method": "between_markers",
                "start": "ProductDetail_ExtInfo_div",
                "end": "</div>",
                "extract_as": "link_list",
                "link_categories": {
                    "manual": ["Manual", "Quick Start", "Instruction", "QSG", "Operation"],
                    "firmware": ["Firmware", "Download Latest"],
                    "software": ["ASCOM", "Driver", "Commander", "iPolar Software"],
                },
                "notes": "Document links are inside div#ProductDetail_ExtInfo_div (initially display:none). Contains manual PDFs, firmware downloads, and software links."
            },
            "images": {
                "method": "og_and_product_images",
                "skip_patterns": ["logo", "icon", "pixel", "spacer", "clear1x1",
                                  "btn", "arrow", "sprite", "badge", "tracking",
                                  "vsecure", "cc.png", "template", "thumbnail",
                                  "small", "tiny", "50x50", "40x40", "30x30"],
                "include_patterns": ["photos", "vspfiles/photos"],
                "min_dimension_hint": 200,
                "notes": "Product images from og:image and Volusion's vspfiles/photos directory. Thumbnails (-2T.jpg, -3S.jpg) are filtered out. Full-size images from <a href> links are preferred."
            }
        }
    },
    "Celestron": {
        "platform": "shopify",
        "base_url": "https://www.celestron.com",
        "url_pattern": "https://www.celestron.com/products/{slug}",
        "sections": {
            "description": {
                "method": "shopify_product_json",
                "json_path": "product.description",
                "fallback_method": "between_markers",
                "fallback_start": "product__description",
                "fallback_end": "product__specs",
                "notes": "Celestron uses Shopify; product JSON has the description HTML"
            },
            "features": {
                "method": "extract_from_description",
                "pattern": "bullet_lists",
                "notes": "Features are embedded in the description as <ul> lists"
            },
            "specs": {
                "method": "shopify_product_json",
                "json_path": "product.metafields",
                "fallback_method": "between_markers",
                "fallback_start": "Specifications",
                "fallback_end": "What's in the Box",
                "extract_as": "key_value_table",
                "notes": "Specs often in an accordion or table within the product page"
            },
            "documents": {
                "method": "link_scan",
                "link_categories": {
                    "manual": ["Manual", "Guide", "Instructions"],
                    "firmware": ["Firmware", "Update"],
                    "software": ["Software", "Driver", "CPWI"],
                },
                "notes": "Celestron hosts docs on separate support pages"
            },
            "images": {
                "method": "shopify_product_json",
                "json_path": "product.images",
                "fallback_method": "og_and_product_images",
                "skip_patterns": ["logo", "icon", "pixel", "badge"],
                "include_patterns": ["cdn.shopify", "product"],
                "notes": "Images from Shopify product JSON"
            }
        }
    },
    "ZWO": {
        "platform": "woocommerce",
        "base_url": "https://astronomy-imaging-camera.com",
        "url_pattern": "https://astronomy-imaging-camera.com/product/{slug}",
        "sections": {
            "description": {
                "method": "between_markers",
                "start": "woocommerce-Tabs",
                "end": "Additional information",
                "fallback_start": "tab-description",
                "fallback_end": "tab-additional_information",
                "notes": "WooCommerce description tab content"
            },
            "features": {
                "method": "extract_from_description",
                "pattern": "bullet_lists",
                "notes": "Features usually in description as bullet lists"
            },
            "specs": {
                "method": "between_markers",
                "start": "Additional information",
                "end": "Reviews",
                "fallback_start": "tab-additional_information",
                "fallback_end": "tab-reviews",
                "extract_as": "key_value_table",
                "notes": "WooCommerce additional information tab = specs table"
            },
            "documents": {
                "method": "link_scan",
                "url_patterns": [r"\.pdf$", r"/manuals/", r"/download/"],
                "link_categories": {
                    "manual": ["Manual", "Guide", "Datasheet"],
                    "firmware": ["Firmware", "Driver"],
                    "software": ["Software", "ASCOM", "ASIAIR"],
                },
                "notes": "PDF links and download references"
            },
            "images": {
                "method": "og_and_product_images",
                "skip_patterns": ["logo", "icon", "pixel", "badge", "avatar",
                                  "comment", "gravatar", "emoji"],
                "include_patterns": ["product", "uploads", "wp-content"],
                "notes": "Product images from WooCommerce media"
            }
        }
    },
    "Sky-Watcher": {
        "platform": "shopify",
        "base_url": "https://www.skywatcherusa.com",
        "url_pattern": "https://www.skywatcherusa.com/products/{slug}",
        "sections": {
            # Sky-Watcher's site is built on Shopify, so the cleanest way to
            # pull content is from the product JSON the theme embeds in the
            # page (`<script type="application/json">{...}</script>`). The
            # previous between-markers config was grabbing JS template source
            # ("+specsData[spec]+") because the markers it searched for
            # straddled an inline template block.
            "title": {
                "method": "shopify_product_json",
                "json_path": "product.title",
                "notes": "Clean product title from Shopify product JSON. The "
                         "draft creator appends ' - <SKU>'."
            },
            "description": {
                "method": "shopify_product_json",
                "json_path": "product.description",
                "notes": "Body description HTML from Shopify product JSON "
                         "(rendered into the Description tab on skywatcherusa.com)."
            },
            # Highlights bullets shown in the upper-right of the product page
            # (next to the price). Sky-Watcher's theme renders these from a
            # metafield into a div with class `pdp-short-description`. We
            # scrape the rendered HTML directly because they aren't in the
            # embedded product JSON.
            "highlights": {
                "method": "between_markers",
                "start": 'class="pdp-short-description"',
                "end": "</div>",
                "extract_as": "bullet_list",
                "notes": "Bullets next to the product photo, rendered from a "
                         "metafield into the .pdp-short-description div."
            },
            "features": {
                "method": "extract_from_description",
                "pattern": "bullet_lists",
                "notes": "Bullet lists embedded in the description body (post-extracted)"
            },
            # NOTE: documents extraction intentionally omitted for Sky-Watcher.
            # Their Shopify theme renders a generic "Support Documents" nav
            # block on every product page (Manuals / Firmware / Software
            # categories), and link_scan was pulling those into every draft
            # even when the product itself has no associated downloads.
            # Operators can manually add doc links to the description if a
            # specific product genuinely has its own support files.
            "images": {
                "method": "shopify_product_json",
                "json_path": "product.images",
                "fallback_method": "og_and_product_images",
                "skip_patterns": ["logo", "icon", "pixel", "badge"],
                "include_patterns": ["cdn.shopify", "product"],
                "notes": "Product images from Shopify product JSON"
            }
        }
    },
    "Baader Planetarium": {
        # Baader runs Magento 2 with a PWA Studio (React SPA) frontend, so the
        # product page HTML is just an empty shell — all content is loaded
        # client-side from the Magento GraphQL Store API. We query that API
        # directly instead of scraping HTML markers (the old approach, which
        # never matched because the markup is rendered in the browser).
        "platform": "magento_graphql",
        "base_url": "https://www.baader-planetarium.com",
        "api_url": "https://backend.baader-planetarium.com/graphql",
        "store_header": "en",
        "url_pattern": "https://www.baader-planetarium.com/en/{slug}.html",
        # Which custom attributes count as "specifications". Magento mixes the
        # real spec attributes (spec_*, filter_*) in with dozens of internal /
        # third-party module attributes (hide_price, aw_shopbybrand_brand, …),
        # so we select by code prefix + an explicit include list rather than
        # dumping everything. Add prefixes here if other Baader product
        # categories (eyepieces, mounts) use different namespaces.
        "spec_attribute_prefixes": ["spec_", "filter_"],
        "spec_attribute_include": ["manufacturer", "ean_code", "weight_net"],
        "notes": (
            "Magento GraphQL extraction. The product is resolved by url_key "
            "(the final URL path segment minus '.html'). Mapping: "
            "short_description bullets -> Highlights, description -> body HTML, "
            "media_gallery -> images, spec_*/filter_* + manufacturer custom "
            "attributes + weight -> specifications (labels resolved via "
            "customAttributeMetadataV2 with the 'en' store view). No CSS/marker "
            "sections are used for this platform."
        ),
        # sections is intentionally empty: the magento_graphql path extracts
        # everything holistically from one API response.
        "sections": {}
    },
}


def get_default_config(vendor: str) -> Optional[Dict]:
    """Return the default scrape config for a known vendor, or None."""
    return DEFAULT_CONFIGS.get(vendor)


def get_all_default_vendors() -> List[str]:
    """Return list of vendors with default configs."""
    return list(DEFAULT_CONFIGS.keys())


# ─── EXTRACTION ENGINE ───────────────────────────────────────────

class VendorScraper:
    """
    Extracts structured product data from a manufacturer webpage
    using a vendor-specific scrape_config.
    """

    def __init__(self, config: Dict):
        self.config = config
        self.platform = config.get("platform", "unknown")
        self.base_url = config.get("base_url", "")
        self.sections = config.get("sections", {})

    async def fetch_page(self, url: str) -> Optional[str]:
        """Fetch a product page and return raw HTML."""
        try:
            async with httpx.AsyncClient(timeout=25, follow_redirects=True) as client:
                resp = await client.get(url, headers={
                    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                                   "Chrome/120.0.0.0 Safari/537.36"),
                    "Accept": "text/html,application/xhtml+xml,*/*",
                    "Accept-Language": "en-US,en;q=0.9",
                })
                if resp.status_code == 200 and len(resp.text) > 500:
                    logger.info("Fetched %s (%d bytes)", url, len(resp.text))
                    return resp.text
                else:
                    logger.warning("Fetch returned %d for %s", resp.status_code, url)
        except Exception as e:
            logger.warning("Failed to fetch %s: %s", url, e)
        return None

    async def scrape(self, url: str, variant_sku: str = None) -> Dict[str, Any]:
        """Unified extraction entry point.

        Routes by platform: ``magento_graphql`` vendors are extracted from
        the Magento Store API (their pages are client-rendered SPAs with no
        scrapeable HTML); every other vendor uses the HTML fetch + marker
        extraction path. Returns the standard extraction dict
        (title/description/highlights/features/specs/documents/images), or
        {} when nothing could be fetched.

        ``variant_sku`` is an optional hint (e.g. the pricelist SKU) used by
        Magento configurable products to select the specific child variant
        whose specs/title to use, when the URL doesn't carry a ``?sku=`` param.
        """
        if self.platform == "magento_graphql":
            return await self._scrape_magento_graphql(url, variant_sku)
        html = await self.fetch_page(url)
        if not html:
            return {}
        return self.extract_all(html, url)

    # ── Magento GraphQL extraction ────────────────────────────────

    async def _scrape_magento_graphql(self, url: str,
                                       variant_sku: str = None) -> Dict[str, Any]:
        """Extract a product from a Magento 2 GraphQL Store API.

        Resolves the product by ``url_key`` (the final path segment of the
        URL, minus ``.html``), then maps Magento fields to our standard
        extraction shape.

        Most spec-bearing attributes (size, shape, thickness, mounted, net
        weight, EAN) live on the specific *variant* of a configurable product,
        not the parent — so we resolve the target variant from the URL's
        ``?sku=`` param (or the ``variant_sku`` hint) and source the
        specs/title/highlights from it, falling back to the parent when no
        variant matches. The body description and image gallery always come
        from the parent (variants inherit them). Spec attributes are selected
        by configured prefix (spec_*, filter_*) + include list, with labels
        resolved from customAttributeMetadataV2.
        """
        api_url = self.config.get("api_url")
        store = self.config.get("store_header", "")
        if not api_url:
            logger.warning("magento_graphql config missing api_url")
            return {}

        # url_key = last non-empty path segment, with a trailing .html removed.
        from urllib.parse import urlsplit, parse_qs
        parts = urlsplit(url)
        slug = parts.path.rstrip("/").split("/")[-1]
        if slug.lower().endswith(".html"):
            slug = slug[:-5]
        if not slug:
            logger.warning("Could not derive url_key from %s", url)
            return {}
        # Target variant: prefer the URL's ?sku= param, else the caller hint.
        target_sku = (parse_qs(parts.query).get("sku") or [None])[0] or variant_sku

        headers = {
            "Content-Type": "application/json",
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/120.0.0.0 Safari/537.36"),
        }
        if store:
            headers["Store"] = store

        attr_block = (
            " custom_attributesV2 { items { code"
            "   ... on AttributeValue { value }"
            "   ... on AttributeSelectedOptions { selected_options { label } } } }"
        )
        product_query = (
            "query($key:String!){ products(filter:{url_key:{eq:$key}}){ items {"
            " name sku"
            " short_description { html }"
            " description { html }"
            " media_gallery { url position disabled }"
            + attr_block +
            " ... on ConfigurableProduct { variants { product { sku name"
            "   short_description { html }" + attr_block + " } } }"
            " } } }"
        )
        try:
            async with httpx.AsyncClient(timeout=25, follow_redirects=True) as client:
                resp = await client.post(api_url, headers=headers, json={
                    "query": product_query, "variables": {"key": slug},
                })
                payload = resp.json()
        except Exception as e:
            logger.warning("Magento GraphQL fetch failed for %s: %s", url, e)
            return {}

        items = ((((payload or {}).get("data") or {}).get("products") or {})
                 .get("items") or [])
        if not items:
            logger.warning("Magento GraphQL returned no product for url_key=%s", slug)
            return {}
        p = items[0]

        # Pick the attribute/title/highlight source: the matching variant if we
        # can resolve one, else the parent. Description + images stay on parent.
        attr_source = p
        title = (p.get("name") or "").strip()
        sd_html = (p.get("short_description") or {}).get("html") or ""
        if target_sku:
            for v in (p.get("variants") or []):
                vp = (v or {}).get("product") or {}
                if (vp.get("sku") or "") == target_sku:
                    attr_source = vp
                    title = (vp.get("name") or title).strip()
                    sd_html = (vp.get("short_description") or {}).get("html") or sd_html
                    break
            else:
                logger.info("Magento: variant sku=%s not found under %s; "
                            "using parent attributes", target_sku, slug)

        # Highlights: the bullet list shown next to the price (short_description).
        highlights = self._parse_bullet_list(sd_html)

        # Body description: always from the parent (variants inherit it).
        description = self._clean_magento_html(
            (p.get("description") or {}).get("html") or ""
        )

        # Images: parent media_gallery, ordered by position, skip disabled.
        images = []
        gallery = sorted(
            (p.get("media_gallery") or []),
            key=lambda m: m.get("position") if m.get("position") is not None else 999,
        )
        for m in gallery:
            if m.get("disabled"):
                continue
            u = m.get("url")
            if u and u not in images:
                images.append(u)

        # Specs: attributes whose code matches a configured prefix (spec_*,
        # filter_*) plus an explicit include list (manufacturer, ean_code,
        # weight_net). This avoids the dozens of internal/module attributes
        # (hide_price, status, etc.) custom_attributesV2 also returns. Sourced
        # from the resolved variant so size/shape/thickness/mounted/EAN/net
        # weight are correct for the specific item. Labels resolved in one
        # metadata call.
        prefixes = tuple(self.config.get("spec_attribute_prefixes")
                         or ("spec_", "filter_"))
        include = set(self.config.get("spec_attribute_include") or ("manufacturer",))
        raw_specs: Dict[str, str] = {}
        for attr in ((attr_source.get("custom_attributesV2") or {}).get("items") or []):
            code = attr.get("code") or ""
            if not (code.startswith(prefixes) or code in include):
                continue
            if attr.get("value"):
                val = self._strip_tags(str(attr["value"]))
            elif attr.get("selected_options"):
                labels = [o.get("label", "").strip()
                          for o in attr["selected_options"]
                          if o.get("label", "").strip()]
                val = ", ".join(labels)
            else:
                val = ""
            val = (val or "").strip()
            if val:
                raw_specs[code] = val

        label_map = await self._magento_attr_labels(
            api_url, headers, list(raw_specs.keys())
        )

        def _label(code: str) -> str:
            # Trailing '?'/spaces are an admin-label artifact ("Single or Set?")
            lbl = label_map.get(code) or code
            return lbl.rstrip(" ?") or code

        specs = [{"key": _label(code), "value": val}
                 for code, val in raw_specs.items()]

        return {
            "title": title,
            "highlights": highlights,
            "description": description,
            "features": None,        # bullets are surfaced as highlights
            "specs": specs,
            "documents": None,
            "images": images[:10],
        }

    async def _magento_attr_labels(self, api_url: str, headers: Dict,
                                    codes: List[str]) -> Dict[str, str]:
        """Resolve Magento attribute codes to their storefront labels via
        customAttributeMetadataV2. Returns {code: label}; on any failure
        returns {} so callers fall back to the raw code."""
        if not codes:
            return {}
        attrs = ",".join(
            '{attribute_code:"%s",entity_type:"catalog_product"}' % c
            for c in codes
        )
        query = ("{ customAttributeMetadataV2(attributes:[%s]) "
                 "{ items { code label } } }" % attrs)
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.post(api_url, headers=headers,
                                         json={"query": query})
                data = resp.json()
            items = (((data or {}).get("data") or {})
                     .get("customAttributeMetadataV2") or {}).get("items") or []
            return {it["code"]: it["label"] for it in items
                    if it.get("code") and it.get("label")}
        except Exception as e:
            logger.warning("Magento attribute-label lookup failed: %s", e)
            return {}

    def _clean_magento_html(self, html: str) -> str:
        """Clean a Magento description body for use in a Shopify listing:
        strip ``{{widget}}`` / ``{{block}}`` directives, drop the trailing
        'Further information ... Blogposts' block (CMS-only widgets), and
        absolutize site-relative ``/media/...`` URLs."""
        if not html:
            return ""
        # Drop Magento template directives ({{block ...}}, {{widget ...}}, {{media ...}})
        html = re.sub(r'\{\{[^}]*\}\}', '', html)
        # Remove the trailing blog-embed section (heading + now-empty widgets)
        html = re.sub(r'<h3>\s*Further information[\s\S]*$', '', html,
                      flags=re.IGNORECASE)
        # Absolutize site-relative URLs (href="/media/..", src="/media/..").
        # Protocol-relative ("//host/..") URLs are left untouched.
        base = self.base_url.rstrip("/")
        html = re.sub(
            r'(src|href)="(/(?!/)[^"]*)"',
            lambda m: '%s="%s%s"' % (m.group(1), base, m.group(2)),
            html,
        )
        return html.strip()

    def extract_all(self, html: str, page_url: str) -> Dict[str, Any]:
        """
        Extract all configured sections from the HTML.
        Returns a dict with keys: description, features, specs, documents, images.
        Each value is the extracted content in a structured format.
        """
        result = {}
        for section_name, section_config in self.sections.items():
            try:
                result[section_name] = self._extract_section(
                    html, page_url, section_name, section_config
                )
            except Exception as e:
                logger.warning("Error extracting %s: %s", section_name, e)
                result[section_name] = None
        return result

    def _extract_section(self, html: str, page_url: str,
                         name: str, cfg: Dict) -> Any:
        """Extract a single section based on its config."""
        method = cfg.get("method", "")

        if method == "between_markers":
            return self._extract_between_markers(html, page_url, cfg)
        elif method == "og_and_product_images":
            return self._extract_images(html, page_url, cfg)
        elif method == "shopify_product_json":
            return self._extract_shopify_json(html, page_url, name, cfg)
        elif method == "extract_from_description":
            return None  # Handled in post-processing from description
        elif method == "link_scan":
            return self._extract_links(html, page_url, cfg)
        else:
            logger.warning("Unknown extraction method: %s", method)
            return None

    # ── Between-markers extraction ────────────────────────────────

    def _extract_between_markers(self, html: str, page_url: str,
                                 cfg: Dict) -> Any:
        """
        Extract content between two text markers in the HTML.
        Handles the common pattern of tabbed content where section
        headers appear in the HTML even though only one tab is visible.
        
        Config options:
          use_last: bool — if True, find the LAST occurrence of the start
                    marker. Useful for Volusion sites where tab headers
                    appear before content but the same text repeats.
        """
        start_marker = cfg.get("start", "")
        end_marker = cfg.get("end", "")
        fallback_start = cfg.get("fallback_start", "")
        fallback_end = cfg.get("fallback_end", "")
        use_last = cfg.get("use_last", False)

        content = self._find_between(html, start_marker, end_marker, use_last)
        if not content and fallback_start:
            content = self._find_between(html, fallback_start,
                                         fallback_end or end_marker, use_last)
        if not content:
            return None

        extract_as = cfg.get("extract_as", "text")

        if extract_as == "key_value_table":
            return self._parse_kv_table(content)
        elif extract_as == "bullet_list":
            return self._parse_bullet_list(content)
        elif extract_as == "link_list":
            return self._parse_link_list(content, page_url, cfg)
        else:
            # Return cleaned text/HTML
            if cfg.get("strip_html_tables"):
                content = re.sub(r'<table[\s\S]*?</table>', '', content,
                                 flags=re.IGNORECASE)
            # Strip standalone link blocks (like manual/firmware links)
            # that appear as list items or paragraphs containing only a link
            content = re.sub(
                r'<(?:li|p)[^>]*>\s*<a[^>]+href=["\'][^"\']*\.(?:pdf|exe|zip)["\'][^>]*>'
                r'[^<]*</a>\s*</(?:li|p)>',
                '', content, flags=re.IGNORECASE
            )
            # Strip link-only list items (manual, firmware, software links)
            content = re.sub(
                r'<(?:li|p)[^>]*>\s*<a[^>]+>[^<]{0,80}</a>\s*</(?:li|p)>',
                '', content, flags=re.IGNORECASE
            )
            # Strip bold section headers for docs (Manual:, Firmware:, etc.)
            content = re.sub(
                r'<(?:b|strong)[^>]*>\s*(?:Manual|Firmware|Software|Download|'
                r'Computer Control|HEM\d+|CEM\d+)[^<]*</(?:b|strong)>',
                '', content, flags=re.IGNORECASE
            )
            return self._clean_html_to_text(content)

    def _find_between(self, html: str, start: str, end: str,
                       use_last: bool = False) -> Optional[str]:
        """Find content between two marker strings (case-insensitive).
        If use_last=True, finds the LAST occurrence of start marker instead
        of the first. This is needed for sites like Volusion where tab headers
        appear early (grouped together) while content appears later.
        """
        if not start:
            return None

        html_lower = html.lower()
        start_lower = start.lower()
        end_lower = end.lower() if end else ""

        if use_last:
            start_pos = html_lower.rfind(start_lower)
        else:
            start_pos = html_lower.find(start_lower)
        if start_pos == -1:
            return None

        # Move past the marker itself
        content_start = start_pos + len(start)

        if end_lower:
            # Find the end marker AFTER the start position
            end_pos = html_lower.find(end_lower, content_start)
            if end_pos == -1:
                # Take a generous chunk if end marker not found
                end_pos = min(content_start + 15000, len(html))
            return html[content_start:end_pos]
        else:
            return html[content_start:content_start + 15000]

    # ── Table parsing ─────────────────────────────────────────────

    def _parse_kv_table(self, html_fragment: str) -> List[Dict[str, str]]:
        """
        Parse a two-column HTML table into key-value pairs.
        Handles both <table> structures and <div>-based layouts.
        """
        specs = []

        # Try standard <tr><td>key</td><td>value</td></tr> pattern
        rows = re.findall(
            r'<tr[^>]*>\s*<td[^>]*>(.*?)</td>\s*<td[^>]*>(.*?)</td>',
            html_fragment, re.IGNORECASE | re.DOTALL
        )
        for key_html, val_html in rows:
            key = self._strip_tags(key_html).strip()
            val = self._strip_tags(val_html).strip()
            if key and val and len(key) < 100:
                specs.append({"key": key, "value": val})

        # If no table rows found, try <th>/<td> pattern
        if not specs:
            rows = re.findall(
                r'<tr[^>]*>\s*<th[^>]*>(.*?)</th>\s*<td[^>]*>(.*?)</td>',
                html_fragment, re.IGNORECASE | re.DOTALL
            )
            for key_html, val_html in rows:
                key = self._strip_tags(key_html).strip()
                val = self._strip_tags(val_html).strip()
                if key and val and len(key) < 100:
                    specs.append({"key": key, "value": val})

        # Try dt/dd pattern (definition lists)
        if not specs:
            dts = re.findall(r'<dt[^>]*>(.*?)</dt>', html_fragment,
                             re.IGNORECASE | re.DOTALL)
            dds = re.findall(r'<dd[^>]*>(.*?)</dd>', html_fragment,
                             re.IGNORECASE | re.DOTALL)
            for dt, dd in zip(dts, dds):
                key = self._strip_tags(dt).strip()
                val = self._strip_tags(dd).strip()
                if key and val:
                    specs.append({"key": key, "value": val})

        return specs

    # ── Bullet list parsing ───────────────────────────────────────

    def _parse_bullet_list(self, html_fragment: str) -> List[str]:
        """Extract bullet points from <li> elements or lines starting with •."""
        items = []

        # Extract from <li> tags
        li_matches = re.findall(r'<li[^>]*>(.*?)</li>',
                                html_fragment, re.IGNORECASE | re.DOTALL)
        for li in li_matches:
            text = self._strip_tags(li).strip()
            if text and len(text) > 3:
                items.append(text)

        # If no <li> items, try line-based bullet patterns
        if not items:
            for line in html_fragment.split('\n'):
                line = self._strip_tags(line).strip()
                if line.startswith(('•', '·', '-', '–', '✓', '★')):
                    text = line.lstrip('•·-–✓★ ').strip()
                    if text and len(text) > 3:
                        items.append(text)

        return items

    # ── Link extraction ───────────────────────────────────────────

    def _parse_link_list(self, html_fragment: str, page_url: str,
                         cfg: Dict) -> Dict[str, List[Dict]]:
        """Extract and categorize links from an HTML fragment."""
        categories = cfg.get("link_categories", {})
        result = {cat: [] for cat in categories}
        result["other"] = []

        links = re.findall(
            r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
            html_fragment, re.IGNORECASE | re.DOTALL
        )

        for href, label_html in links:
            label = self._strip_tags(label_html).strip()
            if not label or not href or href.startswith(('#', 'javascript:')):
                continue

            abs_url = urljoin(page_url, href)
            entry = {"label": label, "url": abs_url}

            # Categorize by matching label text against category keywords
            categorized = False
            for cat_name, keywords in categories.items():
                if any(kw.lower() in label.lower() for kw in keywords):
                    result[cat_name].append(entry)
                    categorized = True
                    break

            if not categorized:
                # Also check URL for patterns like .pdf
                if href.lower().endswith('.pdf'):
                    result.get("manual", result["other"]).append(entry)
                else:
                    result["other"].append(entry)

        # Remove empty categories
        return {k: v for k, v in result.items() if v}

    def _extract_links(self, html: str, page_url: str,
                       cfg: Dict) -> Dict[str, List[Dict]]:
        """Scan full page for relevant links based on URL patterns and categories."""
        categories = cfg.get("link_categories", {})
        url_patterns = cfg.get("url_patterns", [])
        result = {cat: [] for cat in categories}
        result["other"] = []

        links = re.findall(
            r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
            html, re.IGNORECASE | re.DOTALL
        )

        for href, label_html in links:
            label = self._strip_tags(label_html).strip()
            if not label or not href or href.startswith(('#', 'javascript:')):
                continue

            # Check URL patterns first
            matches_pattern = False
            if url_patterns:
                for pat in url_patterns:
                    if re.search(pat, href, re.IGNORECASE):
                        matches_pattern = True
                        break

            # Check category keywords in label
            matches_category = False
            matched_cat = "other"
            for cat_name, keywords in categories.items():
                if any(kw.lower() in label.lower() for kw in keywords):
                    matches_category = True
                    matched_cat = cat_name
                    break

            if matches_pattern or matches_category:
                abs_url = urljoin(page_url, href)
                entry = {"label": label, "url": abs_url}
                result[matched_cat].append(entry)

        return {k: v for k, v in result.items() if v}

    # ── Image extraction ──────────────────────────────────────────

    def _extract_images(self, html: str, page_url: str,
                        cfg: Dict) -> List[str]:
        """Extract product image URLs using og:image and img tags."""
        images = []
        seen_bases = set()  # Track base URLs to avoid thumbnail/full duplicates
        skip = [p.lower() for p in cfg.get("skip_patterns", [])]
        include = [p.lower() for p in cfg.get("include_patterns", [])]

        def _base_url(url):
            """Strip query strings and size suffixes for dedup."""
            return re.split(r'[?#]', url)[0]

        def _looks_like_thumbnail(url):
            """Check if URL contains common thumbnail size indicators."""
            url_lower = url.lower()
            # Volusion thumbnails: -2T.jpg, -3T.jpg, etc. (vs -2.jpg for full)
            if re.search(r'-\d+t\.(jpg|jpeg|png|gif|webp)', url_lower):
                return True
            # Common dimension patterns in URLs
            if re.search(r'[/_-](thumb|tn|small|tiny|\d{2,3}x\d{2,3})', url_lower):
                return True
            return False

        # og:image meta tags (highest priority — always full-size)
        og_matches = re.findall(
            r'<meta[^>]+(?:property=["\']og:image["\'][^>]+content=["\']([^"\']+)'
            r'|content=["\']([^"\']+)["\'][^>]+property=["\']og:image)',
            html, re.IGNORECASE
        )
        for m in og_matches:
            url = m[0] or m[1]
            if url:
                base = _base_url(url)
                if base not in seen_bases:
                    images.append(url)
                    seen_bases.add(base)

        # Look for full-size images linked via <a href="..."><img></a>
        # These are usually the enlarged versions of product thumbnails
        linked_imgs = re.findall(
            r'<a[^>]+href=["\']([^"\']+\.(jpg|jpeg|png|gif|webp)[^"\']*)["\']',
            html, re.IGNORECASE
        )
        for href, ext in linked_imgs:
            url_lower = href.lower()
            if any(s in url_lower for s in skip):
                continue
            if include and not any(i in url_lower for i in include):
                continue
            if _looks_like_thumbnail(href):
                continue
            abs_url = urljoin(page_url, href)
            base = _base_url(abs_url)
            if base not in seen_bases:
                images.append(abs_url)
                seen_bases.add(base)

        # img tags (lower priority, skip thumbnails)
        img_matches = re.findall(
            r'<img[^>]+src=["\']([^"\']+)["\']', html, re.IGNORECASE
        )
        for url in img_matches:
            url_lower = url.lower()
            if any(s in url_lower for s in skip):
                continue
            if include and not any(i in url_lower for i in include):
                continue
            if _looks_like_thumbnail(url):
                continue
            abs_url = urljoin(page_url, url)
            base = _base_url(abs_url)
            if base not in seen_bases:
                images.append(abs_url)
                seen_bases.add(base)

        return images[:10]

    # ── Shopify JSON extraction ───────────────────────────────────

    def _extract_shopify_json(self, html: str, page_url: str,
                              section_name: str, cfg: Dict) -> Any:
        """
        Extract data from Shopify's embedded product JSON.
        Shopify pages include a <script type="application/ld+json"> block
        and often a /products/{handle}.json endpoint.
        """
        json_path = cfg.get("json_path", "")

        # Try to find product JSON in the page
        product_data = None

        # Method 1: Look for Shopify product JSON in script tags
        json_matches = re.findall(
            r'<script[^>]*type=["\']application/json["\'][^>]*>(.*?)</script>',
            html, re.DOTALL | re.IGNORECASE
        )
        for js in json_matches:
            try:
                data = json.loads(js.strip())
                if isinstance(data, dict) and "product" in data:
                    product_data = data
                    break
            except (json.JSONDecodeError, ValueError):
                continue

        # Method 2: Look for product data in JavaScript assignments
        if not product_data:
            js_match = re.search(
                r'var\s+meta\s*=\s*(\{.*?"product".*?\});',
                html, re.DOTALL
            )
            if js_match:
                try:
                    product_data = json.loads(js_match.group(1))
                except (json.JSONDecodeError, ValueError):
                    pass

        def _try_fallback():
            """Apply the configured fallback method, or return None."""
            fb_method = cfg.get("fallback_method")
            if fb_method == "between_markers":
                return self._extract_between_markers(html, page_url, {
                    "start": cfg.get("fallback_start", ""),
                    "end": cfg.get("fallback_end", ""),
                    "extract_as": cfg.get("extract_as", "text"),
                })
            if fb_method == "og_and_product_images":
                return self._extract_images(html, page_url, cfg)
            return None

        if not product_data:
            return _try_fallback()

        # Navigate the JSON path
        try:
            parts = json_path.split(".")
            value = product_data
            for part in parts:
                if isinstance(value, dict):
                    value = value.get(part)
                else:
                    value = None
                    break

            # Image-section special handling. Shopify themes vary in where
            # the image list lives — `product.images` is most common but
            # newer themes use `product.media[].preview_image.src`. If the
            # configured path is empty, we also probe the alternate path
            # before giving up. Empty results trigger the fallback so
            # we still return SOMETHING for products that haven't had
            # images uploaded yet (placeholder OG image).
            if section_name == "images":
                def _normalize_url(u):
                    """Convert protocol-relative ('//cdn.shopify…') and
                    site-relative URLs into fully qualified https URLs.
                    Shopify's media-create mutation rejects URLs that lack
                    a scheme, which is how Sky-Watcher's product JSON
                    embeds image URLs (e.g. //www.skywatcherusa.com/...).
                    """
                    if not u:
                        return None
                    u = str(u).strip()
                    if u.startswith("//"):
                        return "https:" + u
                    if u.startswith("/"):
                        return urljoin(page_url, u)
                    return u

                urls = []
                if isinstance(value, list):
                    for img in value:
                        if isinstance(img, dict):
                            url = (img.get("src")
                                   or img.get("originalSrc")
                                   or img.get("url"))
                            if not url and isinstance(img.get("preview_image"), dict):
                                url = img["preview_image"].get("src")
                            url = _normalize_url(url)
                            if url:
                                urls.append(url)
                        elif isinstance(img, str):
                            url = _normalize_url(img)
                            if url:
                                urls.append(url)
                # Probe product.media when product.images was empty
                if not urls and isinstance(product_data.get("product"), dict):
                    media = product_data["product"].get("media")
                    if isinstance(media, list):
                        for m in media:
                            if isinstance(m, dict):
                                pi = m.get("preview_image") or {}
                                url = (pi.get("src")
                                       or m.get("src")
                                       or m.get("originalSrc"))
                                url = _normalize_url(url)
                                if url:
                                    urls.append(url)
                if urls:
                    return urls[:10]
                # Empty list → fallback to OG/product image scrape
                fb = _try_fallback()
                return fb if fb else []

            # Non-image sections: empty/missing → try fallback
            if value in (None, "", []):
                return _try_fallback()
            return value
        except Exception as e:
            logger.warning("shopify_product_json extraction error: %s", e)
            return _try_fallback()

    # ── Utility methods ───────────────────────────────────────────

    @staticmethod
    def _strip_tags(html_str: str) -> str:
        """Remove HTML tags and decode entities."""
        text = re.sub(r'<[^>]+>', ' ', html_str)
        text = unescape(text)
        text = re.sub(r'\s+', ' ', text)
        return text.strip()

    @staticmethod
    def _clean_html_to_text(html_str: str) -> str:
        """Convert HTML to readable text, preserving paragraph breaks."""
        # Replace block-level tags with newlines
        text = re.sub(r'<br\s*/?>', '\n', html_str, flags=re.IGNORECASE)
        text = re.sub(r'</p>', '\n\n', text, flags=re.IGNORECASE)
        text = re.sub(r'</div>', '\n', text, flags=re.IGNORECASE)
        text = re.sub(r'</li>', '\n', text, flags=re.IGNORECASE)
        # Strip remaining tags
        text = re.sub(r'<[^>]+>', '', text)
        text = unescape(text)
        # Collapse whitespace but keep paragraph breaks
        lines = [line.strip() for line in text.split('\n')]
        lines = [l for l in lines if l]
        return '\n\n'.join(lines)


# ─── STRUCTURED EXTRACTION RESULT ────────────────────────────────

def format_extraction_for_ai(extracted: Dict[str, Any],
                             sku: str, vendor: str,
                             pricelist_description: str = "",
                             page_url: str = "") -> str:
    """
    Format the structured extraction into a clean context string
    for the AI enrichment prompt. This replaces the old approach of
    dumping raw HTML into the prompt.
    """
    parts = []
    parts.append(f"Product SKU: {sku}")
    parts.append(f"Vendor: {vendor}")
    if pricelist_description:
        parts.append(f"Pricelist description: {pricelist_description}")
    if page_url:
        parts.append(f"Manufacturer page: {page_url}")

    desc = extracted.get("description")
    if desc:
        parts.append(f"\n--- PRODUCT DESCRIPTION ---\n{desc}")

    features = extracted.get("features")
    if features and isinstance(features, list):
        parts.append("\n--- KEY FEATURES ---")
        for f in features:
            parts.append(f"• {f}")

    specs = extracted.get("specs")
    if specs and isinstance(specs, list):
        parts.append("\n--- TECHNICAL SPECIFICATIONS ---")
        for s in specs:
            parts.append(f"{s['key']}: {s['value']}")

    documents = extracted.get("documents")
    if documents and isinstance(documents, dict):
        parts.append("\n--- SUPPORT DOCUMENTS ---")
        for category, links in documents.items():
            if links:
                for link in links:
                    parts.append(f"[{category}] {link['label']}: {link['url']}")

    return "\n".join(parts)


def format_documents_html(documents: Optional[Dict[str, List[Dict]]]) -> str:
    """
    Format extracted document links into HTML suitable for inclusion
    in a Shopify product description.
    """
    if not documents:
        return ""

    html_parts = ['<h3>Support Documents</h3>']
    has_content = False

    # Group by category
    category_labels = {
        "manual": "📖 Manuals",
        "firmware": "💾 Firmware & Updates",
        "software": "🖥️ Software & Drivers",
        "other": "📎 Other Resources",
    }

    for cat_key, cat_label in category_labels.items():
        links = documents.get(cat_key, [])
        if links:
            has_content = True
            html_parts.append(f'<p><strong>{cat_label}</strong></p>')
            html_parts.append('<ul>')
            for link in links:
                html_parts.append(
                    f'<li><a href="{link["url"]}" target="_blank" '
                    f'rel="noopener">{link["label"]}</a></li>'
                )
            html_parts.append('</ul>')

    if not has_content:
        return ""

    return '\n'.join(html_parts)


def format_specs_html(specs: Optional[List[Dict[str, str]]]) -> str:
    """Format extracted specs into an HTML table for the product description."""
    if not specs:
        return ""

    rows = []
    for s in specs:
        rows.append(
            f'<tr><td style="padding:4px 8px;"><strong>{s["key"]}</strong></td>'
            f'<td style="padding:4px 8px;">{s["value"]}</td></tr>'
        )

    return (
        '<h3>Technical Specifications</h3>\n'
        '<table style="border-collapse:collapse; width:100%;">\n'
        + '\n'.join(rows)
        + '\n</table>'
    )
