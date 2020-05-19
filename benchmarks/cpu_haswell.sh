#!/bin/bash
#SBATCH --nodes=1
#SBATCH --constraint=haswell
#SBATCH --time=15
#SBATCH --qos=debug

echo "Start time: $(date --iso-8601=seconds)"

# Returns the number of seconds since unix epoch
_time() {
    echo $(date +%s.%N)
}

# Returns the elapsed time in seconds between start and end
# setting scale=3 and dividing by 1 to limit to millisecond precision
_elapsed_time () {
    echo "scale=3; ($2 - $1) / 1" | bc
}

# Environement setup
source /global/cfs/cdirs/desi/software/desi_environment.sh master

export PATH=$(pwd)/bin:$PATH
export PYTHONPATH=$(pwd)/py:$PYTHONPATH

# Assemble command with arguments
basedir="/global/cfs/cdirs/desi/spectro/redux/andes"
input="$basedir/preproc/20200219/00051060/preproc-r0-00051060.fits"
psf="$basedir/exposures/20200219/00051060/psf-r0-00051060.fits"
output="$SCRATCH/frame-r0-00051060.fits"
cmd="spex --mpi -w 5761.0,7620.0,0.8 -i $input -p $psf -o $output"

# Perform benchmark
start_time=$(_time)
srun -n 32 -c 2 $cmd
end_time=$(_time)

elapsed_time=$(_elapsed_time ${start_time} ${end_time})

nnodes=1
nframes=1
nodehours=$(echo "$nnodes * $elapsed_time / (60 * 60)" | bc -l)
framespernodehour=$(echo "scale=1; $nframes / $nodehours" | bc)

echo "elapsed time (seconds): $(_elapsed_time ${start_time} ${end_time})"
echo "frames per node hour: ${framespernodehour}"
echo "End time: $(date --iso-8601=seconds)"

