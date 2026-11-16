# Deep Trading Desk

Local delivery planner, Excel import, Dhan CMP and paper journal. Paper mode is the default. Live CNC orders use real money and require both the global live switch and an Enabled trade plan.

Start by double-clicking Start-Deep-Trading-Desk.cmd, or run `python launch.py`. The launcher normally opens http://127.0.0.1:8080 and preserves http://127.0.0.1:8081 through a local compatibility listener using the same backend. It rejects obsolete backend versions instead of silently using incompatible APIs. Use this application's server, not Live Server or python -m http.server. Python 3.10+ is sufficient; no packages are required. Keep this loopback-only server local. Browser storage remains separate for each port; use your existing address to retain its saved plans. Neither address migrates or overwrites the other address's browser data.

## Field rules

| Column | Source / calculation |
| --- | --- |
| Setup, execution TF, stock, BUY, entry, SL, remarks | Excel or planner input; delivery buy plans only |
| Target | Entry + (entry - SL) x configured reward multiple, default 2. Manual overrides and non-default Excel targets remain editable |
| R:R | (Target - entry) / (entry - SL) |
| Brokerage | 2 x configurable delivery brokerage per order, default zero. Positive recorded historical brokerage is preserved |
| Quantity | New plans: floor(capital x risk% / (entry - SL)), capped by floor(capital / entry). Executed records retain actual quantities |
| CMP | Dhan LTP only. Excel CMP remains reference data, never a live fallback |
| Diff / Diff % | CMP - entry; (CMP - entry) / entry x 100 |
| Sale price | Excel recorded sale, verified linked Dhan SELL order, or explicitly labelled paper exit |
| Required margin | Entry x quantity: full delivery cash, excluding charges |
| P/L | Closed: (sale - recorded fill) x actual quantity - brokerage. Holdings use CMP and show '(open)'. Watching plans have no realized P/L |
| Status / actions | Application state; importing does not arm or place orders |

Brokerage is not total transaction costs. Completed-trade P/L now deducts estimated Dhan delivery fees or an actual total from the contract note; external Zerodha fees remain pending until supplied. Configure brokerage for your own tariff. Dhan published standard equity-delivery brokerage is zero: https://dhan.co/pricing/.

## Dhan connection and data

In Settings & connection, enter client ID and daily token. Backend verifies /v2/profile. Tokens are never saved in browser storage or API responses. Remember securely optionally saves Windows DPAPI-encrypted credentials in runtime/dhan-session.bin, bound to the current Windows account. Restart restores and validates the saved token; Disconnect deletes the saved copy. Without Remember, the token remains memory-only. Requests require the same local origin.

CMP and connection health refresh every 30 seconds while the page is visible. Refresh Dhan CMP also fetches on demand. Symbols resolve using Dhan's public instrument master, restricted to unambiguous NSE EQ cash equities. /v2/marketfeed/ltp requests include access-token and client-id headers. Invalid tokens, missing data permissions/subscription, unavailable instruments and missing prices show explicit messages. Failed requests clear CMP, without fallback to Excel. LTP may be the last traded price outside market hours. Displayed quote time is retrieval time, not exchange tick time.

For an imported holding, choose Sync Dhan exit under All trade plans, then enter its specific executed delivery SELL order ID. Backend reads /v2/trades/{order-id}, verifies account, stock, side, product, unique exchange trade IDs and matching full recorded quantity, then applies the quantity-weighted fill price. Partial exits, mismatched quantities and ambiguous records are rejected. One sell order cannot be linked to several plans. Recorded purchase entry remains fill cost. This reads trades only; it does not submit orders or reconcile all holdings automatically. Older exits can be imported from Excel when unavailable through Dhan's order-based trade endpoint.

References: https://dhanhq.co/docs/v2/authentication/, https://dhanhq.co/docs/v2/market-quote/, https://dhanhq.co/docs/v2/orders/.

## Import / export

Personal workbook data is not included in this code repository. In your local workspace, tracker-data.json loads the supplied Tracker 4.0 rows once on first visit; that file is ignored by Git and must be copied locally when setting up from a clean clone. You can also import XLSX or UTF-8 CSV via Excel import. Tracker 4.0 and headers are detected automatically with manual mapping available. Empty formula rows/totals are ignored. Save cached formula results in Excel before importing. XLS is unsupported; expiry should use ISO text dates.

