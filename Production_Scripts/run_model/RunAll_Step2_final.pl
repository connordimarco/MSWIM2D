#!/usr/bin/perl -s
#
# RunAll_Step2_final.pl -- STEP 2 (final) of the two-step production run.
#
# This is the "final" Step-2 driver: it writes to Output_final/ (not Output/)
# and is shared by two SLURM wrappers -- run_step2_final.sbatch (full Step-2
# span) and run_step2.1.sbatch (the 2020-2025 Solar Orbiter re-run patch).
#
# Step 1 (RunAll_Step1.pl) cold-starts at 1996-01 and runs time-accurate with
# Tim's OMNI input (data/L1-old) through Dec 2003, producing a fully spun-up
# restart in Output_final/200312/RESTART/OH/. Step 2 picks up from that restart
# at the official seam 2004-01 and continues with the MIDL input (data/L1), the
# operational forward driver. MIDL plasma-dropout gaps are handled by the
# per-variable interpolation in create_midl_l1.py (keeps real B/V). earliest_date
# stays 199601 so the simulation clock is continuous across the handoff.
#
# Runs end at the manifest DATA_SAFE month (currently 202506, limited by
# STEREO-A) so every output month has all active sources present.
#
# Run with:  RunAll_Step2_final.pl -s=200407 -e=202506
# Requires:  Output_final/<prev>/RESTART/OH/{restart.H,octree.rst,data.rst}.
# Note: there is a small OMNI->MIDL boundary-source step at the 199803 seam.

my $start_date = ($s or $start or "200001");
my $end_date = ($e or $end or "200002");
my $interval = ($i or $interval or "month");
# Output sub-dir on /nfs/turbo. Defaults to Output_final (the data-safe product);
# run_step2_preliminary.sbatch passes -o=Output_preliminary for the provisional
# beyond-data-safe tail. Restarts for the first month are read from this same dir,
# so the preliminary wrapper seeds it with the data-safe end restart first.
my $out_subdir = ($o or "Output_final");
my $Help = ($h or 0);

push @INC, ".";

use strict;

&print_help if $Help;

my $gitclone = './BATSRUS/share/Scripts/gitclone -s';
# Run in node-local scratch. On Great Lakes /tmp is a private, auto-cleaned
# per-job tmpfs namespace on fast local XFS, so the ~6700 small per-PE plot
# pieces/month and PostProc's merge avoid /nfs/turbo's NFS small-file latency
# (which was ~2/3 of each month's wall time). Output/ stays on the shared FS.
my $local_scratch = $ENV{SLURM_JOB_ID} ? "/tmp" : "/tmp/mswim_$$";
my $rundir = "$local_scratch/run";
my $localout = "$local_scratch/Output";   # node-local PostProc target (same FS as $rundir)
my $output = "./$out_subdir";             # relative label (messages, cleanup from PWD)
my $outroot = "$ENV{PWD}/$out_subdir";    # absolute shared-FS Output (final destination)
my $input  = './Input';
my $earliest_date = 199601;
my $earliest_year = 1996;
# MPI rank count: follow the SLURM allocation when run under sbatch/salloc,
# else default to 8 (interactive / shared-machine smoke).
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
	    if($month >= $start_month and $month <= $end_month){
		push(@months_to_run, sprintf("%04d%02d\n",$year,$month));
	    }
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

# Compile BATSRUS and PIDL; make run directory
# add if statement to download BATSRUS if missing
print "Updating BATSRUS Config.pl...\n";
qx(cd ./BATSRUS; ./Config.pl -noopenmp -u=OuterHelio2d -e=OuterHelio -f -g=10,10,2 -ng=2);
print "Making BATSRUS and PIDL...\n";
qx(cd ./BATSRUS; make -j BATSRUS);
qx(cd ./BATSRUS; make PIDL);
if (-e $rundir and -d $rundir){
    print "Run directory already exists.\n";
}else{
    print "Creating OH run directory...\n";
    qx(cd ./BATSRUS; make rundir RUNDIR=$rundir COMPONENT=OH);
}

# Calculate simulation time from start of interval.
my $end_sim_time = 0;
my @restart_months = ();
foreach my $year ($earliest_year..$start_year)
{
    foreach my $month (1..12)
    {
	if ($year * 100 + $month >= int($earliest_date) and $year * 100 + $month < int($start_date))
	{
	    push(@restart_months, sprintf("%04d%02d\n",$year,$month));
	}
    }
}
foreach my $restart_month (@restart_months)
{
    my $year = int(substr($restart_month,0,4));
    my $month = int(substr($restart_month,4,6));
    $end_sim_time += 28 if $month == 2 and $year % 4;
    $end_sim_time += 29 if $month == 2 and not $year % 4;
    if($month == 4 or $month == 6 or $month == 9 or $month == 11)
    {	
	$end_sim_time += 30;
    }
    elsif($month != 2)
    {
	$end_sim_time += 31;
    }
}


