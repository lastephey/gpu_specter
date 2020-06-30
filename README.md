# Demo for Allinea/Arm Forge training July 16, 2020

Demo based on spectral extraction code from DESI experiment.

More information about [DESI](https://www.desi.lbl.gov/).

# Data

Note that this demo requires DESI data files. Real data cannot be made public
and we are looking for a compatiable set of simulated files. We expect these
files by July 9, 2020 (PI is currently on vacation).

# To build your conda environment

We may provide a pre-built environment that users can clone, but
for the moment these directions will produce an environment with
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

```
source run_setup.sh
srun -n 32 -c 2 spex --mpi -w 5760.0,7620.0,0.8 -i data/preproc-r0-00051060.fits -p data/psf-r0-00051060.fits -o $SCRATCH/frame-r0-00051060.fits
```

# To run with Allinea/Arm Forge Performance Reports

https://docs.nersc.gov/development/performance-debugging-tools/performancereports/

```
module load allinea-reports
perf-report srun -n 32 -c 2 spex --mpi -w 5760.0,7620.0,0.8 -i data/preproc-r0-00051060.fits -p data/psf-r0-00051060.fits -o $SCRATCH/frame-r0-00051060.fits
```

This will write `.txt` and `.html` output files . 
