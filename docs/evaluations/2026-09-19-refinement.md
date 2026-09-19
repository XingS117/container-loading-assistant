# Layout evaluation

Revision: `72c67a0be208e5dec7416458cb21e1f6027e658a`; tracked dirty: False.

Offline deterministic solver only; no AI, network or queue latency. Safety validity is not field acceptance.
Void = bounding rectangle minus footprint union at the same base height; includes edge notches and configured gaps.
Upper void = maximum per-layer void. Support = minimum direct bottom-face support; N/A means no upper cargo.

| Case / source | Profile | Loaded / requested | Valid | Floor void m2 | Upper void m2 | Min support % | X / Y imbalance % | Steps | Solve median / max s |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| customer-five-sku / customer_template | high_fill | 55 / 55 | True | 3.1066 | 0.0 | 100.0 | 16.86 / 17.19 | 6 | 0.014074 / 0.01414 |
| customer-five-sku / customer_template | stable | 55 / 55 | True | 3.1066 | 0.0 | 100.0 | 16.86 / 0.36 | 6 | 0.014074 / 0.01414 |
| customer-five-sku / customer_template | easy | 55 / 55 | True | 3.1066 | 0.0 | 100.0 | 16.86 / 9.03 | 6 | 0.014074 / 0.01414 |
| historical-six-sku / historical_reproduction | high_fill | 54 / 54 | True | 4.7435 | 2.24 | 100.0 | 2.57 / 9.56 | 13 | 7.21206 / 7.232541 |
| historical-six-sku / historical_reproduction | stable | 54 / 54 | True | 4.7435 | 1.79 | 100.0 | 1.9 / 1.78 | 26 | 7.21206 / 7.232541 |
| historical-six-sku / historical_reproduction | easy | 54 / 54 | True | 4.7435 | 2.3658 | 100.0 | 2.57 / 6.32 | 13 | 7.21206 / 7.232541 |
| safety-fragile-gap / synthetic | high_fill | 10 / 10 | True | 0.3304 | 0.01 | 100.0 | 29.28 / 43.28 | 8 | 0.015323 / 0.015737 |
| safety-fragile-gap / synthetic | stable | 10 / 10 | True | 0.3304 | 0.01 | 100.0 | 29.28 / 43.28 | 7 | 0.015323 / 0.015737 |
| safety-fragile-gap / synthetic | easy | 10 / 10 | True | 0.3304 | 0.01 | 100.0 | 29.28 / 43.28 | 2 | 0.015323 / 0.015737 |
| stress-30-sku / synthetic | high_fill | 1295 / 5000 | True | 2.49975 | 24.675625 | 100.0 | 6.28 / 3.85 | 216 | 1.353744 / 1.354537 |
| stress-30-sku / synthetic | stable | 1295 / 5000 | True | 2.49975 | 24.675625 | 100.0 | 6.06 / 3.68 | 216 | 1.353744 / 1.354537 |
| stress-30-sku / synthetic | easy | 1295 / 5000 | True | 2.49975 | 24.675625 | 100.0 | 6.28 / 3.85 | 11 | 1.353744 / 1.354537 |
| stress-5000-cartons / synthetic | high_fill | 5000 / 5000 | True | 0.0 | 0.14 | 100.0 | 5.68 / 2.32 | 1 | 5.199645 / 5.271433 |
| stress-5000-cartons / synthetic | stable | 5000 / 5000 | True | 0.0 | 0.14 | 100.0 | 4.02 / 2.11 | 1 | 5.199645 / 5.271433 |
| stress-5000-cartons / synthetic | easy | 5000 / 5000 | True | 0.0 | 0.14 | 100.0 | 5.68 / 2.32 | 1 | 5.199645 / 5.271433 |

## Provenance and run status

customer-five-sku: status=ok; repeatable=True; acceptance=unreviewed.
Source: backend/tests/test_customer_template_required.py five_sku_request; docs/HANDOFF.md notes missing customer weights.
- 100 kg per pallet and 500 kg top load are test assumptions, not customer measurements.
- Template regression is not field acceptance.


historical-six-sku: status=ok; repeatable=True; acceptance=unreviewed.
Source: Existing local reproduction input _temp/user-case-40hq.json; also covered by backend/tests/test_packing.py six_sku_pallet_request.
- Original collection method and actual shipment weights have not been independently verified.
- No field-approved reference layout or acceptance record.


safety-fragile-gap: status=ok; repeatable=True; acceptance=unreviewed.
Source: Evaluation scenario combining existing must-load, fragile, gap, orientation and top-load constraints.
- Synthetic safety coverage only; not an operationally approved loading sequence.


stress-30-sku: status=ok; repeatable=True; acceptance=unreviewed.
Source: backend/tests/test_large_order.py test_30_sku_order_finishes_within_service_budget
- Synthetic capacity and runtime regression; no customer or field acceptance.


stress-5000-cartons: status=ok; repeatable=True; acceptance=unreviewed.
Source: backend/tests/test_large_order.py test_5000_small_boxes_finish_within_service_budget
- Synthetic capacity and runtime regression; no customer or field acceptance.

## Baseline comparison

Comparable: True

| Case | Profile | Loaded delta | Floor void delta m2 | Upper void delta m2 | X imbalance delta pp | Steps delta | Solve median delta s |
| --- | --- | --- | --- | --- | --- | --- | --- |
| customer-five-sku | high_fill | 0 | 0.0 | 0.0 | 0.0 | 0 | 0.002102 |
| customer-five-sku | stable | 0 | 0.0 | 0.0 | 0.0 | 0 | 0.002102 |
| customer-five-sku | easy | 0 | 0.0 | 0.0 | 0.0 | 0 | 0.002102 |
| historical-six-sku | high_fill | 0 | 0.0 | 0.0 | 0.0 | 0 | 0.072748 |
| historical-six-sku | stable | 0 | 0.0 | -2.915 | -0.67 | 13 | 0.072748 |
| historical-six-sku | easy | 0 | 0.0 | 0.0 | 0.0 | 0 | 0.072748 |
| safety-fragile-gap | high_fill | 0 | 0.0 | 0.0 | 0.0 | 0 | 0.001281 |
| safety-fragile-gap | stable | 0 | 0.0 | 0.0 | 0.0 | 0 | 0.001281 |
| safety-fragile-gap | easy | 0 | 0.0 | 0.0 | 0.0 | 0 | 0.001281 |
| stress-30-sku | high_fill | 0 | 0.0 | 0.0 | 0.0 | 0 | 0.039348 |
| stress-30-sku | stable | 0 | 0.0 | 0.0 | 0.0 | 0 | 0.039348 |
| stress-30-sku | easy | 0 | 0.0 | 0.0 | 0.0 | 0 | 0.039348 |
| stress-5000-cartons | high_fill | 0 | 0.0 | 0.0 | 0.0 | 0 | -0.01065 |
| stress-5000-cartons | stable | 0 | 0.0 | 0.0 | 0.0 | 0 | -0.01065 |
| stress-5000-cartons | easy | 0 | 0.0 | 0.0 | 0.0 | 0 | -0.01065 |

Deltas are current minus baseline. Fewer pieces and smaller voids are a tradeoff, not an unconditional improvement. Timing changes are observations, not a performance guarantee.
