# Procurement Materials Platform — Project Knowledge Base

Version baseline: v2.3  
Purpose: preserve business context, design principles, procurement cases, and future development direction.

## 1. Project purpose

The platform is a procurement decision-support system for international raw-material analysis. It should help answer:

1. How much did relevant raw materials change?
2. Is a supplier's cited material actually moving in the claimed direction?
3. What share of the finished product cost is affected?
4. What finished-price change is reasonably attributable to those inputs?
5. What increase is the supplier requesting?
6. What is the negotiation gap?

## 2. Public market-analysis layer

- World Bank Pink Sheet is the primary monthly market benchmark.
- FRED is independent corroboration only.
- The public GitHub Pages site must not contain company inventory, supplier/PO records, private Google Sheet exports, credentials, or automatic buy/no-buy decisions.

## 3. Company daily market-data layer — C3

The company Google Sheet remains private operational data. The Python collector is the data-engineering layer.

Verified daily source contract from the original `update_prices_v5.py`:

- Copper Cash: LME Cash OFFER.
- Copper 3-month: LME 3-month OFFER.
- Aluminium / Lead / Nickel / Tin / Zinc: LME Cash OFFER.
- Electrolytic Copper: SMM `1#电解铜` / average value.
- Brent: yfinance `BZ=F` Close.
- Silver: yfinance `SI=F` Close × 100, stored as cents/oz.
- Gold: yfinance `GC=F` Close.

C3.1 strengthens this legacy behavior by adding semantic LME OFFER-column validation, explicit SMM average selection, safe target-row resolution, full A:L write guards, audit metadata, credential isolation, and Live Dry Run mode.

The current authoritative Google Sheet labels must match those actual sources rather than stale display text.

## 4. Copper daily decision principle

For daily copper purchasing, LME Copper Cash OFFER is the first market reference. World Bank monthly Copper remains medium-term benchmark context and must not replace the daily Cash OFFER.

C3.2 will add explainable market-condition analytics such as day-over-day change, 5D/20D averages, 60D percentile, Cash-vs-3M spread, and comparison against the latest World Bank monthly benchmark. It remains decision support only.

## 5. Supplier cases retained for later phases

### TTP Technologies India / transformer radiators

- Supplier cited incoming-cost increases across steel, zinc, packaging, and gas.
- CR Coil 1.00mm market references and LME Zinc are important external evidence.
- Inferred cost shares must remain labeled as assumptions until verified by BOM or supplier cost breakdown.

### Bushing / PCORE-Hubbell

- Supplier-request analysis must distinguish raw-material basket movement from finished-product price movement.
- Copper, silver, resin, FX, inventory/lag, and cost-share transmission need explicit treatment.

## 6. Long-term procurement transaction integration

Target flow:

```text
Market Price History
→ Supplier Quotation / Increase Request
→ Negotiation Case
→ PO Actual Transaction Price
```

ERP / WPS procurement history, supplier history, same-day multi-price cases, and actual PO outcomes are future private/internal integrations, not data to publish in the current public site.
