#!/bin/bash

export PATH=$(pwd)/bin:$PATH
export PYTHONPATH=$(pwd)/py:$PYTHONPATH

#run command
#srun -n 32 -c 2 spex --mpi -w 5760.0,7620.0,0.8 -i data/preproc-r0-00051060.fits -p data/psf-r0-00051060.fits -o $SCRATCH/frame-r0-00051060.fits



