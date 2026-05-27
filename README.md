Copyright (C) YYYY Regents of the University of Michigan,
portions used with permission.

Michigan Solar Wind Model in 2D

Created by Tim Keebler and Gabor Toth and maintained by Connor DiMarco. 

This document outlines the installation and usage of the Michigan Solar Wind
Model - 2D (MSWiM2D). It requires BATSRUS in stand-alone mode configured as
the OH component.

## Obtain BATSRUS

Read the [instructions](http://herot.engin.umich.edu/~gtoth/SWMF/doc/GitLab_instructions.pdf)
about registering, passwordless access, amil notifications, and defining
the "gitlabclone (or gitclone) alias/function.

Check out the BATSRUS distribution in the main MSWIM2D repository.
'''
cd MSWIM2D
gitlabclone BATSRUS
'''

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
`util/CRASH/doc/`` directories, and can be accessed by opening
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

Running this test will properly configure BATSRUS for use with MSWiM2D, as
well as confirming proper function.
'''
cd BATSRUS
make -j test_outerhelio2d
'''
The '-j' flag allows parallel compilation.
This requires a machine where 'mpiexec' is available.
The test runs with 2 MPI processors and 2 threads by default.
A successful test is indicated by creation of an empty test_outerhelio2d.diff file.


## Create data files for running MSWiM2D

