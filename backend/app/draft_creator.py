"""
Draft Product Creator - creates Shopify draft products from pricelist data,
enriched with content from the manufacturer website via AI.

v2: Uses vendor_scraper.py for structured content extraction. Instead of
    sending raw HTML to the AI, we extract description, features, specs,
    documents, and images into clean structured data first. The AI then
    composes a professional Shopify listing from well-organized input.
"""
import logging
import os
import re
import json

import httpx

from .vendor_scraper import (
    VendorScraper,
    get_default_config,
    format_extraction_for_ai,
    format_documents_html,
    format_specs_html,
)

logger = logging.getLogger(__name__)

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = os.getenv("OPENROUTER_MODEL", "moonshotai/kimi-k2.5")


def _get_scraper(vendor: str, db=None) -> VendorScraper:
    """
    Get a VendorScraper for the given vendor. Tries the database
    scrape_config first, falls back to built-in defaults.
    """
    config = None

    # Try loading from vendor_settings.scrape_config in the database
    if db:
        try:
            conn = db._get_connection()
            cursor = conn.cursor()
            cursor.execute(
                "SELECT scrape_config FROM vendor_settings WHERE vendor = ?",
                vendor
            )
            row = cursor.fetchone()
            if row and row[0]:
                config = json.loads(row[0])
                logger.info("Loaded scrape config from DB for %s", vendor)
            conn.close()
        except Exception as e:
            logger.warning("Could not load scrape config from DB for %s: %s",
                           vendor, e)

    # Fall back to built-in defaults
    if not config:
        config = get_default_config(vendor)

    if not config:
        # No config at all — use a generic config that does basic extraction
        config = {
            "platform": "unknown",
            "base_url": "",
            "sections": {
                "description": {
                    "method": "between_markers",
                    "start": "<body",
                    "end": "</body>",
                    "notes": "Generic fallback — full page body"
                },
                "images": {
                    "method": "og_and_product_images",
                    "skip_patterns": ["logo", "icon", "pixel", "spacer",
                                      "badge", "tracking", "btn", "arrow"],
                    "include_patterns": ["product", "catalog", "upload",
                                         "image", "media", "cdn", "photo"],
                    "notes": "Generic image extraction"
                }
            }
        }

    return VendorScraper(config)


async def _enrich_with_ai(sku: str, vendor: str, structured_context: str,
                           extracted: dict = None) -> dict:
    """
    Use AI to generate a Shopify product listing from structured extraction data.
    The AI receives clean, organized content rather than raw HTML.
    """
    if not OPENROUTER_API_KEY:
        return None

    # Build the prompt — the structured_context already has all the
    # organized product info (description, features, specs, documents)
    prompt = """You are creating a product listing for a Canadian astronomy equipment retailer's Shopify store.

Based on the structured product information below, generate a Shopify product listing.

Return ONLY a JSON object with these fields:
- "title": Clean product title (include brand name, do NOT include the SKU)
- "description_html": Rich HTML product description. Structure it as:
  1. An engaging overview paragraph (rewrite the manufacturer's description, don't copy verbatim)
  2. Key features as <ul><li> bullet points
  3. Do NOT include specs or documents — those will be appended separately
  Use <p>, <ul>, <li>, <strong>, <h3> tags. Professional, informative tone.
- "product_type": Category (e.g. "Telescope Mount", "Astronomy Camera", "Telescope", "Eyepiece", "Filter", "Accessory", "Guide Scope", "Focuser", "Filter Wheel", "Tripod", "Counterweight", "Power Supply", "Software")
- "tags": Array of relevant tags (include brand, category, key features like "strain wave", "GoTo", target use like "astrophotography" or "visual", mount type if applicable)

IMPORTANT: Write the description in your own words based on the product info. Do not copy manufacturer text verbatim.

%s""" % structured_context

    try:
        async with httpx.AsyncClient(timeout=90) as client:
            resp = await client.post(
                OPENROUTER_URL,
                json={
                    "model": DEFAULT_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 4000,
                    "temperature": 0.2,
                },
                headers={
                    "Authorization": "Bearer " + OPENROUTER_API_KEY,
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://tc-planner-app.azurewebsites.net",
                },
            )
            if resp.status_code != 200:
                logger.warning("AI enrichment failed: %d %s",
                               resp.status_code, resp.text[:200])
                return None

            data = resp.json()
            text = data.get("choices", [{}])[0].get("message", {}).get("content", "")

            # Clean markdown fences
            text = text.strip()
            if text.startswith("```"):
                lines = text.split("\n")
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]
                text = "\n".join(lines)

            return json.loads(text)
    except json.JSONDecodeError:
        logger.warning("AI returned non-JSON response")
        return None
    except Exception as e:
        logger.warning("AI enrichment error: %s", e)
        return None


