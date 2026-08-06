"""
TC Competitor Price Checker
===========================
Standalone tool that uses Crawl4AI to check competitor prices and posts
results to the TC Planner API.

Run on Mac Studio (has compute power for headless browser).

Setup:
    pip install crawl4ai httpx
    crawl4ai-setup

Usage:
    # Check prices for top 50 products
    python price_checker.py --limit 50

    # Check specific vendor
    python price_checker.py --vendor Celestron --limit 20

    # Check a single SKU
    python price_checker.py --sku "NexStar 8SE"

    # Dry run (don't post to API)
    python price_checker.py --limit 10 --dry-run

Environment:
    TC_PLANNER_URL=https://tc-planner-app.azurewebsites.net
    TC_PLANNER_TOKEN=your-auth-token
"""

import asyncio
import json
import logging
import os
import re
import sys
import argparse
from dataclasses import dataclass, field
from typing import Optional, List, Dict

import httpx

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

# ─── CONFIGURATION ───────────────────────────────────────────────

TC_PLANNER_URL = os.environ.get('TC_PLANNER_URL', 'https://tc-planner-app.azurewebsites.net')
TC_PLANNER_TOKEN = os.environ.get('TC_PLANNER_TOKEN', '')


@dataclass
class CompetitorConfig:
    name: str
    domain: str
    search_url: str  # {query} will be replaced
    platform: str = 'shopify'  # shopify, woocommerce, other
    currency: str = 'CAD'


COMPETITORS = [
    CompetitorConfig(
        name='All-Star Telescope',
        domain='allstartelescope.com',
        search_url='https://www.allstartelescope.com/search?q={query}&type=product',
        platform='shopify',
    ),
    CompetitorConfig(
        name='Astronomy Plus',
        domain='astronomyplus.com',
        search_url='https://www.astronomyplus.com/search?q={query}&type=product',
        platform='shopify',
    ),
    CompetitorConfig(
        name='Ontario Telescope',
        domain='ontariotelescope.ca',
        search_url='https://ontariotelescope.ca/?s={query}&post_type=product',
        platform='woocommerce',
    ),
    CompetitorConfig(
        name='Khan Scope',
        domain='khanscope.com',
        search_url='https://www.khanscope.com/search?q={query}&type=product',
        platform='shopify',
    ),
    CompetitorConfig(
        name='David Astro',
        domain='davidastro.com',
        search_url='https://davidastro.com/search?q={query}&type=product',
        platform='shopify',
    ),
]


# ─── TC PLANNER API CLIENT ──────────────────────────────────────

async def get_products_from_planner(vendor: str = None, limit: int = 50) -> List[Dict]:
    """Fetch top products from TC Planner API."""
    params = {'limit': limit}
    if vendor:
        params['vendor'] = vendor

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{TC_PLANNER_URL}/api/market/products",
            params=params,
            headers={'Authorization': f'Bearer {TC_PLANNER_TOKEN}'},
        )
        resp.raise_for_status()
        return resp.json()