Legacy Excel QTY > 0 preserves actual executed quantity. A positive sale price makes it a closed record; otherwise it is an imported holding. QTY zero is a watching plan. CSV Status=Watching remains a plan even with positive calculated quantity. Notes alone never imply execution. Stable Trade IDs support updates; without IDs the key is stock + timeframe + entry + SL. Repeat uploads are idempotent. Active paper trades and Dhan-verified exits are protected.

Download all plans (CSV) exports calculated values, actual recorded quantities, status and quote/exit provenance. The separate import-tab export follows the legacy convention (zero executed QTY for watching plans). Exports do not preserve every setting or timestamp. State is saved in browser localStorage; clearing browser data removes it.

## Paper mode

Arm paper trade simulates entries against Dhan CMP with gap, expiry, capital and risk checks. Subsequent quotes evaluate armed paper entries and paper exits. Imported real holdings never auto-exit. The page must stay open. No actual broker protection exists. Timeframes are descriptive; candle-close triggers and automatic partial-exit allocation are not implemented in paper mode. Polling can miss crossings and is not a live execution guarantee or backtest.

## Checks

From this directory run:

```
python -m unittest test_dhan.py test_market.py
node test-import.js
node test-performance.js
node test-trade-math.js
node test-connection-ui.js
node test-market-ui.js
node test-trade-tabs.js
```

Authenticated API tests use simulated Dhan responses. Your token and market-data access still need verification in the app.


## Live CNC delivery automation

Existing demat holdings can be given explicitly requested sell exits with `holding_exit.py SYMBOL --quantity N --sl PRICE --target PRICE`. Without `--apply` this only validates a proposal. `--apply` submits a CNC Forever OCO sell with matching trigger and limit prices after checking DDPI, holdings, tick size, LTP and conflicting orders. The command never buys or changes another order. It records an intent in `runtime/holding-exits.sqlite` before submission and refuses repeats, including uncertain network outcomes. Reconciliation and modifications require reviewing the broker order; this CLI is not an automatic holdings-exit manager. Dhan's compact instrument tick size is converted from paise to rupees. Exact prices outside that tick grid are rejected rather than silently changed.

Live delivery is a separate opt-in workflow. The server starts paused; connecting a token or importing a spreadsheet never enables live execution. Use one backend process per workspace.

1. Connect Dhan. Active DDPI is required by this app for unattended delivery sales. Configure the execution server's static public IP in Dhan. The app checks that a public IP whitelist exists but does not change it or prove the current network matches it; Dhan enforces the execution IP.
2. In Trade Plans, leave a plan Enabled to opt it into automatic execution, or Disable it to exclude it. Enabled plans default to on, as configured by the user.
3. Open Holdings and choose Enable live execution for enabled plans. The app prechecks each Enabled Watching plan against funds, available capital, total portfolio risk, broker holdings/positions, pending orders and exchange tick size, then queues only the plans that pass. A failed precheck is shown with the stock and reason; it is not queued. There is no per-trade Review/Arm click.
4. A server worker checks queued plans every five seconds. After it observes a fresh CMP touch/cross of the planned entry within the configured 1% band, it rechecks funds, exposure, the market window and a fresh price, then sends one BUY LIMIT CNC Super Order at the reviewed tick-rounded entry, including target, SL and trailingJump=0.
5. After entry fills, Dhan activates the linked exits. Dhan's Super Order OCO cancels the other leg when one triggers. CNC exit legs can remain active across days subject to broker rules. Exit legs trigger market orders; trigger prices are not guaranteed execution prices.

Normal entry window is weekdays 09:15-15:25 IST; there is no special-session/holiday calendar, and Dhan validates actual market availability. The Python process and computer must remain awake for unsent entries and reconciliation. Broker-accepted orders and exit legs remain on Dhan when the browser or local server closes. Tokens can be remembered using Windows-account encryption. Restart or reconnect pauses new live submissions, and you must explicitly resume monitoring.

The durable ledger is runtime/live.sqlite. It contains order snapshots and state, never credentials, and is blocked from HTTP downloads. Do not remove the ledger while orders exist. Stable correlation IDs and a transactional submission claim prevent repeat placement of the same trade. Uncertain placement responses are never retried automatically: the engine reconciles order ID/correlation ID, and pauses new submissions if it cannot identify the broker state uniquely. Rejected/completed trade IDs cannot be reused; create a distinct new trade plan for a deliberate new attempt.

