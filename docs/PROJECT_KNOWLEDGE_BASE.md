# Procurement Materials Platform — Project Knowledge Base

Version baseline: v2.3

## Project purpose

Procurement decision-support for international raw-material analysis, supplier price rationality, Should-Cost, and negotiation evidence.

## Public market-analysis layer

- World Bank Pink Sheet = primary monthly benchmark.
- FRED = independent corroboration only.
- Public GitHub Pages does not store private company procurement data.

## Private company daily market-data layer — C3

Verified source contract from the original `update_prices_v5.py`:

- Copper Cash: LME Cash OFFER.
- Copper 3-month: LME 3-month OFFER.
- Aluminium / Lead / Nickel / Tin / Zinc: LME Cash OFFER.
- Electrolytic Copper: SMM average (`均價`).
- Brent: yfinance `BZ=F` Close.
- Silver: yfinance `SI=F` Close ×100, cents/oz.
- Gold: yfinance `GC=F` Close.

`BZ=F`, `SI=F`, and `GC=F` are futures market references, not spot prices.

C3.1 adds semantic OFFER detection, explicit SMM-average detection, safe date-row resolution, full-row fail-closed guards, audit metadata, credential isolation, and Live Dry Run mode.

## Copper daily decision principle

LME Copper Cash OFFER is the first same-day market reference. World Bank monthly Copper provides medium-term benchmark context and must not replace the daily Cash OFFER.

C3.2 will add DoD, 5D/20D averages, 60D percentile, Cash-vs-3M spread, and World Bank monthly benchmark comparison. It remains decision support only.
