"""
Core scaffolding for divide and conquer extraction algorithm
"""

import sys

import numpy as np

try:
    import cupy as cp
except ImportError:
    pass

from gpu_specter.util import get_logger
from gpu_specter.util import get_array_module
from gpu_specter.util import Timer

class Patch(object):
    def __init__(self, ispec, iwave, bspecmin, nspectra_per_patch, nwavestep, wavepad, nwave,
        bundlesize, ndiag):
        """Convenience data wrapper for divide and conquer extraction patches

        Args:
            ispec: starting spectrum index
            iwave: starting wavelength index
            bspecmin: starting spectrum index of the bundle that this patch belongs to
            nspectra_per_patch: number of spectra to extract (not including padding)
            nwavestep: number of wavelengths to extract (not including padding)
            wavepad: number of extra wave bins to extract (and discard) on each end
            nwave: number of wavelength bins in for entire bundle
            bundlesize: size of fiber bundles
            ndiag: number of diagonal elements to keep in the resolution matrix

        All args become attributes.

        Additional attributes created:
            specslice: where this patch goes in the bundle result array
            waveslice: where this patch goes in the bundle result array
            keepslice: wavelength slice to keep from padded patch (the final patch in the bundle
                will be narrower when (nwave % nwavestep) != 0)
        """

        self.ispec = ispec
        self.iwave = iwave

        self.nspectra_per_patch = nspectra_per_patch
        self.nwavestep = nwavestep

        #- padding to apply to patch
        self.wavepad = wavepad

        #- where this patch should go
        #- note: spec indexing is relative to subbundle
        self.bspecmin = bspecmin
        self.specslice = np.s_[ispec-bspecmin:ispec-bspecmin+nspectra_per_patch]
        self.waveslice = np.s_[iwave-wavepad:iwave-wavepad+nwavestep]

        #- how much of the patch to keep
        nwavekeep = min(nwavestep, nwave - (iwave-wavepad))
        self.keepslice = np.s_[0:nwavekeep]

        #- to help with reassembly
        self.nwave = nwave
        self.bundlesize = bundlesize
        self.ndiag = ndiag


def assemble_bundle_patches(rankresults):
    """
    Assembles bundle patches into output arrays

    Args:
        rankresults: list of lists containing individual patch extraction results

    Returns:
        (spexflux, specivar, Rdiags) tuple
    """

    #- flatten list of lists into single list
    allresults = list()
    for rr in rankresults:
        allresults.extend(rr)

    #- peak at result to get bundle params
    patch = allresults[0][0]
    nwave = patch.nwave
    bundlesize = patch.bundlesize
    ndiag = patch.ndiag

    xp = get_array_module(allresults[0][1][0])

    #- Allocate output arrays to fill
    specflux = xp.zeros((bundlesize, nwave))
    specivar = xp.zeros((bundlesize, nwave))
    Rdiags = xp.zeros((bundlesize, 2*ndiag+1, nwave))

    #- Now put these into the final arrays
    for patch, result in allresults:
        fx = result[0]
        fxivar = result[1]
        xRdiags = result[2]

        #- put the extracted patch into the output arrays
        specflux[patch.specslice, patch.waveslice] = fx[:, patch.keepslice]
        specivar[patch.specslice, patch.waveslice] = fxivar[:, patch.keepslice]
        Rdiags[patch.specslice, :, patch.waveslice] = xRdiags[:, :, patch.keepslice]

    return specflux, specivar, Rdiags


