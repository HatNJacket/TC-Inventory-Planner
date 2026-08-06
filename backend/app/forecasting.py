"""
TC Inventory Planner - Forecasting Engine
Calculates sales velocity per SKU with seasonal adjustments
and generates replenishment recommendations.

Approach:
1. Calculate base daily sales velocity from trailing 12 months of order data
2. Compute per-SKU seasonal multipliers from each product's own monthly pattern
   - If a SKU has enough history (8+ units across 4+ months), use its own curve
   - For new/low-volume products, fall back to the store-level seasonal multiplier
3. Project forward demand over the planning period (lead_time + safety_stock days),
   accounting for month-by-month seasonality across the window
4. Recommend replenishment qty = projected_demand - current_stock - on_order
"""
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from .config import config

logger = logging.getLogger(__name__)

# Thresholds for using per-SKU seasonality vs store-level fallback
MIN_SALES_FOR_SKU_SEASONALITY = 8    # need 8+ units sold in trailing 12 months
MIN_MONTHS_WITH_SALES = 4            # need sales in 4+ distinct months

# New-listing velocity adjustment: for products listed less than 365 days ago,
# annualize sales using days-listed instead of 365 (so the daily rate isn't
# diluted by months when the product wasn't on the market).
# We require at least this many days of listing history before applying the
# adjustment — too few days produces wildly overconfident velocity estimates
# from tiny samples (e.g. 1 sale in 5 days = 73 sales/year).
MIN_DAYS_FOR_LISTING_ADJUSTMENT = 30

# Trailing-window for "recent velocity" rule. When projecting forward demand,
# we use the higher of the trailing-365 average and the trailing-N rate. This
# catches accelerating items (e.g. seasonal ramp-ups, viral products, items
# moving to a Best Seller list) that the annual average would miss. We need
# enough days for the recent rate to be statistically meaningful — 90 days
# strikes a balance between responsiveness and noise.
RECENT_VELOCITY_WINDOW_DAYS = 90
MIN_SALES_FOR_RECENT_VELOCITY = 2  # need 2+ sales in window to use recent rate

# Floor on the per-SKU seasonal multiplier when projecting forward demand.
# Without this, a SKU that historically sells only in November/December gets
# a multiplier near zero in April, which can suppress legitimate replenishment
# signals for items that customers ARE actively asking about. The floor of
# 0.5 means an off-peak month projects at no less than 50% of average rate.
SKU_SEASONAL_MULTIPLIER_FLOOR = 0.5


@dataclass
class SalesVelocity:
    """Sales velocity data for a single SKU."""
    sku: str
    variant_id: str
    product_id: str
    vendor: str

    # Raw sales data
    total_units_sold_365d: int = 0
    total_units_sold_90d: int = 0
    total_units_sold_30d: int = 0
    monthly_sales: Dict[int, int] = None  # month_number -> units sold

    # Calculated velocity
    avg_daily_velocity: float = 0.0       # base (non-seasonal) daily rate
    avg_monthly_velocity: float = 0.0     # base monthly rate
    seasonal_daily_velocity: float = 0.0  # adjusted for planning period
    seasonal_monthly_velocity: float = 0.0

    # Per-SKU seasonal multipliers (month -> multiplier)
    sku_seasonal_multipliers: Dict[int, float] = None
    uses_sku_seasonality: bool = False  # True if using own curve, False if store-level

    # Trend
    trend_direction: str = "stable"  # "up", "down", "stable"
    trend_magnitude: float = 0.0     # % change recent vs historical

    # New-listing window adjustment
    days_listed: Optional[int] = None         # how long the product has been listed
    velocity_window_days: int = 365           # denominator used for avg_daily_velocity
    listing_adjusted: bool = False            # True if we shortened the window

    # Recent velocity (trailing 90d). Used as a floor when projecting forward
    # demand — catches accelerating items that the annual average dilutes.
    recent_daily_velocity: float = 0.0        # trailing 90d daily rate
    uses_recent_velocity: bool = False         # True if recent > annual was used

    def __post_init__(self):
        if self.monthly_sales is None:
            self.monthly_sales = {}
        if self.sku_seasonal_multipliers is None:
            self.sku_seasonal_multipliers = {}


