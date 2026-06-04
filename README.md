Copyright (C) 2026 Regents of the University of Michigan,
portions used with permission.

# MSWIM2D: Michigan Solar Wind Model in 2D

Created by Tim Keebler and Gabor Toth, maintained by Connor DiMarco.

MSWIM2D is a 2-D MHD model of the solar wind in the heliospheric **ecliptic
plane**. It drives [BATSRUS](https://github.com/SWMFsoftware/BATSRUS) (the SWMF
Outer Heliosphere component) on a 2-D spherical grid from **1 AU out to ~75 AU**,
with the inner boundary at 1 AU set by real spacecraft observations (OMNI/MIDL at
L1, STEREO-A, STEREO-B, Solar Orbiter). The result is a continuous, hourly,
self-consistent picture of plasma + field across every longitude and every
planet/spacecraft orbit, 1996 to the present (with a forecast tail).

This README takes you from a fresh checkout to **running the whole system end to
end**. For the deepest internals (file formats, coordinate conventions,
debugging recipes) see `AGENTS.md`/`CLAUDE.md`.

---

## 1. The big picture

One continuous BATSRUS simulation, fed by observations, split into **three
confidence tiers**, then turned into web + Python data products:

```
  EXTERNAL ARCHIVES (OMNI/MIDL, CDAWeb COHO, NASA SPDF)
        │   data_download/update_satellite_data.sh
        ▼
  data/<source>/<source>_<year>.dat.gz   +   data/DATA_MANIFEST.txt
        │                                       │  (the two seam dates:
        │                                       │   DATA_SAFE, LAST_POSSIBLE)
        ▼                                       ▼
  BATSRUS run drivers (Production_Scripts/RunAll_*.pl)   ← months derived from
        │     manifest via manifest_dates.sh                the manifest
        ▼
  ┌─────────────── three output trees, one unbroken clock ───────────────┐
  │  Output_final/        1996-01 → DATA_SAFE        all sources present  │
  │  Output_preliminary/  DATA_SAFE+1 → LAST_POSSIBLE  thinner data       │
  │  Output_prediction/   LAST_POSSIBLE+1 → +12 mo   persistence forecast │
  └──────────────────────────────────────────────────────────────────────┘
        │  website_build/build_website_data.sh        │ website_build/
        ▼  (flatten + coarse grid + products.json)         ▼ make_traj + precompute
  website_data/MSWIM2D_Data_New/                      spice/  (trajectory traces)
        │                                                   │
        └──────────────── rsync / symlink to herot ─────────┘
                                  │
                    ┌─────────────┴──────────────┐
                    ▼                             ▼
        MSWIM2D-Web/ (public website)     CSEM-MSWIM2D/ (pip: mswim2d)
        server-side interpolation,        xarray client over the same
        field movie, tier-aware Data page static products
```

**The two seam dates are never hard-coded.** `update_satellite_data.sh` writes
`data/DATA_MANIFEST.txt`, whose `DATA_SAFE` (last hour *all* active sources
report) ends the **final** tier and `LAST_POSSIBLE` (furthest *any* source
reaches) ends the **preliminary** tier. Everything downstream reads those two
records, so the tier boundaries **march forward automatically** as new data
lands. See `Production_Scripts/run_model/manifest_dates.sh`.

---

## 2. Repository layout

