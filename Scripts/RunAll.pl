#!/usr/bin/perl -s

my $start_date = ($s or $start or "200001");
my $end_date = ($e or $end or "200002");
my $interval = ($i or $interval or "month");
my $Help = ($h or 0);

push @INC, ".";

use strict;

&print_help if $Help;

my $gitclone = './BATSRUS/share/Scripts/gitclone -s';
my $rundir = './BATSRUS/run';
my $output = './Output';
my $input  = './Input';
my $earliest_date = 199803;
my $earliest_year = 1998;
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
qx(cd ./BATSRUS; ./Config.pl -noopenmp -u=OuterHelio2d -e=Mhd -f -g=10,10,2);
print "Making BATSRUS and PIDL...\n";
qx(cd ./BATSRUS; make -j BATSRUS);
qx(cd ./BATSRUS; make PIDL);
if (-e $rundir and -d $rundir){
    print "Run directory already exists.\n";
}else{
    print "Creating OH run directory...\n";
    qx(cd ./BATSRUS; make rundir COMPONENT=OH);
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
    my $year = int(substr($month_string,0,4));
    my $month = int(substr($month_string,4,6));
    print "Running $year-$month...   ";

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
	qx(cp $output/$restart_date/RESTART/OH/restart.H $rundir/restartIN/);
	qx(cp $output/$restart_date/RESTART/OH/octree.rst $rundir/restartIN/);
	qx(cp $output/$restart_date/RESTART/OH/data.rst $rundir/restartIN/);
    }
    
    # Select correct data files.
    my $StereoA = ($year >= 2007 and $year <= 2025);
    my $StereoB = ($year >= 2007 and $year <= 2014);
    my $SolarOrbiter = ($year >= 2022 and $year <= 2025);
    
    # Unzip the data.
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
    my $tmpdir = "$ENV{PWD}/tmp";
    qx(mkdir -p $tmpdir);
    qx(cd $rundir; TMPDIR=$tmpdir mpiexec -n 8 ./BATSRUS.exe > runlog);

    # Process the results.
    qx(rm -rf $output/$month_string);
    qx(cd $rundir; ./PostProc.pl -M ../../$output/$month_string);

    # things will be removed by make clean and make cleanall

    print "complete.\n";
}
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