@dataclass
class ReplenishmentRecommendation:
    """Replenishment recommendation for a single variant."""
    # Product info
    product_id: str
    variant_id: str
    product_title: str
    variant_title: str
    sku: str
    barcode: str
    vendor: str
    product_type: str
    image_url: str

    # Current state
    price: float
    cost: float
    current_stock: int
    on_order: int

    # Velocity & forecast
    velocity: SalesVelocity
    lead_time_days: int
    planning_start: str
    planning_end: str

    # Recommendation
    replenish_qty: int
    days_of_stock: float        # how many days current stock will last
    sells_out_date: str         # estimated date stock runs out
    forecast_profit: float      # profit pro-rated by 30d sell-through rate
    total_forecast_profit: float  # full profit if all replenished units sell: qty × (price − cost)
    sales_365d: int
    avg_sales_per_month: float
    sales_velocity_per_month: float  # seasonal-adjusted
    projected_demand: float = 0.0  # demand forecast over the planning window;
                                   # cached so PO changes can recompute replenish_qty
                                   # cheaply: max(0, round(projected_demand - stock - on_order))

    # Vendor metadata (from Shopify metafields)
    cost_usd: str = ""
    system_code: str = ""
    tags: str = ""  # comma-separated tags from Shopify
    inventory_policy: str = "DENY"  # DENY or CONTINUE

    # User-set hard minimum on this SKU's stock level. 0 = no minimum.
    # When current_stock + on_order < min_stock_level, the replenish_qty is
    # floored at the gap (top-to-min), unless a status override (Discontinued,
    # Replacement Part, DENY+OOS) zeroes everything out.
    min_stock_level: int = 0
    below_min: bool = False

    # Alternative seasonal-adjusted recommendation, surfaced in brackets on
    # the Replenishment table for reference. Computed using the per-SKU
    # seasonal multipliers; not the value used to drive replenish_qty.
    seasonal_replenish_qty: int = 0
    seasonal_projected_demand: float = 0.0

    # Listing data (when the product first became visible on storefront)
    listed_at: Optional[datetime] = None

    # Vendor-sale fields. Populated when an active vendor_promo_pricing row
    # applies to this SKU; cost above is overridden with the sale cost (CAD)
    # so margin/forecast_profit reflect the discounted reorder price.
    on_sale: bool = False
    regular_cost: float = 0.0       # original (non-sale) cost in CAD
    sale_cost_foreign: float = 0.0  # sale price in vendor invoice currency
    sale_currency: str = ""
    sale_ends_at: Optional[str] = None  # ISO 8601

    @property
    def replenishment_cost(self) -> float:
        return self.replenish_qty * self.cost

    @property
    def replenishment_retail(self) -> float:
        return self.replenish_qty * self.price


