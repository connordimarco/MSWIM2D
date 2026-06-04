#!/usr/bin/perl -s
#
# RunAll_Prediction.pl -- OPERATIONAL persistence-forecast driver (run2_prediction).
#
# Continues from the last restart of the preliminary run (Output_preliminary/<last>,
# e.g. 202603) and marches the forecast horizon (default 12 months) driven by the
# PERSISTENCE tables in data_prediction/ -- the last ~1 Carrington rotation of real
# data ending at the manifest frontier (2026-03-28), tiled forward (built by
# Scripts/make_prediction_tables.py). Because the solar wind takes months to reach
# the outer heliosphere, the held inner boundary yields a genuine forecast: traces
# stay skillful for ~(R-1AU)/V_sw before the looped boundary diverges from reality.
#
# At the 2026 frontier only L1 still reports, so data_prediction/ holds L1 only and
# the run is L1-driven (the SW2/3/4 blocks below are emitted only if their files
# exist -- they don't here). This is the honest persistence state at the frontier.
#
# *** ISOLATION (never clobber the data-safe / preliminary products): ***
#   - INPUT  read from  data_prediction/        (NOT data/)
#   - OUTPUT written to  Output_prediction/      (NOT Output_final/ or _preliminary/)
#   - the seed restart is copied READ-ONLY from Output_preliminary/.
#
# Usage:  perl Scripts/RunAll_Prediction.pl -s=202604 -e=202703
#         (the wrapper run_step2_prediction.sbatch derives the range from the manifest)

my $start_date = ($s or $start or "202604");
my $end_date   = ($e or $end or "202703");
my $build = defined($build) ? $build : 1;   # -build=0 to skip the BATSRUS rebuild
my $Help = ($h or 0);

push @INC, ".";
use strict;
&print_help if $Help;

# Node-local scratch (same fast-I/O pattern as the production drivers).
my $local_scratch = $ENV{SLURM_JOB_ID} ? "/tmp" : "/tmp/mswim_pred_$$";
my $rundir = "$local_scratch/run";
my $localout = "$local_scratch/Output";
my $output  = "./Output_prediction";
my $outroot = "$ENV{PWD}/Output_prediction";        # ISOLATED forecast output root
my $seedroot = "$ENV{PWD}/Output_preliminary";      # preliminary run, read-only (seed restart)
my $datadir = "data_prediction";                    # ISOLATED persistence input tables
my $input  = './Input';
my $earliest_date = 199601;
my $earliest_year = 1996;
my $np = $ENV{MPI_RANKS} || $ENV{SLURM_NTASKS} || 8;   # MPI ranks; SLURM_NTASKS kept for Great Lakes
my $start_year = int(substr($start_date,0,4));
my $end_year = int(substr($end_date,0,4));
my $start_month = int(substr($start_date,4,6));
my $end_month = int(substr($end_date,4,6));

# Make array of months to run.
my @months_to_run = ();
foreach my $year ($start_year..$end_year)
{
    foreach my $month (1..12)
    {
	if($start_year == $end_year){
	    push(@months_to_run, sprintf("%04d%02d\n",$year,$month))
		if($month >= $start_month and $month <= $end_month);
	}
	elsif($year == $start_year){
	    push(@months_to_run, sprintf("%04d%02d\n",$year,$month))if($month >= $start_month);
	}
	elsif($year == $end_year){
	    push(@months_to_run, sprintf("%04d%02d\n",$year,$month))if($month <= $end_month);
	}
	else{
	    push(@months_to_run, sprintf("%04d%02d\n",$year,$month));
	}
    }
}