Live partial fills, pending entries, exit-leg states and action-required alerts appear under Live delivery and in the planner. A broker exit trigger alone does not create realized P/L: matching actual SELL fills must confirm full executed quantity and weighted sale price. Broker API limitations for older orders, unmatched child IDs or external manual exits may require checking Dhan. The UI has in-app alerts, not out-of-app notifications. Trades you place elsewhere without a recorded plan/SL are not included in calculated portfolio risk.

Pause stops new submissions. It does not cancel pending broker orders or protective exits, and cannot revoke a request already in flight. Only an unsent ARMED plan can be disarmed here. Manage submitted-order cancellations and price modifications in Dhan. If you manually exit a Super Order position in Dhan, its target/SL legs can remain active and must be managed there. Broker-level exit changes or missing protection pause new app entries for review.

Implementation references: https://dhanhq.co/docs/v2/super-order/ and https://dhan.co/regulatory-and-other-information-pdf/Dhan_Super_Order_TC.pdf.

Additional checks: `python -m unittest test_live_engine.py test_live_broker.py test_live_api.py` and `node test-live-controls.js`. These use simulated brokers; no real orders are sent by tests. Live credentials, IP permissions, DDPI, partial fills and overnight broker behavior still require verification with your account before relying on unattended execution.


## Deep Trading Desk interface

Market Radar shows compact readiness cards and actual Dhan feed status. Trade Plans contains only uncompleted setups and open positions, plus the planner and capital overview. Completed Trades contains only closed positions, with stock/setup/notes search, outcome/source/timeframe filters, realized R, setup results, profit factor, average win/loss and CSV export. The cumulative P/L graph uses journal order; imported historical dates are not invented. Paper, Excel and verified Dhan executions are labelled separately. Header statistics cover the whole journal; completed-page analysis follows its filters.

Use Start-Deep-Trading-Desk.cmd for branded startup (the original launcher still works). The workspace folder and browser storage key are preserved. URLs ending #planner or #completed open the corresponding tab directly. Plan CSV exports the filtered open plans; completed CSV exports the filtered closed trades with execution details. Import & Export retains the full tracker export.


### Planned-entry trigger clarification

The 1% band is preparation only. The live worker submits the reviewed BUY LIMIT only after observing CMP equal to entry or crossing it from either side between fresh observations (at most 15 seconds apart on the same IST date). Starting already below/above entry does not count as a touch. A price gap across entry can count as a crossing, but the current and final rechecked CMP must remain in the 1% band and between SL and target. Crossings after a stale feed or overnight gap are not inferred. This is an application-side observed trigger, not a broker-native pending stop order; polling can miss touches between samples. Entry remains capped at the planned limit; execution can be lower and is not guaranteed. Linked target/SL trigger levels are retained.


### Dense terminal layout

The main dashboard is a selectable, scrollable watchlist with larger tabular numbers, sticky column headers and a single instrument panel. Click a stock row (or focus it and press Enter/Space) to inspect planned levels, size, risk and actions. The chart shows planned entry/SL/target, plus actual Dhan snapshots collected during this page session. It is not a historical candle feed. Without a data subscription/feed, only planned levels are shown; collected quotes are not fabricated or replaced by Excel CMP. Samples are kept in memory (last 240 per symbol), reset on reload, and retain retrieval-time spacing. Completed Trades and Trade Plans remain separate with independent exports.


### Theme and radar preferences

Market Radar now defaults to compact Stock tiles and a light colour theme. Use the header theme button to switch Light/Dark, or the radar layout selector to choose Stock tiles or Watchlist & chart. Preferences are saved under deepDesk.theme and deepDesk.radarView in browser localStorage. Chart & details opens the selected stock in watchlist/chart mode. Trading data, credentials and execution rules are independent of these display preferences.


### Connection persistence

