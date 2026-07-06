# Software Center Price Proration

This project runs a GitHub Actions job that finds products in BigCommerce
category `44`, keeps only products with proration brand IDs, and reduces
explicit variant prices when the variant SKU ends in `-MY`. The reduction is
calculated from the parent product's default price. The parent product price is
never changed.

For example, a `$120.00` parent default price becomes `$110.00` for event 1,
`$100.00` for event 2, and `$90.00` for event 3. This does not compound from
the current variant price.

## Built-in safeguards

- Scheduled workflow runs apply changes to category `44`; manual runs apply only when **Actually update prices** is checked.
- The product scope is locked to numeric category ID `44`, not a category name.
- Within category `44`, only products with brand ID `40` (`1 month proration`) or `39` (`6 month proration`) are eligible.
- Visible and hidden products in category `44` are both eligible.
- Only variant SKUs ending in `-MY` (case-insensitive) are selected.
- The parent product default price is used as the base price for the calculation.
- A matching variant without an explicit variant price aborts the entire run.
- The run aborts before any update when more than 25 products are found.
- The run aborts before any update when more than 50 variants match.
- Prices use decimal arithmetic, round to cents, and never drop below `$0.01`.
- API throttling and temporary server failures are retried.
- Every completed run uploads a JSON audit report.
- GitHub Actions concurrency prevents two runs from changing prices at once.
- Apply runs update `state/proration-state.json` so reruns do not repeat the same event.

The script updates only matching variant-level `price` values. Parent product
SKUs ending in `-MY` do not qualify a product by themselves. It does not change
the parent product price, price lists, or sale prices.

## Proration schedule

Production monthly mode is implemented in code but the current workflow is set
to `daily_test` for short-term testing.

Monthly production rules:

- Fiscal year resets on September 1.
- Brand ID `40` prorates on Oct 1 through Aug 1: 11 events.
- Brand ID `39` prorates on Oct 1 through Mar 1: 6 events, then freezes.
- Each event sets the `-MY` variant price from the parent default price:
  `base_price * (12 - event_number) / 12`.

Daily test mode:

- The workflow runs daily at `0 5 * * *`, which is midnight America/Chicago during daylight time.
- Each new calendar day advances eligible variants by one event.
- Same-day reruns are skipped by state.
- Brand ID `40` stops after event 11.
- Brand ID `39` stops after event 6.

## BigCommerce setup

Create a store-level API account with read/write access to Products. Record the
store hash from the API path and the access token. Create the `prorationtest`
category with numeric ID `44`, and add the products you want to test.
Set test products' brand to `1 month proration` (brand ID `40`) or `6 month
proration` (brand ID `39`).

In GitHub, open **Settings > Secrets and variables > Actions** and add:

| Kind | Name | Value |
| --- | --- | --- |
| Secret | `BIGCOMMERCE_STORE_HASH` | Store hash only, not a URL |
| Secret | `BIGCOMMERCE_ACCESS_TOKEN` | Store API account token |
| Variable | `PRORATION_MAX_PRODUCTS` | A small test limit such as `10` |
| Variable | `PRORATION_MAX_VARIANTS` | A small test limit such as `10` |

Do not store the token in the repository.

## Safe rollout

1. Run **Prorate BigCommerce prices** manually with **Actually update prices** unchecked.
2. Inspect the workflow log and downloaded `proration-report` artifact.
3. Run it manually once with **Actually update prices** checked.
4. Verify the products in BigCommerce.

There is currently no scheduled trigger. To re-enable a first-day-of-month run
later, add a schedule trigger like this:

```yaml
on:
  schedule:
    - cron: "17 13 1 * *"
  workflow_dispatch:
```

## Run locally

```powershell
$env:PYTHONPATH = "src"
$env:BIGCOMMERCE_STORE_HASH = "your-store-hash"
$env:BIGCOMMERCE_ACCESS_TOKEN = "your-token"
$env:BIGCOMMERCE_CATEGORY_ID = "44"
$env:ALLOWED_CATEGORY_ID = "44"
$env:BRAND_IDS = "40,39"
$env:SKU_SUFFIX = "-MY"
$env:PRORATION_MODE = "daily_test"
$env:APPLY_CHANGES = "false"
python -m proration
```

Run the tests with:

```powershell
python -m unittest discover -s tests -v
```