async def post_price_to_planner(entry: Dict) -> bool:
    """Post a competitor price to TC Planner API."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{TC_PLANNER_URL}/api/market/prices",
            json=entry,
            headers={
                'Authorization': f'Bearer {TC_PLANNER_TOKEN}',
                'Content-Type': 'application/json',
            },
        )
        if resp.status_code < 300:
            return True
        else:
            logger.error(f"Failed to post price: {resp.status_code} {resp.text[:200]}")
            return False


# ─── PRICE EXTRACTION ────────────────────────────────────────────

def extract_prices_from_markdown(markdown: str, product_name: str, our_price: float) -> List[Dict]:
    """
    Extract product prices from crawled markdown content.
    Returns list of {title, price, url, in_stock} matches.
    """
    results = []

    # Look for price patterns: $X,XXX.XX or $XXX.XX
    price_pattern = r'\$[\d,]+\.?\d{0,2}'

    # Split into blocks that might represent products
    lines = markdown.split('\n')

    # Build context windows — group nearby lines that contain both title-like text and prices
    for i, line in enumerate(lines):
        prices_in_line = re.findall(price_pattern, line)
        if not prices_in_line:
            continue

        # Get surrounding context for product title
        context_start = max(0, i - 3)
        context_end = min(len(lines), i + 3)
        context = ' '.join(lines[context_start:context_end])

        # Check if context mentions something close to our product
        product_words = set(product_name.lower().split())
        context_lower = context.lower()

        # Score: how many product name words appear in context
        word_matches = sum(1 for w in product_words if len(w) > 2 and w in context_lower)
        if word_matches < 2:
            continue

        # Parse the best price
        for price_str in prices_in_line:
            try:
                price_val = float(price_str.replace('$', '').replace(',', ''))
                # Skip prices that are wildly different (likely unrelated)
                if our_price > 0 and (price_val < our_price * 0.3 or price_val > our_price * 3):
                    continue

                # Check for out-of-stock indicators in context
                in_stock = True
                oos_indicators = ['sold out', 'out of stock', 'unavailable', 'backorder', 'notify me']
                for indicator in oos_indicators:
                    if indicator in context_lower:
                        in_stock = False
                        break

                results.append({
                    'price': price_val,
                    'context': context[:200],
                    'in_stock': in_stock,
                    'word_matches': word_matches,
                })
            except ValueError:
                continue

    # Return the best match (most word matches, then closest price to ours)
    if results:
        results.sort(key=lambda r: (-r['word_matches'], abs(r['price'] - our_price)))
        return results[:1]

    return []


# ─── CRAWL4AI CRAWLER ────────────────────────────────────────────

async def crawl_competitor_search(competitor: CompetitorConfig, query: str) -> Optional[str]:
    """Crawl a competitor's search page and return markdown content."""
    try:
        from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig

        url = competitor.search_url.format(query=query.replace(' ', '+'))
        logger.info(f"  Crawling {competitor.name}: {url}")

        browser_config = BrowserConfig(
            headless=True,
            verbose=False,
        )

        crawler_config = CrawlerRunConfig(
            wait_until='networkidle',
            page_timeout=15000,
        )

        async with AsyncWebCrawler(config=browser_config) as crawler:
            result = await crawler.arun(url=url, config=crawler_config)

            if result and result.success:
                return result.markdown
            else:
                logger.warning(f"  Crawl failed for {competitor.name}: {getattr(result, 'error_message', 'unknown')}")
                return None

    except ImportError:
        logger.error("crawl4ai not installed. Run: pip install crawl4ai && crawl4ai-setup")
        return None
    except Exception as e:
        logger.error(f"  Crawl error for {competitor.name}: {e}")
        return None


async def check_price_for_product(product: Dict, competitor: CompetitorConfig) -> Optional[Dict]:
    """Check a single product's price on a single competitor."""
    # Build search query — use product title, strip vendor name for cleaner search
    title = product['product_title']
    vendor = product.get('vendor', '')
    query = title

    # Try to make a shorter, more effective search query
    # Remove common prefixes and keep the model-specific part
    for prefix in [vendor, 'Telescopes Canada']:
        if query.startswith(prefix):
            query = query[len(prefix):].strip(' -')

    # Limit query length
    query_words = query.split()[:6]
    query = ' '.join(query_words)

    markdown = await crawl_competitor_search(competitor, query)
    if not markdown:
        return None

    matches = extract_prices_from_markdown(markdown, title, product['our_price'])
    if not matches:
        return None

    best = matches[0]
    return {
        'sku': product['sku'],
        'product_title': title,
        'competitor': competitor.name,
        'competitor_price': best['price'],
        'competitor_url': competitor.search_url.format(query=query.replace(' ', '+')),
        'competitor_in_stock': best['in_stock'],
        'our_price': product['our_price'],
    }


# ─── LIGHTWEIGHT FALLBACK (no Crawl4AI needed) ──────────────────

