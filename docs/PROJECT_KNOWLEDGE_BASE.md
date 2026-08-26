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
7. What evidence should procurement request?
8. Is there a price-reduction opportunity when markets fall?

The platform must not automatically accept or reject a supplier price request.

## 2. Core analytical flow

```text
Raw Data
→ Validated Data
→ Derived Metrics
→ Market Signal
→ Should-Cost
→ Supplier Rationality
→ Negotiation Report
→ Human Decision
```

Market price change must never be treated as identical to finished-product price change.

## 3. Current data strategy

### World Bank Pink Sheet

Role: primary market source.

Used for:
- market direction
- monthly price series
- trend calculations
- negotiation signal basis

### FRED

Role: independent comparison / corroboration only.

FRED may strengthen or weaken confidence in the observed market direction but must not overwrite the World Bank primary trend signal.

### Current core materials

- Zinc
- Copper
- Aluminium
- Nickel
- Iron Ore
- Brent Crude Oil
- Natural Gas

## 4. Decision-support principles

A supplier's reasonable finished-price change should consider at least:

- raw-material cost share
- actual material price change
- purchasing lag
- inventory cycle
- FX exposure
- processing cost
- energy cost
- freight
- packaging
- tariff / tax where applicable
- contract pricing formula
- supplier margin changes

Avoid double counting. For example, if a supplier's quoted material price is already denominated in the purchasing currency and already includes FX effects, adding a second FX adjustment may overstate the impact.

## 5. TTP TECHNOLOGIES PVT. LTD. — Transformer Radiator case

### Background

Supplier: TTP TECHNOLOGIES PVT. LTD., India.  
Product family: transformer radiators.

### Supplier claim discussed historically

The supplier cited approximately `Incoming Cost +20.5%`, including items such as:

- steel
- zinc
- packaging
- gas / energy

The supplier also stated that it had not adjusted pricing since 2023 and generally quoted within a roughly six-month delivery horizon.

### Steel reference used in analysis

CR Coil 1.00 mm:

```text
2023 reference: about INR 74,050 / ton
later reference: about INR 81,720 / ton
market change: about +10%
```

A prior analytical estimate suggested about `+6.2%` finished-product impact from steel. If interpreted as a simple linear model, this implies an estimated steel cost share near 62%, but that figure remains an inference until supported by BOM or supplier cost breakdown.

### Additional factors researched

- JPC CR Coil monthly / quarterly trend
- LME Zinc
- INR/USD
- Brent crude oil
- India CPI
- India GDP
- labor / minimum wage
- freight

### Platform use case

The TTP case should eventually support:

```text
Steel share × Steel change
+ Zinc share × Zinc change
+ Packaging share × Packaging change
+ Energy share × Energy change
+ Labor share × Labor change
+ FX impact
+ Freight impact
```

Then compare the calculated result with the supplier request.

## 6. Bushing / PCORE / Hubbell price case

### Historical figures discussed

```text
Old price: USD 45,500
Supplier requested price: USD 55,500
Requested increase: about 21.98%
```

Supplier-side cost explanation included:

- Copper
- Silver
- Resin
- aggregate material increase around +43%
- material cost share around 57%
- FX discussed around +3%

A simple material-only transmission calculation gives:

```text
43% × 57% ≈ 24.51%
```

This does not automatically prove a finished-price increase of that amount. The model must still verify material basket definition, base period, actual purchasing lag, FX treatment, inventory, and other cost shares.

An internal negotiation preference previously discussed was to keep accepted increase at or below roughly 10% where evidence did not support the full request.

## 7. Supplier intelligence scope

### PCORE / Hubbell

Topics researched:
- RIS
- RIP
- PRC / oil-free PRC
- POC
- HVPRC
- Quicklink
- Test Terminal
- seismic capability 2G–5G
- lead times around 55–72 weeks

### YASH

Topics researched:
- RIP / RIS
- voltage range
- applicability below 230kV
- lead time
- India / North America / global business mix
- BSE SME listing

### REL

Topics researched:
- CTC
- epoxy
- self-bonding
- paper-covered conductor
- 2023–2025 revenue
- supplier comparison

### ZDVolt

Topics researched:
- China-related background
- manufacturing location
- sourcing and geopolitical risk

These belong in a Supplier Intelligence module rather than the raw-material core.

## 8. ERP / Excel / WPS extension path

Existing procurement data work has included:

- requisition detail not yet converted to PO
- delivery plan data
- part number as primary key
- latest PO
- latest unit price
- supplier
- creation date
- same-day multiple price handling

Long-term value comes from linking three layers:

```text
Market Price History
→ Supplier Quotation History
→ PO Actual Transaction History
```

This makes it possible to compare market movement, supplier requests, and actual negotiated outcomes.

## 9. Supplier Case target schema

```text
case_id
supplier
product_family
part_number
country
currency
old_price
requested_price
requested_change
request_date
effective_date
cost_breakdown
market_base_period
market_current_period
should_cost_change
negotiation_gap
negotiation_target
final_price
status
evidence
notes
```

## 10. v2.3 scope

v2.3 is the maintainability baseline:

- refresh project documentation
- establish version and roadmap
- document schemas and calculations
- separate Demo Mode from Real Data Mode
- make data source / formula / signal logic traceable
- prepare for TTP and Bushing Supplier Cases

New supplier-case functionality should be built only after this baseline is stable.
