#!/usr/bin/env python
'''
This script creates an initial condition file for MPAS-Ocean.

Internal tide test case
see:
Demange, J., Debreu, L., Marchesiello, P., Lemarié, F., Blayo, E., Eldred, C., 2019. Stability analysis of split-explicit free surface ocean models: Implication of the depth-independent barotropic mode approximation. Journal of Computational Physics 398, 108875. https://doi.org/10.1016/j.jcp.2019.108875
Marsaleix, P., Auclair, F., Floor, J.W., Herrmann, M.J., Estournel, C., Pairaud, I., Ulses, C., 2008. Energy conservation issues in sigma-coordinate free-surface ocean models. Ocean Modelling 20, 61–89. https://doi.org/10.1016/j.ocemod.2007.07.005
'''
import os
import shutil
import numpy as np
import xarray as xr
from mpas_tools.io import write_netcdf
import argparse
import math
import time
verbose=True


def main():
    timeStart = time.time()
    parser = argparse.ArgumentParser()
    parser.add_argument('-i', '--input_file', dest='input_file',
                        default='base_mesh.nc',
                        help='Input file, containing base mesh'
                        )
    parser.add_argument('-o', '--output_file', dest='output_file',
                        default='initial_state.nc',
                        help='Output file, containing initial variables'
                        )
    parser.add_argument('-L', '--nVertLevels', dest='nVertLevels',
                        default=50,
                        help='Number of vertical levels'
                        )
    nVertLevels = parser.parse_args().nVertLevels
    ds = xr.open_dataset(parser.parse_args().input_file)

    #comment('obtain dimensions and mesh variables')
    nCells = ds['nCells'].size
    nEdges = ds['nEdges'].size
    nVertices = ds['nVertices'].size

    maxDepth = 5000.0 
    xCell = ds['xCell']
    xEdge = ds['xEdge']
    xVertex = ds['xVertex']
    yCell = ds['yCell']
    yEdge = ds['yEdge']
    yVertex = ds['yVertex']

    # Adjust coordinates so first edge is at zero in x and y
    xOffset = xEdge.min()
    xCell -= xOffset
    xEdge -= xOffset
    xVertex -= xOffset
    yOffset = np.min(yEdge)
    yCell -= yOffset
    yEdge -= yOffset
    yVertex -= yOffset

    comment('create and initialize variables')
    time1 = time.time()

    varsZ = ['refLayerThickness','refBottomDepth','refZMid','vertCoordMovementWeights']
    for var in varsZ:
        globals()[var] = np.nan*np.ones(nVertLevels)

    vars2D = ['ssh', 'bottomDepth','bottomDepthObserved']
    for var in vars2D:
        globals()[var] = np.nan*np.ones(nCells)
    maxLevelCell = np.ones(nCells, dtype=np.int32)

    vars3D = ['temperature', 'salinity','zMid', 'layerThickness', 'restingThickness', 'density',\
               'surfaceStress', 'atmosphericPressure', 'boundaryLayerDepth']
    for var in vars3D:
        globals()[var] = -9*np.ones([1, nCells, nVertLevels])
        #globals()[var] = np.nan*np.ones([1, nCells, nVertLevels])

    #comment('create reference variables for z-level grid')
    # equally-spaced layers
    refLayerThickness[:] = maxDepth/nVertLevels
    refBottomDepth[0] = refLayerThickness[0]
    refZMid[0] = -0.5 * refLayerThickness[0]
    for k in range(1, nVertLevels):
        refBottomDepth[k] = refBottomDepth[k - 1] + refLayerThickness[k]
        refZMid[k] = -refBottomDepth[k - 1] - 0.5 * refLayerThickness[k]
    #comment('z-level: ssh in top layer only')
    vertCoordMovementWeights[:] = 0.0
    vertCoordMovementWeights[0] = 1.0

    # Marsaleix et al 2008 page 81
    # Gaussian function in depth for deep sea ridge
    xMid = 0.5*(min(xCell) + max(xCell))
    bottomDepth[:] = 5000.0 - 1000.0*np.exp( -(( xCell[:] - xMid)/150e3)**2 )
    # SSH varies from 0 to 1m across the domain
    ssh[:] = xCell[:]/4800e3
    # z-level: ssh in top layer only
    layerThickness[0, :, 0] += ssh[:]

    for iCell in range(0, nCells):
        # z-star: spread layer thicknesses proportionally
        #layerThickness[0, iCell, :] = refLayerThickness[:]*(maxDepth+ssh[iCell])/maxDepth

        for k in range(nVertLevels-1,0,-1):
            if bottomDepth[iCell] > refBottomDepth[k-1]:
                maxLevelCell[iCell] = k
                # Partial bottom cells
                layerThickness[0, iCell, k] = bottomDepth[iCell] - refBottomDepth[k-1]
                zMid[0, iCell, k] = -bottomDepth[iCell] + 0.5*layerThickness[0, iCell, k]
                break
        for k in range(maxLevelCell[iCell]-1,1,-1):
            layerThickness[0, iCell, k] = refLayerThickness[k]
            zMid[0, iCell, k] = zMid[0, iCell, k+1]  \
               + 0.5*(layerThickness[0, iCell, k+1] + layerThickness[0, iCell, k])

    k = 0
    layerThickness[0, :, k] = refLayerThickness[k] + ssh[:]
    zMid[0, :, k] = zMid[0, :, k+1]  \
        + 0.5*(layerThickness[0, :, k+1] + layerThickness[0, :, k])

    restingThickness[:, :] = layerThickness[0, :, :]
    restingThickness[:, 0] = refLayerThickness[0]
    bottomDepthObserved[:] = bottomDepth[:]

    #comment('initialize tracers')
    rho0 = 1000.0 # kg/m^3
    rhoz = -2.0e-4 # kg/m^3/m in z 
    S0 = 35.0
    
    # I believe this is needed to be able to overwrite the file later
    #ds.load()
    #maxLevelCell = ds.maxLevelCell.values - 1
    # linear equation of state
    # rho = rho0 - alpha*(T-Tref) + beta*(S-Sref)
    # set S=Sref
    # T = Tref - (rho - rhoRef)/alpha 
    config_eos_linear_alpha = 0.2
    config_eos_linear_beta = 0.8
    config_eos_linear_Tref = 10.0
    config_eos_linear_Sref = 35.0
    config_eos_linear_densityref = 1000.0

    for k in range(0, nVertLevels):
        activeCells = k <= maxLevelCell
        salinity[0, activeCells, k] = S0
        density[0,activeCells,k] = rho0 + rhoz*zMid[0, activeCells, k]
        # T = Tref - (rho - rhoRef)/alpha 
        temperature[0,activeCells,k] = config_eos_linear_Tref \
            - (density[0,activeCells,k] - config_eos_linear_densityref)/config_eos_linear_alpha

    normalVelocity = (('Time', 'nEdges', 'nVertLevels',), 0.0)

    #comment('initialize coriolis terms')
    ds['fCell'] = (('nCells', 'nVertLevels',), np.zeros([nCells, nVertLevels]))
    ds['fEdge'] = (('nEdges', 'nVertLevels',), np.zeros([nEdges, nVertLevels]))
    ds['fVertex'] = (('nVertices', 'nVertLevels',), np.zeros([nVertices, nVertLevels]))

    #comment('initialize other fields')
    surfaceStress[:] = 0.0
    atmosphericPressure[:] = 0.0
    boundaryLayerDepth[:] = 0.0
    print('   time: %f'%((time.time()-time1)))

    comment('finalize and write file')
    time1 = time.time()
    ds['maxLevelCell'] = (['nCells'], maxLevelCell + 1)
    for var in varsZ:
        ds[var] = (['nVertLevels'], globals()[var])
    for var in vars2D:
        ds[var] = (['nCells'], globals()[var])
    for var in vars3D:
        ds[var] = (['Time','nCells','nVertLevels'], globals()[var])
    # If you prefer not to have NaN as the fill value, you should consider using mpas_tools.io.write_netcdf() instead
    #ds.to_netcdf('initial_state.nc', format='NETCDF3_64BIT_OFFSET')
    #ds = Dataset(parser.parse_args().output_file, 'a', format='NETCDF3_64BIT_OFFSET')
    write_netcdf(ds,'initial_state.nc')
    print('   time: %f'%((time.time()-time1)))
    print('Total time: %f'%((time.time()-timeStart)))

def comment(string):
    if verbose:
        print('***   '+string)

if __name__ == '__main__':
    # If called as a primary module, run main
    main()

# vim: foldmethod=marker ai ts=4 sts=4 et sw=4 ft=python
