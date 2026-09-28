# API provider confirmation record

## Evidence received September 28, 2026

The owner relayed feedback from the API technical team, using a BTC/USDT example on the CZR public hostname. This is user-supplied support evidence; no ticket ID or respondent name was supplied. It confirms endpoint-level semantics, not a statement that CZR trading has launched. The owner previously confirmed the CZR pair identifier and endpoint.

- `time` is market snapshot time, not the latest executed trade time. The numeric timestamp is epoch milliseconds, typically with second precision.
- `open` and `amount` use a rolling 24-hour window.
- `rose = (last - open) / open`. The bot independently computes `(last - open) / open * 100` and displays percent.
- The [official ticker documentation](https://exchangedocsv2.gitbook.io/open-api-doc-v2/spot#id-24hrs-ticker) identifies `last` as last price and `amount` as quote-currency volume. The latter is USDT for CZR/USDT.

## Operational meaning

The five-minute freshness threshold checks the age of the exchange market snapshot. It cannot establish how recently a trade executed. The post's Updated timestamp refers to that snapshot, never to the last executed trade. No trade-recency guarantee is made.

The bot maps USDT volume directly to `amount`; it does not multiply base quantity by the latest price. Invalid/missing inputs, zero opening price, stale/future snapshots, duplicates and unchanged displayed metrics continue to skip.

High/low and base-volume calculation confirmation remains unrecorded and those optional statistics remain disabled. The October 1, 2026 03:00 UTC launch gate remains unchanged. Prelaunch API data must not be presented as launched-market data.

## Approval and activation

The owner supplied and approved the routine format. Recording provider evidence does not enable posting. Automatic posting remains controlled by live environment settings, final configuration hash approval, correct X account, launch timing, and an alert destination.
