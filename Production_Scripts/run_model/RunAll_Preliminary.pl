#!/usr/bin/perl -s
#
# RunAll_Preliminary.pl -- PRELIMINARY tier driver (writes Output_preliminary/).
#
# Produces the PROVISIONAL months past the data-safe point: continues the same
# real-data-driven simulation from the data-safe end restart (Output_final/<last>,
# the DATA_SAFE month) through the manifest LAST_POSSIBLE month. These months are
# "preliminary" only because fewer sources cover them (STEREO-A plasma ends
# 2025-06-30, so 2025 H2 is L1+SolO and 2026 is L1-only); the physics/inputs are
# otherwise identical to RunAll_Step2_final.pl -- this is literally that driver with
# its output redirected to Output_preliminary/ and a built-in restart seed.
#
# It SEEDS the first month's restart read-only from Output_final/ (the data-safe
# run), so it can be launched standalone without the wrapper pre-seeding. The sim
# clock (earliest_date 199601) stays continuous across the data-safe -> preliminary
# handoff. INPUT is real data/ (L1, STEREOA, SolarOrbiter) exactly as the final run.
#
# Run with:  RunAll_Preliminary.pl -s=202507 -e=202603
# Requires:  Output_final/<start-1month>/RESTART/OH/{restart.H,octree.rst,data.rst}.

my $start_date = ($s or $start or "202507");
my $end_date = ($e or $end or "202603");
my $interval = ($i or $interval or "month");
# Output sub-dir on /nfs/turbo. Defaults to Output_preliminary (the provisional
# beyond-data-safe tail); overridable with -o. Restarts for the first month are
# read from this dir, seeded read-only from Output_final/ below.
my $out_subdir = ($o or "Output_preliminary");
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
my $seedroot = "$ENV{PWD}/Output_final";  # data-safe run, read-only (first-month seed)
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

# Seed the first month's restart from the data-safe run (Output_final), read-only,
# so the preliminary chain continues seamlessly. Only when starting fresh -- a resume
# (start month already has a prior month in $outroot) skips this.
my ($seed_year, $seed_month);
if ($start_month != 1) { $seed_year = $start_year;     $seed_month = $start_month - 1; }
else                   { $seed_year = $start_year - 1; $seed_month = 12; }
my $seed_date = sprintf("%04d%02d", $seed_year, $seed_month);
if (! -e "$outroot/$seed_date/RESTART/OH/restart.H") {
    die "Seed restart not found: $seedroot/$seed_date/RESTART/OH/ -- has the data-safe run produced $seed_date?\n"
	unless -e "$seedroot/$seed_date/RESTART/OH/restart.H";
    print "Seeding $outroot/$seed_date/RESTART/OH from Output_final (read-only copy)...\n";
    qx(mkdir -p $outroot/$seed_date/RESTART/OH);
    qx(cp $seedroot/$seed_date/RESTART/OH/restart.H  $outroot/$seed_date/RESTART/OH/);
    qx(cp $seedroot/$seed_date/RESTART/OH/octree.rst $outroot/$seed_date/RESTART/OH/);
    qx(cp $seedroot/$seed_date/RESTART/OH/data.rst   $outroot/$seed_date/RESTART/OH/);
} else {
    print "Seed restart already present in $outroot/$seed_date.\n";
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
    my $SolarOrbiter = ($year >= 2020 and $year <= 2025);   # 2026 SolO is ~1 day only -> not assimilated (intentional)
    
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
