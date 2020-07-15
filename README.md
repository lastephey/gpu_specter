# Python Demo for Allinea/Arm Forge training July 16, 2020

Demo based on spectral extraction code from DESI experiment. 
This is a Python CPU MPI code. The example will run 32 ranks
but can be adjusted.

More information about [DESI](https://www.desi.lbl.gov/).

# Data

Note that real DESI data cannot be shared, so we are using
simulated data for this demo kindly provided by Stephen
Bailey.

The data files are too large to be hosted on github so you
will find them on Cori at `/global/cscratch1/sd/stephey/desi/examples/`.

To begin, log onto Cori and then:

```
cd $SCRATCH
git clone https://github.com/lastephey/gpu_specter
```

Once you have cloned this repo, you'll need to create a folder
called `data` and copy the desi data files into it:

``` 
cd gpu_specter
mkdir data
cd data
cp /global/cscratch1/sd/stephey/desi/examples/*.fits .

```

# To build your conda environment

You will need to build a conda environment to run and profile
this example. These directions will produce an environment with
all required dependencies.

```
module load python
conda create --name armdemo --clone lazy-mpi4py
source activate armdemo
conda install numpy scipy numba cudatoolkit pyyaml astropy
pip install fitsio
pip install speclite
```

Required specs for the conda environment are also available in the
`desi-requirements.txt` file.

# To run at NERSC

First get an interactive node via salloc

```
source run_setup.sh
time srun -n 32 -c 2 spex -i data/preproc-r0-00000020.fits -p data/psfnight-r0-20101020.fits -o blat.fits --mpi
```

# To run with Allinea/Arm Forge Performance Reports

https://docs.nersc.gov/development/performance-debugging-tools/performancereports/

```
module load allinea-forge
perf-report srun -n 32 -c 2 spex -i data/preproc-r0-00000020.fits -p data/psfnight-r0-20101020.fits -o blat.fits --mpi
```

This will write `.txt` and `.html` output files which can be viewed in your
text editor or browser, respectively.


