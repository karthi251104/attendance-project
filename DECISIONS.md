# Decisions

Choices made where the spec left room, in the order they came up.

## Phase 1

1. **Grid and exceptions live in `app/core/grid.py`.** `workbook_reader.py` only reads the file; `grid.py` turns
   the data into the grid and the exceptions list. The spec's tree has no `grid.py`, but splitting it keeps each
   file small. `processing.py` (Phase 2) will call `read_workbook` → `build_cycle_data` → save.
2. **The status is calculated in `grid.py`, not while reading.** The thresholds then come from one settings
   object, which keeps tests and config changes simple.
3. **pandas is not used.** Plain dicts are enough for the pivot of about 3,000 rows and are easier to follow.
   It will be added only if a later phase needs it.
4. **Date columns** start at column N, or after the last identity column if that is further right. Blank header
   cells before the first date are skipped, and the run stops at the first header that is not a date.
   Duplicate dates are a fatal error.
5. **Missing cached values check:** if every `Manager Email` cell in TO CONSIDER is `None` (not an error value,
   but truly empty), the file was never saved by Excel and the upload stops with *"Please open the file in Excel,
   save it, and upload again."*
6. **Totals mapping** is read from the first data row's formulas (`COUNTIF(..., "X")`). Columns without a
   formula (for example `Remarks`) are left out. `Total` is kept with `code = None`.
7. **Extra exception types** beyond section 9, all needed to report bad data clearly:
   `invalid_employee_id`, `duplicate_employee`, `invalid_sap_row`, `invalid_worked_hours` (counted as 0),
   `duplicate_sap_row` (first row wins), `invalid_status` (sheet value not in `ALLOWED_STATUSES`, such as `MTL`),
   `empty_day` (no value in the sheet and none from SAP), and `status_not_in_totals`.
8. **Withdrawn managers** are reported once per manager, not once per employee.
9. **`GRID_SOURCE=sap` with an unknown schedule (`?`)** falls back to the TO CONSIDER value, so `?` is never
   chosen when a real value exists.
10. **Unreadable `Worked Hours`** text raises `ValueError` in `to_seconds`; the reader reports it and uses 0.
    Excel durations over 24 hours (read as dates in 1899/1900) are converted correctly.
11. **Test workbooks are `.xlsx`.** openpyxl cannot create a VBA project, so the fixture cannot be a real `.xlsm`.
    The `.xlsm` round trip (Phase 4) must be checked once on the real file, locally.
12. **The fixture writes static values in TO CONSIDER columns C–M.** openpyxl cannot store a formula together with
    a cached value. The `formulas_only=True` option writes formulas only, to test the missing-cache error.
13. **`DEV_LOGIN` defaults to `false` in code**, and `.env.example` sets it to `true` for local use.
14. **HR uploads the file *before* the macro has run** (confirmed by the user, 29/10/2026). All headers are there,
    including the date headers N:AQ and the total headers AR:AZ, but these cells are empty:
    - TO CONSIDER day cells (N:AQ), which the app fills from the SAP calculation,
    - TO CONSIDER total formulas (AR:AY), whose codes are taken from the headers (Present=P, Absent=A,
      Weekly Off=OFF, Comp Off=CO, first Maternity Leave=ML, second Maternity Leave=PL, PH=PH, Total=SUM),
    - SAP `Remarks` (1-4) and `Remarks Code` (P/A/OFF/PH) in U:V.
    If the formulas are present, they are still used to read the codes (`TotalColumn.has_formula`).
    Columns C–M are still expected to have cached values.
15. **The app does the macro's job on download.** This changes section 11 of the spec. The Phase 4 writer will:
    - write every TO CONSIDER day cell where `days` differs from `sheet_days` (the value at upload; None = empty).
      These are the cells filled from SAP and the cells edited in the portal.
    - write the macro's `COUNTIF` / `SUM` formulas in empty total cells,
    - write the macro's formulas in empty SAP `Remarks` / `Remarks Code` cells, rows 2 to `sap_last_row`.
      **So the SAP sheet does change** (only those two columns, and only empty cells). No other SAP cell is touched.
16. **Checked against the real October 2026 workbook** (local only, never committed):
    - filled file: 2,880 / 2,880 employee-days match, 0 mismatches.
    - copy with N:AY and SAP U:V cleared: all 2,880 days filled from SAP, identical to the macro's values.
17. **The verify script needs a file where the macro has already filled the days.** On an empty upload it
    reports how many cells were filled from SAP and that there was nothing to compare.

## Phases 2–6

18. **Write-back edits the workbook XML directly** (`app/core/xlsx_patch.py`) instead of re-saving with openpyxl.
    Re-saving with openpyxl drops every formula's cached value, can lose unsupported features, and must load
    HC_Teamlease. Patching changes only the target cells; all other zip parts are copied byte for byte.
    Strings are written as inline strings, so `sharedStrings.xml` is untouched. `fullCalcOnLoad` is set so Excel
    recalculates on open.
19. **Round trip on the real workbook (29/10/2026, local only):** `scripts/side_by_side.py` cleared what the macro
    fills, ran the app, and compared the result with the macro's file. It found 0 differences in TO CONSIDER
    (4,947 cells) and in SAP (57,622 cells). The VBA project and all 23 other parts were identical.
