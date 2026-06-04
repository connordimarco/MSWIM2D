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
end**.

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

**Python toolchains.** Three, by design:
- **`mswim2d_env/`** — the processing venv for the **website build + trajectory
  precompute** (numpy, pandas, spiceypy). `refresh.sh` uses it for everything in
  `website_build/`. Create it once (this box's SSL certs are broken for pip, and
  spiceypy's sdist tries to fetch CSPICE, hence the flags):
  ```
  python3 -m venv mswim2d_env
  mswim2d_env/bin/pip install --prefer-binary --only-binary=:all: \
      --trusted-host pypi.org --trusted-host files.pythonhosted.org -r requirements.txt
  ```
  It's gitignored; deps are pinned in `requirements.txt`. Override with `MSWIM2D_PY=...`.
- `python3.8` (anaconda) + `python3.12` (`/usr/bin/python3.12`) — used **only by the
  satellite refresh** (`data_download/`): SolO/STEREO/ephemeris on 3.8 (spacepy),
  L1/MIDL on 3.12. Override with `PY38=... PY312=...`.

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

**Automation + email.** In production this refresh is run for you by the
workspace-level orchestrator `../monthly_refresh.sh` (cron, 1st of month 00:01),
which calls `./refresh.sh --run` and then MIDL's refresh, emailing a stats summary
after each (MSWIM2D's email renders the `products.json` tier table). `refresh.sh`
itself sends no email; running it by hand notifies no one. See the workspace root
`../CLAUDE.md` for the orchestrator + shared notifier (`../notify/`, `../.env`).

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
`stitch.py` concatenates into `spice/chunks/`. **Incremental:** `refresh.sh` only
feeds months `>= MIN_MONTH` (= `DATA_SAFE - 1`) to the precompute, so a routine
update re-traces just the moving window (~23 months: final-overlap + preliminary +
prediction) and `stitch.py` reuses the settled `interp_out` history — ~20 min vs
~hours for all 507. This stage **skips with a notice if `INTERP_OUTPUT.exe` is
absent** (set `INTERP_OUTPUT_EXE=...` or build it); the maps are still refreshed.
`precompute.sbatch` is the SLURM equivalent for Great Lakes.

**Stage 4: website products** (`website_build/build_website_data.sh all`).
Flattens every tier's `.outs` into one `Output_flat/` (so `interpolate.php` never
needs tier logic), builds the coarse field-movie grid (`split_outs.py`), writes
**`products.json`** (`build_products_manifest.py`, the tier index the Data page
reads), and refreshes the in-situ overlay CSVs (`export_website_data.py` +
`chunk_satellite_data.py`). Also incremental: `refresh.sh` passes the same
`MIN_MONTH=DATA_SAFE-1`, so only the moving window is re-flattened/re-decimated
and merged into the existing coarse manifest (force a deeper rebuild with
`MIN_MONTH=0`; required if you change the coarse grid — see "Website Pipeline").

**Stage 5: deploy.** `rsync` of `website_data/MSWIM2D_Data_New/` → `$HEROT_DIR`
and the trajectory chunks (`spice/chunks/`) → the sibling
`/homedata/MSWIM2D/precomputed_trajectories/chunks/`, using `HEROT_AUTH` /
`HEROT_DIR` from **`.env`** over the existing ssh key. The served site is
`/homedata/MSWIM2D` (public URL `https://csem.engin.umich.edu/MSWIM2D/`; the
legacy V1 site now lives at `https://csem.engin.umich.edu/MSWIM2D.V1/`);
`MSWIM2D_Data_New` is served straight off `/data` over NFS via a symlink.
**Everything served must be `o+rX`** — apache is not in the `cdimarco` group, so
group-only files 403 and the Data page falls back to live interpolation; Stage 5
`chmod -R o+rX`s the chunks before rsync (the website tree is handled in Stage 4).

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