async def create_draft_from_pricelist(shopify_client, db, vendor, sku,
                                       description, cost_foreign, cost_cad,
                                       sale_price_cad, vendor_url=None,
                                       product_url=None,
                                       barcode=None, map_cad=None):
    """
    Create a draft Shopify product from pricelist data.
    Uses the vendor_scraper for structured content extraction, then AI
    for composing the final listing.

    ``map_cad`` (optional): explicit Canadian MAP from the pricelist. When
    present and >0, this is used as the listing price in preference to
    ``sale_price_cad`` (which may be a markup-derived value rather than
    the vendor's published Canadian MAP).

    ``barcode`` (optional): UPC/EAN from the pricelist. Set on the
    Shopify variant so it appears in the Barcode field.
    """
    # Get the vendor-specific scraper
    scraper = _get_scraper(vendor, db)

    # Fetch and extract structured content. ``scrape`` routes by platform
    # (HTML markers for most vendors; the Magento GraphQL API for Baader).
    extracted = {}
    image_urls = []
    scraped_title = None
    if product_url:
        # Pass the pricelist SKU as a variant hint — for Magento configurable
        # products (Baader) it selects the specific size variant whose specs
        # (size/shape/thickness/EAN/net weight) we want, when the URL itself
        # doesn't carry a ?sku= param.
        extracted = await scraper.scrape(product_url, variant_sku=sku) or {}
        if extracted:
            image_urls = extracted.get("images", []) or []
            # Some configs (e.g. Sky-Watcher Shopify product JSON, Baader
            # Magento) extract a clean title directly. We prefer this over
            # AI-generated titles because manufacturer-published titles match
            # what the customer sees on the brand site.
            raw_title = extracted.get("title")
            if isinstance(raw_title, str) and raw_title.strip():
                scraped_title = raw_title.strip()

    # Format structured data for the AI prompt
    structured_context = format_extraction_for_ai(
        extracted, sku, vendor, description, product_url
    )

    # AI enrichment with structured data
    enriched = await _enrich_with_ai(sku, vendor, structured_context, extracted)

    if enriched:
        title = enriched.get("title", "%s %s" % (vendor, description))
        desc_html = enriched.get("description_html", "<p>%s</p>" % description)
        product_type = enriched.get("product_type", "")
        tags = enriched.get("tags", [])
    else:
        title = ("%s %s - %s" % (vendor, description, sku)
                 if description else "%s %s" % (vendor, sku))
        desc_html = "<p>%s</p>" % description if description else ""
        product_type = ""
        tags = []

    # Title preference: scraped manufacturer title beats AI-generated title.
    # Always append " - <SKU>" so the listing is filterable / searchable by
    # SKU even when the customer pastes the bare product name.
    if scraped_title:
        title = scraped_title
    if sku and ("- " + sku) not in title and (" " + sku) not in title:
        title = title.rstrip(" -") + " - " + sku

    # Description composition for scraped sources. We assemble the listing
    # body in this order so it mirrors what customers see on the brand site:
    #   1. <h2>Highlights</h2> + <ul> of the upper-right bullets (when the
    #      vendor's scrape config exposes them — Sky-Watcher's
    #      `pdp-short-description` div)
    #   2. The body description from the manufacturer (verbatim HTML)
    #
    # Falls back to AI-generated description when scraping yields nothing.
    scraped_desc = extracted.get("description")
    highlights = extracted.get("highlights")
    composed_parts = []

    if isinstance(highlights, list) and highlights:
        # De-dupe + trim. Skip vacuous bullets (very short or duplicate of
        # the body description's first sentence).
        seen = set()
        clean_bullets = []
        for h in highlights:
            t = (h or "").strip()
            if not t or len(t) < 3:
                continue
            key = t.lower()
            if key in seen:
                continue
            seen.add(key)
            clean_bullets.append(t)
        if clean_bullets:
            items_html = "".join("<li>%s</li>" % b for b in clean_bullets)
            composed_parts.append(
                "<h2>Highlights</h2>\n<ul>%s</ul>" % items_html
            )

    if isinstance(scraped_desc, str) and scraped_desc.strip():
        composed_parts.append(scraped_desc.strip())

    if composed_parts:
        desc_html = "\n".join(composed_parts)

    # Append structured specs table to description (not AI-generated)
    specs_html = format_specs_html(extracted.get("specs"))
    if specs_html:
        desc_html += "\n" + specs_html

    # Append support document links to description ONLY when the vendor's
    # scrape config actually targets a per-product documents section. Some
    # vendors (e.g. Sky-Watcher) put a generic "Support Documents" nav block
    # on every product page; including it here adds unrelated download links
    # that don't belong on the listing. We treat the documents section as
    # opt-in: it has to be present in the extracted data AND non-trivial.
    docs_html = format_documents_html(extracted.get("documents"))
    if docs_html:
        desc_html += "\n" + docs_html

    # Pricing precedence: explicit Canadian MAP from the pricelist beats the
    # markup-derived sale_price_cad. The vendor's published CAD MAP is the
    # canonical retail price; sale_price_cad may be a markup-on-cost
    # calculation that differs from what the brand wants displayed.
    if map_cad and map_cad > 0:
        price = float(map_cad)
        price_source = "cad_map"
    else:
        price = sale_price_cad or 0
        price_source = "computed"

    pricing_warning = None
    if cost_cad and price and cost_cad > price:
        pricing_warning = (
            "Cost ($%.2f CAD) exceeds sale price ($%.2f) - review before "
            "activating" % (cost_cad, price)
        )
        tags.append("Pricing Review Needed")

    # Every new draft is created with no stock on hand, so tag it
    # ``istock-preorder`` — the system reads this as "orderable but not
    # currently in stock". Applied to ALL drafts regardless of manufacturer.
    # (Paired with Sell-when-out-of-stock = CONTINUE, set in create_draft_product.)
    if not tags:
        tags = []
    if "istock-preorder" not in [str(t).lower() for t in tags]:
        tags.append("istock-preorder")

    # Create the draft product in Shopify
    result = await shopify_client.create_draft_product(
        title=title,
        vendor=vendor,
        sku=sku,
        price=price,
        cost=cost_cad or 0,
        description=desc_html,
        product_type=product_type,
        tags=tags,
        barcode=barcode,
    )

    # Attach images if we found any
    images_attached = 0
    if image_urls and result.get("product_id"):
        try:
            images_attached = await _attach_images(
                shopify_client, result["product_id"], image_urls[:5]
            )
        except Exception as e:
            logger.warning("Failed to attach images: %s", e)

    return {
        "status": "ok",
        "product_id": result["product_id"],
        "title": title,
        "sku": sku,
        "price": price,
        "price_source": price_source,
        "cost": cost_cad,
        "barcode": barcode,
        "enriched": enriched is not None,
        "title_source": "scraped" if scraped_title else ("ai" if enriched else "fallback"),
        "page_url": product_url,
        "product_type": product_type,
        "pricing_warning": pricing_warning,
        "images_attached": images_attached,
        "publications_published": result.get("publications_published", 0),
        "publication_error": result.get("publication_error"),
        "extraction_summary": {
            "has_description": bool(extracted.get("description")),
            "highlight_count": len(extracted.get("highlights") or []),
            "feature_count": len(extracted.get("features") or []),
            "spec_count": len(extracted.get("specs") or []),
            "document_categories": list((extracted.get("documents") or {}).keys()),
            "image_count": len(image_urls),
        },
    }


