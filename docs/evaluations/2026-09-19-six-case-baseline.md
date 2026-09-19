# Layout evaluation

Revision: `9435f8fca9c15cd8ffdb6dff9ad52cb07ee4ab82`; tracked dirty: False.

Offline deterministic solver only; no AI, network or queue latency. Safety validity is not field acceptance.
Void = bounding rectangle minus footprint union at the same base height; includes edge notches and configured gaps.
Upper void = maximum per-layer void. Support = minimum direct bottom-face support; N/A means no upper cargo.

| Case / source | Profile | Loaded / requested | Valid | Floor void m2 | Upper void m2 | Min support % | X / Y imbalance % | Steps | Solve median / max s |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| customer-five-sku / customer_template | high_fill | 55 / 55 | True | 3.1066 | 0.0 | 100.0 | 16.86 / 17.19 | 6 | 0.01548 / 0.015547 |
| customer-five-sku / customer_template | stable | 55 / 55 | True | 3.1066 | 0.0 | 100.0 | 16.86 / 0.36 | 6 | 0.01548 / 0.015547 |
| customer-five-sku / customer_template | easy | 55 / 55 | True | 3.1066 | 0.0 | 100.0 | 16.86 / 9.03 | 6 | 0.01548 / 0.015547 |
| historical-six-sku / historical_reproduction | high_fill | 54 / 54 | True | 4.7435 | 2.24 | 100.0 | 2.57 / 9.56 | 13 | 7.649781 / 7.706972 |
| historical-six-sku / historical_reproduction | stable | 54 / 54 | True | 4.7435 | 1.79 | 100.0 | 1.9 / 1.78 | 26 | 7.649781 / 7.706972 |
| historical-six-sku / historical_reproduction | easy | 54 / 54 | True | 4.7435 | 2.3658 | 100.0 | 2.57 / 6.32 | 13 | 7.649781 / 7.706972 |
| safety-fragile-gap / synthetic | high_fill | 10 / 10 | True | 0.3304 | 0.01 | 100.0 | 29.28 / 43.28 | 8 | 0.016344 / 0.016349 |
| safety-fragile-gap / synthetic | stable | 10 / 10 | True | 0.3304 | 0.01 | 100.0 | 29.28 / 43.28 | 7 | 0.016344 / 0.016349 |
| safety-fragile-gap / synthetic | easy | 10 / 10 | True | 0.3304 | 0.01 | 100.0 | 29.28 / 43.28 | 2 | 0.016344 / 0.016349 |
| stress-30-sku / synthetic | high_fill | 1295 / 5000 | True | 2.49975 | 24.675625 | 100.0 | 6.28 / 3.85 | 216 | 1.486863 / 1.498433 |
| stress-30-sku / synthetic | stable | 1295 / 5000 | True | 2.49975 | 24.675625 | 100.0 | 6.06 / 3.68 | 216 | 1.486863 / 1.498433 |
| stress-30-sku / synthetic | easy | 1295 / 5000 | True | 2.49975 | 24.675625 | 100.0 | 6.28 / 3.85 | 11 | 1.486863 / 1.498433 |
| stress-5000-cartons / synthetic | high_fill | 5000 / 5000 | True | 0.0 | 0.14 | 100.0 | 5.68 / 2.32 | 1 | 5.569669 / 5.696867 |
| stress-5000-cartons / synthetic | stable | 5000 / 5000 | True | 0.0 | 0.14 | 100.0 | 4.02 / 2.11 | 1 | 5.569669 / 5.696867 |
| stress-5000-cartons / synthetic | easy | 5000 / 5000 | True | 0.0 | 0.14 | 100.0 | 5.68 / 2.32 | 1 | 5.569669 / 5.696867 |
| user-abc-test / historical_reproduction | high_fill | 63 / 63 | True | 3.243 | 0.0 | 100.0 | 6.51 / 11.72 | 2 | 0.348982 / 0.350044 |
| user-abc-test / historical_reproduction | stable | 63 / 63 | True | 3.243 | 0.0 | 100.0 | 2.36 / 0.96 | 22 | 0.348982 / 0.350044 |
| user-abc-test / historical_reproduction | easy | 63 / 63 | True | 3.243 | 15.3225 | 100.0 | 12.37 / 13.98 | 2 | 0.348982 / 0.350044 |

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


user-abc-test: status=ok; repeatable=True; acceptance=unreviewed.
Source: User supplied A/B/C dimensions, weights and quantities in task 019ffbef-798a-7d93-ab72-530a7cec650d; existing test_floor_first_layout.abc_request and common-abc UI example.
- User-provided test inputs, not verified actual shipment or field acceptance.
- 40HQ, upright rotations, two layers for A/B, one layer for C, 500 kg top load and 300 mm door reserve are test assumptions; packaging strength unverified.
