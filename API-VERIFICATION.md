# API provider confirmation record — PENDING

The owner supplied `https://openapi.czrex.com` and the exact pair identifier. A read-only request succeeded on September 28, 2026. No direct API provider confirmation was received. These are questions for the exchange's API provider support channel, prepared but **not sent**.

Please confirm for CZR Exchange tenant 1858:

| Item | Confirmation needed |
|---|---|
| Full endpoint | Is `https://openapi.czrex.com/sapi/v2/ticker?symbol=czrtoken1858usdt1858` the canonical production public HTTPS endpoint? Confirm public authentication policy, limits, errors, cache behavior and redirects. |
| Pair scope | Does this exact query return CZR/USDT exclusively? The observed response omits `symbol`; confirm that this is expected and document errors for unknown symbols. |
| `last` | Is this the last executed spot trade price in USDT per CZR, excluding indicative, reference, prelaunch, synthetic or test prices? |
| `time` | What event does it timestamp, in what epoch/unit/timezone? Docs say “Open Time.” Does it update on a trade, ticker calculation, cache refresh, or each request? Can it be fresh while `last` is stale? Supply an authoritative last-trade/market-update timestamp if necessary. |
| Window | Are 24-hour fields rolling 86,400-second aggregates, a UTC calendar day, or another window? Boundary inclusion, reset time, empty/no-trade behavior? |
| `open` | Exact baseline and selection rule, including when there is no trade at the boundary. Is it valid for `(last-open)/open*100`? |
| `high`, `low` | Max/min of which eligible trades over which exact interval? Treatment of corrections/outliers and no trades? |
| `vol` | Base CZR quantity or another unit? Gross/net volume, sum formula and eligible trade/window definitions? |
| `amount` | Is this summed quote USDT turnover (`sum(price*quantity)`)? Rounding, adjustments, and window? |
| `rose` | Ratio, percent, or another unit? Exact baseline, formula, rounding, and zero-baseline behavior? Bot will calculate percent independently from confirmed inputs. |
| `askPrice`, `bidPrice` | Best current spot ask/bid? Null/zero semantics, observation timestamp, and whether these are part of the same snapshot? |
| `askVolume`, `bidVolume` | Size at best price or aggregate depth? Base/quote units and observation time? |
| Availability | Exact public trading opening time, and whether prelaunch endpoint values are genuine executable-market trades or test/indicative data? |

Record the support ticket/document reference, respondent, confirmation date, and exact answers. Update configuration mappings only from that evidence. The price-only path uses `last` and a verified freshness timestamp; 24-hour fields remain disabled. Bid/ask fields and `rose` are never displayed by this implementation. If timestamp semantics or the response structure differ, update and retest the adapter before approval.

## Documentation checked September 28, 2026

The linked [spot API documentation](https://exchangedocsv2.gitbook.io/open-api-doc-v2/spot) identifies `amount` as quote-currency trading volume, so the adapter maps USDT volume to `amount`, never to `vol` or an estimate made by multiplying volume by last price. It labels `time` as “Open Time”; that does not establish freshness of the last executed trade. Exact window, opening-price selection, quote-volume calculation, and last-trade freshness remain unconfirmed. Verification gates remain pending.

## Approved routine format

The owner supplied the routine template on September 28, 2026. The configuration now uses that exact structure with five-decimal prices, independently calculated percentage from confirmed last/open inputs, reported quote volume, UTC, and the @czrexchange mention. This approves the routine wording only; it does not certify market-data semantics or activate posting. Required missing/unverified fields cause a skip, including in previews.
