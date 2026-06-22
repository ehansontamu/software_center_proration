# Software Center Price Proration

This project runs a GitHub Actions job that finds products in one BigCommerce
category and reduces the explicit variant price of variants whose SKU ends in
`MY` by `1/12` (8.3333%) per run. The parent product price is never changed.

For example, `$120.00` becomes `$110.00` on the first run, then `$100.83` on the
second. This is a compounding reduction. It does not subtract `1/12` of an
original saved price. That distinction should be revisited before switching to
the future brand-based monthly workflow.

## Built-in safeguards

- Runs are dry-run unless a separate write switch is enabled.
- The product scope is a numeric category ID, not a category name.
- Only variant SKUs ending in `MY` (case-insensitive) are selected.
- A matching variant without an explicit variant price aborts the entire run.
- All products must be hidden by default.
- The run aborts before any update when more than 25 products are found.
- The run aborts before any update when more than 50 variants match.
- Prices use decimal arithmetic, round to cents, and never drop below `$0.01`.
- API throttling and temporary server failures are retried.
- Every completed run uploads a JSON audit report.
- GitHub Actions concurrency prevents two runs from changing prices at once.

The script updates only the matching variant-level `price`. It does not change
the parent product price, price lists, or sale prices. A manually re-run
successful apply job will reduce matching variant prices again, so do not re-run
an apply job merely to recreate its report.

## BigCommerce setup

Create a store-level API account with read/write access to Products. Record the
store hash from the API path and the access token. Create the `proration testing`
category, add only non-visible test products, and find the category's numeric ID
in the control panel URL or Catalog API.

In GitHub, open **Settings > Secrets and variables > Actions** and add:

| Kind | Name | Value |
| --- | --- | --- |
| Secret | `BIGCOMMERCE_STORE_HASH` | Store hash only, not a URL |
| Secret | `BIGCOMMERCE_ACCESS_TOKEN` | Store API account token |
| Variable | `BIGCOMMERCE_CATEGORY_ID` | Numeric test category ID |
| Variable | `PRORATION_APPLY_CHANGES` | `false` initially |
| Variable | `PRORATION_REQUIRE_HIDDEN` | `true` |
| Variable | `PRORATION_MAX_PRODUCTS` | A small test limit such as `10` |
| Variable | `PRORATION_MAX_VARIANTS` | A small test limit such as `10` |

Do not store the token in the repository.

## Safe rollout

1. Leave `PRORATION_APPLY_CHANGES=false`.
2. Run **Prorate BigCommerce prices** manually with **Actually update prices** unchecked.
3. Inspect the workflow log and downloaded `proration-report` artifact.
4. Run it manually once with **Actually update prices** checked.
5. Verify the products in BigCommerce.
6. Set `PRORATION_APPLY_CHANGES=true` only when scheduled runs should write.

The schedule is `17 13 * * *`: daily at 13:17 UTC, which is 8:17 AM Central
Daylight Time or 7:17 AM Central Standard Time. GitHub scheduled workflows use
UTC and run from the default branch. To change it to the first day of every
month, use:

```yaml
- cron: "17 13 1 * *"
```

## Run locally

```powershell
$env:PYTHONPATH = "src"
$env:BIGCOMMERCE_STORE_HASH = "your-store-hash"
$env:BIGCOMMERCE_ACCESS_TOKEN = "your-token"
$env:BIGCOMMERCE_CATEGORY_ID = "123"
$env:APPLY_CHANGES = "false"
python -m proration
```

Run the tests with:

```powershell
python -m unittest discover -s tests -v
```
