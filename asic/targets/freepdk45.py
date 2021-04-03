
import os
import sys
import re
import siliconcompiler

####################################################
# PDK Setup
####################################################

def setup_platform(chip):

    foundry = 'virtual'
    process = 'freepdk45'
    rev = 'r1p0'
    stackup = '10M'
    edavendor = 'openroad'
    libtype = '10t'
    pdkdir = '/'.join(["asic",
                       foundry,
                       process,
                       'pdk',
                       rev])
    

    #if you are calling this file, you are in asic mode
    chip.set('mode','asic')

    # process name
    chip.set('pdk','foundry', foundry)
    chip.set('pdk','process', process)
    chip.set('pdk','rev', rev)
    chip.set('pdk','stackup', stackup)

    # APR tech file
    chip.set('pdk','aprtech',stackup, libtype, edavendor,
             pdkdir+'/apr/freepdk45.tech.lef')

    # Techlef Overrides
    chip.set('pdk','aprlayer',stackup, 'metal1 X 0.095 0.19')
    chip.add('pdk','aprlayer',stackup, 'metal1 Y 0.07  0.14')
    chip.add('pdk','aprlayer',stackup, 'metal2 X 0.095 0.19')
    chip.add('pdk','aprlayer',stackup, 'metal2 Y 0.07  0.14')
    chip.add('pdk','aprlayer',stackup, 'metal3 X 0.095 0.19')
    chip.add('pdk','aprlayer',stackup, 'metal3 Y 0.07  0.14')
    chip.add('pdk','aprlayer',stackup, 'metal4 X 0.095 0.28')
    chip.add('pdk','aprlayer',stackup, 'metal4 Y 0.07  0.28')
    chip.add('pdk','aprlayer',stackup, 'metal5 X 0.095 0.28')
    chip.add('pdk','aprlayer',stackup, 'metal5 Y 0.07  0.28')
    chip.add('pdk','aprlayer',stackup, 'metal6 X 0.095 0.28')
    chip.add('pdk','aprlayer',stackup, 'metal6 Y 0.07  0.28')
    chip.add('pdk','aprlayer',stackup, 'metal7 X 0.095 0.8')
    chip.add('pdk','aprlayer',stackup, 'metal7 Y 0.07  0.8')
    chip.add('pdk','aprlayer',stackup, 'metal8 X 0.095 0.8')
    chip.add('pdk','aprlayer',stackup, 'metal8 Y 0.07  0.8')
    chip.add('pdk','aprlayer',stackup, 'metal9 X 0.095 1.6')
    chip.add('pdk','aprlayer',stackup, 'metal9 Y 0.07  1.6')
    chip.add('pdk','aprlayer',stackup, 'metal10 X 0.095 1.6')
    chip.add('pdk','aprlayer',stackup, 'metal10 Y 0.07 1.6')

####################################################
# Library Setup
####################################################
def setup_libs(chip, vendor=None):
 
    foundry = 'virtual'
    process = 'freepdk45'
    libname = 'NangateOpenCellLibrary'
    libtype = '10t'
    size = '0.19 1.4'
    rev = 'r1p0'
    corner = 'typical'
    objectives = ['setup']
    libdir = '/'.join(["asic",
                       foundry,
                       process,
                       'libs',
                       libname,
                       rev])
    
    # rev
    chip.set('stdcell',libname,'rev',rev)    

    # timing
    chip.set('stdcell',libname, 'model', corner, 'nldm', 'lib',
             libdir+'/lib/NangateOpenCellLibrary_typical.lib')

    # lef
    chip.set('stdcell',libname,'lef',
             libdir+'/lef/NangateOpenCellLibrary.macro.lef')    
    # gds
    chip.set('stdcell',libname,'gds',
             libdir+'/gds/NangateOpenCellLibrary.gds')
    # site name
    chip.set('stdcell',libname,'site',
             'FreePDK45_38x28_10R_NP_162NW_34O')
    # lib arch
    chip.set('stdcell',libname,'libtype',libtype)

    # lib site/tile/size
    chip.set('stdcell',libname,'size',size)

#########################
def setup_design(chip, optmode):

    chip.set('asic', 'stackup', chip.get('pdk', 'stackup')[0])
    chip.set('asic', 'targetlib', chip.getkeys('stdcell'))

    corner = 'typical'
    # hard coded mcmm settings (only one corner!)
    chip.set('mcmm','worst','libcorner', corner)
    chip.set('mcmm','worst','pexcorner', corner)
    chip.set('mcmm','worst','mode', 'func')
    chip.set('mcmm','worst','check', ['setup','hold'])
    
#########################
if __name__ == "__main__":    

    # File being executed
    prefix = os.path.splitext(os.path.basename(__file__))[0]
    output = prefix + '.json'

    # create a chip instance
    chip = siliconcompiler.Chip()
    # load configuration
    setup_platform(chip)
    setup_libs(chip)
    # write out result
    chip.writecfg(output)

   