async def check_price_lightweight(product: Dict, competitor: CompetitorConfig) -> Optional[Dict]:
    """
    Lightweight price check using httpx — works for Shopify stores
    that expose search/suggest.json endpoint.
    """
    if competitor.platform != 'shopify':
        return None

    title = product['product_title']
    vendor = product.get('vendor', '')
    query = title
    for prefix in [vendor]:
        if query.startswith(prefix):
            query = query[len(prefix):].strip(' -')
    query_words = query.split()[:5]
    query = ' '.join(query_words)

    suggest_url = f"https://{competitor.domain}/search/suggest.json"
    params = {
        'q': query,
        'resources[type]': 'product',
        'resources[limit]': '5',
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(suggest_url, params=params, follow_redirects=True)

            if resp.status_code != 200:
                logger.debug(f"  Suggest API returned {resp.status_code} for {competitor.name}")
                return None

            data = resp.json()
            products = data.get('resources', {}).get('results', {}).get('products', [])

            if not products:
                return None

            # Find best matching product
            title_lower = title.lower()
            best_match = None
            best_score = 0

            for p in products:
                p_title = (p.get('title') or '').lower()
                # Score by word overlap
                title_words = set(title_lower.split())
                p_words = set(p_title.split())
                overlap = len(title_words & p_words)

                if overlap > best_score:
                    best_score = overlap
                    best_match = p

            if not best_match or best_score < 2:
                return None

            # Extract price
            price_str = best_match.get('price')
            if not price_str:
                return None

            try:
                # Shopify suggest returns price in cents (string) or dollars
                price_val = float(price_str)
                if price_val > 10000:  # Likely in cents
                    price_val /= 100
            except (ValueError, TypeError):
                return None

            # Check availability
            in_stock = best_match.get('available', True)

            # Build product URL
            handle = best_match.get('handle', '')
            product_url = f"https://{competitor.domain}/products/{handle}" if handle else None

            return {
                'sku': product['sku'],
                'product_title': product['product_title'],
                'competitor': competitor.name,
                'competitor_price': round(price_val, 2),
                'competitor_url': product_url,
                'competitor_in_stock': in_stock,
                'our_price': product['our_price'],
            }

    except Exception as e:
        logger.debug(f"  Lightweight check failed for {competitor.name}: {e}")
        return None


# ─── MAIN ORCHESTRATOR ───────────────────────────────────────────

async def run_price_check(
    vendor: str = None,
    sku: str = None,
    limit: int = 50,
    dry_run: bool = False,
    use_crawl4ai: bool = True,
):
    """Run price checks across all competitors."""
    logger.info("=" * 60)
    logger.info("TC Competitor Price Checker")
    logger.info("=" * 60)

    # Get products to check
    if sku:
        # Single SKU mode — create a product dict
        products = [{'sku': sku, 'product_title': sku, 'our_price': 0, 'vendor': ''}]
        logger.info(f"Checking single SKU: {sku}")
    else:
        logger.info(f"Fetching top {limit} products from TC Planner...")
        products = await get_products_from_planner(vendor=vendor, limit=limit)
        logger.info(f"Got {len(products)} products")

    if not products:
        logger.warning("No products to check")
        return

    results = []
    total_checks = len(products) * len(COMPETITORS)
    completed = 0
    found = 0

    for product in products:
        logger.info(f"\n[{product['sku']}] {product['product_title'][:60]} (${product['our_price']})")

        for competitor in COMPETITORS:
            completed += 1

            # Try lightweight first (faster, no browser needed)
            result = await check_price_lightweight(product, competitor)

            # Fall back to Crawl4AI if lightweight didn't work and it's enabled
            if not result and use_crawl4ai:
                result = await check_price_for_product(product, competitor)

            if result:
                found += 1
                diff = result['competitor_price'] - product['our_price']
                diff_str = f"+${diff:.2f}" if diff >= 0 else f"-${abs(diff):.2f}"
                status = "✅ In stock" if result.get('competitor_in_stock') else "❌ OOS"
                logger.info(f"  {competitor.name}: ${result['competitor_price']:.2f} ({diff_str}) {status}")

                if not dry_run:
                    posted = await post_price_to_planner(result)
                    if posted:
                        logger.debug(f"  → Posted to TC Planner")

                results.append(result)
            else:
                logger.info(f"  {competitor.name}: not found")

            # Rate limit between requests
            await asyncio.sleep(1)

        # Rate limit between products
        await asyncio.sleep(0.5)

    # Summary
    logger.info("\n" + "=" * 60)
    logger.info(f"COMPLETE: {completed} checks, {found} prices found")
    logger.info(f"Products checked: {len(products)}")
    logger.info(f"Competitors: {len(COMPETITORS)}")
    if dry_run:
        logger.info("DRY RUN — no prices were posted to TC Planner")
    else:
        logger.info(f"Posted {found} price entries to TC Planner")
    logger.info("=" * 60)

    return results


# ─── CLI ─────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='TC Competitor Price Checker')
    parser.add_argument('--vendor', help='Filter by vendor (e.g., Celestron)')
    parser.add_argument('--sku', help='Check a single SKU')
    parser.add_argument('--limit', type=int, default=50, help='Number of products to check (default: 50)')
    parser.add_argument('--dry-run', action='store_true', help="Don't post results to TC Planner")
    parser.add_argument('--no-crawl4ai', action='store_true', help='Use only lightweight checks (no browser)')

    args = parser.parse_args()

    if not TC_PLANNER_TOKEN and not args.dry_run:
        print("ERROR: Set TC_PLANNER_TOKEN environment variable")
        print("  export TC_PLANNER_TOKEN=your-token")
        sys.exit(1)

    asyncio.run(run_price_check(
        vendor=args.vendor,
        sku=args.sku,
        limit=args.limit,
        dry_run=args.dry_run,
        use_crawl4ai=not args.no_crawl4ai,
    ))


if __name__ == '__main__':
    main()