def extract_bundle(image, imageivar, psf, wave, fullwave, bspecmin, bundlesize=25, nsubbundles=1,
    nwavestep=50, wavepad=10, comm=None, rank=0, size=1, gpu=None, loglevel=None):
    """
    Extract 1D spectra from a single bundle of a 2D image.

    Args:
        image: full 2D array of image pixels
        imageivar: full 2D array of inverse variance for the image
        psf: dictionary psf object (see gpu_specter.io.read_psf)
        wave: 1D array of wavelengths to extract
        fullwave: Padded 1D array of wavelengths to extract
        bspecmin: index of the first spectrum in the bundle

    Options:
        bundlesize: fixed number of spectra per bundle (25 for DESI)
        nsubbundles: number of spectra per patch
        nwavestep: number of wavelength bins per patch
        wavepad: number of wavelengths bins to add on each end of patch for extraction
        comm: mpi communicator (no mpi: None)
        rank: integer process identifier (no mpi: 0)
        size: number of mpi processes (no mpi: 1)
        gpu: use GPU for extraction (not yet implemented)
        loglevel: log print level

    Returns:
        bundle: (flux, ivar, R) tuple

    """
    timer = Timer()

    log = get_logger(loglevel)

    #- Extracting on CPU or GPU?
    if gpu:
        from gpu_specter.extract.gpu import \
                get_spots, ex2d_padded
    else:
        from gpu_specter.extract.cpu import \
                get_spots, ex2d_padded

    nwave = len(wave)
    ndiag = psf['PSF'].meta['HSIZEY']

    timer.split('init')

    #- Cache PSF spots for all wavelengths for spectra in this bundle
    if gpu:
        cp.cuda.nvtx.RangePush('get_spots')
    spots, corners = get_spots(bspecmin, bundlesize, fullwave, psf)
    if gpu:
        cp.cuda.nvtx.RangePop()

    timer.split('spots/corners')

    #- Size of the individual spots
    spot_nx, spot_ny = spots.shape[2:4]

    #- Organize what sub-bundle patches to extract
    patches = list()
    nspectra_per_patch = bundlesize // nsubbundles
    for ispec in range(bspecmin, bspecmin+bundlesize, nspectra_per_patch):
        for iwave in range(wavepad, wavepad+nwave, nwavestep):
            patch = Patch(ispec, iwave, bspecmin,
                          nspectra_per_patch, nwavestep, wavepad,
                          nwave, bundlesize, ndiag)
            patches.append(patch)

    timer.split('organize patches')

    #- place to keep extraction patch results before assembling in rank 0
    results = list()
    for patch in patches[rank::size]:

        log.debug(f'rank={rank}, ispec={patch.ispec}, iwave={patch.iwave}')

        #- Always extract the same patch size (more efficient for GPU
        #- memory transfer) then decide post-facto whether to keep it all

        if gpu:
            cp.cuda.nvtx.RangePush('ex2d_padded')

        result = ex2d_padded(image, imageivar,
                             patch.ispec-bspecmin, patch.nspectra_per_patch,
                             patch.iwave, patch.nwavestep,
                             spots, corners,
                             wavepad=patch.wavepad,
                             bundlesize=bundlesize)
        if gpu:
            cp.cuda.nvtx.RangePop()

        results.append( (patch, result) )

    timer.split('extracted patches')

    patches = []
    flux = []
    fluxivar = []
    resolution = []
    for patch, results in results:
        patches.append(patch)
        flux.append(results['flux'])
        fluxivar.append(results['ivar'])
        resolution.append(results['Rdiags'])

    def gather_ndarray(sendbuf, comm, rank, root=0):
        sendbuf = np.array(sendbuf)
        shape = sendbuf.shape
        sendbuf = sendbuf.ravel()
        # Collect local array sizes using the high-level mpi4py gather
        sendcounts = np.array(comm.gather(len(sendbuf), root))
        if rank == root:
            recvbuf = np.empty(sum(sendcounts), dtype=sendbuf.dtype)
        else:
            recvbuf = None
        comm.Gatherv(sendbuf=sendbuf, recvbuf=(recvbuf, sendcounts), root=root)
        if rank == root:
            recvbuf = recvbuf.reshape((-1,) + shape[1:])
        return recvbuf

    if comm is not None:
        patches = comm.gather(patches, root=0)
        flux = gather_ndarray(flux, comm, rank)
        fluxivar = gather_ndarray(fluxivar, comm, rank)
        resolution = gather_ndarray(resolution, comm, rank)

        if rank == 0:
            patches = [patch for rankpatches in patches for patch in rankpatches]
            rankresults = [zip(patches, zip(flux, fluxivar, resolution)), ]
    else:
        rankresults = [results,]

    timer.split('gathered patches')

    bundle = None
    if rank == 0:
        if gpu:
            cp.cuda.nvtx.RangePush('assemble patches on device')
            device_id = cp.cuda.runtime.getDevice()
            log.info(f'Rank {rank}: Assembling bundle {bspecmin} patches on device {device_id}')

        bundle = assemble_bundle_patches(rankresults)
        if gpu:
            cp.cuda.nvtx.RangePop()
        timer.split('assembled patches')
        timer.log_splits(log)

        if gpu:
            cp.cuda.nvtx.RangePush('copy bundle results to host')
            device_id = cp.cuda.runtime.getDevice()
            log.info(f'Rank {rank}: Moving bundle {bspecmin} to host from device {device_id}')
            bundle = tuple(cp.asnumpy(x) for x in bundle)
            cp.cuda.nvtx.RangePop()

    return bundle


