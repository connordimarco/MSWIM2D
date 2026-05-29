Copyright (C) 2026 Regents of the University of Michigan,
portions used with permission.

Michigan Solar Wind Model in 2D

Created by Tim Keebler and Gabor Toth and maintained by Connor DiMarco. 

This document outlines the installation and usage of the Michigan Solar Wind
Model - 2D (MSWIM2D). It requires BATSRUS in stand-alone mode configured as
the OH component.

## Obtain BATSRUS

BATSRUS is open source on GitHub. Clone it into the top of this repository:
```
cd MSWIM2D
git clone https://github.com/SWMFsoftware/BATSRUS
```
The supporting repositories (`share`, `util`, `srcBATL`, ...) are pulled in
automatically during installation (`Config.pl -install`, see below).

Registered University of Michigan users may alternatively obtain the full
distribution from UM GitLab (`git@gitlab.umich.edu:swmf_software/BATSRUS`)
using the SWMF `gitclone` script; see the
[GitLab instructions](http://herot.engin.umich.edu/~gtoth/SWMF/doc/GitLab_instructions.pdf).

Note: BATSRUS is a plain clone checked out in place, **not** a git submodule
of this repository (it is gitignored). MSWIM2D applies a small local patch to
it — see "How this BATSRUS differs from upstream" below.

## Install and test BATSRUS for stand-alone OH component.

Many machines used by UofM are already recognized by the
`share/Scripts/Config.pl`.
For these platform/compiler combinations installation is very simple:
```
Config.pl -install
```
On other platforms the Fortran (and C) compilers should be explicitly given.
To see available choices, type
```
Config.pl -compiler
```
Then install the code with the selected Fortran (and default C) compiler, e.g.
```
Config.pl -install -compiler=gfortran
```
A non-default C compiler can be added after a comma, e.g.
```
Config.pl -install -compiler=mpxlf90,mpxlc
```
For machines with no MPI library, use
```
Config.pl -install -nompi -compiler=....
```
This will only allow serial execution, of course.

The ifort compiler (and possibly others too) use the stack for temporary arrays,
so the stack size should be large. For csh/tcsh add the following to `.cshrc`:
```
unlimit stacksize
```
For bash/ksh add the following to `.bashrc` or equivalent initialization file:
```
ulimit -s unlimited
```
# Create the manuals

Please note that creating the PDF manuals requires
that LaTex (available through the command line) and ps2pdf
be installed on your system.

To create the PDF manuals for BATSRUS and CRASH type
```
make PDF
cd util/CRASH/doc/Tex; make PDF
```
in the BATSRUS directory. The manuals will be in the `Doc/` and
`util/CRASH/doc/` directories, and can be accessed by opening
`Doc/index.html` and `util/CRASH/doc/index.html`.

The input parameters of BATSRUS/CRASH are described in the `PARAM.XML`
in the main directory. This is the best source of information when
constructing the input parameter file and it is used to generate the
"Input Parameters" section of the manual.

## Cleaning the documentation
```
cd doc/Tex
make clean
```
To remove all the created documentation type
```
cd doc/Tex
make cleanpdf
```

# Read the manuals

All manuals can be accessed by opening the top index file
```
open Doc/index.html
```
You may also read the PDF files directly with a PDF reader.
The most important document is the user manual in
```
Doc/USERMANUAL.pdf
```

# Test the OH component in stand-alone mode

Running this test will properly configure BATSRUS for use with MSWIM2D, as
well as confirming proper function.
```
cd BATSRUS
make -j test_outerhelio2d
```
The `-j` flag allows parallel compilation.
This requires a machine where `mpiexec` is available.
The test runs with 2 MPI processors and 2 threads by default.
A successful test is indicated by creation of an empty `test_outerhelio2d.diff` file.

# How this BATSRUS differs from upstream

MSWIM2D drives a stock BATSRUS with one functional change to the OuterHelio2d
user module. A fresh `gitlabclone BATSRUS` does **not** include it, so re-apply
it after checking out BATSRUS:

- `srcUserExtra/ModUserOuterHelio2d.f90`: `MaxNumLookupTables = 4` (upstream
  ships `3`), with the companion arrays `TimeFirst_I`/`TimeLast_I` dimensioned
  from that constant. The four solar-wind inputs occupy fixed slots
  (SW1=L1, SW2=STEREO-A, SW3=STEREO-B, SW4=Solar Orbiter); because STEREO-B
  data ends in 2014, 2022–2025 runs use SW1, SW2, SW4 with a gap at SW3, and
  the boundary loop must allow 4 tables to reach the Solar Orbiter slot.

Edit the **source** module (`srcUserExtra/ModUserOuterHelio2d.f90`), not the
generated `src/ModUser.f90` — `Config.pl -u=OuterHelio2d` (run by
`Scripts/RunAll.pl`) regenerates `src/ModUser.f90` from the source on every
build, silently discarding edits made to the generated copy.

# Create data files for running MSWiM2D

MSWIM2D reads hourly solar-wind lookup tables (one gzipped `.dat.gz` per
satellite per year) from `data/`. They are generated from external archives by
the scripts in `Scripts/`; see `AGENTS.md` for the full data pipeline,
dependencies, and coordinate conventions. In brief:

- **L1 (MIDL):** `fetch_earth_ephemeris.py` then `create_midl_l1.py` →
  `data/L1/l1_<year>.dat.gz`.
- **STEREO-A / STEREO-B (CDAWeb COHO):** `create_imf.py` →
  `data/STEREOA/`, `data/STEREOB/`.
- **Solar Orbiter (NASA SPDF COHO):** `create_solo.py` → `propagate_solo.py` →
  `create_solo.py --write-lookup-table` → `data/SolarOrbiter/`.

All tables share one format (HGI vectors, hourly cadence, time in seconds since
1965-01-01); the column layout is documented in `AGENTS.md`.

# Run MSWIM2D

The driver is `Scripts/RunAll.pl`, which runs BATSRUS month by month over a
date range. Despite the `-s=YYYY` help text, it takes `YYYYMM` strings:

```
cd MSWIM2D
module load mpi/openmpi-x86_64       # or your platform's MPI module
Scripts/RunAll.pl -s=199803 -e=202512
```

For each month it reconfigures and rebuilds BATSRUS, writes `BATSRUS/run/PARAM.in`
from the templates in `Input/` (`PARAM.in` for the first month, `PARAM.in.restart`
otherwise), unzips that year's lookup tables into `BATSRUS/run/`, adds a
`#LOOKUPTABLE` block for each satellite whose data covers the year, runs
`mpiexec -n 8 ./BATSRUS.exe`, and collects results into `Output/<YYYYMM>/` via
`PostProc.pl`. Months after the first restart from the previous month's output.

This is a shared resource — check the load (`uptime`, `nproc`) and consider a
smaller rank count before launching long production runs. See `AGENTS.md` for
smoke-test recipes, post-processing details, and known-good output.

# Build the website data products

The public website lives in the `MSWIM2D-Web/` git submodule. Initialize it
with:

```
git submodule update --init
```

This repository produces the two data products the site consumes (both
gitignored):

```
Scripts/export_website_data.py   # data/*.dat.gz -> Website_data/*.csv (in-situ inputs)
Scripts/flatten_output.sh        # Output/<YYYYMM>/OH/*.outs -> Output_flat/<YYYYMM>.outs
```

Inside `MSWIM2D-Web/`, those are staged under `MSWIM2D_Data_New/` and
pre-chunked for the browser by `chunk_satellite_data.py` and `split_outs.py`.
See `AGENTS.md` ("Website Pipeline") for the full flow.
