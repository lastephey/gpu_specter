This is a README file for the DESI gpu hackathon code updated 02/25/2020.

The answers are wrong and some bookkeeping issues need to be fixed (so it can't be swapped directly into the desi pipeline), but this is good enough to get started for our purposes of moving this to the gpu. 

# For both the cpu and gpu:

To run the both versions on our cori gpu skylakes/v100s (everyone should be able to run, no linux group necessary)

`ssh cori.nersc.gov`

`module load esslurm python cuda/10.1.243`

Cuda must be version 10.1 to be compatible with the latest release of CuPy (also 10.1)

Get a cori gpu node:

`salloc -C gpu -N 1 -t 60 -c 10 --gres=gpu:1 -A m1759`

Source our special desi gpu conda environment:

`source activate /global/cfs/cdirs/m1759/desi/desi_gpu`

Then source the custom desi modules 

`source /global/cfs/cdirs/m1759/desi/desi_libs.sh`

`cd /global/cfs/cdirs/m1759/desi/gpu_specter`

# To run the cpu version (which still inclues mpi):

`time srun -u -n 5 -c 2 python -u cpu_wrapper_specter.py -o test.fits`

This runs in about 3 mins on the skylake using 1/8 of a cpu.

You'll see some mpi warning messages at the end but don't panic, apparently these are `normal` on corigpu for mpi4py

Everything ran correctly if you see all 20 ranks report:

```
INFO:cpu_wrapper_specter.py:351:main: extract:  Done pix-r0-00003578.fits spectra 375:400 at Wed Feb 12 12:41:56 2020
INFO:cpu_wrapper_specter.py:351:main: extract:  Done pix-r0-00003578.fits spectra 275:300 at Wed Feb 12 12:41:56 2020
INFO:cpu_wrapper_specter.py:351:main: extract:  Done pix-r0-00003578.fits spectra 175:200 at Wed Feb 12 12:41:57 2020
INFO:cpu_wrapper_specter.py:351:main: extract:  Done pix-r0-00003578.fits spectra 475:500 at Wed Feb 12 12:41:57 2020
INFO:cpu_wrapper_specter.py:351:main: extract:  Done pix-r0-00003578.fits spectra 75:100 at Wed Feb 12 12:41:58 2020
```

# To run the gpu version (mpi has been removed):

`time srun -u python -u gpu_wrapper_specter.py -o test.fits`

Right now it successfully runs on 1 cori gpu (and 1/8 skylake). The runtime is very long (~5 minutes) because the bundles are currently computed serially. For debugging/profiling we can add `--nspec 50` to process only two bundles, for example. 

We have added a decorator for nvtx profiling: 
```
@nvtx_profile(profile=nvtx_collect,name='function_name')
```

# To profile using nvprof

On cori gpu run nvprof and have it write an output file:

```
srun nvprof --log-file desi_nvprof_02252020.log python -u gpu_wrapper_specter.py -o test.fits --nspec 50 --nwavestep 50
```

# To profile using nsys

On cori gpu run nsys and write .qdrep file, move to laptop for local analysis.

```
srun nsys profile -o desi_nsys_02252020 -t cuda,nvtx --force-overwrite true python -u gpu_wrapper_specter.py -o test.fits --nspec 50 --nwavestep 50
```

# Plans for Hackathon (3/3/2020 - 3/6/2020)

##Goals for the hackathon:

* Pre-hackathon-- get correctness testing in place
* Optimize overall structure of code to fully occupy GPU. Use CUDA/CuPy streams instead of computing bundles serially.
* Get rid of unnecessary HtD and DtH transfer. Understand mystery overhead shown in nsys. May need kernel fusion to prevent CuPy from moving data back to the host. May need to do all memory management manually.
* Overlap data transfer (like at the end of `ex2d_patch`) and compute. Try pinned memory.
* GPU-ize code in ex2d (still largely on CPU).
* Open to other goals too, so please suggest something!


##In the meantime, what can you do?

* Please make sure you can log in to cori/corigpu and are able to run our code!
* Take a look at our hackathon code
* Take a look at Mark's CPU profiling in our dropbox folder
* Please make sure you are familiar with CuPy and Numba CUDA basics
* Try to use nvprof, nsight systems, and nsight compute GPU profiling tools
* Keep an eye on our hackathon README (this page!)-- we'll continue to update it with our progress throughout the week
