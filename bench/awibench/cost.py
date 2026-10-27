"""§2 cost mapping + D3 two tier columns. AC5, AC6, AC14.

pricing.yaml entry per provider:
  {provider, pricing_url, checked_at, model: per_request|credits|tier|per_hour_browser|per_gb,
   credits_per_call: {call_type: n} (credits model), proxy_per_gb,
   free_tier: {units_per_month, needs_card, note},
   tiers: {entry: {...}, volume: {...}}}
tier fields by model (any value "NOT CAPTURED"/null that the model needs -> no cost, "n/a"):
  per_request: name, unit_price
  credits: name, usd_per_credit
  tier: name, plan_fee, included_units, overage_per_unit
  per_hour_browser: name, usd_per_hour, min_minutes, rounding_minutes
  per_gb: name, usd_per_gb
Free credits/quotas are never used to lower cost: cost is always at list price.
"""
import math
from .util import not_captured

NEED = {"per_request": ["unit_price"], "credits": ["usd_per_credit"], "tier": ["plan_fee", "included_units"],
        "per_hour_browser": ["usd_per_hour"], "per_gb": ["usd_per_gb"]}

def billed_minutes(raw_minutes, min_minutes=0, rounding_minutes=0):
    m = max(raw_minutes, min_minutes or 0)
    if rounding_minutes: m = math.ceil(m / rounding_minutes - 1e-9) * rounding_minutes
    return m

def billed_usd(pricing, tier_key, lines):
    """lines: run-log lines (status pass/fail; skipped_tos excluded by caller). Returns $ or None if NOT CAPTURED."""
    model = pricing.get("model"); tier = (pricing.get("tiers") or {}).get(tier_key)
    if not_captured(model) or not tier or model not in NEED: return None
    if any(not_captured(tier.get(k)) for k in NEED[model]): return None
    units = sum(l["billed_units"] for l in lines)
    if model == "per_request": usd = units * tier["unit_price"]
    elif model == "credits": usd = units * tier["usd_per_credit"]
    elif model == "tier":
        inc = tier["included_units"]; usd = units * tier["plan_fee"] / inc
        if units > inc:
            if not_captured(tier.get("overage_per_unit")): return None
            usd = tier["plan_fee"] + (units - inc) * tier["overage_per_unit"]
    elif model == "per_hour_browser":
        if any(k in tier and not_captured(tier[k]) for k in ("min_minutes", "rounding_minutes")): return None   # vendor minimum/rounding unknown
        usd = sum(billed_minutes(l["billed_units"], tier.get("min_minutes") or 0, tier.get("rounding_minutes") or 0) for l in lines) * tier["usd_per_hour"] / 60
    else: usd = units * tier["usd_per_gb"]
    proxy_gb = sum(l.get("proxy_gb") or 0 for l in lines)
    if proxy_gb:
        if not_captured(pricing.get("proxy_per_gb")): return None
        usd += proxy_gb * pricing["proxy_per_gb"]
    return usd

def min_rounding_not_published(pricing, tier_key):
    """per_hour_browser tier whose vendor minimum/rounding is explicitly NOT CAPTURED (checked, not on the page)."""
    tier = ((pricing or {}).get("tiers") or {}).get(tier_key) or {}
    return (pricing or {}).get("model") == "per_hour_browser" and any(k in tier and not_captured(tier[k]) for k in ("min_minutes", "rounding_minutes"))

def is_lower_bound(pricing, tier_key):
    """Browser-hour pricing without the vendor's minimum/rounding fields: billed time can only be higher, so cost is a lower bound.
    Also any tier with a non-empty "lower_bound" reason (a part of the vendor charge the units leave out, e.g. Apify compute units)."""
    tier = ((pricing or {}).get("tiers") or {}).get(tier_key) or {}
    if isinstance(tier.get("lower_bound"), str) and tier["lower_bound"].strip(): return True
    return (pricing or {}).get("model") == "per_hour_browser" and ("min_minutes" not in tier or "rounding_minutes" not in tier)

def lower_bound_reason(pricing, tier_key):
    """Why a lower-bound cost is low: the tier's own "lower_bound" text, else the browser-hour minimum/rounding reason; None if exact."""
    if not is_lower_bound(pricing, tier_key): return None
    tier = ((pricing or {}).get("tiers") or {}).get(tier_key) or {}
    return tier.get("lower_bound") or "vendor minimum/rounding not on pricing page; billed time could be higher"

def per_1k(usd, attempted, passed):
    """cost_per_1k = 1000*$/attempted; cost_per_successful_1k = 1000*$/passed (inf at 0 passes)."""
    if usd is None or attempted == 0: return None, None
    return 1000 * usd / attempted, (math.inf if passed == 0 else 1000 * usd / passed)

def cost_columns(pricing, lines):
    """D3: entry_tier_* and volume_tier_* columns plus the free-quota note. skipped_tos lines are excluded."""
    scored = [l for l in lines if l["status"] in ("pass", "fail")]
    attempted, passed = len(scored), sum(l["status"] == "pass" for l in scored)
    out = {}
    for key in ("entry", "volume"):
        usd = billed_usd(pricing or {}, key, scored) if pricing else None
        c, cs = per_1k(usd, attempted, passed)
        tier = ((pricing or {}).get("tiers") or {}).get(key) or {}
        out[f"{key}_tier_cost_per_1k"] = c; out[f"{key}_tier_cost_per_successful_1k"] = cs
        out[f"{key}_tier_name"] = tier.get("name") if c is not None else None
        out[f"{key}_tier_lower_bound"] = c is not None and is_lower_bound(pricing, key)
        out[f"{key}_tier_na_reason"] = "lower bound n/a (not published)" if c is None and min_rounding_not_published(pricing, key) else None
    ft = (pricing or {}).get("free_tier") or {}
    out["free_quota_note"] = ft.get("note") or (f"free tier: {ft['units_per_month']} units/month" if not not_captured(ft.get("units_per_month")) else "free tier: NOT CAPTURED")
    return out

def fmt(v, lower_bound=False):
    if v is None: return "n/a"
    if v == math.inf: return "∞"
    return f"≥ ${v:.2f} (lower bound)" if lower_bound else f"${v:.2f}"
