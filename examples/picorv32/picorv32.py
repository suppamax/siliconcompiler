#!/usr/bin/env python3

import os
import siliconcompiler


def rtl2gds(design='picorv32',
            target="skywater130_demo",
            sdc=None,
            rtl=None,
            width=1000,
            height=1000,
            jobname='job0',
            fp=None):
    '''RTL2GDS flow'''

    # CREATE OBJECT
    chip = siliconcompiler.Chip(design)

    # SETUP
    chip.load_target(target)
    rootdir = os.path.dirname(__file__)
    if rtl is None:
        chip.input(os.path.join(rootdir, f"{design}.v"))
    if sdc is None:
        chip.input(os.path.join(rootdir, f"{design}.sdc"))

    chip.set('option', 'relax', True)
    chip.set('option', 'quiet', True)

    chip.set('constraint', 'outline', [(0, 0), (width, height)])
    chip.set('constraint', 'corearea', [(10, 10), (width - 10, height - 10)])

    # RUN
    chip.run()

    # ANALYZE
    chip.summary()

    return chip


if __name__ == '__main__':
    rtl2gds()