async def test_scrape_config(vendor: str, product_url: str,
                              db=None) -> dict:
    """
    Test a vendor's scrape config against a product URL without
    creating anything in Shopify. Returns the raw extraction results
    so the user can verify the config is working correctly.
    """
    scraper = _get_scraper(vendor, db)
    extracted = await scraper.scrape(product_url)

    if not extracted:
        return {
            "status": "error",
            "error": "No product data extracted from %s. For HTML vendors the "
                     "page may have failed to fetch; for Magento (Baader) the "
                     "url_key may not resolve a product." % product_url,
        }

    # Build a preview of what the AI would receive
    structured_context = format_extraction_for_ai(
        extracted, "TEST-SKU", vendor, "Test pricelist description", product_url
    )

    # Generate preview HTML for specs and documents
    specs_preview = format_specs_html(extracted.get("specs"))
    docs_preview = format_documents_html(extracted.get("documents"))

    return {
        "status": "ok",
        "extraction": {
            "description": extracted.get("description"),
            "highlights": extracted.get("highlights"),
            "features": extracted.get("features"),
            "specs": extracted.get("specs"),
            "documents": extracted.get("documents"),
            "images": extracted.get("images", [])[:5],
        },
        "ai_context_preview": structured_context[:3000],
        "specs_html_preview": specs_preview,
        "docs_html_preview": docs_preview,
        "summary": {
            "has_description": bool(extracted.get("description")),
            "description_length": len(extracted.get("description") or ""),
            "highlight_count": len(extracted.get("highlights") or []),
            "feature_count": len(extracted.get("features") or []),
            "spec_count": len(extracted.get("specs") or []),
            "document_categories": list(
                (extracted.get("documents") or {}).keys()
            ),
            "image_count": len(extracted.get("images") or []),
        },
    }


async def _attach_images(shopify_client, product_id, image_urls):
    """Attach images to a Shopify product using productUpdate with media."""
    if not image_urls:
        return 0

    media_input = []
    for url in image_urls:
        media_input.append({
            "originalSource": url,
            "mediaContentType": "IMAGE",
        })

    mutation = """
    mutation productUpdate($product: ProductUpdateInput!, $media: [CreateMediaInput!]) {
        productUpdate(product: $product, media: $media) {
            product { id media(first: 10) { nodes { id } } }
            userErrors { field message }
        }
    }
    """
    data = await shopify_client._query(mutation, {
        "product": {"id": product_id},
        "media": media_input,
    })
    result = data.get("productUpdate", {})
    errors = result.get("userErrors", [])
    if errors:
        logger.warning("Image attach errors: %s", errors)

    nodes = result.get("product", {}).get("media", {}).get("nodes", [])
    return len(nodes)