Enable Remember securely on this Windows account when connecting. Saved tokens restore after backend restarts, while live automation stays paused. Dhan web tokens still expire after 24 hours or when revoked; remembering does not renew or extend them. Temporary health-check failures retain the token and retry; Data API access is independent of Trading API authentication. Run the supplied launcher directly under your Windows login: restricted sandboxes may block DPAPI encryption. Connection tests isolate the real credential store. `python -m unittest test_secret_store.py` checks encrypted storage and restore; the native DPAPI test needs normal Windows account permissions.


### Editable planning values

Watching plans support Auto/Manual selectors for target, quantity and estimated total brokerage. Entry, SL, setup, timeframe, expiry and notes are editable inputs. Auto target follows default reward ratio; Auto quantity follows risk/capital; Auto brokerage uses delivery defaults. Manual values remain fixed when other inputs change, and Reset all to Auto restores automatic calculations. Dependent R:R, margin, price risk and potential P/L stay calculated for consistency. Planning quantity must be a positive integer and may exceed current capital/risk limits. Paper arming and live review check after exchange tick rounding and never increases a requested manual quantity. Broker charges remain estimates unless actual historical brokerage was recorded. Mode flags and manual estimates are included in plan CSV exports/imports. Submitted live plans and executed quantities stay immutable here; disarm an unsent plan before editing and review it again.


### Record or correct a completed sale

Use Record sale in Trade Plans for a trade already executed outside the app. Enter actual buy fill, actual executed quantity and per-share sale price; optional brokerage and sale date update the P/L preview. Unknown executed quantities are not prefilled from calculated planned size. Saving moves the same record into Completed Trades, where Edit sale permits corrections. Manually corrected rows are protected from Excel overwrite, labelled Manual journal, and saved in browser storage. This journal action does not submit, cancel or protect broker orders. An active or broker-verified live ledger record blocks manual changes; reconcile those through Dhan. Completed CSV includes actual Entry Fill; imports preserve that cost basis.


### Dhan automatic delivery brokerage

Auto brokerage uses the published standard Dhan equity-delivery/CNC brokerage of zero (checked 14 November 2026), rather than a custom per-order flat fee. STT, GST, stamp duty, exchange/SEBI fees and DP charges are separate from brokerage. Completed-trade net P/L deducts their Dhan estimate or an actual total from the contract note. Sale recording supports Auto Dhan or Manual/recorded actual brokerage. Existing actual historical fees and manual overrides are preserved. Pricing source: https://dhan.co/pricing/.


### Per-plan Enabled / Disabled

The last action in Trade Plans enables/disables new entries, default Enabled. Disabled plans remain visible and cannot arm paper/live entries. Disabling an unsent armed live plan disarms it transactionally in the server ledger and invalidates pending previews. A disabled flag is persisted in SQLite and checked before preview, arm and submission claim. Re-enabling does not re-arm a plan or enable global live mode. Submitted/filled broker entries cannot be disabled as a substitute for broker cancellation; their toggle is locked and existing exit legs stay intact. Paper exits remain active even if a plan is disabled. Enabled flags are included in CSV export and imported disabled values are retained.

Dhan connection updates: the client ID stays visible and is remembered in this browser (no token in browser storage). With secure saving checked, client ID and token are encrypted using Windows DPAPI and restored on backend startup. Credential storage status is shown in Settings. Failed connection attempts retain the masked token input for retry. Expired/rejected saved tokens retain their account ID and show Update token; transient network failures keep retrying every 30 seconds while the page is visible. Disconnect removes the encrypted token file. A fresh Dhan Web token is still required after its broker-defined expiry; saving credentials does not extend validity.

Local AI Chart Lab - built-in offline marked-chart reader
-------------------------------------------------------
No separate AI application, Ollama server, cloud key or model download is required. The app uses the English text-recognition component already installed in Windows, via the private chart-ocr.ps1 helper. Image bytes are passed through stdin into an in-memory bitmap; no uploaded screenshot is written to disk or sent to an external service. Native helper processes have bounded time and screenshot dimensions. This reader extracts marked labels and annotations; it does not discover unmarked trading zones or generate trades from candle patterns.