20. **Download runs synchronously in a worker thread.** On the real file, building takes about 2.5 s (plus about
    6 s to read the file), so a background job queue with a "preparing" state stored in the database was not worth
    the extra parts. The button shows "Preparing download…". Revisit if files grow a lot.
21. **Exceptions are stored in their own collection** (`exceptions`), not inside the cycle document. A cycle
    with many mismatches could otherwise approach the Cosmos DB 2 MB document limit.
22. **Uploads are all-or-nothing.** The file is fully read and validated before anything is saved. If saving fails,
    the partial cycle and the stored file are removed.
23. **Versions:** each upload is a new cycle. The newest upload of a period becomes `active` and older ones become
    inactive and read-only. HR can make an older version active again. A manager's home page opens their active
    cycle.
24. **Locking blocks everyone, including HR** (HR unlocks first). **The deadline blocks managers only.** The deadline
    is 23:59:59 IST on the chosen date.
25. **Saves are all-or-nothing on validation.** If any change in a batch is for another team, has an invalid
    status, or has a date outside the cycle, nothing is saved. "Not your team" and "no such employee" give the same
    error, so other teams' IDs cannot be probed.
26. **Concurrent edits:** the browser sends the value it showed (`old_value`). If someone else changed that day in
    the meantime, the save reports a conflict instead of overwriting.
27. **One optional comment per save**, applied to every change in that save.
28. **The audit log stores `manager_email`** next to each change, so the review page can count changes per manager.
29. **Emails are sent after the response** (FastAPI BackgroundTasks), so HR is not kept waiting. The review page
    shows each manager's latest email status.
30. **`MONGODB_URI=mongomock://`** gives an in-memory database for trying the app without Docker. It is refused when
    `ENV=production`. `mongomock` is a dev dependency only.
31. **Production startup checks** refuse to start in these cases:
    - DEV_LOGIN is on,
    - SECRET_KEY is weak or the default,
    - the Entra settings are missing,
    - APP_BASE_URL is not https,
    - the database is mongomock.
32. **Security headers:** a strict CSP with no inline scripts or styles, X-Frame-Options DENY, nosniff and
    Referrer-Policy. HSTS is added in production. CSV exports escape cells that start with `= + - @`.
33. **Logout** also signs out of Microsoft (Entra end-session endpoint) when Entra login is used.

## Correction: TO CONSIDER is built by the app (30/10/2026)

This replaces decisions 4, 5, 6, 9, 12, 14, 15 and 17, the `GRID_SOURCE` setting and `scripts/verify_against_macro.py`.

34. **HR uploads only `SAP` and `HC_Teamlease`** (confirmed by the user, 30/10/2026). The macro creates
    `TO CONSIDER` itself (`CreatePivotOnNewSheet`, `Manipulation_1`–`_4`), so the app does the same instead of
    expecting the sheet. A file that already has `TO CONSIDER` is refused with a clear message.
35. **Rows and columns follow the macro:** one row per SAP employee, sorted by Employee ID (the pivot's order);
    one column per date found in SAP, from column M; then the totals.
36. **Column D is dropped** (the user does not need it). In the old file it was a VLOOKUP into another workbook
    (`[1]Sheet1`), added by hand after the macro ran. Without it, columns C–L are exactly the macro's.
37. **HC_Teamlease columns are matched by header name**, not by the macro's fixed positions (7, 4, 12, 23, 25, 42,
    43, 44). The first row of an Employee ID wins, as with VLOOKUP. Only the columns up to the last one needed are
    read (about 5 s for 22,000 rows).
38. **Values, not VLOOKUP formulas, go into C–L.** The raw export can hold IDs as text, which makes VLOOKUP fail
    until *Text to Columns* has run. Writing values keeps the download the same as what managers saw in the portal.
    Missing employees or managers get `#N/A` (an Excel error value, as VLOOKUP shows). Empty HC cells stay empty
    where VLOOKUP would show 0 (or 00/01/1900 for LWD).
39. **The totals keep the existing file's headers and codes:** Present P, Absent A, Weekly Off OFF, Comp Off CO,
    Maternity Leave ML, Maternity Leave PL, PH, Total = SUM, and an empty Remarks column. (The macro code itself
    still says `MTL` and has one Maternity Leave column; the sheet the user works with has ML and PL.)
40. **Adding a sheet** edits workbook.xml (new `<sheet>`, local defined names and the active tab shifted by one),
    its rels, `[Content_Types].xml` and styles.xml (a bold font, a thin border and four cell styles). The sheet
    that was active stays active, so no other sheet's XML changes.
41. **Checked against the real October 2026 workbook (30/10/2026, local only):** `scripts/side_by_side.py` deleted
    `TO CONSIDER`, cleared SAP Remarks, ran the app and compared with the macro's file: 96 employees in the same
    order, 4,896 TO CONSIDER cells (cached VLOOKUP and COUNTIF values compared with the app's values) and 57,622
    SAP cells, 0 differences. The VBA project and the 22 other parts were identical.