# Compile BATSRUS and PIDL; make run directory (skip with -build=0 if prebuilt).
if ($build) {
    print "Updating BATSRUS Config.pl...\n";
    qx(cd ./BATSRUS; ./Config.pl -noopenmp -u=OuterHelio2d -e=OuterHelio -f -g=10,10,2 -ng=2);
    print "Making BATSRUS and PIDL...\n";
    qx(cd ./BATSRUS; make -j BATSRUS);
    qx(cd ./BATSRUS; make PIDL);
} else {
    print "Skipping BATSRUS rebuild (-build=0); reusing existing executables.\n";
}
if (-e $rundir and -d $rundir){
    print "Run directory already exists.\n";
}else{
    print "Creating OH run directory...\n";
    qx(cd ./BATSRUS; make rundir RUNDIR=$rundir COMPONENT=OH);
}

# Seed the isolated output root with the restart preceding the first month, copied
# READ-ONLY from the preliminary run. The month loop then reads it from $outroot
# exactly like a normal continuation and never writes back to $seedroot.
my ($seed_year, $seed_month);
if ($start_month != 1) { $seed_year = $start_year;     $seed_month = $start_month - 1; }
else                   { $seed_year = $start_year - 1; $seed_month = 12; }
my $seed_date = sprintf("%04d%02d", $seed_year, $seed_month);
if (! -e "$outroot/$seed_date/RESTART/OH/restart.H") {
    die "Seed restart not found: $seedroot/$seed_date/RESTART/OH/ -- has the preliminary run finished?\n"
	unless -e "$seedroot/$seed_date/RESTART/OH/restart.H";
    print "Seeding $outroot/$seed_date/RESTART/OH from preliminary run (read-only copy)...\n";
    qx(mkdir -p $outroot/$seed_date/RESTART/OH);
    qx(cp $seedroot/$seed_date/RESTART/OH/restart.H  $outroot/$seed_date/RESTART/OH/);
    qx(cp $seedroot/$seed_date/RESTART/OH/octree.rst $outroot/$seed_date/RESTART/OH/);
    qx(cp $seedroot/$seed_date/RESTART/OH/data.rst   $outroot/$seed_date/RESTART/OH/);
} else {
    print "Seed restart already present in $outroot/$seed_date.\n";
}

# Accumulated sim time from the cold-start date so the clock matches the seed restart.
my $end_sim_time = 0;
my @restart_months = ();
foreach my $year ($earliest_year..$start_year)
{
    foreach my $month (1..12)
    {
	push(@restart_months, sprintf("%04d%02d\n",$year,$month))
	    if ($year * 100 + $month >= int($earliest_date) and $year * 100 + $month < int($start_date));
    }
}
foreach my $restart_month (@restart_months)
{
    my $year = int(substr($restart_month,0,4));
    my $month = int(substr($restart_month,4,6));
    $end_sim_time += 28 if $month == 2 and $year % 4;
    $end_sim_time += 29 if $month == 2 and not $year % 4;
    if($month == 4 or $month == 6 or $month == 9 or $month == 11){ $end_sim_time += 30; }
    elsif($month != 2){ $end_sim_time += 31; }
}

