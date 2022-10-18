#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""

Image processing

    Jy_per_pix_to_MJy_per_sr(improve)
    iuncert(improve)
    iptg(improve)
    islice(improve)
    icrop(improve)
    irebin(improve)
    igroupixel(improve)
    ismooth(improve)
    imontage(improve)
        reproject, reproject_mc, coadd, clean
    iswarp(improve):
        footprint, combine, combine_mc, clean
    iconvolve(improve):
        spitzer_irs, choker, do_conv, filenames, clean

    wmask, wclean, interfill 
    concatenate

"""

from tqdm import tqdm, trange
import os
import math
import numpy as np
from scipy.interpolate import interp1d
from astropy import wcs
from astropy.io import ascii
from astropy.table import Table
from reproject import reproject_interp, reproject_exact, reproject_adaptive
from reproject.mosaicking import reproject_and_coadd
import subprocess as SP
import warnings
# warnings.filterwarnings("ignore", category=RuntimeWarning) 
# warnings.filterwarnings("ignore", message="Skipping SYSTEM_VARIABLE record")

## Local
import rapyuta.utbox as UT
import rapyuta.inout as IO
import rapyuta.latte as LA
import rapyuta.maths as MA
from rapyuta.inout import fitsext, csvext, ascext, savext
from .utils import *


##------------------------------------------------
##
##            <improve> based tools
##
##------------------------------------------------

class Jy_per_pix_to_MJy_per_sr(improve):
    '''
    Convert image unit from Jy/pix to MJy/sr

    ------ INPUT ------
    filIN               input FITS file
    filOUT              output FITS file
                          if not None, should be full name with ".fits"!
    ------ OUTPUT ------
    '''
    def __init__(self, filIN=None, header=None, images=None,
                 wave=None, wmod=0, whdr=None,
                 filUNC=None, verbose=False, filext=fitsext,
                 instr=None, instr_auto=True,
                 ## other inputs
                 filOUT=None):
        super().__init__(filIN=filIN, header=header, images=images,
                         wave=wave, wmod=wmod, whdr=whdr,
                         filUNC=filUNC, verbose=verbose, filext=filext,
                         instr=instr, instr_auto=instr_auto)

        ## gmean( Jy/MJy / sr/pix )
        ufactor = np.sqrt(np.prod(1.e-6/MA.pix2sr(1., self.cdelt)))
        self.images = self.images * ufactor
        self.header['BUNIT'] = 'MJy/sr'

        if filOUT is not None:
            IO.write_fits(filOUT=filOUT, header=self.header, data=self.images,
                          wave=self.wave, wmod=self.wmod, filext=self.filext)

class iuncert(improve):
    '''
    Generate uncertainties

    ------ INPUT ------
    filIN               input map (FITS)
    filWGT              input weight map (FITS)
    wfac                multiplication factor for filWGT (Default: 1)
    fill_zeros          value to replace zeros (Default: NaN)
    BG_images           background image array
    BG_weight           background weight array
    filOUT              output uncertainty map (FITS)
    ------ OUTPUT ------
    '''
    def __init__(self, filIN=None, header=None, images=None,
                 wave=None, wmod=0, whdr=None,
                 filUNC=None, verbose=False, filext=fitsext,
                 instr=None, instr_auto=True,
                 ## improve.BGunc param
                 filOUT=None, filWGT=None, wfac=1, fill_zeros=np.nan,
                 BG_images=None, BG_weight=None):
        super().__init__(filIN=filIN, header=header, images=images,
                         wave=wave, wmod=wmod, whdr=whdr,
                         filUNC=filUNC, verbose=verbose, filext=filext,
                         instr=instr, instr_auto=instr_auto)

        self.BGunc(filOUT=filOUT, BG_images=BG_images, fill_zeros=fill_zeros,
                   filWGT=filWGT, wfac=wfac, BG_weight=BG_weight, filext=filext)

class iptg(improve):
    '''
    Add PoinTinG uncertainty to WCS

    ------ INPUT ------
    accrand             pointing accuracy (in arcsec)
    header              baseline
    fill                fill value of no data regions after shift
                          'med': axis median (default)
                          'avg': axis average
                          'near': nearest non-NaN value on the same axis
                          float: constant
    xscale,yscale       regrouped super pixel size
    swarp               use SWarp to perform position shifts
                          Default: False (not support supix)
    ------ OUTPUT ------
    '''
    def __init__(self, filIN=None, header=None, images=None,
                 wave=None, wmod=0, whdr=None,
                 filUNC=None, verbose=False, filext=fitsext,
                 instr=None, instr_auto=True,
                 ## improve.rand_pointing param
                 accrand=0, fill='med',
                 ## other inputs
                 swarp=False, tmpdir=None):
        super().__init__(filIN=filIN, header=header, images=images,
                         wave=wave, wmod=wmod, whdr=whdr,
                         filUNC=filUNC, verbose=verbose, filext=filext,
                         instr=instr, instr_auto=instr_auto)

        oldimage = self.images

        ## Resampling
        if swarp:
            ## Set path of tmp files (SWarp use only)
            path_tmp = UT.maketmp(os.getcwd()+'/tmp_swp')
            ## Works but can be risky since iswarp.combine included rand_pointing...
            IO.write_fits(path_tmp+'tmp_rand_shift',
                          newheader, self.images, self.wave)
            swp = iswarp(refheader=self.header, tmpdir=path_tmp)
            rep = swp.combine(path_tmp+'tmp_rand_shift',
                              combtype='avg', keepedge=True)
            self.images = rep.data
        else:
            self.rand_pointing()

        ## Original NaN mask
        mask_nan = np.isnan(oldimage)
        self.images[mask_nan] = np.nan
        ## Recover new NaN pixels with zeros
        mask_recover = np.logical_and(np.isnan(self.images), ~mask_nan)
        self.images[mask_recover] = 0
        
    return self.images

class islice(improve):
    '''
    Slice a cube

    self: slcnames, slcdir
    ------ INPUT ------
    filIN               input FITS file
    filSLC              ouput path+basename
    slctype             Default: None
                          None - normal slices
                          'inv_sq' - inversed square slices
    postfix             postfix of output slice names
    ------ OUTPUT ------
    '''
    def __init__(self, filIN=None, header=None, images=None,
                 wave=None, wmod=0, whdr=None,
                 filUNC=None, verbose=False, filext=fitsext,
                 instr=None, instr_auto=True,
                 ## improve.slice param
                 filSLC=None, slctype=None, postfix='', randerr=False):
        super().__init__(filIN=filIN, header=header, images=images,
                         wave=wave, wmod=wmod, whdr=whdr,
                         filUNC=filUNC, verbose=verbose, filext=filext,
                         instr=instr, instr_auto=instr_auto)

        if filSLC is None:
            self.slcdir = UT.maketmp()

            filSLC = self.slcdir+'/slice'
        else:
            self.slcdir = UT.maketmp(filSLC)
        self.filSLC = filSLC

        if randerr:
            if len(filUNC)==1:
                self.rand_norm()
            elif len(filUNC)==2:
                self.rand_splitnorm()

        if slctype=='inv_sq':
            self.slcnames = self.slice_inv_sq(filSLC, postfix=postfix, filext=filext)
        else:
            self.slcnames = self.slice(filSLC, postfix=postfix, filext=filext)

class icrop(improve):
    '''
    CROP 2D image or 3D cube
    '''
    def __init__(self, filIN=None, header=None, images=None,
                 wave=None, wmod=0, whdr=None,
                 filUNC=None, verbose=False, filext=fitsext,
                 instr=None, instr_auto=True,
                 ## improve.crop param
                 filOUT=None, sizpix=None, cenpix=None,
                 sizval=None, cenval=None, randerr=False):
        ## slicrop: slice 
        super().__init__(filIN=filIN, header=header, images=images,
                         wave=wave, wmod=wmod, whdr=whdr,
                         filUNC=filUNC, verbose=verbose, filext=filext,
                         instr=instr, instr_auto=instr_auto)
        
        if randerr:
            if len(filUNC)==1:
                self.rand_norm()
            elif len(filUNC)==2:
                self.rand_splitnorm()
        
        self.newimage = self.crop(filOUT=filOUT, sizpix=sizpix, cenpix=cenpix,
                                  sizval=sizval, cenval=cenval)

class irebin(improve):
    '''
    REBIN 2D image or 3D cube
    '''
    def __init__(self, filIN=None, header=None, images=None,
                 wave=None, wmod=0, whdr=None,
                 filUNC=None, verbose=False, filext=fitsext,
                 instr=None, instr_auto=True,
                 ## improve.rebin param
                 filOUT=None, pixscale=None, total=False, extrapol=False):
        super().__init__(filIN=filIN, header=header, images=images,
                         wave=wave, wmod=wmod, whdr=whdr,
                         filUNC=filUNC, verbose=verbose, filext=filext,
                         instr=instr, instr_auto=instr_auto)
        
        if len(filUNC)==1:
            self.rand_norm()
        elif len(filUNC)==2:
            self.rand_splitnorm()

        im_rebin = self.rebin(filOUT=filOUT, pixscale=pixscale,
                              total=total, extrapol=extrapol)

class igroupixel(improve):
    '''
    GROUP a cluster of PIXELs (with their mean value)
    '''
    def __init__(self, filIN=None, header=None, images=None,
                 wave=None, wmod=0, whdr=None,
                 filUNC=None, verbose=False, filext=fitsext,
                 instr=None, instr_auto=True,
                 ## improve.groupixel param
                 filOUT=None, xscale=1, yscale=1):
        super().__init__(filIN=filIN, header=header, images=images,
                         wave=wave, wmod=wmod, whdr=whdr,
                         filUNC=filUNC, verbose=verbose, filext=filext,
                         instr=instr, instr_auto=instr_auto)

        im_grp = self.groupixel(xscale=xscale, yscale=yscale, filOUT=filOUT)
    
class ismooth(improve):
    '''
    SMOOTH wavelengths
    '''
    def __init__(self, filIN=None, header=None, images=None,
                 wave=None, wmod=0, whdr=None,
                 filUNC=None, verbose=False, filext=fitsext,
                 instr=None, instr_auto=True,
                 ## imporve.smooth param
                 filOUT=None, smooth=1, wgrid=None, wstart=None):
        super().__init__(filIN=filIN, header=header, images=images,
                         wave=wave, wmod=wmod, whdr=whdr,
                         filUNC=filUNC, verbose=verbose, filext=filext,
                         instr=instr, instr_auto=instr_auto)

        im_smooth = self.smooth(smooth=smooth, filOUT=filOUT,
                                wgrid=wgrid, wstart=wstart)

class imontage(improve):
    '''
    2D image or 3D cube montage toolkit
    Based on reproject v0.7.1 or later

    ------ INPUT ------
    reproject_function  resampling algorithms
                          'interp': fastest (Default)
                          'exact': slowest
                          'adaptive': DeForest2004
    tmpdir              tmp file path
    verbose             (Default: False)
    ------ OUTPUT ------
    '''
    def __init__(self, reproject_function='interp',
                 tmpdir=None, verbose=False):
        '''
        self: func, path_tmp, verbose
        '''
        if reproject_function=='interp':
            self.func = reproject_interp
        elif reproject_function=='exact':
            self.func = reproject_exact
        elif reproject_function=='adaptive':
            self.func = reproject_adaptive
        else:
            UT.strike('imontage', 'unknown reprojection algorithm.',
                      cat='InputError')
        
        ## Set path of tmp files
        if tmpdir is None:
            path_tmp = os.getcwd()+'/tmp_mtg/'
        else:
            path_tmp = tmpdir
        if not os.path.exists(path_tmp):
            os.makedirs(path_tmp)
        self.path_tmp = path_tmp

        ## Verbose
        if verbose==False:
            devnull = open(os.devnull, 'w')
        else:
            devnull = None
        self.verbose = verbose
        self.devnull = devnull
    
    def reproject(self, flist, refheader, filOUT=None,
                  dist=None, acc_ptg=0, fill_ptg='near') :
        '''
        Reproject 2D image or 3D cube

        ------ INPUT ------
        flist               FITS files to reproject
        refheader           reprojection header
        filOUT              output FITS file
        acc_ptg             pointing accuracy in arcsec (Default: 0)
        fill_ptg            fill value of no data regions after shift
                              'med': axis median
                              'avg': axis average
                              'near': nearest non-NaN value on the same axis (default)
                              float: constant
        ------ OUTPUT ------
        newimage            reprojected images
        '''
        flist = LA.listize(flist)

        newimage = []
        for fname in flist:
            ## Set tmp and out
            filename = os.path.basename(fname)
            if filOUT is None:
                filOUT = self.path_tmp+filename+'_rep'

            super().__init__(fname)
            
            ## Uncertainty propagation
            if dist=='norm':
                self.rand_norm()
            elif dist=='splitnorm':
                self.reinit(fname,
                            filUNC=[fname+'_unc_N', fname+'_unc_P'])
                self.rand_splitnorm()
            self.rand_pointing(acc_ptg, fill=fill_ptg)
            IO.write_fits(filOUT, self.header, self.images, self.wave, wmod=0)
            
            ## Do reprojection
            ##-----------------
            im = self.func(filOUT+fitsext, refheader)[0]
            newimage.append(im)
    
            comment = "Reprojected by <imontage>. "
            IO.write_fits(filOUT, refheader, im, self.wave, wmod=0,
                       COMMENT=comment)
        
        return newimage

    def reproject_mc(self, filIN, refheader, filOUT=None,
                     dist=None, acc_ptg=0, fill_ptg='near', Nmc=0):
        '''
        Generate Monte-Carlo uncertainties for reprojected input file
        '''
        ds = type('', (), {})()

        hyperim = [] # [j,(w,)y,x]
        for j in trange(Nmc+1, leave=False,
                        desc='<imontage> Reprojection [MC]'):

            if j==0:
                im0 = self.reproject(filIN, refheader, filOUT)[0]
            else:
                hyperim.append( self.reproject(filIN, refheader, filOUT+'_'+str(j),
                                               dist, acc_ptg, fill_ptg)[0] )
        im0 = np.array(im0)
        hyperim = np.array(hyperim)
        unc = np.nanstd(hyperim, axis=0)
        comment = "Reprojected by <imontage>. "

        if Nmc>0:
            IO.write_fits(filOUT+'_unc', refheader, unc, self.wave,
                       COMMENT=comment)

        ds.data = im0
        ds.unc = unc
        ds.hyperdata = hyperim

        return ds

    def coadd(self, flist, refheader, filOUT=None,
              dist=None, acc_ptg=0, fill_ptg='near', Nmc=0):
        '''
        Reproject and coadd
        '''
        flist = LA.listize(flist)
        ds = type('', (), {})()
        comment = "Created by <imontage>"

        slist = [] # slist[j,if,iw]
        for j in trange(Nmc+1, leave=False,
                        desc='<imontage> Slicing... [MC]'):
            sl = [] # sl[f,w]
            for f in flist:
                super().__init__(f)

                ## Set tmp and out
                filename = os.path.basename(f)
                if filOUT is None:
                    filOUT = self.path_tmp+filename+'_rep'
                        
                coadd_tmp = self.path_tmp+filename+'/'
                if not os.path.exists(coadd_tmp):
                    os.makedirs(coadd_tmp)
                        
                if j==0:
                    sl.append(self.slice(coadd_tmp+'slice', ext=fitsext))
                else:
                    if dist=='norm':
                        self.rand_norm(f+'_unc')
                    elif dist=='splitnorm':
                        self.rand_splitnorm([f+'_unc_N', f+'_unc_P'])
                    self.rand_pointing(acc_ptg, fill=fill_ptg)
                        
                    sl.append(self.slice(coadd_tmp+'slice',
                                         postfix='_'+str(j), ext=fitsext))
            slist.append(np.array(sl))
        slist = np.array(slist)
        
        Nw = self.Nw
        superim = []
        for j in trange(Nmc+1, leave=False,
                        desc='<imontage> Coadding... [MC]'):
            if j==0:
                im = []
                if self.Ndim==3:
                    for iw in range(Nw):
                        im.append(reproject_and_coadd(slist[j,:,iw], refheader,
                                                      reproject_function=self.func)[0])
                elif self.Ndim==2:
                    im = reproject_and_coadd(slist[j,:,0], refheader,
                                             reproject_function=self.func)[0]
                im = np.array(im)

                IO.write_fits(filOUT, refheader, im, self.wave, wmod=0,
                           COMMENT=comment)
            else:
                hyperim = []
                for iw in range(Nw):
                    hyperim.append(reproject_and_coadd(slist[j,:,iw], refheader,
                                                       reproject_function=self.func)[0])
                superim.append(np.array(hyperim))

                IO.write_fits(filOUT+'_'+str(j), refheader, hyperim, self.wave, wmod=0,
                           COMMENT=comment)
        superim = np.array(superim)
        unc = np.nanstd(superim, axis=0)

        if Nmc>0:
            IO.write_fits(filOUT+'_unc', refheader, unc, self.wave, wmod=0,
                       COMMENT=comment)

        ds.wave = self.wave
        ds.data = im
        ds.unc = unc
        ds.hyperdata = superim
        
        return ds

    def clean(self, filIN=None):
        if filIN is not None:
            fclean(filIN)
        else:
            fclean(self.path_tmp)

class iswarp(improve):
    '''
    SWarp drop-in image montage toolkit
    i means <improve>-based
    Alternative to its fully Python-based twin <imontage>

    ------ INPUT ------
    flist               ref FITS files used to make header (footprint)
    refheader           scaling matrix adopted if co-exist with file
    center              center of output image frame
                          None - contains all input fields
                          str('hh:mm:ss,dd:mm:ss') - manual input RA,DEC
    pixscale            pixel scale (arcsec)
                          None - median of pixscale at center input frames
                          float() - in arcseconds
    verbose             default: True
    tmpdir              tmp file path
    ------ OUTPUT ------
    coadd.fits
    
    By default, SWarp reprojects all input to a WCS with diag CD matrix.
    "To implement the unusual output features required, 
     one must write a coadd.head ASCII file that contains 
     a custom anisotropic scaling matrix. "
    '''
    def __init__(self, flist=None, refheader=None,
                 center=None, pixscale=None, 
                 verbose=False, tmpdir=None):
        '''
        self: path_tmp, verbose
        (filIN, wmod, hdr, w, Ndim, Nx, Ny, Nw, im, wvl)
        '''
        if verbose==False:
            devnull = open(os.devnull, 'w')
        else:
            devnull = None
        self.verbose = verbose
        self.devnull = devnull
        
        ## Set path of tmp files
        if tmpdir is None:
            path_tmp = os.getcwd()+'/tmp_swp/'
        else:
            path_tmp = tmpdir
        if not os.path.exists(path_tmp):
            os.makedirs(path_tmp)
        
        self.path_tmp = path_tmp

        fclean(path_tmp+'coadd*') # remove previous coadd.fits/.head

        if flist is None:
            if refheader is None:
                UT.strike('iswarp', 'no input.', cat='InputError')
            
            ## Define coadd frame via refheader
            else:
                if center is not None or pixscale is not None:
                    warnings.warn('The keywords center and pixscale are dumb.')

                self.refheader = refheader
        else:
            ## Input files in list object
            flist = LA.listize(flist)
                
            ## Images
            image_files = ' '
            list_ref = []
            for i in range(len(flist)):
                image = IO.read_fits(flist[i]).data
                hdr = fixwcs(flist[i]+fitsext).header
                file_ref = flist[i]
                if image.ndim==3:
                    ## Extract 1st frame of the cube
                    file_ref = path_tmp+os.path.basename(flist[i])+'_ref'
                    IO.write_fits(file_ref, hdr, image[0])
                
                image_files += file_ref+fitsext+' ' # SWarp input str
                list_ref.append(file_ref+fitsext) # reproject input

            ## Define coadd frame
            ##--------------------
            
            ## via SWarp without refheader (isotropic scaling matrix)
            
            ## Create config file
            SP.call('swarp -d > swarp.cfg',
                    shell=True, cwd=path_tmp, stdout=devnull, stderr=SP.STDOUT)
            
            ## Config param list
            swarp_opt = ' -c swarp.cfg -SUBTRACT_BACK N -IMAGEOUT_NAME coadd.ref.fits '
            if center is not None:
                swarp_opt += ' -CENTER_TYPE MANUAL -CENTER '+center
            if pixscale is not None:
                swarp_opt += ' -PIXELSCALE_TYPE MANUAL -PIXEL_SCALE '+str(pixscale)
            if verbose=='quiet':
                swarp_opt += ' -VERBOSE_TYPE QUIET '
            
            ## Run SWarp
            SP.call('swarp '+swarp_opt+image_files,
                    shell=True, cwd=path_tmp, stdout=devnull, stderr=SP.STDOUT)

            self.refheader = IO.read_fits(path_tmp+'coadd.ref').header
            
            ## via reproject with refheader (custom anisotropic scaling matrix)
            if refheader is not None:
                if center is not None or pixscale is not None:
                    warnings.warn('The keywords center and pixscale are dumb. ')

                super().__init__(path_tmp+'coadd.ref')
                pix_old = [[0, 0]]
                pix_old.append([0, self.Ny])
                pix_old.append([self.Nx, 0])
                pix_old.append([self.Nx, self.Ny])
                world_arr = self.w.all_pix2world(np.array(pix_old), 1)
                
                w = fixwcs(header=refheader).wcs
                try:
                    pix_new = w.all_world2pix(world_arr, 1)
                except wcs.wcs.NoConvergence as e:
                    pix_new = e.best_solution
                    print("Best solution:\n{0}".format(e.best_solution))
                    print("Achieved accuracy:\n{0}".format(e.accuracy))
                    print("Number of iterations:\n{0}".format(e.niter))
                xmin = min(pix_new[:,0])
                xmax = max(pix_new[:,0])
                ymin = min(pix_new[:,1])
                ymax = max(pix_new[:,1])

                refheader['CRPIX1'] += -xmin
                refheader['CRPIX2'] += -ymin
                refheader['NAXIS1'] = math.ceil(xmax - xmin)
                refheader['NAXIS2'] = math.ceil(ymax - ymin)
                
                self.refheader = refheader

        # fclean(path_tmp+'*ref.fits')

    def footprint(self, filOUT=None):
        '''
        Save reprojection footprint
        '''
        if filOUT is None:
            filOUT = self.path_tmp+'footprint'
        
        Nx = self.refheader['NAXIS1']
        Ny = self.refheader['NAXIS2']
        im_fp = np.ones((Ny, Nx))
        
        comment = "<iswarp> footprint"
        IO.write_fits(filOUT, self.refheader, im_fp, COMMENT=comment)

        return im_fp

    def combine(self, flist, combtype='med', keepedge=False, cropedge=False,
                dist=None, acc_ptg=0, fill_ptg='near', filOUT=None, tmpdir=None):
        '''
        SWarp combine (coadding/reprojection)

        ------ INPUT ------
        flist               input FITS files should have the same wvl
        combtype            combine type
                              'med' - median (default)
                              'avg' - average
                              'wgt_avg' - inverse variance weighted average
        keepedge            default: False
        cropedge            crop the NaN edge of the frame (Default: False)
        dist                add uncertainties (filename+'_unc.fits' needed)
        acc_ptg              pointing accuracy in arcsec (Default: 0)
        fill_ptg             fill value of no data regions after shift
                              'med': axis median
                              'avg': axis average
                              'near': nearest non-NaN value on the same axis (default)
                              float: constant
        filOUT              output FITS file
        ------ OUTPUT ------
        coadd.head          key for SWarp (inherit self.refheader)
        '''
        ds = type('', (), {})()
        
        verbose = self.verbose
        devnull = self.devnull
        path_tmp = self.path_tmp
        
        if tmpdir is None:
            path_comb = path_tmp+'comb/'
        else:
            path_comb = tmpdir
        if not os.path.exists(path_comb):
            os.makedirs(path_comb)

        ## Input files in list format
        flist = LA.listize(flist)
        
        ## Header
        ##--------
        with open(path_tmp+'coadd.head', 'w') as f:
            f.write(str(self.refheader))

        ## Images and weights
        ##--------------------
        Nf = len(flist)
        
        imshape = IO.read_fits(flist[0]).data.shape
        if len(imshape)==3:
            Nw = imshape[0]
            wvl = IO.read_fits(flist[0]).wave
        else:
            Nw = 1
            wvl = None
        
        ## Build imlist & wgtlist (size=Nf)
        imlist = []
        wgtlist = []
        for i in range(Nf):
            filename = os.path.basename(flist[i])
            ## Set slice file
            file_slice = path_comb+filename
            
            ## Slice
            super().__init__(flist[i])
            if dist=='norm':
                self.rand_norm(flist[i]+'_unc')
            elif dist=='splitnorm':
                self.rand_splitnorm([flist[i]+'_unc_N', flist[i]+'_unc_P'])
            self.rand_pointing(acc_ptg, fill=fill_ptg)
            imlist.append(self.slice(file_slice, ''))
            
            if combtype=='wgt_avg':
                super().__init__(flist[i]+'_unc')
                wgtlist.append(self.slice_inv_sq(file_slice, '.weight'))

        ## Build image_files & weight_files (size=Nw)
        image_files = [' ']*Nw
        weight_files = [' ']*Nw

        ## Let's SWarp
        ##-------------
        hyperimage = []
        for k in trange(Nw, leave=False, 
            desc='<iswarp> Combining (by wvl)'):
            for i in range(Nf):
                image_files[k] += imlist[i][k]+fitsext+' '

                if combtype=='wgt_avg':
                    weight_files[k] += wgtlist[i][k]+fitsext+' '

            ## Create config file
            SP.call('swarp -d > swarp.cfg',
                    shell=True, cwd=path_tmp, stdout=devnull, stderr=SP.STDOUT)
            ## Config param list
            swarp_opt = ' -c swarp.cfg -SUBTRACT_BACK N '
            if combtype=='med':
                pass
            elif combtype=='avg':
                swarp_opt += ' -COMBINE_TYPE AVERAGE '
            elif combtype=='wgt_avg':
                swarp_opt += ' -COMBINE_TYPE WEIGHTED '
                swarp_opt += ' -WEIGHT_TYPE MAP_WEIGHT '
                swarp_opt += ' -WEIGHT_SUFFIX .weight.fits '
                # swarp_opt += ' -WEIGHT_IMAGE '+weight_files[k] # not worked
            if verbose=='quiet':
                swarp_opt += ' -VERBOSE_TYPE QUIET '
            ## Run SWarp
            SP.call('swarp '+swarp_opt+' -RESAMPLING_TYPE LANCZOS3 '+image_files[k],
                    shell=True, cwd=path_tmp, stdout=devnull, stderr=SP.STDOUT)
            coadd = IO.read_fits(path_tmp+'coadd')
            newimage = coadd.data
            newheader = coadd.header

            ## Add back in the edges because LANCZOS3 kills the edges
            ## Do it in steps of less and less precision
            if keepedge==True:
                oldweight = IO.read_fits(path_tmp+'coadd.weight').data
                if np.sum(oldweight==0)!=0:
                    SP.call('swarp '+swarp_opt+' -RESAMPLING_TYPE LANCZOS2 '+image_files[k],
                        shell=True, cwd=path_tmp, stdout=devnull, stderr=SP.STDOUT)
                    edgeimage = IO.read_fits(path_tmp+'coadd').data
                    newweight = IO.read_fits(path_tmp+'coadd.weight').data
                    edgeidx = np.logical_and(oldweight==0, newweight!=0)
                    if edgeidx.any():
                        newimage[edgeidx] = edgeimage[edgeidx]

                    oldweight = IO.read_fits(path_tmp+'coadd.weight').data
                    if np.sum(oldweight==0)!=0:
                        SP.call('swarp '+swarp_opt+' -RESAMPLING_TYPE BILINEAR '+image_files[k],
                            shell=True, cwd=path_tmp, stdout=devnull, stderr=SP.STDOUT)
                        edgeimage = IO.read_fits(path_tmp+'coadd').data
                        newweight = IO.read_fits(path_tmp+'coadd.weight').data
                        edgeidx = np.logical_and(oldweight==0, newweight!=0)
                        if edgeidx.any():
                            newimage[edgeidx] = edgeimage[edgeidx]

                        oldweight = IO.read_fits(path_tmp+'coadd.weight').data
                        if np.sum(oldweight==0)!=0:
                            SP.call('swarp '+swarp_opt+' -RESAMPLING_TYPE NEAREST '+image_files[k],
                                shell=True, cwd=path_tmp, stdout=devnull, stderr=SP.STDOUT)
                            edgeimage = IO.read_fits(path_tmp+'coadd').data
                            newweight = IO.read_fits(path_tmp+'coadd.weight').data
                            edgeidx = np.logical_and(oldweight==0, newweight!=0)
                            if edgeidx.any():
                                newimage[edgeidx] = edgeimage[edgeidx]
            
            ## Astrometric flux-rescaling based on the local ratio of pixel scale
            ## Complementary for lack of FITS kw 'FLXSCALE'
            ## Because SWarp is conserving surface brightness/pixel
            oldcdelt = get_pc(wcs=fixwcs(flist[i]+fitsext).wcs).cdelt
            newcdelt = get_pc(wcs=fixwcs(path_tmp+'coadd'+fitsext).wcs).cdelt
            old_pixel_fov = abs(oldcdelt[0]*oldcdelt[1])
            new_pixel_fov = abs(newcdelt[0]*newcdelt[1])
            newimage = newimage * old_pixel_fov/new_pixel_fov
            newimage[newimage==0] = np.nan
            # IO.write_fits(path_comb+'coadd_'+str(k), newheader, newimage)
            # tqdm.write(str(old_pixel_fov))
            # tqdm.write(str(new_pixel_fov))
            # tqdm.write(str(abs(newheader['CD1_1']*newheader['CD2_2'])))

            if Nw==1:
                hyperimage = newimage
            else:
                hyperimage.append(newimage)

        hyperimage = np.array(hyperimage)

        if cropedge:
            reframe = improve(header=newheader, images=hyperimage, wave=wvl)
            xlist = []
            for x in range(reframe.Nx):
                if reframe.Ndim==3:
                    allnan = np.isnan(reframe.im[:,:,x]).all()
                elif reframe.Ndim==2:
                    allnan = np.isnan(reframe.im[:,x]).all()
                if not allnan:
                    xlist.append(x)
            ylist = []
            for y in range(reframe.Ny):
                if reframe.Ndim==3:
                    allnan = np.isnan(reframe.im[:,y,:]).all()
                elif reframe.Ndim==2:
                    allnan = np.isnan(reframe.im[y,:]).all()
                if not allnan:
                    ylist.append(y)
            xmin = min(xlist)
            xmax = max(xlist)+1
            ymin = min(ylist)
            ymax = max(ylist)+1
            dx = xmax-xmin
            dy = ymax-ymin
            x0 = xmin+dx/2
            y0 = ymin+dy/2

            reframe.crop(filOUT=path_tmp+'coadd.ref',
                         sizpix=(dx,dy), cenpix=(x0,y0))
            newheader = reframe.hdr
            hyperimage = reframe.im
            cropcenter = (x0,y0)
            cropsize = (dx,dy)
        else:
            cropcenter = None
            cropsize = None
            
        if filOUT is not None:
            IO.write_fits(filOUT, header=newheader, data=hyperimage,
                       wave=wvl, wmod=self.wmod, filext=self.filext)

        if tmpdir is None:
            fclean(path_comb)

        ds.header = newheader
        ds.data = hyperimage
        ds.wave = wvl
        ds.cropcenter = cropcenter
        ds.cropsize = cropsize

        return ds

    def combine_mc(self, filIN, Nmc=0,
                   combtype='med', keepedge=False, cropedge=False,
                   dist=None, acc_ptg=0, fill_ptg='near',
                   filOUT=None, tmpdir=None):
        '''
        Generate Monte-Carlo uncertainties for reprojected input file
        '''
        ds = type('', (), {})()

        hyperim = [] # [j,(w,)y,x]
        for j in trange(Nmc+1, leave=False,
                        desc='<iswarp> Reprojection (MC level)'):

            if j==0:
                comb = self.combine(filIN, filOUT=filOUT, tmpdir=tmpdir,
                                    combtype=combtype, keepedge=keepedge, cropedge=cropedge)
                im0 = comb.data
            else:
                hyperim.append( self.combine(filIN, filOUT=filOUT+'_'+str(j),
                                             tmpdir=tmpdir, combtype=combtype,
                                             keepedge=keepedge, cropedge=cropedge,
                                             dist=dist, acc_ptg=acc_ptg, fill_ptg=fill_ptg).data )
        im0 = np.array(im0)
        hyperim = np.array(hyperim)
        unc = np.nanstd(hyperim, axis=0)
        comment = "Created by <iswarp>"

        if Nmc>0:
            IO.write_fits(filOUT+'_unc', comb.header, unc, comb.wave,
                       COMMENT=comment)

        ds.data = im0
        ds.unc = unc
        ds.hyperdata = hyperim

        return ds
    
    def clean(self, filIN=None):
        if filIN is not None:
            fclean(filIN)
        else:
            fclean(self.path_tmp)

class iconvolve(improve):
    '''
    Convolve 2D image or 3D cube with given kernels
    i means <improve>-based or IDL-based

    ------ INPUT ------
    filIN               input FITS file
    kfile               convolution kernel(s) (tuple or list)
    klist               CSV file storing kernel names
    dist                uncertainty distribution
                          'norm' - N(0,1)
                          'splitnorm' - SN(0,lam,lam*tau)
    acc_ptg              pointing accuracy in arcsec (Default: 0)
    fill_ptg             fill value of no data regions after shift
                          'med': axis median
                          'avg': axis average
                          'near': nearest non-NaN value on the same axis (default)
                          float: constant
    psf                 list of PSF's FWHM (should be coherent with kfile!!!)
    convdir             do_conv path (Default: None -> filIN path)
    filOUT              output file
    ------ OUTPUT ------
    '''
    def __init__(self, filIN=None, header=None, images=None,
                 wave=None, wmod=0, whdr=None,
                 filUNC=None, verbose=False, filext=fitsext,
                 instr=None, instr_auto=True,
                 ## other inputs
                 kfile=None, klist=None,
                 dist=None, acc_ptg=0, fill_ptg='near',
                 psf=None, convdir=None, filOUT=None):
        ## INPUTS
        super().__init__(filIN=filIN, header=header, images=images,
                         wave=wave, wmod=wmod, whdr=whdr,
                         filUNC=filUNC, verbose=verbose, filext=filext,
                         instr=instr, instr_auto=instr_auto)
        
        if dist=='norm':
            self.rand_norm(filIN+'_unc')
        elif dist=='splitnorm':
            self.rand_splitnorm(filIN+'_unc')
        self.rand_pointing(acc_ptg, fill=fill_ptg)

        ## Input kernel file in list format
        self.kfile = LA.listize(kfile)

        ## doc (csv) file of kernel list
        self.klist = klist
        self.path_conv = convdir
        self.filOUT = filOUT

        ## Init
        self.psf = psf
        self.fwhm_lam = None
        self.sigma_lam = None
        
    def spitzer_irs(self):
        '''
        Spitzer/IRS PSF profil
        [REF]
        Pereira-Santaella, Miguel, Almudena Alonso-Herrero, George H.
        Rieke, Luis Colina, Tanio Díaz-Santos, J.-D. T. Smith, Pablo G.
        Pérez-González, and Charles W. Engelbracht. “Local Luminous
        Infrared Galaxies. I. Spatially Resolved Observations with the
        Spitzer Infrared Spectrograph.” The Astrophysical Journal
        Supplement Series 188, no. 2 (June 1, 2010): 447.
        doi:10.1088/0067-0049/188/2/447.
        https://iopscience.iop.org/article/10.1088/0067-0049/188/2/447/pdf
        '''
        sim_par_wave = [0, 13.25, 40.]
        sim_par_fwhm = [2.8, 3.26, 10.1]
        sim_per_wave = [0, 15.5, 40.]
        sim_per_fwhm = [3.8, 3.8, 10.1]
        
        ## fwhm (arcsec)
        fwhm_par = np.interp(self.wave, sim_par_wave, sim_par_fwhm)
        fwhm_per = np.interp(self.wave, sim_per_wave, sim_per_fwhm)
        self.fwhm_lam = np.sqrt(fwhm_par * fwhm_per)
        
        ## sigma (arcsec)
        sigma_par = fwhm_par / (2. * np.sqrt(2.*np.log(2.)))
        sigma_per = fwhm_per / (2. * np.sqrt(2.*np.log(2.)))
        self.sigma_lam = np.sqrt(sigma_par * sigma_per)

    # def choker(self, flist):
    #     '''
    #     ------ INPUT ------
    #     flist               FITS files to be convolved
    #     ------ OUTPUT ------
    #     '''
    #     ## Input files in list format
    #     flist = LA.listize(flist)
        
    #     ## CHOose KERnel(s)
    #     lst = []
    #     for i, image in enumerate(flist):
    #         ## check PSF profil (or is not a cube)
    #         if self.sigma_lam is not None:
    #             image = flist[i]
    #             ind = LA.closest(self.psf, self.sigma_lam[i])
    #             kernel = self.kfile[ind]
    #         else:
    #             image = flist[0]
    #             kernel = self.kfile[0]
    #         ## lst line elements: image, kernel
    #         k = [image, kernel]
    #         lst.append(k)

    #     ## write csv file
    #     IO.write_csv(self.klist, header=['Images', 'Kernels'], dset=lst)

    def choker(self, flist):
        '''
        ------ INPUT ------
        flist               FITS files to be convolved
        ------ OUTPUT ------
        '''
        ## Input files in list format
        flist = LA.listize(flist)
        
        ## CHOose KERnel(s)
        images = []
        kernels = []
        for i, filim in enumerate(flist):
            ## check PSF profil (or is not a cube)
            if self.fwhm_lam is not None:
                images.append(filim)
                ind = LA.closest(self.psf, self.fwhm_lam[i])
                # print('ind = ',ind)
                # print('psf = ',self.psf[ind])
                # print('kfile = ',self.kfile[ind])
                kernels.append(self.kfile[ind])
            else:
                images.append(flist[0])
                kernels.append(self.kfile[0])

        ## write csv file
        dataset = Table([images, kernels], names=['Images', 'Kernels'])
        ascii.write(dataset, self.klist+csvext, format='csv')

    def do_conv(self, idldir, verbose=False):
        '''
        ------ INPUT ------
        idldir              path of IDL routines
        ------ OUTPUT ------
        '''
        if verbose==False:
            devnull = open(os.devnull, 'w')
        else:
            devnull = None

        filename = os.path.basename(self.filIN)

        if self.Ndim==3:
            if self.path_conv is not None:
                f2conv = self.slice(self.path_conv+filename)
            else:
                f2conv = self.slice(self.filIN)
            
            self.spitzer_irs()

        elif self.Ndim==2:
            f2conv = [self.filIN]
        
        self.choker(f2conv)

        SP.call('idl conv.pro',
                shell=True, cwd=idldir, stdout=devnull, stderr=SP.STDOUT)

        ## OUTPUTS
        ##---------
        if self.Ndim==3:
            im = []
            self.slist = []
            for f in f2conv:
                im.append(IO.read_fits(f+'_conv').data)
                self.slist.append(f+'_conv')

            self.convim = np.array(im)
            ## recover 3D header cause the lost of WCS due to PS3_0='WCS-TAB'
            # self.header = IO.read_fits(self.filIN).header

            fclean(f+'_conv'+fitsext)
        elif self.Ndim==2:
            self.convim = IO.read_fits(self.filIN+'_conv').data

            fclean(self.filIN+'_conv'+fitsext)
        
        if self.filOUT is not None:
            comment = "Convolved by G. Aniano's IDL routine."
            IO.write_fits(self.filOUT, self.header, self.convim, self.wave, 
                COMMENT=comment)

    def filenames(self):
        return self.slist

    def clean(self, filIN=None):
        if filIN is not None:
            fclean(filIN)
        else:
            if self.path_conv is not None:
                fclean(self.path_conv)

def wmask(filIN, filOUT=None):
    '''
    MASK Wavelengths

    --- INPUT ---
    filIN       input fits file 
    filOUT      overwrite fits file (Default: NO)
    --- OUTPUT ---
    data_new    new fits data
    wave_new    new fits wave
    '''
    pass

def wclean(filIN, cmod='eq', cfile=None,
           wmod=0, filOUT=None, verbose=False):
    '''
    CLEAN Wavelengths, alternative to the wrange option of concatenate()

    --- INPUT ---
    filIN       input fits file
    wmod        wave mode
    cmod        clean mode (Default: 'eq')
    cfile       input csv file (archived info)
    filOUT      overwrite fits file (Default: NO)
    verbose     display wclean info (Default: False)
    --- OUTPUT ---
    data_new    new fits data
    wave_new    new fits wave
    '''
    ds = IO.read_fits(filIN)
    hdr = ds.header
    data = ds.data
    wave = ds.wave
    Nw = len(wave)
    
    ind = [] # list of indices of wvl to remove
    if cfile is not None:
        # indarxiv = IO.read_csv(cfile, 'Ind')[0]
        indarxiv = ascii.read(cfile+csvext)['Ind']
        ind = []
        for i in indarxiv:
            ind.append(int(i))
    else:
        ## Detect crossing wvl
        ##---------------------
        for i in range(Nw-1):
            if wave[i]>=wave[i+1]: # found wave(i+1), i_max=Nw-2
                
                wmin = -1 # lower limit: closest wave smaller than wave[i+1]
                wmax = 0 # upper limit: closest wave larger than wave[i]
                
                for j in range(i+1):
                    dw = wave[i+1] - wave[i-j]
                    if dw>0: # found the closest smaller wave[i-j]
                        wmin = i-j
                        break # only the innermost loop
                if wmin==-1:
                    warnings.warn('Left side fully covered! ')
                
                for j in range(Nw-i-1):
                    dw = wave[i+1+j] - wave[i]
                    if dw>0: # found the closest larger wave[i+1+j]
                        wmax = i+1+j
                        break
                if wmax==0:
                    warnings.warn('Right side fully covered! ')

                Nw_seg = wmax-wmin-1 # number of crossing wvl in segment
                wave_seg = [] # a segment (every detect) of wave
                ind_seg = [] # corresponing segment for sort use
                for k in range(Nw_seg):
                    wave_seg.append(wave[wmin+1+k])
                    ind_seg.append(wmin+1+k)
                ## index list of sorted wave_seg
                ilist = sorted(range(len(wave_seg)), key=wave_seg.__getitem__)
                ## index of wave_seg center
                icen = math.floor((Nw_seg-1)/2)

                ## Visualisation (for test use)
                ##------------------------------
                # print('wave, i: ', wave[i], i)
                # print('wave_seg: ', wave_seg)
                # print('ind_seg: ', ind_seg)
                # print('ilist: ', ilist)
                # print('icen: ', icen)

                ## Remove all crossing wvl between two channels
                ##----------------------------------------------
                if cmod=='all': # most conservative but risk having holes
                    pass
                ## Remove (almost) equal wvl (NOT nb of wvl!) for both sides
                ##-----------------------------------------------------------
                elif cmod=='eq': # (default)
                    ## Select ascendant pair closest to segment center
                    for k in range(icen):
                        if ilist[icen]>ilist[0]: # large center
                            if ilist[icen-k]<ilist[0]:
                                for p in range(ilist[icen-k]+1):
                                    del ind_seg[0]
                                for q in range(Nw_seg-ilist[icen]):
                                    del ind_seg[-1]
                                break
                        else: # small center
                            if ilist[icen+k]>ilist[0]:
                                for p in range(ilist[icen]+1):
                                    del ind_seg[0]
                                for q in range(Nw_seg-ilist[icen+k]):
                                    del ind_seg[-1]
                                break
                ## Leave 2 closest wvl not crossing
                ##----------------------------------
                elif cmod=='closest_left':
                    for k in range(ilist[0]):
                        del ind_seg[0]
                elif cmod=='closest_right':
                    for k in range(Nw_seg-ilist[0]):
                        del ind_seg[-1]
                ## Others
                ##--------
                else:
                    raise ValueError('Non-supported clean mode! ')

                # print('ind_seg (final): ', ind_seg)
                ind.extend(ind_seg)

    ## Do clean
    ##----------
    data_new = np.delete(data, ind, axis=0)
    wave_new = list(np.delete(np.array(wave), ind))

    ## Display clean detail
    ##----------------------
    if verbose==True:
        print('\n>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>')
        print('Number of wavelengths deleted: ', len(ind))
        print('Ind, wavelengths: ')
        for i in ind:
            print(i, wave[i])
        print('>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>\n')

    ## Overwrite fits file
    ##---------------------
    if filOUT is not None:
        # comment = 'Wavelength removal info in _wclean_info.csv'
        IO.write_fits(filOUT, header=hdr, data=data_new,
                      wave=wave_new, wmod=self.wmod, filext=self.filext) # hdr auto changed
        
        ## Write csv file
        wlist = []
        for i in ind:
            wlist.append([i, wave[i]])
        IO.write_csv(filOUT+'_wclean_info',
                     header=['Ind', 'Wavelengths'], dset=wlist)

    return data_new, wave_new

def interfill(arr, axis):
    '''
    FILL undersampling/artificial gap by (bspl)INTERpolation

    --- INPUT ---
    arr         array
    axis        axis along which interpolation
    --- OUTPUT ---
    newarr      new array
    '''
    print(">> fill gaps with b-splines <<")

    axsh = arr.shape
    NAXIS = np.size(axsh)
    newarr = np.copy(arr)
    if NAXIS==1: # 1D array
        x = np.arange(axsh[0])
        for i in range(axsh[0]):
            newarr = MA.bsplinterp(x, arr, x)
    if NAXIS==2: # no wavelength
        if axis==0: # col direction
            y = np.arange(axsh[0])
            for i in range(axsh[1]):
                col = MA.bsplinterp(y, arr[:,i], y)
                for j in range(axsh[0]):
                    newarr[j,i] = col[j]
        elif axis==1: # row direction
            x = np.arange(axsh[1])
            for j in range(axsh[0]):
                row = MA.bsplinterp(x, arr[j,:], x)
                for i in range(axsh[1]):
                    newarr[j,i] = row[i]
        else:
            raise ValueError('Unknown axis! ')
    elif NAXIS==3:
        if axis==0: # fill wavelength
            z = np.arange(axsh[0])
            for i in range(axsh[2]):
                for j in range(axsh[1]):
                    wvl = MA.bsplinterp(z, arr[:,j,i], z)
                    for k in range(axsh[0]):
                        newarr[k,j,i] = wvl[k]
        elif axis==1: # col direction
            y = np.arange(axsh[1])
            for k in range(axsh[0]):
                for i in range(axsh[2]):
                    col = MA.bsplinterp(y, arr[k,:,i], y)
                    for j in range(axsh[1]):
                        newarr[k,j,i] = col[j]
        elif axis==2: # row direction
            x = np.arange(axsh[2])
            for k in range(axsh[0]):
                for j in range(axsh[1]):
                    row = MA.bsplinterp(x, arr[k,j,:], x)
                    for i in range(axsh[2]):
                        newarr[k,j,i] = row[i]
        else:
            raise ValueError('Unknown axis! ')
    else:
        raise ValueError('Non-supported array shape! ')

    return newarr

def concatenate(flist, filOUT=None, comment=None,
                wsort=False, wrange=None,
                keepfrag=True, cropedge=False):
    '''
    wsort=True can be used with wclean
    When wsort=False, wrange is used to avoid wavelength overlapping

    '''
    ds = type('', (), {})()

    if wrange is None:
        wrange = [ (2.50, 5.00), # irc
                   (5.21, 7.56), # sl2
                   (7.57, 14.28), # sl1
                   (14.29, 20.66), # ll2
                   (20.67, 38.00), ] # ll1
    wmin = []
    wmax = []
    for i in range(len(wrange)):
        wmin.append(wrange[i][0])
        wmax.append(wrange[i][1])
    
    ## Read data
    wave = []
    data = []

    maskall = 0
    ## Keep all wavelengths and sort them in ascending order
    if wsort==True:
        for f in flist:
            ds = IO.read_fits(f)
            data.append(fi.data)
            wave.append(fi.wave)
            ## If one fragment all NaN, mask
            maskall = np.logical_or(maskall,
                                    np.isnan(fi.data).all(axis=0))
    ## Keep wavelengths in the given ranges (wrange)
    else:
        for f in flist:
            fi = IO.read_fits(f)
            imin = LA.closest(wmin, fi.wave[0])
            imax = LA.closest(wmax, fi.wave[-1])
            iwi = 0
            iws = -1
            for i, w in enumerate(fi.wave[:-2]):
                if w<wmin[imin] and fi.wave[i+1]>wmin[imin]:
                    iwi = i+1
                if w<wmax[imax] and fi.wave[i+1]>wmax[imax]:
                    iws = i+1
            data.append(fi.data[iwi:iws])
            wave.append(fi.wave[iwi:iws])
            ## If one fragment all NaN, mask
            maskall = np.logical_or(maskall,
                                    np.isnan(fi.data).all(axis=0))

    data = np.concatenate(data, axis=0)
    wave = np.concatenate(wave)
    hdr = fi.header
    ## Sort
    ind = sorted(range(len(wave)), key=wave.__getitem__)
    # wave = np.sort(wave)
    wave = wave[ind]
    data = data[ind]
    ## NaN mask
    if not keepfrag:
        for k in range(len(wave)):
            data[k][maskall] = np.nan

    if cropedge:
        reframe = improve(header=hdr, images=data, wave=wave)
        xlist = []
        for x in range(reframe.Nx):
            if not np.isnan(reframe.im[:,:,x]).all():
                xlist.append(x)
        ylist = []
        for y in range(reframe.Ny):
            if not np.isnan(reframe.im[:,y,:]).all():
                ylist.append(y)
        xmin = min(xlist)
        xmax = max(xlist)+1
        ymin = min(ylist)
        ymax = max(ylist)+1
        dx = xmax-xmin
        dy = ymax-ymin
        x0 = xmin+dx/2
        y0 = ymin+dy/2

        reframe.crop(sizpix=(dx,dy), cenpix=(x0,y0))
        data = reframe.im
        hdr = reframe.hdr
        cropcenter = (x0,y0)
        cropsize = (dx,dy)
    else:
        cropcenter = None
        cropsize = None

    ds.wave = wave
    ds.data = data
    ds.header = hdr
    ds.cropcenter = cropcenter
    ds.cropsize = cropsize
    
    ## Write FITS file
    if filOUT is not None:
        IO.write_fits(filOUT, header=hdr, data=data,
                      wave=wave, wmod=self.wmod, filext=self.filext,
                      COMMENT=comment)

    return ds