Paste one or more copied chart images with Ctrl+V, use Paste chart image(s), drag/drop files or upload multiple PNG/JPEGs. The queue accepts up to 20 images, 5 MB each and 50 MB total. Charts are read sequentially and reviewed independently. The stock and execution timeframe are read from the title (for example CYIENT[NSE - 5748] D). The trade setup is read from an explicit setup code or the higher-timeframe zone near Entry: Y->YIT, HY->HYIT, Q->QIT, M->MIT, W->WIT, D->DIT. Thus RB3-M produces MIT while the title D remains daily execution. Entry, SL and target require their own marked labels; reference H1 levels and risk-difference annotations are not treated as targets. Visible text notes in the plot are captured into the saved Trade Plan notes. UI chrome, axis values and footer/account text are excluded. Recognized text and extraction evidence can be inspected in each review.

Unreadable or conflicting stock/setup/timeframe/prices are flagged for a clearer image. A missing target is never silently invented: Use default R:R target is an explicit review action, labelled calculated and included in notes. CYIENT Entry 914.20 and SL 879.10 produce 984.40 at 1:2 if you choose that action. Check each valid result and use Add checked trade plan or Add all checked plans. Existing matching plans are protected against duplicate adds. Importing creates Watching plans only; it does not arm or place any broker order. Saved plans remain editable in Trade Plans. Clear queue releases the chart previews and ignores in-flight results for removed charts.

Validation includes a real Windows OCR call on a generated CYIENT chart-label fixture (entry 914.20, SL 879.10, RB3-M, title D and the follow-up note), deterministic parser tests and browser-logic tests for multiple-image paste/upload, independent review, note transfer, missing-target handling and duplicate prevention. Recognition of every chart layout/font is not guaranteed; original screenshots should be checked before adding.

15 November updates
-------------------
Import identity includes stock, setup, normalized timeframe, entry and SL. Different setups/entry prices remain separate; exact duplicates are idempotent and Trade IDs stay authoritative. A positive SALE PRICE (per share) always classifies the row as completed; without a positive whole executed quantity it becomes Completed - details pending, excluded from P/L. Safe Excel completion can retain previously corrected holding quantity and fill; active broker records stay protected. Re-upload the updated workbook to recover rows that were skipped by the old matcher. Fractional formula quantities are treated as planning data, not real share holdings.

Completed trades show brokerage and total fees separately. Dhan delivery estimates use NSE transaction 0.0030699%, STT 0.1% both sides, SEBI 0.0001%, stamp 0.015% of buy turnover, IPFT 0.0000001%, GST 18% on applicable fees and one DP instruction at 12.50 plus GST. STT and stamp are rounded to rupees; other fees to two decimals. Current reference: https://dhan.co/pricing/ (15 November 2026). Estimates may differ from historical contract notes, split executions, multiple setups in the same ISIN or ETF/scrip-specific tariffs. Actual Total Charges overrides the estimate once, without adding brokerage again. Zerodha records need actual charges rather than applying Dhan rates. Exports and backups preserve actual totals, broker labels and entry fills.