# Run simulation for every month.
my $restart_date = 0;
foreach my $month_string (@months_to_run)
{
    chomp($month_string);   # built with a trailing "\n"; strip it before use in paths
    my $year = int(substr($month_string,0,4));
    my $month = int(substr($month_string,4,6));
    print "Running $year-$month...   ";

    # Clean stale run-directory state from any previous (possibly crashed) month
    # so PostProc never bundles leftover plot frames and restarts are not mixed.
    qx(rm -f $rundir/OH/IO2/* $rundir/OH/restartIN/* $rundir/OH/restartOUT/*);

    # Copy restart files.
    if ($month_string != $earliest_date){
	if($month != 1)
	{
	    $restart_date = sprintf("%04d%02d", $year, $month-1);
	}
	else
	{
	    $restart_date = sprintf("%04d%02d", $year-1, 12);
	}
	qx(cp $outroot/$restart_date/RESTART/OH/restart.H $rundir/restartIN/);
	qx(cp $outroot/$restart_date/RESTART/OH/octree.rst $rundir/restartIN/);
	qx(cp $outroot/$restart_date/RESTART/OH/data.rst $rundir/restartIN/);
    }
    
    # Select correct data files.
    my $StereoA = ($year >= 2007 and $year <= 2025);
    my $StereoB = ($year >= 2007 and $year <= 2014);
    my $SolarOrbiter = ($year >= 2020 and $year <= 2025);
    
    # Unzip the data. Step 2 drives with MIDL (data/L1, 1998-2025), continuing
    # from the OMNI-built restart produced by RunAll_Step1.pl (end of Feb 1998).
    qx(gunzip -c data/L1/l1_$year\.dat > $rundir/L1.dat);
    qx(gunzip -c data/STEREOA/STEREOA_$year\.dat > $rundir/STEREOA.dat) 
	if $StereoA;
    qx(gunzip -c data/STEREOB/STEREOB_$year\.dat > $rundir/STEREOB.dat)
	if $StereoB;
    qx(gunzip -c data/SolarOrbiter/SolarOrbiter_$year\.dat > $rundir/SolarOrbiter.dat)
	if $SolarOrbiter;

    # Update ending simulation time.
    $end_sim_time += 28 if $month == 2 and $year % 4;
    $end_sim_time += 29 if $month == 2 and not $year % 4;
    if($month == 4 or $month == 6 or $month == 9 or $month == 11)
    {	
	$end_sim_time += 30;
    }
    elsif($month != 2)
    {
	$end_sim_time += 31;
    }
    
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

    # Process the results. PostProc's -M *renames* OH/IO2 into the target, which
    # only works within one filesystem -- so collect into the node-local Output
    # (same FS as $rundir), then copy the finished product to the shared FS.
    # (A direct -M to /nfs/turbo fails with "could not rename OH/IO2": cross-
    # device EXDEV.) The copy is one big sequential .outs + a tiny restart, so
    # it's bandwidth-bound and keeps the node-local I/O win.
    qx(mkdir -p $localout);
    qx(rm -rf $localout/$month_string);
    qx(cd $rundir; ./PostProc.pl -M $localout/$month_string);
    qx(rm -rf $outroot/$month_string);
    qx(cp -r $localout/$month_string $outroot/$month_string);
    qx(rm -rf $localout/$month_string);   # free node-local scratch

    # things will be removed by make clean and make cleanall

    print "complete.\n";
}

# This is the run machine: it only produces Output/<YYYYMM>/. Website data
# products (flatten + split) and all post-run analysis are built on the
# analysis machine (solsticedisk), not here.
exit 0;



###############################################################################
sub print_help{
    print "
Options and description for MSWIM2D/Scripts/RunAll.pl

   Execute BATSRUS in stand-alone OH component for 2D outer heliosphere
   runs over the given time interval in yearly increments.

Usage:

   RunAll.pl [-h] [-s=YYYY] [-e=YYYY]
   
   -h -help
                        Display this help message.
   
   -s=YYYY -start=YYYY
                        Start year of the desired run.
   
   -e=YYYY -end=YYYY
                        End year of the desired run (inclusive).
   
Examples:

   RunAll.pl -h
          
         Display this help message.

   RunAll.pl -s=2003 -e=2007

         Complete the 2D outer heliosphere runs in annual increments from
         2003 until 2007, inclusive.

   RunAll.pl -start=2000 -end=2015

         Complete the 2D outer heliosphere runs in annual increments from
         2000 until 2015, inclusive.
\n";
    exit 0;   
}