def extract_frame(imgpixels, imgivar, psf, bundlesize, specmin, nspec, wavelength=None, nwavestep=50, nsubbundles=1,
    comm=None, rank=0, size=1, group_comm=None, group=0, gpu=None, loglevel=None):
    """
    Extract 1D spectra from 2D image.

    Args:
        img: dictionary image object (see gpu_specter.io.read_img)
        psf: dictionary psf object (see gpu_specter.io.read_psf)
        bundlesize: fixed number of spectra per bundle (25 for DESI)
        specmin: index of first spectrum to extract
        nspec: number of spectra to extract

    Options:
        wavelength: wavelength range to extract, formatted as 'wmin,wmax,dw'
        nwavestep: number of wavelength bins per patch
        nsubbundles: number of spectra per patch
        comm: mpi communicator (no mpi: None)
        rank: integer process identifier (no mpi: 0)
        size: number of mpi processes (no mpi: 1)
        gpu: use GPU for extraction (not yet implemented)
        loglevel: log print level

    Returns:
        frame: dictionary frame object (see gpu_specter.io.write_frame)
    """

    timer = Timer()

    log = get_logger(loglevel)

    if wavelength is not None:
        wmin, wmax, dw = map(float, wavelength.split(','))
    else:
        wmin, wmax = psf['PSF'].meta['WAVEMIN'], psf['PSF'].meta['WAVEMAX']
        dw = 0.8

    if rank == 0:
        log.info(f'Extracting wavelengths {wmin},{wmax},{dw}')
    
    #- TODO: calculate this instead of hardcoding it
    wavepad = 10

    #- Wavelength range that we want to extract
    wave = np.arange(wmin, wmax + 0.5*dw, dw)
    nwave = len(wave)
    
    #- Pad that with buffer wavelengths to extract and discard, including an
    #- extra args.nwavestep bins to allow coverage for a final partial bin
    wavelo = np.arange(wavepad)*dw
    wavelo -= (np.max(wavelo)+dw)
    wavelo += wmin
    wavehi = wave[-1] + (1.0+np.arange(wavepad+nwavestep))*dw
    
    fullwave = np.concatenate((wavelo, wave, wavehi))
    assert np.allclose(np.diff(fullwave), dw)
    
    #- TODO: barycentric wavelength corrections

    #- Allocate output arrays to fill
    #- TODO: with multiprocessing, use shared memory?
    ndiag = psf['PSF'].meta['HSIZEY']

    if gpu:
        cp.cuda.nvtx.RangePush('copy imgpixels, imgivar to device')
        device_id = cp.cuda.runtime.getDevice()
        log.info(f'Rank {rank}: Moving image data to device {device_id}')
        imgpixels = cp.asarray(imgpixels)
        imgivar = cp.asarray(imgivar)
        cp.cuda.nvtx.RangePop()

    timer.split('init')

    #- Work bundle by bundle
    bspecmins = list(range(specmin, specmin+nspec, bundlesize))
    #- TODO: fix for gpu/cpu
    bundles = list()

    ngroups = size // group_comm.size
    for bspecmin in bspecmins[group::ngroups]:
        log.info(f'Rank {rank}: Extracting spectra [{bspecmin}:{bspecmin+bundlesize}]')
        sys.stdout.flush()

        timer.split(f'starting bundle {bspecmin}')
        if gpu:
            cp.cuda.nvtx.RangePush('extract_bundle')
        bundle = extract_bundle(
            imgpixels, imgivar, psf,
            wave, fullwave, bspecmin,
            bundlesize=bundlesize, nsubbundles=nsubbundles,
            nwavestep=nwavestep, wavepad=wavepad,
            comm=group_comm, rank=group_comm.rank, size=group_comm.size,
            gpu=gpu
        )
        if gpu:
            cp.cuda.nvtx.RangePop()
        timer.split(f'extracted bundle {bspecmin}')

        bundles.append((bspecmin, bundle))

        #- for good measure, have other ranks wait for rank 0
        if group_comm is not None:
            group_comm.barrier()

    if comm is not None:
        comm_roots = comm.Split(color=group_comm.rank, key=group)
        if group_comm.rank == 0:
            rankbundles = comm_roots.gather(bundles, root=0)
    else:
        rankbundles = [bundles,]

    #- Finalize and write output
    frame = None
    if rank == 0:

        #- flatten list of lists into single list
        allbundles = list()
        for rb in rankbundles:
            allbundles.extend(rb)

        allbundles.sort(key=lambda x: x[0])

        specflux = np.vstack([b[1][0] for b in allbundles])
        specivar = np.vstack([b[1][1] for b in allbundles])
        Rdiags = np.vstack([b[1][2] for b in allbundles])

        timer.split(f'finished all bundles')

        #- Convert flux to photons/A instead of photons/bin
        dwave = np.gradient(wave)
        specflux /= dwave
        specivar *= dwave**2

         #- TODO: specmask and chi2pix
        specmask = (specivar == 0).astype(np.int)
        chi2pix = np.ones(specflux.shape)

        frame = dict(
            specflux = specflux,
            specivar = specivar,
            specmask = specmask,
            wave = wave,
            Rdiags = Rdiags,
            chi2pix = np.ones(specflux.shape),
        )

        timer.split(f'finished frame')
        timer.log_splits(log)

    return frame