# Run simulation for every month.
my $restart_date = 0;
foreach my $month_string (@months_to_run)
{
    chomp($month_string);
    my $year = int(substr($month_string,0,4));
    my $month = int(substr($month_string,4,6));
    print "Running (prediction) $year-$month...   ";

    qx(rm -f $rundir/OH/IO2/* $rundir/OH/restartIN/* $rundir/OH/restartOUT/*);

    # Copy restart files (from the ISOLATED output root).
    if ($month_string != $earliest_date){
	if($month != 1){ $restart_date = sprintf("%04d%02d", $year, $month-1); }
	else           { $restart_date = sprintf("%04d%02d", $year-1, 12); }
	qx(cp $outroot/$restart_date/RESTART/OH/restart.H $rundir/restartIN/);
	qx(cp $outroot/$restart_date/RESTART/OH/octree.rst $rundir/restartIN/);
	qx(cp $outroot/$restart_date/RESTART/OH/data.rst $rundir/restartIN/);
    }

    # Select data files by what the table builder produced (data_prediction/). At
    # the 2026 frontier only L1 exists, so only the SW1/L1 block (already in the
    # PARAM template) is active; the SW2/3/4 blocks below stay off.
    my $StereoA = (-e "$datadir/STEREOA/STEREOA_$year.dat.gz");
    my $StereoB = (-e "$datadir/STEREOB/STEREOB_$year.dat.gz");
    my $SolarOrbiter = (-e "$datadir/SolarOrbiter/SolarOrbiter_$year.dat.gz");

    qx(gunzip -c $datadir/L1/l1_$year\.dat > $rundir/L1.dat);
    qx(gunzip -c $datadir/STEREOA/STEREOA_$year\.dat > $rundir/STEREOA.dat) if $StereoA;
    qx(gunzip -c $datadir/STEREOB/STEREOB_$year\.dat > $rundir/STEREOB.dat) if $StereoB;
    qx(gunzip -c $datadir/SolarOrbiter/SolarOrbiter_$year\.dat > $rundir/SolarOrbiter.dat) if $SolarOrbiter;

    # Update ending simulation time.
    $end_sim_time += 28 if $month == 2 and $year % 4;
    $end_sim_time += 29 if $month == 2 and not $year % 4;
    if($month == 4 or $month == 6 or $month == 9 or $month == 11){ $end_sim_time += 30; }
    elsif($month != 2){ $end_sim_time += 31; }

    # Replace necessary text in PARAM.in file.
    my $param_file = "PARAM.in";
    $param_file = "PARAM.in.restart" if int($month_string) != $earliest_date;
    open(my $in,  '<', "$input/$param_file") or die "Can't read old file: $!";
    open(my $out, '>', "$rundir/PARAM.in") or die "Can't write new file: $!";
    while( <$in> )
    {
	s/YYYY/$year/g;
	s/MM/$month/g;
	s/DDD/$end_sim_time/g;
	print $out $_;
      	if (/^ascii.*TypeFile$/){
	    print $out "
#LOOKUPTABLE
SW2           NameTable
load          NameCommand
STEREOA.dat   NameFile
ascii         TypeFile
" if $StereoA;
	    print $out "

#LOOKUPTABLE
SW3           NameTable
load          NameCommand
STEREOB.dat   NameFile
ascii         TypeFile
" if $StereoB;
	    print $out "

#LOOKUPTABLE
SW4           NameTable
load          NameCommand
SolarOrbiter.dat   NameFile
ascii         TypeFile
" if $SolarOrbiter;
	}
    }
    close $out;

    # Execute the code.
    my $tmpdir = "$local_scratch/tmp";
    qx(mkdir -p $tmpdir);
    qx(cd $rundir; TMPDIR=$tmpdir nice -n 10 mpiexec -n $np ./BATSRUS.exe > runlog);

    # Process the results into the ISOLATED output root.
    qx(mkdir -p $localout);
    qx(rm -rf $localout/$month_string);
    qx(cd $rundir; ./PostProc.pl -M $localout/$month_string);
    qx(rm -rf $outroot/$month_string);
    qx(cp -r $localout/$month_string $outroot/$month_string);
    qx(rm -rf $localout/$month_string);

    print "complete.\n";
}

exit 0;

###############################################################################
sub print_help{
    print "
RunAll_Prediction.pl -- operational persistence forecast (run2_prediction).

Reads  data_prediction/      (last-rotation persistence tables, built by
                              Scripts/make_prediction_tables.py)
Writes Output_prediction/    (isolated; never touches Output_final/_preliminary)
Seeds  the start restart read-only from Output_preliminary/.

Usage:
   RunAll_Prediction.pl [-h] [-s=YYYYMM] [-e=YYYYMM] [-build=0]
   -s=YYYYMM   first forecast month (default 202604)
   -e=YYYYMM   last  forecast month (default 202703)
   -build=0    reuse existing BATSRUS executables (skip reconfigure/make)
\n";
    exit 0;
}