Live Execution now separates a read-only Dhan account snapshot (demat holdings and today's CNC positions, refreshed every 30 seconds) from app automation records (ongoing by default, optional all-history view). Holdings and positions are not summed and account totals are not automatically assigned to multiple strategy plans. These are Trading API reads and do not require the CMP data subscription. An inactive Data Plan is reported clearly for CMP without disconnecting a working trading account. Launch Start-Deep-Trading-Desk.cmd after a Windows restart to keep the local backend available.

Planning quantity and Dhan holding reconciliation
------------------------------------------------
Manual Watching-plan quantities and Excel planning quantities are no longer rejected for exceeding current capital or risk sizing; automatic quantity remains a suggestion based on settings. Paper arming and real-order review/submission retain execution checks. The Trade Plans Sync Dhan holdings button and 30-second portfolio refresh update a unique matching open plan with broker-reported quantity and average buy price, preserve planned Entry/SL/Target, and classify it as Holding / Dhan. Existing Zerodha plans, completed trades, other-account holdings and active app-managed live records are protected. Multiple matching setups require an explicit Link Dhan holding choice; account totals are never copied to every setup or summed across holdings/positions. Missing/overlapping snapshots flag review instead of assuming a sale. Dhan-backed holding quantities refresh from the broker rather than the manual holding editor.

Strict trade-state tabs
----------------------
Trade Plans lists only waiting setups (Watching, armed and unfilled entry-pending/submitting). Executed holdings, partial fills and filled live positions are excluded from that table and its export. Holdings combines tracked open Dhan/Paper/recorded holdings with current Dhan account holdings that have no linked strategy plan, without inventing entry protection or duplicating account quantities. Broker account detail tables remain available in a collapsed details section. Existing Zerodha holdings appear in Holdings with Edit holding and Record Zerodha sale actions; entering the actual sale moves the same record into Completed Trades. Completed Trades contains priced closed trades and completed records awaiting execution details. A missing Dhan holding is flagged for review rather than creating a sale without confirmed fills. Holding edits now open under Holdings; manual sale entry opens the Completed Trades journal. Partial broker fills move out of Trade Plans as soon as filled quantity is known.

Chart analysis review and corrections
-------------------------------------
Each read now shows chart-title and zone-label evidence, original versus current values, price risk/reward and sizing calculations, and the original reader warnings. Edit values and notes opens a correction form for symbol, setup, execution timeframe, Entry, SL, Target and notes. Saving validates the corrections, preserves the original OCR result, marks corrected sources, refreshes calculations, and clears acknowledgement. Adding requires a new review check; editing charts are excluded from bulk add. Draft corrections survive switching between charts. Restore original reading discards overrides. Corrected-price overlays use the detected axis when available; an uncalibrated original marker is not relabelled as a corrected price. Already added charts must be edited in Trade Plans.

Saved-state startup repair
-------------------------
Portfolio reads now start immediately, retry after connection-state changes during an in-flight request, and refresh without depending on the Dhan connection panel event. Fresh server snapshots are not rejected solely because the browser clock differs from the server clock; explicit holding links still require a recently received snapshot. Existing confirmed Zerodha holdings and legacy open/executed statuses are normalized on page load without touching real order records or reopening sold trades. The storage key is unchanged, and current holdings are displayed even without linked plans. Asset build queries keep HTML and JavaScript versions aligned. A full startup regression uses the real HTML control IDs, legacy stored records, a skewed browser clock and a three-holding Dhan response to verify BDL/KNRCON/PAGEIND/TMPV/JKTYRE appear only under Holdings.

Authoritative holding visibility
--------------------------------
The Holdings table starts from Dhan's current holdings/positions, enriches a row only when its stock and instrument match one local strategy, and then adds other recorded positions. An incorrect security ID in a differently named saved row cannot suppress a broker holding. Account holding shortcuts are always shown above the table and can focus a stock directly. A URL such as /?holding=PAGEIND#live opens its Dhan row; Clear filters returns to all open trades. Missing local strategy levels remain blank rather than being invented. Temporary localhost diagnostics used to verify actual browser rendering were removed after verification.

Initial-page account snapshot
-----------------------------
The localhost application GET now embeds a read-only Dhan portfolio snapshot into its HTML (no access token). App startup applies it before the separate portfolio polling module loads, so an unlinked holding such as PAGEIND can appear immediately even if background sync is delayed. The response is not cached, and current authorization is checked before fetching a snapshot. If unavailable, the main open-trades panel displays the sync error beside the table. Regression checks verify server HTML embeds PAGEIND quantity 4 and that client startup renders it before any portfolio-script request.

Chart reader recovery
---------------------
The server embeds its reader readiness and backend version into the initial application page. Status checks use a versioned, uncached URL. Blocked charts are explicitly labelled Reader unavailable rather than remaining Queued, with Analyze this chart, Retry analysis and Analyze queued charts actions. Queued/blocked images automatically retry every 15 seconds while the page is visible and when returning to the tab. Reader recovery retains the file and never adds a plan automatically. Half-yearly zone labels H and HY map to HYIT; execution-chart H still means Hourly. A BAYERCROP weekly-title parser regression covers Entry 3339.10, SL 2848.30, marked target 4357.70 and RB1-H higher-timeframe zone labels.

Dedicated Holdings page
-----------------------
The navigation tab is now Holdings. Open /holdings to load it directly. The server renders verified Dhan holding rows (including PAGEIND when returned by the account API) into the HTML table before JavaScript runs; the browser then enriches linked strategy fields and merges local Zerodha records. Missing SL/target values are not invented. The footer shows frontend/backend build identifiers. Current account symbols are included in Dhan CMP requests even when no strategy plan exists, and unlinked positions get open P/L from their actual broker quantity and average buy price. The existing localStorage key and all user plans are preserved.
