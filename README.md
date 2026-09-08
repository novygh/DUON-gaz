# DUON Gaz

Custom integration for Home Assistant that combines physical gas-meter readings with cumulative CO/CWU statistics from a boiler integration and builds one corrected gas-consumption history in Recorder.

Current development version: **0.3.1**.

> [!IMPORTANT]
> Version 0.3.1 is still being tested on the development branch `feature/store-v2-recorder-sums`. The `main` branch remains the old test version until this work is merged.

## What it does

DUON Gaz uses three layers of data:

1. **physical meter readings** — authoritative m³ anchors,
2. **Recorder CO/CWU cumulative statistics** — the hourly consumption profile,
3. **DUON billing data** — conversion factor and tariff data used for energy/cost calculations.

The integration can:

- select any two Home Assistant sensor entities as CO and CWU sources,
- read their cumulative `sum` statistics from Recorder,
- save exact physical gas-meter readings with timestamps,
- keep trusted invoice readings as lower-precision anchors when appropriate,
- calibrate CO and CWU separately in m³/kWh,
- reconstruct missing hourly source data,
- handle negative source corrections/rollbacks without creating negative gas consumption,
- close settled intervals exactly to physical meter readings,
- build a provisional live tail after the newest physical reading,
- publish one monotonic external statistic: `duon_gaz:canonical_gas`,
- automatically refresh the provisional tail after Home Assistant generates new hourly Recorder statistics,
- calculate current estimated meter state, CO/CWU split, energy and cost diagnostics.

Raw source statistics are not modified.

## Canonical Recorder statistic

The main long-term output is:

```text
duon_gaz:canonical_gas
```

It is an external Home Assistant Recorder statistic in **m³** with a monotonic cumulative `sum`.

The history has two parts:

- **settled** — intervals closed by physical/trusted meter anchors and normalized exactly to the measured m³ delta,
- **provisional** — the open interval after the newest anchor, estimated from CO/CWU Recorder statistics.

When the newest physical reading falls inside an hour, DUON Gaz merges the settled and provisional pieces into one Recorder row for that hour.

From 0.3.1 the provisional tail is refreshed automatically when Home Assistant emits its hourly statistics event. A full rebuild is used when the anchor/calibration/source basis changes.

## Requirements

- Home Assistant with **Recorder** enabled.
- Two source sensors representing cumulative CO and CWU usage and providing Recorder `sum` statistics.
- At least two trusted gas-meter anchors for settled reconstruction and calibration.

### Recorder include/whitelist configurations

If your `recorder:` configuration uses `include:`, the selected CO and CWU source entities must be included there, otherwise Home Assistant will not have the required source history.

Example:

```yaml
recorder:
  include:
    entities:
      - sensor.your_boiler_heating_gas_consumption
      - sensor.your_boiler_dhw_gas_consumption
```

`duon_gaz:canonical_gas` is an **external statistic**, not an entity state, so it does not need to be added to `recorder.include.entities`.

## Installation

### Current development branch

Until 0.3.x is merged to `main`, install the contents of:

```text
custom_components/duon_gaz
```

from branch:

```text
feature/store-v2-recorder-sums
```

into:

```text
/config/custom_components/duon_gaz
```

Restart Home Assistant once after replacing the integration files.

Then open:

**Settings → Devices & services → Add integration → DUON Gaz**

### HACS

The repository already contains `hacs.json`. Normal HACS installation from the default branch should be used after the current development branch is merged to `main`.

## Initial configuration

The config flow asks for:

- CO source sensor,
- CWU source sensor,
- billing conversion factor in kWh/m³,
- gas price net per kWh,
- variable distribution price net per kWh,
- monthly subscription net,
- monthly fixed distribution charge net,
- VAT as a decimal value (`0.23` = 23%).

The source entities are configurable; no Ariston entity ID is hard-coded into the reconstruction engine.

> [!WARNING]
> Numeric defaults in the current development build are only bootstrap/example values from the system used during development. Enter values appropriate for your own contract and invoices. They must not be treated as universal DUON tariffs.

## Adding a physical meter reading

1. Enter the exact current gas-meter state in the DUON Gaz number entity.
2. Press **Zapisz odczyt gazomierza**.
3. DUON Gaz stores the reading with its timestamp and a coherent Recorder source snapshot.
4. After enough trusted readings are available, CO/CWU calibration is recalculated.

New manual readings are stored at 0.001 m³ precision. A separately rounded whole-m³ value is kept for the future SMS workflow.

Physical readings are the highest-priority source of truth. Invoice readings can be used only when they are classified as trusted billing readings; estimated invoice values are not physical anchors.

## Historical reconstruction

The integration contains a generic reconstruction engine. It does not rely on installation-specific dates or meter values.

For each closed meter interval it:

- derives hourly CO/CWU deltas from Recorder cumulative sums,
- retracts source rollbacks from recent positive usage,
- reconstructs missing hours using nearby same-local-hour profiles,
- converts CO and CWU independently using calibrated m³/kWh coefficients,
- scales the interval to the measured physical m³ delta,
- preserves the CO/CWU proportion,
- verifies exact interval closure.

The original boiler statistics remain untouched.

## Services

### `duon_gaz.refresh_tail`

Manually refreshes only the open provisional part of `duon_gaz:canonical_gas`.

Normally this is not needed because 0.3.1 listens for Home Assistant hourly Recorder statistics and refreshes the tail automatically.

If the physical anchor, source basis or calibration has changed, the integration automatically falls back to a full canonical rebuild.

### `duon_gaz.import_history`

Migration/development helper for importing a verified historical JSON seed from `/config`.

This is optional and is not required for a new installation.

## Main entities

Depending on Home Assistant language/entity naming, the integration exposes entities corresponding to:

- gas consumption,
- gas energy,
- total gas cost,
- CO since last reading,
- CWU since last reading,
- billing conversion factor,
- aggregate Ariston correction diagnostic,
- CO calibration,
- CWU calibration,
- data status,
- meter input,
- save meter reading button,
- canonical-history preview button,
- canonical-history publication button.

The **Status danych** sensor also exposes diagnostics for settled history, provisional tail, Recorder publication and verification.

## Data model / source priority

Recommended interpretation of data quality:

1. exact manual physical gas-meter reading,
2. trusted invoice meter indication,
3. Recorder CO/CWU statistics used as the consumption profile,
4. invoice billing data for energy/cost validation,
5. provisional estimates after the latest physical anchor.

A later physical reading converts the preceding provisional period into a settled interval.

## Safety of historical data

DUON Gaz uses Home Assistant Recorder APIs. It does **not** write SQL directly and does not overwrite the original CO/CWU statistics.

The canonical history is stored under DUON's own statistic ID:

```text
duon_gaz:canonical_gas
```

## Not implemented yet

The following are planned but are not complete in 0.3.1:

- automatic Outlook/Microsoft Graph invoice retrieval,
- end-to-end encrypted PDF invoice ingestion from the mailbox,
- automatic SMS sending/composer workflow,
- final public-installation cleanup of bootstrap calibration/tariff defaults,
- automatic Energy Dashboard migration/replacement.

## Development status

The current 0.3.x work is tracked in draft PR #1. It includes Store v2, Recorder-based source history, separate CO/CWU calibration, canonical historical reconstruction, external Recorder publication and automatic live-tail refresh.

The project is intended to be reusable on other installations using compatible cumulative CO/CWU source sensors; installation-specific meter readings, dates, tariffs and calibration values must remain outside the generic reconstruction code.