| Path | What it is |
|------|------------|
| `refresh.sh` | **The one-command monthly update** (root). data → model → trajectories → website → rsync. Prints a plan by default; `--run` executes. See §5. |
| `BATSRUS/` | The MHD solver, cloned in place (gitignored, **not** a submodule). One local patch; see §4. |
| `Input/` | `PARAM.in` / `PARAM.in.restart` templates the run drivers stamp per month. |
| `data/` | Satellite input lookup tables `<source>/<source>_<year>.dat.gz` (gitignored), `L1-old/` (Tim's OMNI), `earth_ephemeris/`, and **`DATA_MANIFEST.txt`**. |
| `data_prediction/` | Persistence-forecast input tables for the prediction tier (built by `make_prediction_tables.py`). |
| `Output_final/`, `Output_preliminary/`, `Output_prediction/` | The three model-output tiers. Per month: `<YYYYMM>/OH/*.outs` + `PARAM.in` + `RESTART/` + `runlog`. (Legacy single `Output/` predates the tier split.) |
| `Production_Scripts/` | **The operational pipeline**, in three stage folders: `data_download/` (satellite refresh + manifest), `run_model/` (the `RunAll_*.pl` tier drivers + `manifest_dates.sh`), `website_build/` (products + trajectory precompute + the map generator). `refresh.sh` drives them in order. |
| `Scripts/` | Untracked scratch / superseded one-offs (gitignored). Not operational. |
| `spice/` | Trajectory data tree: `trajectories/` (per-body `.dat`), `interp_out/` (raw precompute), `chunks/` (stitched per-body/year), `SpiceKernels/`. |
| `website_data/MSWIM2D_Data_New/` | What the website serves: `Output_flat/` (all tiers' raw `.outs`, hard-linked), `snapshots_coarse/` (decimated field-movie grid), `products.json` (tier index), `Satellite_Data/`. |
| `MSWIM2D-Web/` | The public website (separate git repo, gitignored here). Static pages + `interpolate.php` server-side interpolation. |
| `CSEM-MSWIM2D/` | The Python client (`pip install mswim2d`), a git **submodule**. |
| `Tim_MSWIM2D_1hr/` | Tim Keebler's reference run, rsynced (months before the operational seam). |

---

## 3. One-time setup

```
git clone <this repo> MSWIM2D && cd MSWIM2D
git submodule update --init                 # pulls CSEM-MSWIM2D
git clone https://github.com/SWMFsoftware/BATSRUS    # solver, in place
```

The public website lives in its own repo; clone it into `MSWIM2D-Web/` if you
are working on the site.

**Python toolchains** (the satellite refresh uses two, by design):
- `python3.8` (anaconda): Solar Orbiter + STEREO + Earth ephemeris (needs
  `spiceypy`, `spacepy`).
- `python3.12` (`/usr/bin/python3.12`): L1/MIDL builder.
Override with `PY38=... PY312=...` if your interpreters live elsewhere.

Then build + patch BATSRUS (see §4).

---

## 4. BATSRUS: install, patch, test

**Install** (most UM machines are auto-recognized):
```
cd BATSRUS
Config.pl -install                      # or: -install -compiler=gfortran
ulimit -s unlimited                     # large stack for temporary arrays
```
The supporting repos (`share`, `util`, `srcBATL`, …) are pulled in during
install. Create manuals with `make PDF` (`Doc/USERMANUAL.pdf` is the key one).

**The one required patch.** MSWIM2D needs four solar-wind lookup-table slots, not
the three upstream ships. Re-apply after any fresh BATSRUS checkout:

- `srcUserExtra/ModUserOuterHelio2d.f90`: set `MaxNumLookupTables = 4` (upstream
  `3`), with companion arrays `TimeFirst_I`/`TimeLast_I` dimensioned from it. The
  four inputs occupy fixed slots (SW1=L1, SW2=STEREO-A, SW3=STEREO-B,
  SW4=Solar Orbiter). STEREO-B data ends in 2014, so recent runs use SW1/SW2/SW4
  with a gap at SW3 and the boundary loop must reach 4 tables.

Edit the **source** module, not the generated `src/ModUser.f90` ; every build
runs `Config.pl -u=OuterHelio2d` and regenerates the generated copy, silently
discarding edits to it.

**Test** the OH stand-alone configuration (also configures it for MSWIM2D):
```
cd BATSRUS && make -j test_outerhelio2d      # empty *.diff file = pass
```

---

## 5. The operational pipeline: the monthly update, end to end

The whole monthly update is **one command** at the repo root:

```
./refresh.sh            # PLAN: print exactly what it would run, change nothing
./refresh.sh --run      # execute the plan
```

It is **safe by default**: with no flags it only prints the plan (every command,
with the month ranges it computed from the manifest and what is on disk), because
the model stage is hours of MPI and rebuilds the provisional tiers. Always look at
the plan first. Useful flags: `--force` (rebuild preliminary+prediction even if the
frontiers did not move), `--skip-data` (reuse `data/`), `MPI_RANKS=N` (bare-metal
rank count, default 6; no SLURM here), `PRED_HORIZON=N` (forecast months).

`refresh.sh` runs five stages in order; each is a thin wrapper over the scripts in
`Production_Scripts/<stage>/`, which you can also run by hand.

**Stage 1: satellite data → manifest** (`data_download/update_satellite_data.sh`).
Per source/year, re-pulls and rebuilds only what is missing/short/non-finite (up
to today − 3 days), then writes **`data/DATA_MANIFEST.txt`**. Two records drive
everything: `DATA_SAFE` (all active sources present → end of final) and
`LAST_POSSIBLE` (furthest any source reaches → end of preliminary).
`run_model/manifest_dates.sh` turns those into the run months.

**Stage 2: model (the smart part).** Let `S`=DATA_SAFE month, `L`=LAST_POSSIBLE
month, `F`/`P`=last final/preliminary month *with output* on disk. `refresh.sh`
reruns only what each frontier invalidates:

| Tier | Runs when | Range | Driver (`run_model/`) |
|------|-----------|-------|------------------------|
| Final | `S > F` | `F+1 .. S` | `RunAll_Step2_final.pl` (settled; never rebuilt over existing months) |
| Preliminary | final reran → wipe + full re-run; else `L` moved → extend/trim | `S+1 .. L` | `RunAll_Preliminary.pl` (seeds from `Output_final/S`) |
| Prediction | final or preliminary changed | `L+1 .. L+horizon` | `RunAll_Prediction.pl` (rebuilds `data_prediction/` via `make_prediction_tables.py`, seeds from `Output_preliminary/L`) |

So `DATA_SAFE` and `LAST_POSSIBLE` moving independently each trigger only the
work they invalidate. When final advances, the months it now covers are dropped
from `Output_preliminary/` (the "remove promoted months" step) and the whole
provisional tail re-runs from the new seed. Each driver is isolated (prediction
reads `data_prediction/`, writes `Output_prediction/`, never touches the others).

> One-time only (not done by `refresh.sh`): the cold-start spin-up that creates
> `Output_final/` from scratch:
> `perl Production_Scripts/run_model/RunAll_Step1.pl -s=199601 -e=200312` then
> `RunAll_Step2_final.pl -s=200407 -e=<DATA_SAFE>`. `refresh.sh` refuses to run if
> `Output_final/` is empty and points you here.

**Stage 3: trajectory precompute** (`website_build/`). `make_traj.py` regenerates
per-body trajectories out to the forecast horizon; **`make_maps.py`** rebuilds
`month_outs.map` + `body_months.map` for this box (the committed maps had Great
Lakes paths); then `run_month.sh` runs the Fortran `INTERP_OUTPUT.exe` once per
month (fanned out with `xargs -P`, since there is no SLURM/GNU-parallel here) and
`stitch.py` concatenates into `spice/chunks/`. This stage **skips with a notice if
`INTERP_OUTPUT.exe` is absent** (set `INTERP_OUTPUT_EXE=...` or build it); the maps
are still refreshed. `precompute.sbatch` is the SLURM equivalent for Great Lakes.

**Stage 4: website products** (`website_build/build_website_data.sh all`).
Flattens every tier's `.outs` into one `Output_flat/` (so `interpolate.php` never
needs tier logic), builds the coarse field-movie grid (`split_outs.py`), writes
**`products.json`** (`build_products_manifest.py`, the tier index the Data page
reads), and refreshes the in-situ overlay CSVs (`export_website_data.py` +
`chunk_satellite_data.py`).

**Stage 5: deploy.** `rsync` of `website_data/MSWIM2D_Data_New/` (and the
trajectory chunks) to herot, using `HEROT_AUTH` / `HEROT_DIR` from **`.env`** over
the existing ssh key. On herot the docroot reaches it via one symlink, so nothing
else is copied.

---

## 6. What the products feed

- **Website (`MSWIM2D-Web/`).** Static pages plus `interpolate.php`, which runs
  the Fortran `INTERPOLATE.exe` server-side on the raw monthly `.outs` to sample
  any trajectory. The **Data** page is tier-aware (a timeline showing which of
  final/preliminary/prediction your date window pulls from, with a warning before
  fetching provisional/forecast data); **Model Info** describes the three
  products; **Prediction** explains the persistence-forecast skill horizon.
- **Python client (`CSEM-MSWIM2D/`, `pip install mswim2d`).** Pulls the same
  static products and returns them as cached xarray Datasets:
  ```python
  import mswim2d
  data = mswim2d.get_trajectory("Earth", "2015-01-01", "2015-02-01")
  ```

---

## 7. Input data format (reference)

All satellite lookup tables share one format: HGI vectors, hourly cadence, time
in **seconds since 1965-01-01**, 4 header lines then whitespace rows. The
manifest's `last_utc` is the last hour carrying non-fill plasma (the usable model
input), which can be earlier than a record's raw span (e.g. mid-2025 STEREO-A:
PLASTIC plasma ends 2025-06-30 while MAG keeps reporting). Full column layout and
coordinate conventions are in `AGENTS.md`.

---

## 8. Going deeper

- `AGENTS.md` / `CLAUDE.md` : file formats, coordinate conventions, the OMNI→MIDL
  seam history, smoke-test recipes, and known-good output.
- `Production_Scripts/run_model/manifest_dates.sh` : how run months are derived from the
  manifest (regenerate the manifest to advance the runs).
- Each script's header docstring documents its own usage and assumptions.