class ForecastEngine:
    """Calculates sales velocity and replenishment recommendations."""

    def __init__(self):
        self.seasonal_multipliers = config.SEASONAL_MULTIPLIERS

    def calculate_sales_velocity(
        self,
        sku: str,
        variant_id: str,
        product_id: str,
        vendor: str,
        line_items: List[Dict],
        reference_date: Optional[datetime] = None,
        listed_at: Optional[datetime] = None,
    ) -> SalesVelocity:
        """
        Calculate sales velocity for a single SKU from order line items.
        Derives per-SKU seasonal multipliers when enough history exists.

        If listed_at is provided and the product has been listed for less than
        365 days (but at least MIN_DAYS_FOR_LISTING_ADJUSTMENT), the daily
        velocity is calculated using days-listed as the denominator instead
        of 365. This avoids underestimating velocity for newly launched products.
        """
        if reference_date is None:
            reference_date = datetime.now(timezone.utc)

        velocity = SalesVelocity(
            sku=sku,
            variant_id=variant_id,
            product_id=product_id,
            vendor=vendor,
        )

        if not line_items:
            return velocity

        # Aggregate by time period
        total_365d = 0
        total_90d = 0
        total_30d = 0
        monthly_buckets = defaultdict(int)

        cutoff_365 = reference_date - timedelta(days=365)
        cutoff_90 = reference_date - timedelta(days=90)
        cutoff_30 = reference_date - timedelta(days=30)

        for item in line_items:
            order_date = item["order_date"]
            qty = item["quantity"]

            if order_date >= cutoff_365:
                total_365d += qty
                monthly_buckets[order_date.month] += qty

            if order_date >= cutoff_90:
                total_90d += qty

            if order_date >= cutoff_30:
                total_30d += qty

        velocity.total_units_sold_365d = total_365d
        velocity.total_units_sold_90d = total_90d
        velocity.total_units_sold_30d = total_30d
        velocity.monthly_sales = dict(monthly_buckets)

        # Determine the velocity-window denominator:
        # - default = 365 (or however much trailing data we have)
        # - if the product has been listed for less than 365 days AND at least
        #   MIN_DAYS_FOR_LISTING_ADJUSTMENT days, use days-listed instead. This
        #   gives newly launched products a fair daily rate without diluting
        #   them by months when they weren't yet on sale.
        days_of_data = min(365, (reference_date - cutoff_365).days)
        velocity_window = days_of_data

        if listed_at is not None:
            # Normalize tz so subtraction works whether listed_at has tzinfo
            if listed_at.tzinfo is None:
                listed_at = listed_at.replace(tzinfo=timezone.utc)
            days_listed = max(0, (reference_date - listed_at).days)
            velocity.days_listed = days_listed

            if (MIN_DAYS_FOR_LISTING_ADJUSTMENT
                    <= days_listed < days_of_data):
                velocity_window = days_listed
                velocity.listing_adjusted = True

        velocity.velocity_window_days = velocity_window

        # Base velocity (non-seasonal annual average)
        if velocity_window > 0:
            velocity.avg_daily_velocity = total_365d / velocity_window
            velocity.avg_monthly_velocity = velocity.avg_daily_velocity * 30.44

        # Recent velocity (trailing 90 days) — used as a floor when projecting
        # forward demand to catch accelerating items.
        if total_90d >= MIN_SALES_FOR_RECENT_VELOCITY:
            velocity.recent_daily_velocity = total_90d / RECENT_VELOCITY_WINDOW_DAYS

        # ─── Per-SKU Seasonal Multipliers ────────────────────────
        months_with_sales = len(monthly_buckets)
        has_enough_history = (
            total_365d >= MIN_SALES_FOR_SKU_SEASONALITY
            and months_with_sales >= MIN_MONTHS_WITH_SALES
        )

        if has_enough_history:
            # Calculate this SKU's own seasonal curve
            # Monthly average = total / 12
            avg_monthly_sales = total_365d / 12.0
            sku_multipliers = {}
            for month_num in range(1, 13):
                month_sales = monthly_buckets.get(month_num, 0)
                if avg_monthly_sales > 0:
                    sku_multipliers[month_num] = month_sales / avg_monthly_sales
                else:
                    sku_multipliers[month_num] = 1.0

            velocity.sku_seasonal_multipliers = sku_multipliers
            velocity.uses_sku_seasonality = True
        else:
            # Fall back to store-level seasonal curve
            velocity.sku_seasonal_multipliers = dict(self.seasonal_multipliers)
            velocity.uses_sku_seasonality = False

        # Seasonal velocity for current month (used for display/sorting)
        current_month = reference_date.month
        multiplier = velocity.sku_seasonal_multipliers.get(current_month, 1.0)
        # Clamp extreme multipliers to prevent wild swings
        multiplier = max(0.1, min(multiplier, 5.0))
        velocity.seasonal_daily_velocity = velocity.avg_daily_velocity * multiplier
        velocity.seasonal_monthly_velocity = velocity.avg_monthly_velocity * multiplier

        # Trend: compare recent 90d annualized vs full 365d
        if total_365d > 0 and total_90d > 0:
            annualized_90d = total_90d * (365 / 90)
            velocity.trend_magnitude = (
                (annualized_90d - total_365d) / total_365d
            ) * 100

            if velocity.trend_magnitude > 20:
                velocity.trend_direction = "up"
            elif velocity.trend_magnitude < -20:
                velocity.trend_direction = "down"
            else:
                velocity.trend_direction = "stable"

        return velocity

    def _project_demand_flat(self, velocity: SalesVelocity, num_days: int) -> float:
        """Primary projection used by replenish_qty.

        Flat rate of ``max(avg_daily, recent_daily) × num_days``. Doesn't
        apply per-SKU seasonal multipliers — those introduced large
        per-product variance (newly-listed products got inflated multipliers
        from short windows; established products got dampened multipliers
        for current "off-peak" months) which made similar-velocity items
        recommend wildly different quantities.

        ``recent_daily_velocity`` (trailing 90d) is included so accelerating
        items still get a boost — we just no longer let the per-SKU curve
        either inflate or suppress the rate."""
        base = velocity.avg_daily_velocity
        if velocity.recent_daily_velocity > base:
            base = velocity.recent_daily_velocity
            velocity.uses_recent_velocity = True
        if base <= 0:
            return 0.0
        return base * num_days

    def _project_demand_seasonal(
        self,
        velocity: SalesVelocity,
        start_date: datetime,
        num_days: int,
    ) -> float:
        """Alternative projection that DOES apply per-SKU seasonal multipliers.

        Kept for the "seasonal-adjusted" number shown in brackets on the
        Replenishment table — useful when a product has real, strong
        seasonality and you want to see what the seasonal forecaster would
        recommend. Not used as the primary replenish_qty since the
        per-product multipliers are noisy across the whole catalog.
        """
        base_velocity = velocity.avg_daily_velocity
        if velocity.recent_daily_velocity > base_velocity:
            base_velocity = velocity.recent_daily_velocity

        if base_velocity <= 0:
            return 0.0

        # Newly-listed: per-SKU curve has data gaps for months before listing.
        # Project flat at the current seasonal rate so non-listed months
        # don't artificially suppress the forecast.
        if velocity.listing_adjusted and velocity.seasonal_daily_velocity > base_velocity:
            return velocity.seasonal_daily_velocity * num_days

        total_demand = 0.0
        current_date = start_date
        days_remaining = num_days
        while days_remaining > 0:
            month = current_date.month
            if month == 12:
                next_month_start = current_date.replace(year=current_date.year + 1, month=1, day=1)
            else:
                next_month_start = current_date.replace(month=month + 1, day=1)
            days_in_this_segment = min(days_remaining, (next_month_start - current_date).days)
            if days_in_this_segment <= 0:
                days_in_this_segment = days_remaining
            multiplier = velocity.sku_seasonal_multipliers.get(month, 1.0)
            if velocity.uses_sku_seasonality:
                multiplier = max(SKU_SEASONAL_MULTIPLIER_FLOOR, min(multiplier, 5.0))
            else:
                multiplier = max(0.1, min(multiplier, 5.0))
            total_demand += base_velocity * multiplier * days_in_this_segment
            current_date = next_month_start
            days_remaining -= days_in_this_segment
        return total_demand

    def calculate_replenishment(
        self,
        product: Dict,
        velocity: SalesVelocity,
        on_order: int = 0,
        reference_date: Optional[datetime] = None,
        projection_multiplier: float = 1.0,
        cycle_days: int = 30,
        waiter_count: int = 0,
        waiter_conversion_rate: float = 0.0,
        min_stock: int = 0,
        sale_info: Optional[Dict] = None,
    ) -> ReplenishmentRecommendation:
        """
        Calculate replenishment recommendation for a single variant.

        Demand is projected over ``(cycle_days × projection_multiplier) +
        lead_time`` days. The cycle window is what each PO should last AFTER
        it arrives; lead time is added as a fixed buffer to cover depletion
        during shipping. Splitting the two means changing the multiplier
        only scales the ordering frequency, not the supplier's lead time.

        projection_multiplier: scales the cycle window only.
            1.0 = ``cycle_days`` of post-arrival stock (default 30 days)
            1.5 = 1.5× cycle (45 days)
            2.0 = 2× cycle (60 days)

        cycle_days: how long each PO should last after arriving. Default 30.
            Tune via the "Order cycle days" setting on Settings page.

        waiter_count / waiter_conversion_rate: when > 0, applies a hard floor on
            replenish_qty so we order at least ceil(waiter_count * rate) units.
            Status overrides (Discontinued / Replacement Part / DENY+OOS) still
            win — we never auto-order retired items even if customers are waiting.
        """
        if reference_date is None:
            reference_date = datetime.now(timezone.utc)

        vendor = product["vendor"]
        lead_time = config.get_lead_time(vendor)
        current_stock = product["inventory_quantity"]
        cost = product["cost"]
        price = product["price"]

        # Vendor-sale override: if this SKU is on an active vendor sale, swap
        # in the (CAD-converted) sale cost so margin reflects what we'd
        # actually pay if we replenished today. The Shopify-stored cost is
        # kept on `regular_cost` for display.
        regular_cost_for_display = cost
        on_sale = False
        sale_cost_foreign = 0.0
        sale_currency = ""
        sale_ends_at = None
        if sale_info:
            sale_cad = sale_info.get('sale_cost_cad')
            if sale_cad and sale_cad > 0:
                cost = float(sale_cad)
                on_sale = True
                sale_cost_foreign = float(sale_info.get('sale_cost') or 0)
                sale_currency = sale_info.get('sale_currency') or ''
                sale_ends_at = sale_info.get('ends_at')

        # Days of stock remaining (at seasonal velocity)
        if velocity.seasonal_daily_velocity > 0:
            effective_stock = max(current_stock + on_order, 0)
            days_of_stock = effective_stock / velocity.seasonal_daily_velocity
        else:
            days_of_stock = 999 if current_stock > 0 else 0

        # Sells-out date
        if days_of_stock < 999 and days_of_stock > 0:
            sells_out = reference_date + timedelta(days=days_of_stock)
            sells_out_str = sells_out.strftime("%b %d, %Y")
        else:
            sells_out_str = "N/A" if current_stock > 0 else reference_date.strftime("%b %d, %Y")

        # Replenishment quantity. Window = cycle (scaled by multiplier) +
        # lead time. Cycle covers what each PO should last after arriving;
        # lead time covers depletion during shipping. The user sets cycle
        # days via Settings (default 30).
        cycle_window = max(1, round(cycle_days * projection_multiplier))
        demand_days = cycle_window + max(0, lead_time)
        # Primary projection: flat at max(avg_daily, recent_daily). Drives
        # replenish_qty. Consistent across products; no per-SKU seasonal noise.
        projected_demand = self._project_demand_flat(velocity, demand_days)
        replenish_qty = max(0, round(projected_demand - current_stock - on_order))

        # Alternate projection: seasonal-adjusted version, surfaced in
        # brackets on the Replenishment table for reference. Same floors
        # below (waiter, min_stock) are NOT applied here — this is what the
        # forecaster would suggest based purely on the seasonal model.
        seasonal_projected = self._project_demand_seasonal(
            velocity, reference_date, demand_days
        )
        seasonal_replenish_qty = max(0, round(seasonal_projected - current_stock - on_order))

        # OOS-slow-mover floor: when the product is already at zero stock with
        # nothing on order AND we have ANY positive projected demand (i.e. it
        # has SOME real sales history), we must order at least one unit. The
        # plain `round()` math above silently zeroes out anything whose
        # fractional demand over the window is under 0.5 — that catches every
        # accessory selling 0.25 units/month or less and quietly loses sales
        # forever. `ceil()` on the projected demand guarantees a positive
        # recommendation in this exact corner without changing any other
        # behavior (in-stock items, already-on-order items, never-sold items,
        # and Discontinued/Replacement Part SKUs are all unaffected).
        from math import ceil as _ceil
        if (current_stock + on_order) <= 0:
            if projected_demand > 0:
                replenish_qty = max(replenish_qty, _ceil(projected_demand))
            if seasonal_projected > 0:
                seasonal_replenish_qty = max(seasonal_replenish_qty, _ceil(seasonal_projected))

        # Waiter-driven hard floor. If customers are signed up for back-in-stock
        # notifications and the conversion rate is enabled, ensure we order at
        # least enough units to cover them. Subtracts current_stock and on_order
        # so we don't double-count units already in the pipeline. Applied BEFORE
        # status overrides so retired SKUs still don't auto-order.
        if waiter_count > 0 and waiter_conversion_rate > 0:
            from math import ceil
            waiter_demand = max(1, ceil(waiter_count * waiter_conversion_rate))
            # The floor is on units we still need to bring in — already-stocked
            # units count toward fulfilling the wait list.
            waiter_floor = max(0, waiter_demand - current_stock - on_order)
            replenish_qty = max(replenish_qty, waiter_floor)

        # User-set minimum stock floor. If we want at least N units in stock
        # at all times, top up to N when current_stock + on_order falls below.
        # Same status overrides below still win (Discontinued / DENY+OOS), so
        # this never auto-orders retired items.
        below_min = False
        if min_stock and min_stock > 0:
            top_to_min = max(0, int(min_stock) - current_stock - on_order)
            replenish_qty = max(replenish_qty, top_to_min)
            below_min = (current_stock + on_order) < int(min_stock)

        # Status overrides — items in certain states should NEVER be replenished
        # automatically based on demand, regardless of what the math says:
        # - Tagged "Discontinued": vendor or store has stopped selling
        # - Tagged "Replacement Part": stocked on-demand only; ordered when needed
        # - Inventory policy DENY + zero stock: customers can't order it anymore,
        #   typically a soft-discontinued state
        # Note: "Special Order" tag is NOT in this list — per business rules,
        # those items should still replenish based on demand. The tag is only
        # informational for customers.
        #
        # Backorder exception: Replacement Part items get a single carve-out.
        # When current_stock is negative (a customer has already placed a back-
        # order on Shopify), we MUST cover that order even though we wouldn't
        # restock it speculatively. We order just enough to fill the back-order
        # (accounting for whatever's already on order so we don't double-buy).
        # Discontinued / DENY+OOS items don't get this exception — those
        # backorders need manual handling, not auto-replenishment.
        tags_lower = [t.lower().strip() for t in product.get("tags", [])]
        inventory_policy = product.get("inventory_policy", "DENY")
        is_replacement_part = (
            "replacement part" in tags_lower or "replacement parts" in tags_lower
        )
        is_hard_retired = (
            "discontinued" in tags_lower
            or (inventory_policy == "DENY" and current_stock <= 0)
        )
        if is_hard_retired or is_replacement_part:
            # Zero out both the primary and the seasonal alternative so the
            # bracketed UI doesn't suggest speculative reordering.
            replenish_qty = 0
            seasonal_replenish_qty = 0
            # Replacement Part backorder carve-out: cover any negative stock.
            if is_replacement_part and not is_hard_retired and current_stock < 0:
                backorder_cover = max(0, (-current_stock) - on_order)
                if backorder_cover > 0:
                    replenish_qty = backorder_cover
                    seasonal_replenish_qty = backorder_cover

        # Planning period
        planning_start = reference_date.strftime("%b %d")
        planning_end = (reference_date + timedelta(days=lead_time)).strftime("%b %d")

        # Forecast profit from replenished units.
        #   forecast_profit       — pro-rated by the 30-day sell-through rate
        #                           (how much profit lands within the planning
        #                           horizon; slow movers are dampened).
        #   total_forecast_profit — full gross profit if every replenished
        #                           unit sells: qty × per-unit margin. Tracks
        #                           the displayed margin %; sums cleanly per PO.
        margin = price - cost if cost > 0 else price * 0.2
        sell_through_rate = min(1.0, velocity.seasonal_daily_velocity * config.PLANNING_HORIZON_DAYS / max(replenish_qty, 1))
        forecast_profit = replenish_qty * margin * sell_through_rate
        total_forecast_profit = replenish_qty * margin

        return ReplenishmentRecommendation(
            product_id=product["product_id"],
            variant_id=product["variant_id"],
            product_title=product["product_title"],
            variant_title=product.get("variant_title", ""),
            sku=product.get("sku", ""),
            barcode=product.get("barcode", ""),
            vendor=vendor,
            product_type=product.get("product_type", ""),
            image_url=product.get("image_url", ""),
            price=price,
            cost=cost,
            current_stock=current_stock,
            on_order=on_order,
            velocity=velocity,
            lead_time_days=lead_time,
            planning_start=planning_start,
            planning_end=planning_end,
            replenish_qty=replenish_qty,
            days_of_stock=round(days_of_stock, 1),
            sells_out_date=sells_out_str,
            forecast_profit=round(forecast_profit, 2),
            total_forecast_profit=round(total_forecast_profit, 2),
            sales_365d=velocity.total_units_sold_365d,
            avg_sales_per_month=round(velocity.avg_monthly_velocity, 2),
            sales_velocity_per_month=round(velocity.seasonal_monthly_velocity, 2),
            projected_demand=round(projected_demand, 4),
            cost_usd=product.get("cost_usd", ""),
            system_code=product.get("system_code", ""),
            tags=",".join(product.get("tags", [])),
            inventory_policy=product.get("inventory_policy", "DENY"),
            min_stock_level=int(min_stock or 0),
            below_min=below_min,
            seasonal_replenish_qty=int(seasonal_replenish_qty),
            seasonal_projected_demand=round(seasonal_projected, 4),
            on_sale=on_sale,
            regular_cost=regular_cost_for_display,
            sale_cost_foreign=sale_cost_foreign,
            sale_currency=sale_currency,
            sale_ends_at=sale_ends_at,
        )

    def generate_replenishment_report(
        self,
        products: List[Dict],
        order_line_items: List[Dict],
        on_order_by_sku: Optional[Dict[str, int]] = None,
        vendor_filter: Optional[str] = None,
        reference_date: Optional[datetime] = None,
        projection_multiplier: float = 1.0,
        cycle_days: int = 30,
        waiter_count_by_sku: Optional[Dict[str, int]] = None,
        waiter_conversion_rate: float = 0.0,
        min_stock_by_sku: Optional[Dict[str, int]] = None,
        sale_cost_lookup: Optional[Dict[str, Dict]] = None,
    ) -> List[ReplenishmentRecommendation]:
        """
        Generate replenishment recommendations for all products.

        Args:
            products: List of product dicts from Shopify
            order_line_items: Historical order line items
            on_order_by_sku: Dict of SKU -> on-order quantity from stock orders
            vendor_filter: Optional vendor name to filter by
            reference_date: Reference date for calculations
            projection_multiplier: Scale the demand window (1.0 = lead+safety only,
                higher = include cycle stock between POs)
            waiter_count_by_sku: Dict of SKU -> waiter count from latest snapshot
            waiter_conversion_rate: Fraction of waiters expected to buy. When
                positive, applies a hard floor on replenish_qty.
        """
        if on_order_by_sku is None:
            on_order_by_sku = {}
        if waiter_count_by_sku is None:
            waiter_count_by_sku = {}
        if min_stock_by_sku is None:
            min_stock_by_sku = {}
        if sale_cost_lookup is None:
            sale_cost_lookup = {}

        # Index line items by SKU for fast lookup
        items_by_sku = defaultdict(list)
        for item in order_line_items:
            items_by_sku[item["sku"]].append(item)

        recommendations = []

        for product in products:
            # Apply vendor filter
            if vendor_filter and product["vendor"] != vendor_filter:
                continue

            sku = product.get("sku", "")
            if not sku:
                continue

            # Parse listed_at from the product dict (ISO 8601 from Shopify).
            # Empty / missing → None → no listing adjustment
            listed_at = None
            raw = product.get("listed_at")
            if raw:
                try:
                    listed_at = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                except (ValueError, AttributeError):
                    listed_at = None

            # Calculate velocity
            velocity = self.calculate_sales_velocity(
                sku=sku,
                variant_id=product["variant_id"],
                product_id=product["product_id"],
                vendor=product["vendor"],
                line_items=items_by_sku.get(sku, []),
                reference_date=reference_date,
                listed_at=listed_at,
            )

            # Get on-order quantity
            on_order = on_order_by_sku.get(sku, 0)

            # Get waiter count for this SKU. Waiter snapshots store SKUs in the
            # exact case they came from Shopify; the map lookup below tries the
            # same case first then upper-cased to handle case-insensitive matches.
            wc = waiter_count_by_sku.get(sku, waiter_count_by_sku.get(sku.upper(), 0))

            # Per-SKU min stock (case-insensitive lookup, same convention as waiters).
            ms = min_stock_by_sku.get(sku, min_stock_by_sku.get(sku.upper(), 0))

            # Active vendor sale (case-insensitive, same convention).
            sale = sale_cost_lookup.get(sku.upper())

            # Calculate recommendation
            rec = self.calculate_replenishment(
                product=product,
                velocity=velocity,
                on_order=on_order,
                reference_date=reference_date,
                projection_multiplier=projection_multiplier,
                cycle_days=cycle_days,
                waiter_count=wc,
                waiter_conversion_rate=waiter_conversion_rate,
                min_stock=ms,
                sale_info=sale,
            )
            # Attach listed_at for downstream caching (post-construction is fine
            # since it's a dataclass with a default)
            rec.listed_at = listed_at

            recommendations.append(rec)

        # Sort by replenishment retail value (highest first)
        recommendations.sort(
            key=lambda r: r.replenishment_retail,
            reverse=True,
        )

        logger.info(
            f"Generated {len(recommendations)} recommendations"
            f" ({sum(1 for r in recommendations if r.replenish_qty > 0)} need replenishment)"
        )

        return recommendations

    def get_overview_stats(
        self, recommendations: List[ReplenishmentRecommendation]
    ) -> Dict:
        """Calculate overview statistics from recommendations."""
        needs_replenish = [r for r in recommendations if r.replenish_qty > 0]

        # Vendor breakdown
        vendor_stock = defaultdict(lambda: {"cost": 0, "retail": 0, "units": 0})
        vendor_replenish = defaultdict(lambda: {"cost": 0, "retail": 0, "units": 0})

        for r in recommendations:
            if r.current_stock > 0:
                vendor_stock[r.vendor]["cost"] += r.cost * r.current_stock
                vendor_stock[r.vendor]["retail"] += r.price * r.current_stock
                vendor_stock[r.vendor]["units"] += r.current_stock

            if r.replenish_qty > 0:
                vendor_replenish[r.vendor]["cost"] += r.replenishment_cost
                vendor_replenish[r.vendor]["retail"] += r.replenishment_retail
                vendor_replenish[r.vendor]["units"] += r.replenish_qty

        return {
            "total_variants": len(recommendations),
            "variants_in_stock": sum(
                1 for r in recommendations if r.current_stock > 0
            ),
            "total_stock_units": sum(
                max(r.current_stock, 0) for r in recommendations
            ),
            "total_stock_cost": round(
                sum(r.cost * max(r.current_stock, 0) for r in recommendations), 2
            ),
            "total_stock_retail": round(
                sum(r.price * max(r.current_stock, 0) for r in recommendations), 2
            ),
            "replenishment_units": sum(r.replenish_qty for r in needs_replenish),
            "replenishment_cost": round(
                sum(r.replenishment_cost for r in needs_replenish), 2
            ),
            "replenishment_retail": round(
                sum(r.replenishment_retail for r in needs_replenish), 2
            ),
            "vendor_stock": dict(vendor_stock),
            "vendor_replenishment": dict(vendor_replenish),
        }


# Singleton
forecast_engine = ForecastEngine()
