# CompostIQ Simulator

Generates synthetic sensor data for one compost bin and puts a UI in front of it,
so a whole composting cycle can be produced, inspected and saved without a
physical bin in the loop.

## Running it

```bash
python simulator/app.py
```

Then open <http://127.0.0.1:8050>.

Dependencies are in `requirements.txt` (`dash`, `dash-mantine-components`,
`plotly`, `pandas`) — no icon package and no network calls at runtime.

## What's in here

| File | Role |
| --- | --- |
| `app.py` | Dash app: layout wiring and callbacks. Calls the generator, never reimplements it. |
| `generator.py` | The simulation itself. Unchanged in behaviour — it just takes two more optional arguments now. |
| `composting_stages.py` | The five stages and their measured envelopes. Single source of truth for stage data. |
| `run_store.py` | Saves and loads generated cycles under `Data/`. |
| `ui/theme.py` | Stage colours, per-sensor accents, Mantine theme, Plotly templates, inline icons. |
| `ui/charts.py` | Plotly figure builders. |
| `ui/stats.py` | The numbers behind the indicator cards. Pure pandas. |
| `ui/layout.py` | Layout builders for the shell, control panel and analysis panel. |
| `assets/simulator.css` | The handful of styles props can't express. |
| `Data/` | Generated cycles land here (git-ignored, created on demand). |

## The interface

**Left panel — configuration**

- *Cycle*: start stage, start date, duration (blank runs through to maturation), random seed.
- *Sampling & sensor noise*: gap between readings, and per-sensor noise σ under the accordion.
- *Feedstock & operations*: bulk material, how often it's added, mass in the bin, turns over
  the period, and two switches. **These are display only** — they are marked as such in the UI
  and are deliberately not passed to the generator.

**Right panel — analysis**

- A tab bar across the top, one tab per generated cycle. Each tab shows its duration, reading
  count, and a colour strip for the stages it covers. Tabs are rebuilt from `Data/` on startup,
  so they survive a restart.
- A cycle summary strip: date range, seed, reading count, interval, peak temperature, and a
  ring showing the longest unbroken stretch at or above 55 °C against the 3-day EPA Class A
  target. Two actions sit alongside it:
  - **Download JSON** — sends that cycle's readings to the browser as a file.
  - **Upload to cloud** — labelled *mock*. It reports what it *would* have sent and does not
    contact anything; it is a placeholder until the backend API exists.
- **Temperature over time** as the hero chart — every reading drawn as a bar coloured by the
  stage it belongs to, with the 55 °C target marked.
- A stage timeline showing how long the pile spent in each stage.
- Indicator cards for all five sensors: latest reading, trend, min/avg/max, a sparkline, and
  the share of readings that fell inside the range `composting_stages.py` expects for their stage.
- A sensor detail chart with a selector for moisture, O₂, CO₂, NH₃ or all four at once, drawn
  against stage bands and the expected envelope.

Light and dark are both supported; the toggle is in the header and Plotly figures re-template
with it.

## How runs are stored

Each generate writes two files into `Data/`:

- `<run_id>.json` — the readings, an array that validates against
  `mock-data/compostiq_mock_dataset.schema.json`.
- `<run_id>.meta.json` — the settings that produced it plus a summary. Kept separate because
  the schema sets `additionalProperties: false`, so configuration cannot live in the data file.

Runs stay on disk rather than in a browser store, so the page never has to carry thousands of
readings around.

## Changes made to `generator.py`

The simulation logic is untouched. Three additive changes support the UI:

1. Output now goes to `simulator/Data/` instead of `../mock-data/`.
2. `generate_bin_data()` and `generate_stage_data()` accept `sample_interval_minutes` and
   `noise`. Both default to the existing module constants, so calling them the old way behaves
   exactly as before.
3. `NOISE_DEFAULTS` / `resolve_noise()` let a caller override one sensor's noise without
   knowing the constant names.

Running `python simulator/generator.py` from the command line still works the same way.
