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
from gpu_specter.util import gather_ndarray

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

    xp = get_array_module(allresults[0][1]['flux'])

    #- Allocate output arrays to fill
    specflux = xp.zeros((bundlesize, nwave))
    specivar = xp.zeros((bundlesize, nwave))
    Rdiags = xp.zeros((bundlesize, 2*ndiag+1, nwave))

    #- Now put these into the final arrays
    for patch, result in allresults:
        fx = result['flux']
        fxivar = result['ivar']
        xRdiags = result['Rdiags']

        #- put the extracted patch into the output arrays
        specflux[patch.specslice, patch.waveslice] = fx[:, patch.keepslice]
        specivar[patch.specslice, patch.waveslice] = fxivar[:, patch.keepslice]
        Rdiags[patch.specslice, :, patch.waveslice] = xRdiags[:, :, patch.keepslice]

    return specflux, specivar, Rdiags


def extract_bundle(image, imageivar, psf, wave, fullwave, bspecmin, bundlesize=25, nsubbundles=1,
    nwavestep=50, wavepad=10, comm=None, gpu=None, loglevel=None):
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

    if comm is None:
        rank = 0
        size = 1
    else:
        rank = comm.rank
        size = comm.size

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

    if rank == 0:
        log.info(f'Dividing {len(patches)} patches between {size} ranks')

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

    if comm is not None:
        if gpu:
            # If we have gpu and an MPI comm for this bundle, transfer data
            # back to host before assembling the patches
            patches = []
            flux = []
            fluxivar = []
            resolution = []
            for patch, results in results:
                patches.append(patch)
                flux.append(results['flux'])
                fluxivar.append(results['ivar'])
                resolution.append(results['Rdiags'])

            # transfer to host in 3 chunks
            cp.cuda.nvtx.RangePush('copy bundle results to host')
            device_id = cp.cuda.runtime.getDevice()
            log.info(f'Rank {rank}: Moving bundle {bspecmin} patches to host from device {device_id}')
            flux = cp.asnumpy(cp.array(flux, dtype=cp.float64))
            fluxivar = cp.asnumpy(cp.array(fluxivar, dtype=cp.float64))
            resolution = cp.asnumpy(cp.array(resolution, dtype=cp.float64))
            cp.cuda.nvtx.RangePop()

            # gather to root MPI rank
            patches = comm.gather(patches, root=0)
            flux = gather_ndarray(flux, comm, root=0)
            fluxivar = gather_ndarray(fluxivar, comm, root=0)
            resolution = gather_ndarray(resolution, comm, root=0)

            if rank == 0:
                # unpack patches
                patches = [patch for rankpatches in patches for patch in rankpatches]
                # repack everything
                rankresults = [
                    zip(patches, 
                        map(lambda x: dict(flux=x[0], ivar=x[1], Rdiags=x[2]), 
                            zip(flux, fluxivar, resolution)
                        )
                    )
                ]
        else:
            rankresults = comm.gather(results, root=0)
    else:
        # this is fine for GPU w/out MPI comm
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
            if comm is None:
                cp.cuda.nvtx.RangePush('copy bundle results to host')
                device_id = cp.cuda.runtime.getDevice()
                log.info(f'Rank {rank}: Moving bundle {bspecmin} to host from device {device_id}')
                bundle = tuple(cp.asnumpy(x) for x in bundle)
                cp.cuda.nvtx.RangePop()
        timer.split('assembled patches')
        timer.log_splits(log)
    return bundle


