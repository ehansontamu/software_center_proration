# Software Center Price Proration

This project runs a manual GitHub Actions job that finds products in
BigCommerce category `44`, keeps only products with brand ID `40`
(`1 month proration`), and reduces explicit variant prices when either the
parent product SKU or the variant SKU ends in `-MY`. The reduction is `1/12`
(8.3333%) per run. The parent product price is never changed.

For example, `$120.00` becomes `$110.00` on the first run, then `$100.83` on the
second. This is a compounding reduction. It does not subtract `1/12` of an
original saved price. That distinction should be revisited before switching to
the future brand-based monthly workflow.

## Built-in safeguards

- Manual workflow runs apply changes to category `44` when **Actually update prices** is checked.
- The product scope is locked to numeric category ID `44`, not a category name.
- Within category `44`, only products with brand ID `40` (`1 month proration`) are eligible.
- Visible and hidden products in category `44` are both eligible.
- Products are selected when either the parent product SKU or variant SKU ends in `-MY` (case-insensitive).
- A matching variant without an explicit variant price aborts the entire run.
- The run aborts before any update when more than 25 products are found.
- The run aborts before any update when more than 50 variants match.
- Prices use decimal arithmetic, round to cents, and never drop below `$0.01`.
- API throttling and temporary server failures are retried.
- Every completed run uploads a JSON audit report.
- GitHub Actions concurrency prevents two runs from changing prices at once.

The script updates only variant-level `price` values. If a parent product SKU
ends in `-MY`, its variant/default-variant price is eligible, but the parent
product price field itself is not changed. It does not change price lists or
sale prices. A manually re-run successful apply job will reduce matching variant
prices again, so do not re-run an apply job merely to recreate its report.

## BigCommerce setup

Create a store-level API account with read/write access to Products. Record the
store hash from the API path and the access token. Create the `prorationtest`
category with numeric ID `44`, and add the products you want to test.
Set the test products' brand to `1 month proration` (brand ID `40`). Brand ID
`39` is `6 month proration`; that future automation path is not active in this
test workflow yet.

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
$env:BRAND_ID = "40"
$env:SKU_SUFFIX = "-MY"
$env:APPLY_CHANGES = "false"
python -m proration
```

Run the tests with:

```powershell
python -m unittest discover -s tests -v
```