def extract_frame(img, psf, bundlesize, specmin, nspec, wavelength=None, nwavestep=50, nsubbundles=1,
    comm=None, rank=0, size=1, gpu=None, loglevel=None):
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

    #- Determine MPI communication strategy based on number of gpu devices and MPI ranks
    if gpu:
        import cupy as cp
        #- TODO: specify number of gpus to use?
        device_count = cp.cuda.runtime.getDeviceCount()
        assert size % device_count == 0, 'Number of MPI ranks must be divisible by number of GPUs'
        device_id = rank % device_count
        cp.cuda.Device(device_id).use()

        #- Divide mpi ranks evenly among gpus
        device_size = size // device_count
        bundle_rank = rank // device_count

        if device_count > 1:
            #- Multi gpu, MPI communication needs to happen at frame level
            frame_comm = comm.Split(color=bundle_rank, key=device_id)
            if device_size > 1:
                #- If multiple ranks per gpu, also need to communicate at bundle level
                bundle_comm = comm.Split(color=device_id, key=bundle_rank)
            else:
                #- If only one rank per gpu, don't need bundle level communication
                bundle_comm = None
        else:
            #- Single gpu, only do MPI communication at bundle level
            frame_comm = None
            bundle_comm = comm
    else:
        #- No gpu, do MPI communication at bundle level
        frame_comm = None
        bundle_comm = comm

    timer.split('init')

    imgpixels = imgivar = None
    if rank == 0:
        imgpixels = img['image']
        imgivar = img['ivar']

    #- If using MPI, broadcast image, ivar, and psf to all ranks
    if comm is not None:
        if rank == 0:
            log.info('Broadcasting inputs to other MPI ranks')
        imgpixels = comm.bcast(imgpixels, root=0)
        imgivar = comm.bcast(imgivar, root=0)
        psf = comm.bcast(psf, root=0)

    #- If using GPU, move image and ivar to device
    #- TODO: is there a way for ranks to share a pointer to device memory?
    if gpu:
        cp.cuda.nvtx.RangePush('copy imgpixels, imgivar to device')
        device_id = cp.cuda.runtime.getDevice()
        log.info(f'Rank {rank}: Moving image data to device {device_id}')
        imgpixels = cp.asarray(imgpixels)
        imgivar = cp.asarray(imgivar)
        cp.cuda.nvtx.RangePop()

    timer.split('distributed data')

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

    #- Work bundle by bundle
    if frame_comm is None:
        bundle_start = 0
        bundle_step = 1
    else:
        bundle_start = device_id
        bundle_step = device_count
    bspecmins = list(range(specmin, specmin+nspec, bundlesize))
    bundles = list()
    for bspecmin in bspecmins[bundle_start::bundle_step]:
        log.info(f'Rank {rank}: Extracting spectra [{bspecmin}:{bspecmin+bundlesize}]')
        sys.stdout.flush()
        if gpu:
            cp.cuda.nvtx.RangePush('extract_bundle')
        bundle = extract_bundle(
            imgpixels, imgivar, psf,
            wave, fullwave, bspecmin,
            bundlesize=bundlesize, nsubbundles=nsubbundles,
            nwavestep=nwavestep, wavepad=wavepad,
            comm=bundle_comm,
            gpu=gpu
        )
        if gpu:
            cp.cuda.nvtx.RangePop()
        bundles.append((bspecmin, bundle))

        #- for good measure, have other ranks wait for rank 0
        if bundle_comm is not None:
            bundle_comm.barrier()

    timer.split('extracted bundles')

    if frame_comm is not None:
        # gather results from multiple mpi groups
        if bundle_rank == 0:
            bspecmins, bundles = zip(*bundles)
            flux, ivar, resolution = zip(*bundles)
            bspecmins = frame_comm.gather(bspecmins, root=0)
            flux = gather_ndarray(flux, frame_comm)
            ivar = gather_ndarray(ivar, frame_comm)
            resolution = gather_ndarray(resolution, frame_comm)
            if rank == 0:
                bspecmin = [bspecmin for rankbspecmins in bspecmins for bspecmin in rankbspecmins]
                rankbundles = [list(zip(bspecmin, zip(flux, ivar, resolution))), ]
    else:
        # no mpi or single group with all ranks
        rankbundles = [bundles,]

    timer.split('collected data')

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

        timer.split(f'combined data')

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
            imagehdr = img['imagehdr'],
            fibermap = img['fibermap'],
            fibermaphdr =  img['fibermaphdr'],
        )

        timer.split(f'finished frame')
        timer.log_splits(log)

    return frame
