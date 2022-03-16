# Copyright 2020 Silicon Compiler Authors. All Rights Reserved.

import argparse
import base64
import time
import datetime
import multiprocessing
import tarfile
import traceback
import asyncio
from subprocess import run, PIPE
import os
import pathlib
import sys
import gzip
import re
import json
import logging
import hashlib
import shutil
import copy
import importlib
import textwrap
import math
import pandas
import yaml
import graphviz
import time
import uuid
import shlex
import platform
import getpass
import distro
import netifaces
import webbrowser
import pty
from jinja2 import Environment, FileSystemLoader
from pathlib import Path
from timeit import default_timer as timer
from siliconcompiler.client import *
from siliconcompiler.schema import *
from siliconcompiler.scheduler import _deferstep
from siliconcompiler import leflib
from siliconcompiler import utils
from siliconcompiler import _metadata

class Chip:
    """Object for configuring and executing hardware design flows.

    This is the main object used for configuration, data, and
    execution within the SiliconCompiler platform.

    Args:
        design (string): Name of the top level chip design module.

    Examples:
        >>> siliconcompiler.Chip(design="top")
        Creates a chip object with name "top".
    """

    ###########################################################################
    def __init__(self, design=None, loglevel=None):

        # Local variables
        self.scroot = os.path.dirname(os.path.abspath(__file__))
        self.cwd = os.getcwd()
        self.error = 0
        self.cfg = schema_cfg()
        self.cfghistory = {}
        # The 'status' dictionary can be used to store ephemeral config values.
        # Its contents will not be saved, and can be set by parent scripts
        # such as a web server or supervisor process. Currently supported keys:
        # * 'jobhash': A hash or UUID which can identify jobs in a larger system.
        # * 'remote_cfg': Dictionary containing remote server configurations
        #                 (address, credentials, etc.)
        # * 'slurm_account': User account ID in a connected slurm HPC cluster.
        # * 'slurm_partition': Name of the partition in which a task should run
        #                      on a connected slurm HPC cluster.
        # * 'watchdog': Activity-monitoring semaphore for jobs scheduled on an
        #               HPC cluster; expects a 'threading.Event'-like object.
        # * 'max_fs_bytes': A limit on how much disk space a job is allowed
        #                   to consume in a connected HPC cluster's storage.
        self.status = {}

        self.builtin = ['minimum','maximum',
                        'nop', 'mux', 'join', 'verify']

        # We set 'design' and 'loglevel' directly in the config dictionary
        # because of a chicken-and-egg problem: self.set() relies on the logger,
        # but the logger relies on these values.
        self.cfg['design']['value'] = design
        if loglevel:
            self.cfg['loglevel']['value'] = loglevel
        # We set scversion directly because it has its 'lock' flag set by default.
        self.cfg['version']['software']['value'] = _metadata.version
        self.cfg['version']['software']['defvalue'] = _metadata.version

        self._init_logger()

        self._loaded_modules = {
            'flows': [],
            'pdks': [],
            'libs': []
        }

    ###########################################################################
    def _init_logger(self, step=None, index=None):

        self.logger = logging.getLogger(uuid.uuid4().hex)

        # Don't propagate log messages to "root" handler (we get duplicate
        # messages without this)
        # TODO: this prevents us from being able to capture logs with pytest:
        # we should revisit it
        self.logger.propagate = False

        loglevel = self.get('loglevel')

        jobname = self.get('jobname')
        if jobname == None:
            jobname = '---'

        if step == None:
            step = '---'
        if index == None:
            index = '-'

        run_info = '%-7s | %-12s | %-3s' % (jobname, step, index)

        if loglevel=='DEBUG':
            logformat = '| %(levelname)-7s | %(funcName)-10s | %(lineno)-4s | ' + run_info + ' | %(message)s'
        else:
            logformat = '| %(levelname)-7s | ' + run_info + ' | %(message)s'

        handler = logging.StreamHandler()
        formatter = logging.Formatter(logformat)

        handler.setFormatter(formatter)

        # Clear any existing handlers so we don't end up with duplicate messages
        # if repeat calls to _init_logger are made
        if len(self.logger.handlers) > 0:
            self.logger.handlers.clear()

        self.logger.addHandler(handler)
        self.logger.setLevel(loglevel)

    ###########################################################################
    def _deinit_logger(self):
        self.logger = None

    ###########################################################################
    def create_cmdline(self, progname, description=None, switchlist=[]):
        """Creates an SC command line interface.

        Exposes parameters in the SC schema as command line switches,
        simplifying creation of SC apps with a restricted set of schema
        parameters exposed at the command line. The order of command
        line switch settings parsed from the command line is as follows:

         1. design
         2. loglevel
         3. mode
         4. arg_step
         5. fpga_partname
         6. load_target('target')
         7. read_manifest([cfg])
         8. all other switches

        The cmdline interface is implemented using the Python argparse package
        and the following use restrictions apply.

        * Help is accessed with the '-h' switch.
        * Arguments that include spaces must be enclosed with double quotes.
        * List parameters are entered individually. (ie. -y libdir1 -y libdir2)
        * For parameters with Boolean types, the switch implies "true".
        * Special characters (such as '-') must be enclosed in double quotes.
        * Compiler compatible switches include: -D, -I, -O{0,1,2,3}
        * Verilog legacy switch formats are supported: +libext+, +incdir+

        Args:
            progname (str): Name of program to be executed.
            description (str): Short program description.
            switchlist (list of str): List of SC parameter switches to expose
                 at the command line. By default all SC schema switches are
                 available.  Parameter switches should be entered without
                 '-', based on the parameter 'switch' field in the 'schema'.

        Examples:
            >>> chip.create_cmdline(progname='sc-show',switchlist=['source','cfg'])
            Creates a command line interface for 'sc-show' app.

        """

        # Argparse
        parser = argparse.ArgumentParser(prog=progname,
                                         prefix_chars='-+',
                                         formatter_class=argparse.RawDescriptionHelpFormatter,
                                         description=description)



        # Get all keys from global dictionary or override at command line
        allkeys = self.getkeys()

        # Iterate over all keys to add parser argument
        for key in allkeys:
            #Fetch fields from leaf cell
            helpstr = self.get(*key, field='shorthelp')
            typestr = self.get(*key, field='type')
            #Switch field fully describes switch format
            switch = self.get(*key, field='switch')
            if switch is None:
                switches = []
            elif isinstance(switch, list):
                switches = switch
            else:
                switches = [switch]

            switchstrs = []
            dest = None
            for switch in switches:
                switchmatch = re.match(r'(-[\w_]+)\s+(.*)', switch)
                gccmatch = re.match(r'(-[\w_]+)(.*)', switch)
                plusmatch = re.match(r'(\+[\w_\+]+)(.*)', switch)
                if switchmatch:
                    switchstr = switchmatch.group(1)
                    if re.search('_', switchstr):
                        this_dest = re.sub('-','',switchstr)
                    else:
                        this_dest = key[0]
                elif gccmatch:
                    switchstr = gccmatch.group(1)
                    this_dest = key[0]
                elif plusmatch:
                    switchstr = plusmatch.group(1)
                    this_dest = key[0]

                switchstrs.append(switchstr)
                if dest is None:
                    dest = this_dest
                elif dest != this_dest:
                    raise ValueError('Destination for each switch in list must match')

            #Four switch types (source, scalar, list, bool)
            if ('source' not in key) & ((switchlist == []) | (dest in switchlist)):
                if typestr == 'bool':
                    parser.add_argument(*switchstrs,
                                        metavar='',
                                        dest=dest,
                                        action='store_const',
                                        const="true",
                                        help=helpstr,
                                        default=argparse.SUPPRESS)
                #list type arguments
                elif re.match(r'\[', typestr):
                    #all the rest
                    parser.add_argument(*switchstrs,
                                        metavar='',
                                        dest=dest,
                                        action='append',
                                        help=helpstr,
                                        default=argparse.SUPPRESS)
                else:
                    #all the rest
                    parser.add_argument(*switchstrs,
                                        metavar='',
                                        dest=dest,
                                        help=helpstr,
                                        default=argparse.SUPPRESS)


        #Preprocess sys.argv to enable linux commandline switch formats
        #(gcc, verilator, etc)
        scargs = []

        # Iterate from index 1, otherwise we end up with script name as a
        # 'source' positional argument
        for item in sys.argv[1:]:
            #Split switches with one character and a number after (O0,O1,O2)
            opt = re.match(r'(\-\w)(\d+)', item)
            #Split assign switches (-DCFG_ASIC=1)
            assign = re.search(r'(\-\w)(\w+\=\w+)', item)
            #Split plusargs (+incdir+/path)
            plusarg = re.search(r'(\+\w+\+)(.*)', item)
            if opt:
                scargs.append(opt.group(1))
                scargs.append(opt.group(2))
            elif plusarg:
                scargs.append(plusarg.group(1))
                scargs.append(plusarg.group(2))
            elif assign:
                scargs.append(assign.group(1))
                scargs.append(assign.group(2))
            else:
                scargs.append(item)


        # exit on version check
        if '-version' in scargs:
            print(_metadata.version)
            sys.exit(0)

        # Required positional source file argument
        if ((switchlist == []) &
            (not '-cfg' in scargs)) | ('source' in switchlist) :
            parser.add_argument('source',
                                nargs='*',
                                help=self.get('source', field='shorthelp'))

        #Grab argument from pre-process sysargs
        #print(scargs)
        cmdargs = vars(parser.parse_args(scargs))
        #print(cmdargs)
        #sys.exit()

        # Print banner
        print(_metadata.banner)
        print("Authors:", ", ".join(_metadata.authors))
        print("Version:", _metadata.version, "\n")
        print("-"*80)

        os.environ["COLUMNS"] = '80'

        # 1. set design name (override default)
        if 'design' in cmdargs.keys():
            self.name = cmdargs['design']

        # 2. set loglevel if set at command line
        if 'loglevel' in cmdargs.keys():
            self.logger.setLevel(cmdargs['loglevel'])

        # 3. read in target if set
        if 'target' in cmdargs.keys():
            if 'mode' in cmdargs.keys():
                self.set('mode', cmdargs['mode'], clobber=True)
            if 'techarg' in cmdargs.keys():
                print("NOT IMPLEMENTED")
                sys.exit()
            if 'flowarg' in cmdargs.keys():
                print("NOT IMPLEMENTED")
                sys.exit()
            if 'arg_step' in cmdargs.keys():
                self.set('arg', 'step', cmdargs['arg_step'], clobber=True)
            if 'fpga_partname' in cmdargs.keys():
                self.set('fpga', 'partname', cmdargs['fpga_partname'], clobber=True)
            # running target command
            self.load_target(cmdargs['target'])

        # 4. read in all cfg files
        if 'cfg' in cmdargs.keys():
            for item in cmdargs['cfg']:
                self.read_manifest(item, clobber=True, clear=True)

        # insert all parameters in dictionary
        self.logger.info('Setting commandline arguments')
        allkeys = self.getkeys()

        for key, val in cmdargs.items():

            # Unifying around no underscores for now
            keylist = key.split('_')

            orderhash = {}
            # Find keypath with matching keys
            for keypath in allkeys:
                match = True
                for item in keylist:
                    if item in keypath:
                        orderhash[item] = keypath.index(item)
                    else:
                        match = False
                if match:
                    chosenpath = keypath
                    break

            # Turn everything into a list for uniformity
            if isinstance(val, list):
                val_list = val
            else:
                val_list = [val]

            for item in val_list:
                #space used to separate values!
                extrakeys = item.split(' ')
                for i in range(len(extrakeys)):
                    # look for the first default statement
                    # "delete' default in temp list by setting to None
                    if 'default' in chosenpath:
                        next_default = chosenpath.index('default')
                        orderhash[extrakeys[i]] = next_default
                        chosenpath[next_default] = None
                    else:
                        # Creating a sorted list based on key placement
                        args = list(dict(sorted(orderhash.items(),
                                                key=lambda orderhash: orderhash[1])))
                        # Adding data value
                        args = args + [extrakeys[i]]
                        # Set/add value based on type

                        #Check that keypath is valid
                        if self.valid(*args[:-1], quiet=True):
                            if re.match(r'\[', self.get(*args[:-1], field='type')):
                                self.add(*args)
                            else:
                                self.set(*args, clobber=True)
                        else:
                            self.set(*args, clobber=True)

    #########################################################################
    def find_function(self, modulename, funcname, moduletype=None):
        '''
        Returns a function attribute from a module on disk.

        Searches the SC root directory and the 'scpath' parameter for the
        modulename provided and imports the module if found. If the funcname
        provided is found in the module, a callable function attribute is
        returned, otherwise None is returned.

        The function assumes the following directory structure:

        * tools/modulename/modulename.py
        * flows/modulename.py
        * pdks/modulname.py

        If the moduletype is None, the module paths are search in the
        order: 'targets'->'flows'->'tools'->'pdks'->'libs'):


        Supported functions include:

        * targets (make_docs, setup)
        * pdks (make_docs, setup)
        * flows (make_docs, setup)
        * tools (make_docs, setup, check_version, runtime_options,
          pre_process, post_process)
        * libs (make_docs, setup)

        Args:
            modulename (str): Name of module to import.
            funcname (str): Name of the function to find within the module.
            moduletype (str): Type of module (flows, pdks, libs, targets).

        Examples:
            >>> setup_pdk = chip.find_function('freepdk45', 'setup', 'pdks')
            >>> setup_pdk()
            Imports the freepdk45 module and runs the setup_pdk function

        '''

        # module search path depends on modtype
        if moduletype is None:
            for item in ('targets', 'flows', 'pdks', 'libs'):
                relpath = f"{item}/{modulename}.py"
                fullpath = self._find_sc_file(relpath, missing_ok=True)
                if fullpath:
                    break;
        elif moduletype in ('targets','flows', 'pdks', 'libs'):
            fullpath = self._find_sc_file(f"{moduletype}/{modulename}.py", missing_ok=True)
        elif moduletype in ('tools'):
            fullpath = self._find_sc_file(f"{moduletype}/{modulename}/{modulename}.py", missing_ok=True)
        else:
            self.logger.error(f"Illegal module type '{moduletype}'.")
            self.error = 1
            return

        # try loading module if found
        if fullpath:
            if moduletype == 'tools':
                self.logger.debug(f"Loading function '{funcname}' from module '{modulename}'")
            else:
                self.logger.info(f"Loading function '{funcname}' from module '{modulename}'")
            try:
                spec = importlib.util.spec_from_file_location(modulename, fullpath)
                imported = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(imported)

                if hasattr(imported, funcname):
                    function = getattr(imported, funcname)
                else:
                    function = None
                return function
            except:
                traceback.print_exc()
                self.logger.error(f"Module setup failed for '{modulename}'")
                self.error = 1

    ##########################################################################
    def load_target(self, name):
        """
        Loads a target module and runs the setup() function.

        The function searches the $SCPATH for targets/<name>.py and runs
        the setup function in that module if found.

        Args:
            name (str): Module name
            flow (str): Target flow to

        Examples:
            >>> chip.load_target('freepdk45_demo')
            Loads the 'freepdk45_demo' target

        """

        self.set('target', name)

        func = self.find_function(name, 'setup', 'targets')
        if func is not None:
            func(self)
        else:
            self.logger.error(f'Module {name} not found.')
            sys.exit(1)

    ##########################################################################
    def load_pdk(self, name):
        """
        Loads a PDK module and runs the setup() function.

        The function searches the $SCPATH for pdks/<name>.py and runs
        the setup function in that module if found.

        Args:
            name (str): Module name

        Examples:
            >>> chip.load_pdk('freepdk45_pdk')
            Loads the 'freepdk45' pdk

        """

        func = self.find_function(name, 'setup', 'pdks')
        if func is not None:
            self._loaded_modules['pdks'].append(name)
            func(self)
        else:
            self.logger.error(f'Module {name} not found.')
            sys.exit(1)

    ##########################################################################
    def load_flow(self, name):
        """
        Loads a flow  module and runs the setup() function.

        The function searches the $SCPATH for flows/<name>.py and runs
        the setup function in that module if found.

        Args:
            name (str): Module name

        Examples:
            >>> chip.load_flow('asicflow')
            Loads the 'asicflow' flow

        """

        func = self.find_function(name, 'setup', 'flows')
        if func is not None:
            self._loaded_modules['flows'].append(name)
            func(self)
        else:
            self.logger.error(f'Module {name} not found.')
            sys.exit(1)

    ##########################################################################
    def load_lib(self, name):
        """
        Loads a library module and runs the setup() function.

        The function searches the $SCPATH for libs/<name>.py and runs
        the setup function in that module if found.

        Args:
            name (str): Module name

        Examples:
            >>> chip.load_lib('nangate45')
            Loads the 'nangate45' library

        """

        func = self.find_function(name, 'setup', 'libs')
        if func is not None:
            self._loaded_modules['libs'].append(name)
            func(self)
        else:
            self.logger.error(f'Module {name} not found.')
            sys.exit(1)


    ###########################################################################
    def list_metrics(self):
        '''
        Returns a list of all metrics in the schema.

        '''

        return self.getkeys('metric','default','default')

    ###########################################################################
    def help(self, *keypath):
        """
        Returns a schema parameter description.

        Args:
            *keypath(str): Keypath to parameter.

        Returns:
            A formatted multi-line help paragraph for the parameter provided.

        Examples:
            >>> print(chip.help('asic','diearea'))
            Displays help information about the 'asic, diearea' parameter

        """

        self.logger.debug('Fetching help for %s', keypath)

        #Fetch Values

        description = self.get(*keypath, field='shorthelp')
        typestr = self.get(*keypath, field='type')
        switchstr = str(self.get(*keypath, field='switch'))
        defstr = str(self.get(*keypath, field='defvalue'))
        requirement = str(self.get(*keypath, field='require'))
        helpstr = self.get(*keypath, field='help')
        example = self.get(*keypath, field='example')


        #Removing multiple spaces and newlines
        helpstr = helpstr.rstrip()
        helpstr = helpstr.replace("\n", "")
        helpstr = ' '.join(helpstr.split())

        for idx, item in enumerate(example):
            example[idx] = ' '.join(item.split())
            example[idx] = example[idx].replace(", ", ",")

        #Wrap text
        para = textwrap.TextWrapper(width=60)
        para_list = para.wrap(text=helpstr)

        #Full Doc String
        fullstr = ("-"*80 +
                   "\nDescription: " + description +
                   "\nSwitch:      " + switchstr +
                   "\nType:        " + typestr  +
                   "\nRequirement: " + requirement   +
                   "\nDefault:     " + defstr   +
                   "\nExamples:    " + example[0] +
                   "\n             " + example[1] +
                   "\nHelp:        " + para_list[0] + "\n")
        for line in para_list[1:]:
            fullstr = (fullstr +
                       " "*13 + line.lstrip() + "\n")

        return fullstr


    ###########################################################################
    def valid(self, *args, valid_keypaths=None, quiet=True, default_valid=False):
        """
        Checks validity of a keypath.

        Checks the validity of a parameter keypath and returns True if the
        keypath is valid and False if invalid.

        Args:
            keypath(list str): Variable length schema key list.
            valid_keypaths (list of list): List of valid keypaths as lists. If
                None, check against all keypaths in the schema.
            quiet (bool): If True, don't display warnings for invalid keypaths.

        Returns:
            Boolean indicating validity of keypath.

        Examples:
            >>> check = chip.valid('design')
            Returns True.
            >>> check = chip.valid('blah')
            Returns False.
        """

        keypathstr = ','.join(args)
        keylist = list(args)
        if default_valid:
            default = 'default'
        else:
            default = None

        if valid_keypaths is None:
            valid_keypaths = self.getkeys()

        # Look for a full match with default playing wild card
        for valid_keypath in valid_keypaths:
            if len(keylist) != len(valid_keypath):
                continue

            ok = True
            for i in range(len(keylist)):
                if valid_keypath[i] not in (keylist[i], default):
                    ok = False
                    break
            if ok:
                return True

        # Match not found
        if not quiet:
            self.logger.warning(f"Keypath [{keypathstr}] is not valid")
        return False

    ###########################################################################
    def get(self, *keypath, field='value', job=None, cfg=None):
        """
        Returns a schema parameter field.

        Returns a schema parameter filed based on the keypath and value provided
        in the ``*args``. The returned type is consistent with the type field of
        the parameter. Fetching parameters with empty or undefined value files
        returns None for scalar types and [] (empty list) for list types.
        Accessing a non-existent keypath produces a logger error message and
        raises the Chip object error flag.

        Args:
            keypath(list str): Variable length schema key list.
            field(str): Parameter field to fetch.
            job (str): Jobname to use for dictionary access in place of the
                current active jobname.
            cfg(dict): Alternate dictionary to access in place of the default
                chip object schema dictionary.

        Returns:
            Value found for the keypath and field provided.

        Examples:
            >>> foundry = chip.get('pdk', 'foundry')
            Returns the name of the foundry from the PDK.

        """

        if cfg is None:
            if job is not None:
                cfg = self.cfghistory[job]
            else:
                cfg = self.cfg

        keypathstr = ','.join(keypath)

        self.logger.debug(f"Reading from [{keypathstr}]. Field = '{field}'")
        return self._search(cfg, keypathstr, *keypath, field=field, mode='get')

    ###########################################################################
    def getkeys(self, *keypath, cfg=None):
        """
        Returns a list of schema dictionary keys.

        Searches the schema for the keypath provided and returns a list of
        keys found, excluding the generic 'default' key. Accessing a
        non-existent keypath produces a logger error message and raises the
        Chip object error flag.

        Args:
            keypath(list str): Variable length ordered schema key list
            cfg(dict): Alternate dictionary to access in place of self.cfg

        Returns:
            List of keys found for the keypath provided.

        Examples:
            >>> keylist = chip.getkeys('pdk')
            Returns all keys for the 'pdk' keypath.
            >>> keylist = chip.getkeys()
            Returns all list of all keypaths in the schema.
        """

        if cfg is None:
            cfg = self.cfg

        if len(list(keypath)) > 0:
            keypathstr = ','.join(keypath)
            self.logger.debug('Getting schema parameter keys for: %s', keypathstr)
            keys = list(self._search(cfg, keypathstr, *keypath, mode='getkeys'))
            if 'default' in keys:
                keys.remove('default')
        else:
            self.logger.debug('Getting all schema parameter keys.')
            keys = list(self._allkeys(cfg))

        return keys

    ###########################################################################
    def getdict(self, *keypath, cfg=None):
        """
        Returns a schema dictionary.

        Searches the schema for the keypath provided and returns a complete
        dictionary. Accessing a non-existent keypath produces a logger error
        message and raises the Chip object error flag.

        Args:
            keypath(list str): Variable length ordered schema key list
            cfg(dict): Alternate dictionary to access in place of self.cfg

        Returns:
            A schema dictionary

        Examples:
            >>> pdk = chip.getdict('pdk')
            Returns the complete dictionary found for the keypath 'pdk'
        """

        if cfg is None:
            cfg = self.cfg

        if len(list(keypath)) > 0:
            keypathstr = ','.join(keypath)
            self.logger.debug('Getting cfg for: %s', keypathstr)
            localcfg = self._search(cfg, keypathstr, *keypath, mode='getcfg')

        return copy.deepcopy(localcfg)

    ###########################################################################
    def set(self, *args, field='value', clobber=True, cfg=None):
        '''
        Sets a schema parameter field.

        Sets a schema parameter field based on the keypath and value provided
        in the ``*args``. New schema dictionaries are automatically created for
        keypaths that overlap with 'default' dictionaries. The write action
        is ignored if the parameter value is non-empty and the clobber
        option is set to False.

        The value provided must agree with the dictionary parameter 'type'.
        Accessing a non-existent keypath or providing a value that disagrees
        with the parameter type produces a logger error message and raises the
        Chip object error flag.

        Args:
            args (list): Parameter keypath followed by a value to set.
            field (str): Parameter field to set.
            clobber (bool): Existing value is overwritten if True.
            cfg(dict): Alternate dictionary to access in place of self.cfg

        Examples:
            >>> chip.set('design', 'top')
            Sets the name of the design to 'top'
        '''

        if cfg is None:
            cfg = self.cfg

        # Verify that all keys are strings
        for key in args[:-1]:
            if not isinstance(key,str):
                self.logger.error(f"Key [{key}] is not a string [{args}]")

        keypathstr = ','.join(args[:-1])
        all_args = list(args)

        # Special case to ensure loglevel is updated ASAP
        if len(args) == 2 and args[0] == 'loglevel' and field == 'value':
            self.logger.setLevel(args[1])

        self.logger.debug(f"Setting [{keypathstr}] to {args[-1]}")
        return self._search(cfg, keypathstr, *all_args, field=field, mode='set', clobber=clobber)

    ###########################################################################
    def add(self, *args, cfg=None, field='value'):
        '''
        Adds item(s) to a schema parameter list.

        Adds item(s) to schema parameter list based on the keypath and value
        provided in the ``*args``. New schema dictionaries are
        automatically created for keypaths that overlap with 'default'
        dictionaries.

        The value provided must agree with the dictionary parameter 'type'.
        Accessing a non-existent keypath, providing a value that disagrees
        with the parameter type, or using add with a scalar parameter produces
        a logger error message and raises the Chip object error flag.

        Args:
            args (list): Parameter keypath followed by a value to add.
            cfg(dict): Alternate dictionary to access in place of self.cfg
            field (str): Parameter field to set.

        Examples:
            >>> chip.add('source', 'hello.v')
            Adds the file 'hello.v' to the list of sources.
        '''

        if cfg is None:
            cfg = self.cfg

        # Verify that all keys are strings
        for key in args[:-1]:
            if not isinstance(key,str):
                self.logger.error(f"Key [{key}] is not a string [{args}]")

        keypathstr = ','.join(args[:-1])
        all_args = list(args)

        self.logger.debug(f'Appending value {args[-1]} to [{keypathstr}]')
        return self._search(cfg, keypathstr, *all_args, field=field, mode='add')


    ###########################################################################
    def _allkeys(self, cfg, keys=None, keylist=None):
        '''
        Returns list of all keypaths in the schema.
        '''

        if keys is None:
            keylist = []
            keys = []
        for k in cfg:
            newkeys = keys.copy()
            newkeys.append(k)
            if 'defvalue' in cfg[k]:
                keylist.append(newkeys)
            else:
                self._allkeys(cfg[k], keys=newkeys, keylist=keylist)
        return keylist

    ###########################################################################
    def _search(self, cfg, keypath, *args, field='value', mode='get', clobber=True):
        '''
        Internal recursive function that searches the Chip schema for a
        match to the combination of *args and fields supplied. The function is
        used to set and get data within the dictionary.

        Args:
            cfg(dict): The cfg schema to search
            keypath (str): Concatenated keypath used for error logging.
            args (str): Keypath/value variable list used for access
            field(str): Leaf cell field to access.
            mode(str): Action (set/get/add/getkeys/getkeys)
            clobber(bool): Specifies to clobber (for set action)

        '''

        all_args = list(args)
        param = all_args[0]
        val = all_args[-1]
        empty = [None, 'null', [], 'false']

        #set/add leaf cell (all_args=(param,val))
        if (mode in ('set', 'add')) & (len(all_args) == 2):
            # clean error if key not found
            if (not param in cfg) & (not 'default' in cfg):
                self.logger.error(f"Set/Add keypath [{keypath}] does not exist.")
                self.error = 1
            else:
                # making an 'instance' of default if not found
                if (not param in cfg) & ('default' in cfg):
                    cfg[param] = copy.deepcopy(cfg['default'])
                list_type =bool(re.match(r'\[', cfg[param]['type']))
                # checking for illegal fields
                if not field in cfg[param] and (field != 'value'):
                    self.logger.error(f"Field '{field}' for keypath [{keypath}]' is not a valid field.")
                    self.error = 1
                # check legality of value
                if field == 'value':
                    (type_ok,type_error) = self._typecheck(cfg[param], param, val)
                    if not type_ok:
                        self.logger.error("%s", type_error)
                        self.error = 1
                # converting python True/False to lower case string
                if (field == 'value') and (cfg[param]['type'] == 'bool'):
                    if val == True:
                        val = "true"
                    elif val == False:
                        val = "false"
                # checking if value has been set
                # TODO: fix clobber!!
                selval = cfg[param]['value']
                # updating values
                if cfg[param]['lock'] == "true":
                    self.logger.debug("Ignoring {mode}{} to [{keypath}]. Lock bit is set.")
                elif (mode == 'set'):
                    #print(keypath, "**", param, field, val, isinstance(val, list))
                    #TODO: line below is broken, should check for field
                    if (selval in empty) | clobber:
                        if field in ('copy', 'lock'):
                            # boolean fields
                            if val is True:
                                cfg[param][field] = "true"
                            elif val is False:
                                cfg[param][field] = "false"
                            else:
                                self.logger.error(f'{field} must be set to boolean.')
                                self.error = 1
                        elif field in ('hashalgo', 'scope', 'require','type',
                                       'shorthelp', 'switch', 'help'):
                            # awlays string scalars
                            cfg[param][field] = val
                        elif field in ('example'):
                            # list from default schema (already a list)
                            cfg[param][field] = val
                        elif field in ('signature', 'filehash', 'date', 'author'):
                            # convert to list if appropriate
                            if isinstance(val, list) | (not list_type):
                                cfg[param][field] = val
                            else:
                                cfg[param][field] = [val]
                        elif (not list_type) & (val is None):
                            # special case for None
                            cfg[param][field] = None
                        elif (not list_type) & (not isinstance(val, list)):
                            # convert to string for scalar value
                            cfg[param][field] = str(val)
                        elif list_type & (not isinstance(val, list)):
                            # convert to string for list value
                            cfg[param][field] = [str(val)]
                        elif list_type & isinstance(val, list):
                            # converting tuples to strings
                            if re.search(r'\(', cfg[param]['type']):
                                cfg[param][field] = list(map(str,val))
                            else:
                                cfg[param][field] = val
                        else:
                            self.logger.error(f"Assigning list to scalar for [{keypath}]")
                            self.error = 1
                    else:
                        self.logger.debug(f"Ignoring set() to [{keypath}], value already set. Use clobber=true to override.")
                elif (mode == 'add'):
                    if field in ('filehash', 'date', 'author', 'signature'):
                        cfg[param][field].append(str(val))
                    elif field in ('copy', 'lock'):
                        self.logger.error(f"Illegal use of add() for scalar field {field}.")
                        self.error = 1
                    elif list_type & (not isinstance(val, list)):
                        cfg[param][field].append(str(val))
                    elif list_type & isinstance(val, list):
                        cfg[param][field].extend(val)
                    else:
                        self.logger.error(f"Illegal use of add() for scalar parameter [{keypath}].")
                        self.error = 1
                return cfg[param][field]
        #get leaf cell (all_args=param)
        elif len(all_args) == 1:
            if not param in cfg:
                self.error = 1
                self.logger.error(f"Get keypath [{keypath}] does not exist.")
            elif mode == 'getcfg':
                return cfg[param]
            elif mode == 'getkeys':
                return cfg[param].keys()
            else:
                if not (field in cfg[param]) and (field!='value'):
                    self.error = 1
                    self.logger.error(f"Field '{field}' not found for keypath [{keypath}]")
                elif field == 'value':
                    #Select default if no value has been set
                    if field not in cfg[param]:
                        selval = cfg[param]['defvalue']
                    else:
                        selval =  cfg[param]['value']
                    #check for list
                    if bool(re.match(r'\[', cfg[param]['type'])):
                        sctype = re.sub(r'[\[\]]', '', cfg[param]['type'])
                        return_list = []
                        if selval is None:
                            return None
                        for item in selval:
                            if sctype == 'int':
                                return_list.append(int(item))
                            elif sctype == 'float':
                                return_list.append(float(item))
                            elif sctype == '(str,str)':
                                if isinstance(item,tuple):
                                    return_list.append(item)
                                else:
                                    tuplestr = re.sub(r'[\(\)\'\s]','',item)
                                    return_list.append(tuple(tuplestr.split(',')))
                            elif sctype == '(float,float)':
                                if isinstance(item,tuple):
                                    return_list.append(item)
                                else:
                                    tuplestr = re.sub(r'[\(\)\s]','',item)
                                    return_list.append(tuple(map(float, tuplestr.split(','))))
                            else:
                                return_list.append(item)
                        return return_list
                    else:
                        if selval is None:
                            # Unset scalar of any type
                            scalar = None
                        elif cfg[param]['type'] == "int":
                            #print(selval, type(selval))
                            scalar = int(float(selval))
                        elif cfg[param]['type'] == "float":
                            scalar = float(selval)
                        elif cfg[param]['type'] == "bool":
                            scalar = (selval == 'true')
                        elif re.match(r'\(', cfg[param]['type']):
                            tuplestr = re.sub(r'[\(\)\s]','',selval)
                            scalar = tuple(map(float, tuplestr.split(',')))
                        else:
                            scalar = selval
                        return scalar
                #all non-value fields are strings (or lists of strings)
                else:
                    if cfg[param][field] == 'true':
                        return True
                    elif cfg[param][field] == 'false':
                        return False
                    else:
                        return cfg[param][field]
        #if not leaf cell descend tree
        else:
            ##copying in default tree for dynamic trees
            if not param in cfg and 'default' in cfg:
                cfg[param] = copy.deepcopy(cfg['default'])
            elif not param in cfg:
                self.error = 1
                self.logger.error(f"Get keypath [{keypath}] does not exist.")
                return None
            all_args.pop(0)
            return self._search(cfg[param], keypath, *all_args, field=field, mode=mode, clobber=clobber)

    ###########################################################################
    def _prune(self, cfg, top=True, keeplists=False):
        '''
        Internal recursive function that creates a local copy of the Chip
        schema (cfg) with only essential non-empty parameters retained.

        '''

        # create a local copy of dict
        if top:
            localcfg = copy.deepcopy(cfg)
        else:
            localcfg = cfg

        #10 should be enough for anyone...
        maxdepth = 10
        i = 0

        #Prune when the default & value are set to the following
        if keeplists:
            empty = ("null", None)
        else:
            empty = ("null", None, [])

        # When at top of tree loop maxdepth times to make sure all stale
        # branches have been removed, not elegant, but stupid-simple
        # "good enough"
        while i < maxdepth:
            #Loop through all keys starting at the top
            for k in list(localcfg.keys()):
                #removing all default/template keys
                # reached a default subgraph, delete it
                if k == 'default':
                    del localcfg[k]
                # reached leaf-cell
                elif 'help' in localcfg[k].keys():
                    del localcfg[k]['help']
                elif 'example' in localcfg[k].keys():
                    del localcfg[k]['example']
                elif 'defvalue' in localcfg[k].keys():
                    if localcfg[k]['defvalue'] in empty:
                        if 'value' in localcfg[k].keys():
                            if localcfg[k]['value'] in empty:
                                del localcfg[k]
                        else:
                            del localcfg[k]
                #removing stale branches
                elif not localcfg[k]:
                    localcfg.pop(k)
                #keep traversing tree
                else:
                    self._prune(cfg=localcfg[k], top=False, keeplists=keeplists)
            if top:
                i += 1
            else:
                break

        return localcfg

    ###########################################################################
    def _find_sc_file(self, filename, missing_ok=False):
        """
        Returns the absolute path for the filename provided.

        Searches the SC root directory and the 'scpath' parameter for the
        filename provided and returns the absolute path. If no valid absolute
        path is found during the search, None is returned.

        Shell variables ('$' followed by strings consisting of numbers,
        underscores, and digits) are replaced with the variable value.

        Args:
            filename (str): Relative or absolute filename.

        Returns:
            Returns absolute path of 'filename' if found, otherwise returns
            None.

        Examples:
            >>> chip._find_sc_file('flows/asicflow.py')
           Returns the absolute path based on the sc installation directory.

        """

        # Replacing environment variables
        filename = self._resolve_env_vars(filename)

        # If we have a path relative to our cwd or an abs path, pass-through here
        if os.path.exists(os.path.abspath(filename)):
            return os.path.abspath(filename)

        # Otherwise, search relative to scpaths
        scpaths = [self.scroot, self.cwd]
        scpaths.extend(self.get('scpath'))
        if 'SCPATH' in os.environ:
            scpaths.extend(os.environ['SCPATH'].split(os.pathsep))

        searchdirs = ', '.join(scpaths)
        self.logger.debug(f"Searching for file {filename} in {searchdirs}")

        result = None
        for searchdir in scpaths:
            if not os.path.isabs(searchdir):
                searchdir = os.path.join(self.cwd, searchdir)

            abspath = os.path.abspath(os.path.join(searchdir, filename))
            if os.path.exists(abspath):
                result = abspath
                break

        if result is None and not missing_ok:
            self.error = 1
            self.logger.error(f"File {filename} was not found")

        return result

    ###########################################################################
    def find_files(self, *keypath, cfg=None, missing_ok=False):
        """
        Returns absolute paths to files or directories based on the keypath
        provided.

        By default, this function first checks if the keypath provided has its
        `copy` parameter set to True. If so, it returns paths to the files in
        the build directory. Otherwise, it resolves these files based on the
        current working directory and SC path.

        The keypath provided must point to a schema parameter of type file, dir,
        or lists of either. Otherwise, it will trigger an error.

        Args:
            keypath (list str): Variable length schema key list.
            cfg (dict): Alternate dictionary to access in place of the default
                chip object schema dictionary.

        Returns:
            If keys points to a scalar entry, returns an absolute path to that
            file/directory, or None if not found. It keys points to a list
            entry, returns a list of either the absolute paths or None for each
            entry, depending on whether it is found.

        Examples:
            >>> chip.find_files('source')
            Returns a list of absolute paths to source files, as specified in
            the schema.

        """
        if cfg is None:
            cfg = self.cfg

        copyall = self.get('copyall', cfg=cfg)
        paramtype = self.get(*keypath, field='type', cfg=cfg)

        if 'file' in paramtype:
            copy = self.get(*keypath, field='copy', cfg=cfg)
        else:
            copy = False

        if 'file' not in paramtype and 'dir' not in paramtype:
            self.logger.error('Can only call find_files on file or dir types')
            self.error = 1
            return None

        is_list = bool(re.match(r'\[', paramtype))

        paths = self.get(*keypath, cfg=cfg)
        # Convert to list if we have scalar
        if not is_list:
            paths = [paths]

        result = []

        # Special case where we're looking to find tool outputs: check the
        # output directory and return those files directly
        if keypath[0] == 'eda' and keypath[2] in ('input', 'output', 'report'):
            step = keypath[3]
            index = keypath[4]
            if keypath[2] == 'report':
                io = ""
            else:
                io = keypath[2] + 's'
            iodir = os.path.join(self._getworkdir(step=step, index=index), io)
            for path in paths:
                abspath = os.path.join(iodir, path)
                if os.path.isfile(abspath):
                    result.append(abspath)
            return result

        for path in paths:
            if (copyall or copy) and ('file' in paramtype):
                name = self._get_imported_filename(path)
                abspath = os.path.join(self._getworkdir(step='import'), 'outputs', name)
                if os.path.isfile(abspath):
                    # if copy is True and file is found in import outputs,
                    # continue. Otherwise, fall through to _find_sc_file (the
                    # file may not have been gathered in imports yet)
                    result.append(abspath)
                    continue
            result.append(self._find_sc_file(path, missing_ok=missing_ok))
        # Convert back to scalar if that was original type
        if not is_list:
            return result[0]

        return result

    ###########################################################################
    def find_result(self, filetype, step, jobname='job0', index='0'):
        """
        Returns the absolute path of a compilation result.

        Utility function that returns the absolute path to a results
        file based on the provided arguments. The result directory
        structure is:

        <dir>/<design>/<jobname>/<step>/<index>/outputs/<design>.filetype

        Args:
            filetype (str): File extension (.v, .def, etc)
            step (str): Task step name ('syn', 'place', etc)
            jobname (str): Jobid directory name
            index (str): Task index

        Returns:
            Returns absolute path to file.

        Examples:
            >>> manifest_filepath = chip.find_result('.vg', 'syn')
           Returns the absolute path to the manifest.
        """

        workdir = self._getworkdir(jobname, step, index)
        design = self.get('design')
        filename = f"{workdir}/outputs/{design}.{filetype}"

        self.logger.debug("Finding result %s", filename)

        if os.path.isfile(filename):
            return filename
        else:
            self.error = 1
            return None

    ###########################################################################
    def _abspath(self, cfg):
        '''
        Internal function that goes through provided dictionary and resolves all
        relative paths where required.
        '''

        for keypath in self.getkeys(cfg=cfg):
            paramtype = self.get(*keypath, cfg=cfg, field='type')
            value = self.get(*keypath, cfg=cfg)
            if value:
                #only do something if type is file or dir
                if 'file' in paramtype or 'dir' in paramtype:
                    abspaths = self.find_files(*keypath, cfg=cfg, missing_ok=True)
                    self.set(*keypath, abspaths, cfg=cfg)

    ###########################################################################
    def _print_csv(self, cfg, file=None):
        allkeys = self.getkeys(cfg=cfg)
        for key in allkeys:
            keypath = f'"{",".join(key)}"'
            value = self.get(*key, cfg=cfg)
            if isinstance(value,list):
                for item in value:
                    print(f"{keypath},{item}", file=file)
            else:
                print(f"{keypath},{value}", file=file)

    ###########################################################################
    def _print_tcl(self, cfg, file=None, prefix=""):
        '''
        Prints out schema as TCL dictionary
        '''

        allkeys = self.getkeys(cfg=cfg)

        for key in allkeys:
            typestr = self.get(*key, cfg=cfg, field='type')
            value = self.get(*key, cfg=cfg)
            # everything becomes a list
            # convert None to empty list
            if value is None:
                alist = []
            elif bool(re.match(r'\[', typestr)):
                alist = value
            elif typestr == "bool" and value:
                alist = ["true"]
            elif typestr == "bool" and not value:
                alist = ["false"]
            else:
                alist = [value]

            #replace $VAR with env(VAR) for tcl
            for i, val in enumerate(alist):
                m = re.match(r'\$(\w+)(.*)', str(val))
                if m:
                    alist[i] = ('$env(' + m.group(1) + ')' + m.group(2))

            #create a TCL dict
            keystr = ' '.join(key)
            valstr = ' '.join(map(str, alist)).replace(';', '\\;')
            outstr = f"{prefix} {keystr} [list {valstr}]\n"

            #print out all nom default values
            if 'default' not in key:
                print(outstr, file=file)


    ###########################################################################
    def merge_manifest(self, cfg, job=None, clobber=True, clear=True, check=False):
        """
        Merges an external manifest with the current compilation manifest.

        All value fields in the provided schema dictionary are merged into the
        current chip object. Dictionaries with non-existent keypath produces a
        logger error message and raises the Chip object error flag.

        Args:
            job (str): Specifies non-default job to merge into
            clear (bool): If True, disables append operations for list type
            clobber (bool): If True, overwrites existing parameter value
            check (bool): If True, checks the validity of each key

        Examples:
            >>> chip.merge_manifest('my.pkg.json')
           Merges all parameters in my.pk.json into the Chip object

        """

        if job is not None:
            # fill ith default schema before populating
            self.cfghistory[job] = schema_cfg()
            dst = self.cfghistory[job]
        else:
            dst = self.cfg

        for keylist in self.getkeys(cfg=cfg):
            #only read in valid keypaths without 'default'
            key_valid = True
            if check:
                key_valid = self.valid(*keylist, quiet=False, default_valid=True)
            if key_valid and 'default' not in keylist:
                # update value, handling scalars vs. lists
                typestr = self.get(*keylist, cfg=cfg, field='type')
                val = self.get(*keylist, cfg=cfg)
                arg = keylist.copy()
                arg.append(val)
                if bool(re.match(r'\[', typestr)) & bool(not clear):
                    self.add(*arg, cfg=dst)
                else:
                    self.set(*arg, cfg=dst, clobber=clobber)

                # update other fields that a user might modify
                for field in self.getdict(*keylist, cfg=cfg).keys():
                    if field in ('value', 'switch', 'type', 'require', 'defvalue',
                                 'shorthelp', 'example', 'help'):
                        # skip these fields (value handled above, others are static)
                        continue
                    v = self.get(*keylist, cfg=cfg, field=field)
                    self.set(*keylist, v, cfg=dst, field=field)

    ###########################################################################
    def _keypath_empty(self, key):
        '''
        Utility function to check key for an empty list.
        '''

        emptylist = ("null", None, [])

        value = self.get(*key)
        defvalue = self.get(*key, field='defvalue')
        value_empty = (defvalue in emptylist) and (value in emptylist)

        return value_empty

    ###########################################################################
    def _check_files(self):
        allowed_paths = [os.path.join(self.cwd, self.get('dir'))]
        allowed_paths.extend(os.environ['SC_VALID_PATHS'].split(os.pathsep))

        for keypath in self.getkeys():
            if 'default' in keypath:
                continue

            paramtype = self.get(*keypath, field='type')
            #only do something if type is file or dir
            if 'file' in paramtype or 'dir' in paramtype:

                if self.get(*keypath) is None:
                    # skip unset values (some directories are None by default)
                    continue

                abspaths = self.find_files(*keypath, missing_ok=True)
                if not isinstance(abspaths, list):
                    abspaths = [abspaths]

                for abspath in abspaths:
                    ok = False

                    if abspath is not None:
                        for allowed_path in allowed_paths:
                            if os.path.commonpath([abspath, allowed_path]) == allowed_path:
                                ok = True
                                continue

                    if not ok:
                        self.logger.error(f'Keypath {keypath} contains path(s) '
                            'that do not exist or resolve to files outside of '
                            'allowed directories.')
                        return False

        return True

    ###########################################################################
    def check_filepaths(self):
        '''
        Verifies that paths to all files in manifest are valid.
        '''

        allkeys = self.getkeys()
        for keypath in allkeys:
            allpaths = []
            paramtype = self.get(*keypath, field='type')
            if 'file' in paramtype or 'dir' in paramtype:
                if 'dir' not in keypath and self.get(*keypath):
                    allpaths = list(self.get(*keypath))
                for path in allpaths:
                    #check for env var
                    m = re.match(r'\$(\w+)(.*)', path)
                    if m:
                        prefix_path = os.environ[m.group(1)]
                        path = prefix_path + m.group(2)
                    file_error = 'file' in paramtype and not os.path.isfile(path)
                    dir_error = 'dir' in paramtype and not os.path.isdir(path)
                    if file_error or dir_error:
                        self.logger.error(f"Paramater {keypath} path {path} is invalid")
                        self.error = 1

    ###########################################################################
    def check_manifest(self):
        '''
        Verifies the integrity of the pre-run compilation manifest.

        Checks the validity of the current schema manifest in
        memory to ensure that the design has been properly set up prior
        to running compilation. The function is called inside the run()
        function but can also be called separately. Checks performed by the
        check_manifest() function include:

        * Has a flowgraph been defined?
        * Does the manifest satisfy the schema requirement field settings?
        * Are all flowgraph input names legal step/index pairs?
        * Are the tool parameter setting requirements met?

        Returns:
            Returns True if the manifest is valid, else returns False.

        Examples:
            >>> manifest_ok = chip.check_manifest()
            Returns True of the Chip object dictionary checks out.

        '''
        flow = self.get('flow')
        steplist = self.get('steplist')
        if not steplist:
            steplist = self.list_steps()

        #1. Checking that flowgraph is legal
        if flow not in self.getkeys('flowgraph'):
            self.error = 1
            self.logger.error(f"flowgraph {flow} not defined.")
        legal_steps = self.getkeys('flowgraph',flow)

        if 'import' not in legal_steps:
            self.error = 1
            self.logger.error("Flowgraph doesn't contain import step.")

        #2. Check libary names
        for item in self.get('asic', 'logiclib'):
            if item not in self.getkeys('library'):
                self.error = 1
                self.logger.error(f"Target library {item} not found.")

        #3. Check requirements list
        allkeys = self.getkeys()
        for key in allkeys:
            keypath = ",".join(key)
            if 'default' not in key:
                key_empty = self._keypath_empty(key)
                requirement = self.get(*key, field='require')
                if key_empty and (str(requirement) == 'all'):
                    self.error = 1
                    self.logger.error(f"Global requirement missing for [{keypath}].")
                elif key_empty and (str(requirement) == self.get('mode')):
                    self.error = 1
                    self.logger.error(f"Mode requirement missing for [{keypath}].")

        #4. Check per tool parameter requirements (when tool exists)
        for step in steplist:
            for index in self.getkeys('flowgraph', flow, step):
                tool = self.get('flowgraph', flow, step, index, 'tool')
                if (tool not in self.builtin) and (tool in self.getkeys('eda')):
                    # checking that requirements are set
                    if self.valid('eda', tool, 'require', step, index):
                        all_required = self.get('eda', tool, 'require', step, index)
                        for item in all_required:
                            keypath = item.split(',')
                            if self._keypath_empty(keypath):
                                self.error = 1
                                self.logger.error(f"Value empty for [{keypath}] for {tool}.")
                    if self._keypath_empty(['eda', tool, 'exe']):
                        self.error = 1
                        self.logger.error(f'Executable not specified for tool {tool}')

        if 'SC_VALID_PATHS' in os.environ:
            if not self._check_files():
                self.error = 1

        if not self._check_flowgraph_io():
            self.error = 1

        # Dynamic checks
        # We only perform these if arg, step and arg, index are set.
        # We don't check inputs for skip all
        # TODO: Need to add skip step
        step = self.get('arg', 'step')
        index = self.get('arg', 'index')
        if step and index and not self.get('skipall'):
            tool = self.get('flowgraph', flow, step, index, 'tool')
            if self.valid('eda', tool, 'input', step, index):
                required_inputs = self.get('eda', tool, 'input', step, index)
            else:
                required_inputs = []
            input_dir = os.path.join(self._getworkdir(step=step, index=index), 'inputs')
            for filename in required_inputs:
                path = os.path.join(input_dir, filename)
                if not os.path.isfile(path):
                    self.logger.error(f'Required input {filename} not received for {step}{index}.')
                    self.error = 1

            if (not tool in self.builtin) and self.valid('eda', tool, 'require', step, index):
                all_required = self.get('eda', tool, 'require', step, index)
                for item in all_required:
                    keypath = item.split(',')
                    paramtype = self.get(*keypath, field='type')
                    if ('file' in paramtype) or ('dir' in paramtype):
                        abspath = self.find_files(*keypath)
                        if abspath is None or (isinstance(abspath, list) and None in abspath):
                            self.logger.error(f"Required file keypath {keypath} can't be resolved.")
                            self.error = 1

        return self.error

    ###########################################################################
    def _gather_outputs(self, step, index):
        '''Return set of filenames that are guaranteed to be in outputs
        directory after a successful run of step/index.'''

        flow = self.get('flow')
        tool = self.get('flowgraph', flow, step, index, 'tool')

        outputs = set()
        if tool in self.builtin:
            in_tasks = self.get('flowgraph', flow, step, index, 'input')
            in_task_outputs = [self._gather_outputs(*task) for task in in_tasks]

            if tool in ('minimum', 'maximum'):
                if len(in_task_outputs) > 0:
                    outputs = in_task_outputs[0].intersection(*in_task_outputs[1:])
            elif tool in ('join', 'nop'):
                if len(in_task_outputs) > 0:
                    outputs = in_task_outputs[0].union(*in_task_outputs[1:])
            else:
                # TODO: logic should be added here when mux/verify builtins are implemented.
                self.logger.error(f'Builtin {tool} not yet implemented')
        else:
            # Not builtin tool
            if self.valid('eda', tool, 'output', step, index):
                outputs = set(self.get('eda', tool, 'output', step, index))
            else:
                outputs = set()

        if step == 'import':
            imports = {self._get_imported_filename(p) for p in self._collect_paths()}
            outputs.update(imports)

        return outputs

    ###########################################################################
    def _check_flowgraph_io(self):
        '''Check if flowgraph is valid in terms of input and output files.

        Returns True if valid, False otherwise.
        '''

        flow = self.get('flow')
        steplist = self.get('steplist')

        if not steplist:
            steplist = self.list_steps()

        if len(steplist) < 2:
            return True

        for step in steplist:
            for index in self.getkeys('flowgraph', flow, step):
                # For each task, check input requirements.
                tool = self.get('flowgraph', flow, step, index, 'tool')
                if tool in self.builtin:
                    # We can skip builtins since they don't have any particular
                    # input requirements -- they just pass through what they
                    # receive.
                    continue

                # Get files we receive from input tasks.
                in_tasks = self.get('flowgraph', flow, step, index, 'input')
                if len(in_tasks) > 1:
                    self.logger.error(f'Tool task {step}{index} has more than one input task.')
                elif len(in_tasks) > 0:
                    in_step, in_index = in_tasks[0]
                    if in_step not in steplist:
                        # If we're not running the input step, the required
                        # inputs need to already be copied into the build
                        # directory.
                        jobname = self.get('jobname')
                        if self.valid('jobinput', jobname, step, index):
                            in_job = self.get('jobinput', jobname, step, index)
                        else:
                            in_job = jobname
                        workdir = self._getworkdir(jobname=in_job, step=in_step, index=in_index)
                        in_step_out_dir = os.path.join(workdir, 'outputs')
                        inputs = set(os.listdir(in_step_out_dir))
                    else:
                        inputs = self._gather_outputs(in_step, in_index)
                else:
                    inputs = set()

                if self.valid('eda', tool, 'input', step, index):
                    requirements = self.get('eda', tool, 'input', step, index)
                else:
                    requirements = []
                for requirement in requirements:
                    if requirement not in inputs:
                        self.logger.error(f'Invalid flow: {step}{index} will '
                            f'not receive required input {requirement}.')
                        return False

        return True

    ###########################################################################
    def read_manifest(self, filename, job=None, clear=True, clobber=True):
        """
        Reads a manifest from disk and merges it with the current compilation manifest.

        The file format read is determined by the filename suffix. Currently
        json (*.json) and yaml(*.yaml) formats are supported.

        Args:
            filename (filepath): Path to a manifest file to be loaded.
            job (str): Specifies non-default job to merge into.
            clear (bool): If True, disables append operations for list type.
            clobber (bool): If True, overwrites existing parameter value.

        Examples:
            >>> chip.read_manifest('mychip.json')
            Loads the file mychip.json into the current Chip object.
        """

        abspath = os.path.abspath(filename)
        self.logger.debug('Reading manifest %s', abspath)

        #Read arguments from file based on file type
        with open(abspath, 'r') as f:
            if abspath.endswith('.json'):
                localcfg = json.load(f)
            elif abspath.endswith('.yaml') | abspath.endswith('.yml'):
                localcfg = yaml.load(f, Loader=yaml.SafeLoader)
            else:
                self.error = 1
                self.logger.error('Illegal file format. Only json/yaml supported')
        f.close()

        #Merging arguments with the Chip configuration
        self.merge_manifest(localcfg, job=job, clear=clear, clobber=clobber)

    ###########################################################################
    def write_manifest(self, filename, prune=True, abspath=False, job=None):
        '''
        Writes the compilation manifest to a file.

        The write file format is determined by the filename suffix. Currently
        json (*.json), yaml (*.yaml), tcl (*.tcl), and (*.csv) formats are
        supported.

        Args:
            filename (filepath): Output filepath
            prune (bool): If True, essential non-empty parameters from the
                 the Chip object schema are written to the output file.
            abspath (bool): If set to True, then all schema filepaths
                 are resolved to absolute filepaths.

        Examples:
            >>> chip.write_manifest('mydump.json')
            Prunes and dumps the current chip manifest into mydump.json
        '''

        filepath = os.path.abspath(filename)
        self.logger.debug('Writing manifest to %s', filepath)

        if not os.path.exists(os.path.dirname(filepath)):
            os.makedirs(os.path.dirname(filepath))

        if prune:
            self.logger.debug('Pruning dictionary before writing file %s', filepath)
            # Keep empty lists to simplify TCL coding
            if filepath.endswith('.tcl'):
                keeplists = True
            else:
                keeplists = False
            cfgcopy = self._prune(self.cfg, keeplists=keeplists)
        else:
            cfgcopy = copy.deepcopy(self.cfg)

        # resolve absolute paths
        if abspath:
            self._abspath(cfgcopy)

        # TODO: fix
        #remove long help (adds no value)
        #allkeys = self.getkeys(cfg=cfgcopy)
        #for key in allkeys:
        #    self.set(*key, "...", cfg=cfgcopy, field='help')

        # format specific dumping
        with open(filepath, 'w') as f:
            if filepath.endswith('.json'):
                print(json.dumps(cfgcopy, indent=4, sort_keys=True), file=f)
            elif filepath.endswith('.yaml') | filepath.endswith('yml'):
                print(yaml.dump(cfgcopy, Dumper=YamlIndentDumper, default_flow_style=False), file=f)
            elif filepath.endswith('.core'):
                cfgfuse = self._dump_fusesoc(cfgcopy)
                print("CAPI=2:", file=f)
                print(yaml.dump(cfgfuse, Dumper=YamlIndentDumper, default_flow_style=False), file=f)
            elif filepath.endswith('.tcl'):
                print("#############################################", file=f)
                print("#!!!! AUTO-GENERATED FILE. DO NOT EDIT!!!!!!", file=f)
                print("#############################################", file=f)
                self._print_tcl(cfgcopy, prefix="dict set sc_cfg", file=f)
            elif filepath.endswith('.csv'):
                self._print_csv(cfgcopy, file=f)
            else:
                self.logger.error('File format not recognized %s', filepath)
                self.error = 1

    ###########################################################################
    def check_checklist(self, standard, item=None):
        '''
        Check an item in checklist.

        Checks the status of an item in the checklist for the standard
        provided. If the item is unspecified, all items are checked.

        The function relies on the checklist 'criteria' parameter and
        'step' parameter to check for the existence of report filess
        and a passing metric based criteria. Checklist items with
        empty 'report' values or unmet criteria result in error messages
        and raising the error flag.

        Args:
            standard(str): Standard to check.
            item(str): Item to check from standard.

        Returns:
            Status of item check.

        Examples:
            >>> status = chip.check_checklist('iso9000', 'd000')
            Returns status.
        '''

        if item is None:
            items = self.getkeys('checklist', standard)
        else:
            items = [item]

        flow = self.get('flow')
        global_check = True

        for item in items:
            step = self.get('checklist', standard, item, 'step')
            index = self.get('checklist', standard, item, 'index')
            all_criteria = self.get('checklist', standard, item, 'criteria')
            report_ok = False
            criteria_ok = True
            # manual
            if step not in self.getkeys('flowgraph',flow):
                #criteria not used, so always ok
                criteria_ok = True
                if len(self.getkeys('checklist',standard, item, 'report')) <2:
                    self.logger.error(f"No report found for {item}")
                    report_ok = False
            else:
                tool = self.get('flowgraph', flow, step, index, 'tool')
                # copy report paths over to checklsit
                for reptype in self.getkeys('eda', tool, 'report', step, index):
                    report_ok = True
                    report = self.get('eda', tool, 'report', step, index, reptype)
                    self.set('checklist', standard, item, 'report', reptype, report)
                # quantifiable checklist criteria
                for criteria in all_criteria:
                    m = re.match(r'(\w+)([\>\=\<]+)(\w+)', criteria)
                    if not m:
                        self.logger.error(f"Illegal checklist criteria: {criteria}")
                        return False
                    elif m.group(1) not in self.getkeys('metric', step, index):
                        self.logger.error(f"Critera must use legal metrics only: {criteria}")
                        return False
                    else:
                        param = m.group(1)
                        op = m.group(2)
                        goal = str(m.group(3))
                        value = str(self.get('metric', step, index, param, 'real'))
                        criteria_ok = self._safecompare(value, op, goal)

            #item check
            if not report_ok:
                self.logger.error(f"Report missing for checklist: {standard} {item}")
                global_check = False
                self.error = 1
            elif not criteria_ok:
                self.logger.error(f"Criteria check failed for checklist: {standard} {item}")
                global_check = False
                self.error = 1

        return global_check

    ###########################################################################
    def read_file(self, filename, step='import', index='0'):
        '''
        Read file defined in schema. (WIP)
        '''
        return(0)

    ###########################################################################
    def package(self, filename, prune=True):
        '''
        Create sanitized project package. (WIP)

        The SiliconCompiler project is filtered and exported as a JSON file.
        If the prune option is set to True, then all metrics, records and
        results are pruned from the package file.

        Args:
            filename (filepath): Output filepath
            prune (bool): If True, only essential source parameters are
                 included in the package.

        Examples:
            >>> chip.package('package.json')
            Write project information to 'package.json'
        '''

        return(0)

    ###########################################################################
    def publish(self, filename):
        '''
        Publishes package to registry. (WIP)

        The filename is uploaed to a central package registry based on the
        the user credentials found in ~/.sc/credentials.

        Args:
            filename (filepath): Package filename

        Examples:
            >>> chip.publish('hello.json')
            Publish hello.json to central repository.
        '''

        return(0)


    ###########################################################################
    def _dump_fusesoc(self, cfg):
        '''
        Internal function for dumping core information from chip object.
        '''

        fusesoc = {}

        toplevel = self.get('design', cfg=cfg)

        if self.get('name'):
            name = self.get('name', cfg=cfg)
        else:
            name = toplevel

        version = self.get('projversion', cfg=cfg)

        # Basic information
        fusesoc['name'] = f"{name}:{version}"
        fusesoc['description'] = self.get('description', cfg=cfg)
        fusesoc['filesets'] = {}

        # RTL
        #TODO: place holder fix with pre-processor list
        files = []
        for item in self.get('source', cfg=cfg):
            files.append(item)

        fusesoc['filesets']['rtl'] = {}
        fusesoc['filesets']['rtl']['files'] = files
        fusesoc['filesets']['rtl']['depend'] = {}
        fusesoc['filesets']['rtl']['file_type'] = {}

        # Constraints
        files = []
        for item in self.get('constraint', cfg=cfg):
            files.append(item)

        fusesoc['filesets']['constraints'] = {}
        fusesoc['filesets']['constraints']['files'] = files

        # Default Target
        fusesoc['targets'] = {}
        fusesoc['targets']['default'] = {
            'filesets' : ['rtl', 'constraints', 'tb'],
            'toplevel' : toplevel
        }

        return fusesoc

    ###########################################################################

    def write_flowgraph(self, filename, flow=None,
                        fillcolor='#ffffff', fontcolor='#000000',
                        fontsize='14', border=True, landscape=False):
        '''Renders and saves the compilation flowgraph to a file.

        The chip object flowgraph is traversed to create a graphviz (\*.dot)
        file comprised of node, edges, and labels. The dot file is a
        graphical representation of the flowgraph useful for validating the
        correctness of the execution flow graph. The dot file is then
        converted to the appropriate picture or drawing format based on the
        filename suffix provided. Supported output render formats include
        png, svg, gif, pdf and a few others. For more information about the
        graphviz project, see see https://graphviz.org/

        Args:
            filename (filepath): Output filepath
            flow (str): Name of flowgraph to render
            fillcolor(str): Node fill RGB color hex value
            fontcolor (str): Node font RGB color hex value
            fontsize (str): Node text font size
            border (bool): Enables node border if True
            landscape (bool): Renders graph in landscape layout if True

        Examples:
            >>> chip.write_flowgraph('mydump.png')
            Renders the object flowgraph and writes the result to a png file.
        '''
        filepath = os.path.abspath(filename)
        self.logger.debug('Writing flowgraph to file %s', filepath)
        fileroot, ext = os.path.splitext(filepath)
        fileformat = ext.replace(".", "")

        if flow is None:
            flow = self.get('flow')

        # controlling border width
        if border:
            penwidth = '1'
        else:
            penwidth = '0'

        # controlling graph direction
        if landscape:
            rankdir = 'LR'
        else:
            rankdir = 'TB'

        dot = graphviz.Digraph(format=fileformat)
        dot.graph_attr['rankdir'] = rankdir
        dot.attr(bgcolor='transparent')
        for step in self.getkeys('flowgraph',flow):
            irange = 0
            for index in self.getkeys('flowgraph', flow, step):
                irange = irange +1
            for i in range(irange):
                index = str(i)
                node = step+index
                # create step node
                tool =  self.get('flowgraph', flow, step, index, 'tool')
                if tool in self.builtin:
                    labelname = step
                elif tool is not None:
                    labelname = f"{step}{index}\n({tool})"
                else:
                    labelname = f"{step}{index}"
                dot.node(node, label=labelname, bordercolor=fontcolor, style='filled',
                         fontcolor=fontcolor, fontsize=fontsize, ordering="in",
                         penwidth=penwidth, fillcolor=fillcolor)
                # get inputs
                all_inputs = []
                for in_step, in_index in self.get('flowgraph', flow, step, index, 'input'):
                    all_inputs.append(in_step + in_index)
                for item in all_inputs:
                    dot.edge(item, node)
        dot.render(filename=fileroot, cleanup=True)

    ########################################################################
    def _collect_paths(self):
        '''
        Returns list of paths to files that will be collected by import step.

        See docstring for _collect() for more details.
        '''
        paths = []

        copyall = self.get('copyall')
        allkeys = self.getkeys()
        for key in allkeys:
            leaftype = self.get(*key, field='type')
            if re.search('file', leaftype):
                copy = self.get(*key, field='copy')
                value = self.get(*key)
                if copyall or copy:
                    for item in value:
                        paths.append(item)

        return paths

    ########################################################################
    def _collect(self, step, index, active):
        '''
        Collects files found in the configuration dictionary and places
        them in inputs/. The function only copies in files that have the 'copy'
        field set as true. If 'copyall' is set to true, then all files are
        copied in.

        1. indexing like in run, job1
        2. chdir package
        3. run tool to collect files, pickle file in output/design.v
        4. copy in rest of the files below
        5. record files read in to schema

        '''

        indir = 'inputs'
        flow = self.get('flow')

        if not os.path.exists(indir):
            os.makedirs(indir)

        self.logger.info('Collecting input sources')

        for path in self._collect_paths():
            filename = self._get_imported_filename(path)
            abspath = self._find_sc_file(path)
            if abspath:
                self.logger.info(f"Copying {abspath} to '{indir}' directory")
                shutil.copy(abspath, os.path.join(indir, filename))
            else:
                self._haltstep(step, index, active)

        outdir = 'outputs'
        if not os.path.exists(outdir):
            os.makedirs(outdir)

        # Logic to make links from outputs/ to inputs/, skipping anything that
        # will be output by the tool as well as the manifest. We put this here
        # so that tools used for the import stage don't have to duplicate this
        # logic. We skip this logic for 'join'-based single-step imports, since
        # 'join' does the copy for us.
        tool = self.get('flowgraph', flow, step, index, 'tool')
        if tool not in self.builtin:
            if self.valid('eda', tool, 'output', step, index):
                outputs = self.get('eda', tool, 'output', step, index)
            else:
                outputs = []
            design = self.get('design')
            ignore = outputs + [f'{design}.pkg.json']
            utils.copytree(indir, outdir, dirs_exist_ok=True, link=True, ignore=ignore)
        elif tool not in ('join', 'nop'):
            self.error = 1
            self.logger.error(f'Invalid import step builtin {tool}. Must be tool or join.')

    ###########################################################################
    def archive(self, step=None, index=None, all_files=False):
        '''Archive a job directory.

        Creates a single compressed archive (.tgz) based on the design,
        jobname, and flowgraph in the current chip manifest. Individual
        steps and/or indices can be archived based on argumnets specified.
        By default, all steps and indices in the flowgraph are archived.
        By default, only the outputs directory content and the log file
        are archived.

        Args:
            step(str): Step to archive.
            index (str): Index to archive
            all_files (bool): If True, all files are archived.

        '''

        jobname = self.get('jobname')
        design = self.get('design')
        buildpath = self.get('dir')

        if step:
            steplist = [step]
        elif self.get('arg', 'step'):
            steplist = [self.get('arg', 'step')]
        elif self.get('steplist'):
            steplist = self.get('steplist')
        else:
            steplist = self.list_steps()

        if step:
            archive_name = f"{design}_{jobname}_{step}.tgz"
        else:
            archive_name = f"{design}_{jobname}.tgz"

        with tarfile.open(archive_name, "w:gz") as tar:
            for step in steplist:
                if index:
                    indexlist = [index]
                else:
                    indexlist = self.getkeys('flowgraph', flow, step)
                for item in indexlist:
                    basedir = os.path.join(buildpath, design, jobname, step, item)
                    if all_files:
                         tar.add(os.path.abspath(basedir), arcname=basedir)
                    else:
                        outdir = os.path.join(basedir,'outputs')
                        logfile = os.path.join(basedir, step+'.log')
                        tar.add(os.path.abspath(outdir), arcname=outdir)
                        if os.path.isfile(logfile):
                            tar.add(os.path.abspath(logfile), arcname=logfile)

    ###########################################################################
    def hash_files(self, *keypath, algo='sha256', update=True):
        '''Generates hash values for a list of parameter files.

        Generates a a hash value for each file found in the keypath.
        If the  update variable is True, the has values are recorded in the
        'filehash' field of the parameter, following the order dictated by
        the files within the 'values' parameter field.

        Files are located using the find_files() function.

        The file hash calculation is performed basd on the 'algo' setting.
        Supported algorithms include SHA1, SHA224, SHA256, SHA384, SHA512,
        and MD5.

        Args:
            *keypath(str): Keypath to parameter.
            algo (str): Algorithm to use for file hash calculation
            update (bool): If True, the hash values are recorded in the
                chip object manifest.

        Returns:
            A list of hash values.

        Examples:
            >>> hashlist = hash_files('sources')
             Hashlist gets list of hash values computed from 'sources' files.
        '''

        keypathstr = ','.join(keypath)
        #TODO: Insert into find_files?
        if 'file' not in self.get(*keypath, field='type'):
            self.logger.error(f"Illegal attempt to hash non-file parameter [{keypathstr}].")
            self.error = 1
        else:
            filelist = self.find_files(*keypath)
            #cycle through all paths
            hashlist = []
            if filelist:
                self.logger.info(f'Computing hash value for [{keypathstr}]')
            for filename in filelist:
                if os.path.isfile(filename):
                    #TODO: Implement algo selection
                    hashobj = hashlib.sha256()
                    with open(filename, "rb") as f:
                        for byte_block in iter(lambda: f.read(4096), b""):
                            hashobj.update(byte_block)
                    hash_value = hashobj.hexdigest()
                    hashlist.append(hash_value)
                else:
                    self.error = 1
                    self.logger.info(f"Internal hashing error, file not found")
            # compare previous hash to new hash
            oldhash = self.get(*keypath,field='filehash')
            for i,item in enumerate(oldhash):
                if item != hashlist[i]:
                    self.logger.error(f"Hash mismatch for [{keypath}]")
                    self.error = 1
            self.set(*keypath, hashlist, field='filehash', clobber=True)


    ###########################################################################
    def audit_manifest(self):
        '''Verifies the integrity of the post-run compilation manifest.

        Checks the integrity of the chip object implementation flow after
        the run() function has been completed. Errors, warnings, and debug
        messages are reported through the logger object.

        Audit checks performed include:

        * Time stamps
        * File modifications
        * Error and warning policy
        * IP and design origin
        * User access
        * License terms
        * Version checks

        Returns:
            Returns True if the manifest has integrity, else returns False.

        Example:
            >>> chip.audit_manifest()
            Audits the Chip object manifest and returns 0 if successful.

        '''

        return 0


    ###########################################################################
    def calc_area(self):
        '''Calculates the area of a rectilinear diearea.

        Uses the shoelace formulate to calculate the design area using
        the (x,y) point tuples from the 'diearea' parameter. If only diearea
        paramater only contains two points, then the first and second point
        must be the lower left and upper right points of the rectangle.
        (Ref: https://en.wikipedia.org/wiki/Shoelace_formula)

        Returns:
            Design area (float).

        Examples:
            >>> area = chip.calc_area()

        '''

        vertices = self.get('asic', 'diearea')

        if len(vertices) == 2:
            width = vertices[1][0] - vertices[0][0]
            height = vertices[1][1] - vertices[0][1]
            area = width * height
        else:
            area = 0.0
            for i in range(len(vertices)):
                j = (i + 1) % len(vertices)
                area += vertices[i][0] * vertices[j][1]
                area -= vertices[j][0] * vertices[i][1]
            area = abs(area) / 2

        return area

    ###########################################################################
    def calc_yield(self, model='poisson'):
        '''Calculates raw die yield.

        Calculates the raw yield of the design as a function of design area
        and d0 defect density. Calculation can be done based on the poisson
        model (default) or the murphy model. The die area and the d0
        parameters are taken from the chip dictionary.

        * Poisson model: dy = exp(-area * d0/100).
        * Murphy model: dy = ((1-exp(-area * d0/100))/(area * d0/100))^2.

        Args:
            model (string): Model to use for calculation (poisson or murphy)

        Returns:
            Design yield percentage (float).

        Examples:
            >>> yield = chip.calc_yield()
            Yield variable gets yield value based on the chip manifest.
        '''

        d0 = self.get('pdk', 'd0')
        diearea = self.calc_area()

        if model == 'poisson':
            dy = math.exp(-diearea * d0/100)
        elif model == 'murphy':
            dy = ((1-math.exp(-diearea * d0/100))/(diearea * d0/100))**2

        return dy

    ##########################################################################
    def calc_dpw(self):
        '''Calculates dies per wafer.

        Calculates the gross dies per wafer based on the design area, wafersize,
        wafer edge margin, and scribe lines. The calculation is done by starting
        at the center of the wafer and placing as many complete design
        footprints as possible within a legal placement area.

        Returns:
            Number of gross dies per wafer (int).

        Examples:
            >>> dpw = chip.calc_dpw()
            Variable dpw gets gross dies per wafer value based on the chip manifest.
        '''

        #PDK information
        wafersize = self.get('pdk', 'wafersize')
        edgemargin = self.get('pdk', 'edgemargin')
        hscribe = self.get('pdk', 'hscribe')
        vscribe = self.get('pdk', 'vscribe')

        #Design parameters
        diesize = self.get('asic', 'diesize').split()
        diewidth = (diesize[2] - diesize[0])/1000
        dieheight = (diesize[3] - diesize[1])/1000

        #Derived parameters
        radius = wafersize/2 -edgemargin
        stepwidth = (diewidth + hscribe)
        stepheight = (dieheight + vscribe)

        #Raster dies out from center until you touch edge margin
        #Work quadrant by quadrant
        dies = 0
        for quad in ('q1', 'q2', 'q3', 'q4'):
            x = 0
            y = 0
            if quad == "q1":
                xincr = stepwidth
                yincr = stepheight
            elif quad == "q2":
                xincr = -stepwidth
                yincr = stepheight
            elif quad == "q3":
                xincr = -stepwidth
                yincr = -stepheight
            elif quad == "q4":
                xincr = stepwidth
                yincr = -stepheight
            #loop through all y values from center
            while math.hypot(0, y) < radius:
                y = y + yincr
                while math.hypot(x, y) < radius:
                    x = x + xincr
                    dies = dies + 1
                x = 0

        return int(dies)

    ###########################################################################
    def grep(self, args, line):
        """
        Emulates the Unix grep command on a string.

        Emulates the behavior of the Unix grep command that is etched into
        our muscle memory. Partially implemented, not all features supported.
        The function returns None if no match is found.

        Args:
            arg (string): Command line arguments for grep command
            line (string): Line to process

        Returns:
            Result of grep command (string).

        """

        # Quick return if input is None
        if line is None:
            return None

        # Partial list of supported grep options
        options = {
            '-v' : False, # Invert the sense of matching
            '-i' : False, # Ignore case distinctions in patterns and data
            '-E' : False, # Interpret PATTERNS as extended regular expressions.
            '-e' : False, # Safe interpretation of pattern starting with "-"
            '-x' : False, # Select only matches that exactly match the whole line.
            '-o' : False, # Print only the match parts of a matching line
            '-w' : False} # Select only lines containing matches that form whole words.

        # Split into repeating switches and everything else
        match = re.match(r'\s*((?:\-\w\s)*)(.*)', args)

        pattern = match.group(2)

        # Split space separated switch string into list
        switches = match.group(1).strip().split(' ')

        # Find special -e switch update the pattern
        for i in range(len(switches)):
            if switches[i] == "-e":
                if i != (len(switches)):
                    pattern = ' '.join(switches[i+1:]) + " " + pattern
                    switches = switches[0:i+1]
                    break
                options["-e"] = True
            elif switches[i] in options.keys():
                options[switches[i]] = True
            elif switches[i] !='':
                print("ERROR",switches[i])

        #REGEX
        #TODO: add all the other optinos
        match = re.search(rf"({pattern})", line)
        if bool(match) == bool(options["-v"]):
            return None
        else:
            return line

    ###########################################################################
    def check_logfile(self, jobname=None, step=None, index='0',
                      logfile=None, display=True):
        '''
        Checks logfile for patterns found in the 'regex' parameter.

        Reads the content of the step's log file and compares the
        content found in step 'regex' parameter. The matches are
        stored in the file 'reports/<design>.<suffix>' in the run directory.
        The matches are printed to STDOUT if display is set to True.

        Args:
            step (str): Task step name ('syn', 'place', etc)
            jobname (str): Jobid directory name
            index (str): Task index
            display (bool): If True, printes matches to STDOUT.

        Examples:
            >>> chip.check_logfile('place')
            Searches for regex matches in the place logfile.
        '''

        # Using manifest to get defaults

        flow = self.get('flow')
        design = self.get('design')

        if jobname is None:
            jobname = self.get('jobname')
        if logfile is None:
            logfile = f"{step}.log"
        if step is None:
            step = self.get('arg', 'step')
        if index is None:
            index = self.getkeys('flowgraph', flow, step)[0]

        tool = self.get('flowgraph', flow, step, index, 'tool')

        # Creating local dictionary (for speed)
        # self.get is slow
        checks = {}
        regex_list = []
        if self.valid('eda', tool, 'regex', step, index, 'default'):
            regex_list = self.getkeys('eda', tool, 'regex', step, index)
        for suffix in regex_list:
            checks[suffix] = {}
            checks[suffix]['report'] = open(f"{step}.{suffix}", "w")
            checks[suffix]['args'] = self.get('eda', tool, 'regex', step, index, suffix)

        # Looping through patterns for each line
        with open(logfile) as f:
          for line in f:
              for suffix in checks:
                  string = line
                  for item in checks[suffix]['args']:
                      if string is None:
                          break
                      else:
                          string = self.grep(item, string)
                  if string is not None:
                      #always print to file
                      print(string.strip(), file=checks[suffix]['report'])
                      #selectively print to display
                      if display:
                          self.logger.info(string.strip())

    ###########################################################################
    def summary(self, steplist=None, show_all_indices=False):
        '''
        Prints a summary of the compilation manifest.

        Metrics from the flowgraph steps, or steplist parameter if
        defined, are printed out on a per step basis. All metrics from the
        metric dictionary with weights set in the flowgraph dictionary are
        printed out.

        Args:
            show_all_indices (bool): If True, displays metrics for all indices
                of each step. If False, displays metrics only for winning
                indices.

        Examples:
            >>> chip.summary()
            Prints out a summary of the run to stdout.
        '''

        # display whole flowgraph if no steplist specified
        flow = self.get('flow')
        if not steplist:
            steplist = self.list_steps()

        #only report tool based steps functions
        for step in steplist:
            if self.get('flowgraph',flow, step,'0','tool') in self.builtin:
                index = steplist.index(step)
                del steplist[index]

        # job directory
        jobdir = self._getworkdir()

        # Custom reporting modes
        paramlist = []
        for item in self.getkeys('param'):
            paramlist.append(item+"="+self.get('param',item))

        if paramlist:
            paramstr = ', '.join(paramlist)
        else:
            paramstr = "None"

        info_list = ["SUMMARY:\n",
                     "design : " + self.get('design'),
                     "params : " + paramstr,
                     "jobdir : "+ jobdir,
                     ]

        if self.get('mode') == 'asic':
            info_list.extend(["foundry : " + self.get('pdk', 'foundry'),
                              "process : " + self.get('pdk', 'process'),
                              "targetlibs : "+" ".join(self.get('asic', 'logiclib'))])
        elif self.get('mode') == 'fpga':
            info_list.extend(["partname : "+self.get('fpga','partname')])

        info = '\n'.join(info_list)


        print("-"*135)
        print(info, "\n")

        # Stepping through all steps/indices and printing out metrics
        data = []

        #Creating Header
        header = []
        indices_to_show = {}
        colwidth = 8
        for step in steplist:
            if show_all_indices:
                indices_to_show[step] = self.getkeys('flowgraph', flow, step)
            else:
                # Default for last step in list (could be tool or function)
                indices_to_show[step] = ['0']

                # Find winning index
                for index in self.getkeys('flowgraph', flow, step):
                    stepindex = step + index
                    for i in  self.getkeys('flowstatus'):
                        for j in  self.getkeys('flowstatus',i):
                            for in_step, in_index in self.get('flowstatus',i,j,'select'):
                                if (in_step + in_index) == stepindex:
                                    indices_to_show[step] = index

        # header for data frame
        for step in steplist:
            for index in indices_to_show[step]:
                header.append(f'{step}{index}'.center(colwidth))

        # figure out which metrics have non-zero weights
        metric_list = []
        for step in steplist:
            for metric in self.getkeys('metric','default','default'):
                if metric in self.getkeys('flowgraph', flow, step, '0', 'weight'):
                    if self.get('flowgraph', flow, step, '0', 'weight', metric) is not None:
                        if metric not in metric_list:
                            metric_list.append(metric)

        # print out all metrics
        metrics = []
        for metric in metric_list:
            metrics.append(" " + metric)
            row = []
            for step in steplist:
                for index in indices_to_show[step]:
                    value = None
                    if 'real' in self.getkeys('metric', step, index, metric):
                        value = self.get('metric', step, index, metric, 'real')

                    if value is None:
                        value = 'ERR'
                    else:
                        value = str(value)

                    row.append(" " + value.center(colwidth))
            data.append(row)

        pandas.set_option('display.max_rows', 500)
        pandas.set_option('display.max_columns', 500)
        pandas.set_option('display.width', 100)
        df = pandas.DataFrame(data, metrics, header)
        print(df.to_string())
        print("-"*135)

        # Create a report for the Chip object which can be viewed in a web browser.
        # Place report files in the build's root directory.
        web_dir = os.path.join(self.get('dir'),
                               self.get('design'),
                               self.get('jobname'))
        if os.path.isdir(web_dir):
            # Gather essential variables.
            templ_dir = os.path.join(self.scroot, 'templates', 'report')
            flow = self.get('flow')
            flow_steps = steplist
            flow_tasks = {}
            for step in flow_steps:
                flow_tasks[step] = self.getkeys('flowgraph', flow, step)

            # Copy Bootstrap JS/CSS
            shutil.copyfile(os.path.join(templ_dir, 'bootstrap.min.js'),
                            os.path.join(web_dir, 'bootstrap.min.js'))
            shutil.copyfile(os.path.join(templ_dir, 'bootstrap.min.css'),
                            os.path.join(web_dir, 'bootstrap.min.css'))

            # Call 'show()' to generate a low-res PNG of the design.
            results_gds = self.find_result('gds', step='export')
            if results_gds:
                self.show(results_gds,
                          ['-rd', 'screenshot=1', '-rd', 'scr_w=1024', '-rd', 'scr_h=1024', '-z'])

            # Generate results page by passing the Chip manifest into the Jinja2 template.
            env = Environment(loader=FileSystemLoader(templ_dir))
            results_page = os.path.join(web_dir, 'report.html')
            with open(results_page, 'w') as wf:
                wf.write(env.get_template('sc_report.j2').render(
                    manifest = self.cfg,
                    metric_keys = metric_list,
                    metrics = self.cfg['metric'],
                    tasks = flow_tasks,
                    results_fn = results_gds
                ))

            # Try to open the results page in a browser, only if '-nodisplay' is not set.
            if not self.get('nodisplay'):
                try:
                    webbrowser.get(results_page)
                except webbrowser.Error:
                    # Python 'webbrowser' module includes a limited number of popular defaults.
                    # Depending on the platform, the user may have defined their own with $BROWSER.
                    if 'BROWSER' in os.environ:
                        subprocess.run([os.environ['BROWSER'], results_page])
                    else:
                        self.logger.warning('Unable to open results page in web browser:\n' +
                                            os.path.abspath(os.path.join(web_dir, "report.html")))

    ###########################################################################
    def list_steps(self, flow=None):
        '''
        Returns an ordered list of flowgraph steps.

        All step keys from the flowgraph dictionary are collected and the
        distance from the root node (ie. without any inputs defined) is
        measured for each step. The step list is then sorted based on
        the distance from root and returned.

        Returns:
            A list of steps sorted by distance from the root node.

        Example:
            >>> steplist = chip.list_steps()
            Variable steplist gets list of steps sorted by distance from root.
        '''

        if flow is None:
            flow = self.get('flow')

        #Get length of paths from step to root
        depth = {}
        for step in self.getkeys('flowgraph', flow):
            depth[step] = 0
            for path in self._allpaths(self.cfg, flow, step, str(0)):
                if len(list(path)) > depth[step]:
                    depth[step] = len(path)

        #Sort steps based on path lenghts
        sorted_dict = dict(sorted(depth.items(), key=lambda depth: depth[1]))
        return list(sorted_dict.keys())

    ###########################################################################
    def _allpaths(self, cfg, flow, step, index, path=None):
        '''Recursive helper for finding all paths from provided step, index to
        root node(s) with no inputs.

        Returns a list of lists.
        '''

        if path is None:
            path = []

        inputs = self.get('flowgraph', flow, step, index, 'input', cfg=cfg)

        if not self.get('flowgraph', flow, step, index, 'input', cfg=cfg):
            return [path]
        else:
            allpaths = []
            for in_step, in_index in inputs:
                newpath = path.copy()
                newpath.append(in_step + in_index)
                allpaths.extend(self._allpaths(cfg, flow, in_step, in_index, path=newpath))

        return allpaths

    ###########################################################################
    def clock(self, *, name, pin, period, jitter=0):
        """
        Clock configuration helper function.

        A utility function for setting all parameters associated with a
        single clock definition in the schema.

        The method modifies the following schema parameters:

        ['clock', name, 'pin']
        ['clock', name, 'period']
        ['clock', name, 'jitter']

        Args:
            name (str): Clock reference name.
            pin (str): Full hiearchical path to clk pin.
            period (float): Clock period specified in ns.
            jitter (float): Clock jitter specified in ns.

        Examples:
            >>> chip.clock(name='clk', pin='clk, period=1.0)
           Create a clock namedd 'clk' with a 1.0ns period.
        """

        self.set('clock', name, 'pin', pin)
        self.set('clock', name, 'period', period)
        self.set('clock', name, 'jitter', jitter)

    ###########################################################################
    def node(self, flow, step, tool, index=0):
        '''
        Creates a flowgraph node.

        Creates a flowgraph node by binding a tool to a task. A task is defined
        as the combination of a step and index. A tool can be an external
        exeuctable or one of the built in functions in the SiliconCompiler
        framework). Built in functions include: minimum, maximum, join, mux,
        verify.

        The method modifies the following schema parameters:

        ['flowgraph', flow, step, index, 'tool', tool]
        ['flowgraph', flow, step, index, 'weight', metric]

        Args:
            flow (str): Flow name
            step (str): Task step name
            tool (str): Tool (or builtin function) to associate with task.
            index (int): Task index

        Examples:
            >>> chip.node('asicflow', 'place', 'openroad', index=0)
            Creates a task with step='place' and index=0 and binds it to the 'openroad' tool.
        '''

        # bind tool to node
        self.set('flowgraph', flow, step, str(index), 'tool', tool)
        # set default weights
        for metric in self.getkeys('metric', 'default', 'default'):
            self.set('flowgraph', flow, step, str(index), 'weight', metric, 0)

    ###########################################################################
    def edge(self, flow, tail, head, tail_index=0, head_index=0):
        '''
        Creates a directed edge from a tail node to a head node.

        Connects the output of a tail node with the input of a head node by
        setting the 'input' field of the head node in the schema flowgraph.

        The method modifies the following parameters:

        ['flowgraph', flow, head, str(head_index), 'input']

        Args:
            flow (str): Name of flow
            tail (str): Name of tail node
            head (str): Name of head node
            tail_index (int): Index of tail node to connect
            head_index (int): Index of head node to connect

        Examples:
            >>> chip.edge('place', 'cts')
            Creates a directed edge from place to cts.
        '''

        self.add('flowgraph', flow, head, str(head_index), 'input', (tail, str(tail_index)))

    ###########################################################################
    def join(self, *tasks):
        '''
        Merges outputs from a list of input tasks.

        Args:
            tasks(list): List of input tasks specified as (step,index) tuples.

        Returns:
            Input list

        Examples:
            >>> select = chip.join([('lvs','0'), ('drc','0')])
           Select gets the list [('lvs','0'), ('drc','0')]
        '''

        tasklist = list(tasks)
        sel_inputs = tasklist

        # no score for join, so just return 0
        return sel_inputs

    ###########################################################################
    def nop(self, *task):
        '''
        A no-operation that passes inputs to outputs.

        Args:
            task(list): Input task specified as a (step,index) tuple.

        Returns:
            Input task

        Examples:
            >>> select = chip.nop(('lvs','0'))
           Select gets the tuple [('lvs',0')]
        '''

        return list(task)

    ###########################################################################
    def minimum(self, *tasks):
        '''
        Selects the task with the minimum metric score from a list of inputs.

        Sequence of operation:

        1. Check list of input tasks to see if all metrics meets goals
        2. Check list of input tasks to find global min/max for each metric
        3. Select MIN value if all metrics are met.
        4. Normalize the min value as sel = (val - MIN) / (MAX - MIN)
        5. Return normalized value and task name

        Meeting metric goals takes precedence over compute metric scores.
        Only goals with values set and metrics with weights set are considered
        in the calculation.

        Args:
            tasks(list): List of input tasks specified as (step,index) tuples.

        Returns:
            tuple containing

            - score (float): Minimum score
            - task (tuple): Task with minimum score

        Examples:
            >>> (score, task) = chip.minimum([('place','0'),('place','1')])

        '''
        return self._minmax(*tasks, op="minimum")

    ###########################################################################
    def maximum(self, *tasks):
        '''
        Selects the task with the maximum metric score from a list of inputs.

        Sequence of operation:

        1. Check list of input tasks to see if all metrics meets goals
        2. Check list of input tasks to find global min/max for each metric
        3. Select MAX value if all metrics are met.
        4. Normalize the min value as sel = (val - MIN) / (MAX - MIN)
        5. Return normalized value and task name

        Meeting metric goals takes precedence over compute metric scores.
        Only goals with values set and metrics with weights set are considered
        in the calculation.

        Args:
            tasks(list): List of input tasks specified as (step,index) tuples.

        Returns:
            tuple containing

            - score (float): Maximum score.
            - task (tuple): Task with minimum score

        Examples:
            >>> (score, task) = chip.maximum([('place','0'),('place','1')])

        '''
        return self._minmax(*tasks, op="maximum")

    ###########################################################################
    def _minmax(self, *steps, op="minimum", **selector):
        '''
        Shared function used for min and max calculation.
        '''

        if op not in ('minimum', 'maximum'):
            raise ValueError('Invalid op')

        flow = self.get('flow')
        steplist = list(steps)

        # Keeping track of the steps/indexes that have goals met
        failed = {}
        for step, index in steplist:
            if step not in failed:
                failed[step] = {}
            failed[step][index] = False

            if self.get('flowstatus', step, index, 'error'):
                failed[step][index] = True
            else:
                for metric in self.getkeys('metric', step, index):
                    if 'goal' in self.getkeys('metric', step, index, metric):
                        goal = self.get('metric', step, index, metric, 'goal')
                        real = self.get('metric', step, index, metric, 'real')
                        if abs(real) > goal:
                            self.logger.warning(f"Step {step}{index} failed "
                                f"because it didn't meet goals for '{metric}' "
                                "metric.")
                            failed[step][index] = True

        # Calculate max/min values for each metric
        max_val = {}
        min_val = {}
        for metric in self.getkeys('flowgraph', flow, step, '0', 'weight'):
            max_val[metric] = 0
            min_val[metric] = float("inf")
            for step, index in steplist:
                if not failed[step][index]:
                    real = self.get('metric', step, index, metric, 'real')
                    max_val[metric] = max(max_val[metric], real)
                    min_val[metric] = min(min_val[metric], real)

        # Select the minimum index
        best_score = float('inf') if op == 'minimum' else float('-inf')
        winner = None
        for step, index in steplist:
            if failed[step][index]:
                continue

            score = 0.0
            for metric in self.getkeys('flowgraph', flow, step, index, 'weight'):
                weight = self.get('flowgraph', flow, step, index, 'weight', metric)
                if not weight:
                    # skip if weight is 0 or None
                    continue

                real = self.get('metric', step, index, metric, 'real')

                if not (max_val[metric] - min_val[metric]) == 0:
                    scaled = (real - min_val[metric]) / (max_val[metric] - min_val[metric])
                else:
                    scaled = max_val[metric]
                score = score + scaled * weight

            if ((op == 'minimum' and score < best_score) or
                (op == 'maximum' and score > best_score)):
                best_score = score
                winner = (step,index)

        return (best_score, winner)

    ###########################################################################
    def verify(self, *tasks, **assertion):
        '''
        Tests an assertion on a list of input tasks.

        The provided steplist is verified to ensure that all assertions
        are True. If any of the assertions fail, False is returned.
        Assertions are passed in as kwargs, with the key being a metric
        and the value being a number and an optional conditional operator.
        The allowed conditional operators are: >, <, >=, <=

        Args:
            *steps (str): List of steps to verify
            **assertion (str='str'): Assertion to check on metric

        Returns:
            True if all assertions hold True for all steps.

        Example:
            >>> pass = chip.verify(['drc','lvs'], errors=0)
            Pass is True if the error metrics in the drc, lvs steps is 0.
        '''
        #TODO: implement
        return True

    ###########################################################################
    def mux(self, *tasks, **selector):
        '''
        Selects a task from a list of inputs.

        The selector criteria provided is used to create a custom function
        for selecting the best step/index pair from the inputs. Metrics and
        weights are passed in and used to select the step/index based on
        the minimum or maximum score depending on the 'op' argument.

        The function can be used to bypass the flows weight functions for
        the purpose of conditional flow execution and verification.

        Args:
            *steps (str): List of steps to verify
            **selector: Key value selection criteria.

        Returns:
            True if all assertions hold True for all steps.

        Example:
            >>> sel_stepindex = chip.mux(['route'], wirelength=0)
            Selects the routing stepindex with the shortest wirelength.
        '''

        #TODO: modify the _minmax function to feed in alternate weight path
        return None

    ###########################################################################
    def _runtask_safe(self, step, index, active, error):
        try:
            self._init_logger(step, index)
        except:
            traceback.print_exc()
            print(f"Uncaught exception while initializing logger for step {step}")
            self.error = 1
            self._haltstep(step, index, active, log=False)

        try:
            self._runtask(step, index, active, error)
        except SystemExit:
            # calling sys.exit() in _haltstep triggers a "SystemExit"
            # exception, but we can ignore these -- if we call sys.exit(), we've
            # already handled the error.
            pass
        except:
            traceback.print_exc()
            self.logger.error(f"Uncaught exception while running step {step}.")
            self.error = 1
            self._haltstep(step, index, active)

    ###########################################################################
    def _runtask(self, step, index, active, error):
        '''
        Private per step run method called by run().
        The method takes in a step string and index string to indicated what
        to run. Execution state coordinated through the active/error
        multiprocessing Manager dicts.

        Execution flow:
        T1. Wait in loop until all previous steps/indexes have completed
        T2. Start wall timer
        T3. Defer job to compute node if using job scheduler
        T4. Set up working directory + chdir
        T5. Merge manifests from all input dependancies
        T6. Write manifest to input directory for convenience
        T7. Reset all metrics to 0 (consider removing)
        T8. Select inputs
        T9. Copy data from previous step outputs into inputs
        T10. Copy reference script directory
        T11. Check manifest
        T12. Run pre_process() function
        T13. Set environment variables
        T14. Check EXE version
        T15. Save manifest as TCL/YAML
        T16. Start CPU timer
        T17. Run EXE
        T18. stop CPU timer
        T19. Run post_process()
        T20. Check log file
        T21. Hash all task files
        T22. Stop Wall timer
        T23. Make a task record
        T24. Save manifest to disk
        T25. Clean up
        T26. chdir
        T27. clear error/active bits and return control to run()

        Note that since _runtask occurs in its own process with a separate
        address space, any changes made to the `self` object will not
        be reflected in the parent. We rely on reading/writing the chip manifest
        to the filesystem to communicate updates between processes.
        '''

        ##################
        # Shared parameters (long function!)
        design = self.get('design')
        flow = self.get('flow')
        tool = self.get('flowgraph', flow, step, index, 'tool')
        quiet = self.get('quiet') and (step not in self.get('bkpt'))

        ##################
        # 1. Wait loop
        self.logger.info('Waiting for inputs...')
        while True:
            # Checking that there are no pending jobs
            pending = 0
            for in_step, in_index in self.get('flowgraph', flow, step, index, 'input'):
                pending = pending + active[in_step + in_index]
            # beak out of loop when no all inputs are done
            if not pending:
                break
            # Short sleep
            time.sleep(0.1)

        ##################
        # 2. Start wall timer
        wall_start = time.time()

        ##################
        # 3. Defer job to compute node
        # If the job is configured to run on a cluster, collect the schema
        # and send it to a compute node for deferred execution.
        # (Run the initial 'import' stage[s] locally)

        wall_start = time.time()

        if self.get('jobscheduler') and \
           self.get('flowgraph', flow, step, index, 'input'):
            # Note: The _deferstep method blocks until the compute node
            # finishes processing this step, and it sets the active/error bits.
            _deferstep(self, step, index, active, error)
            return

        ##################
        # 4. Directory setup
        # support for sharing data across jobs
        job = self.get('jobname')
        in_job = job
        if job in self.getkeys('jobinput'):
            if step in self.getkeys('jobinput',job):
                if index in self.getkeys('jobinput',job,step):
                    in_job = self.get('jobinput', job, step, index)

        workdir = self._getworkdir(step=step,index=index)
        cwd = os.getcwd()
        if os.path.isdir(workdir):
            shutil.rmtree(workdir)
        os.makedirs(workdir, exist_ok=True)

        os.chdir(workdir)
        os.makedirs('outputs', exist_ok=True)
        os.makedirs('reports', exist_ok=True)

        ##################
        # 5. Merge manifests from all input dependancies

        all_inputs = []
        if not self.get('remote'):
            for in_step, in_index in self.get('flowgraph', flow, step, index, 'input'):
                index_error = error[in_step + in_index]
                self.set('flowstatus', in_step, in_index, 'error', index_error)
                if not index_error:
                    cfgfile = f"../../../{in_job}/{in_step}/{in_index}/outputs/{design}.pkg.json"
                    self.read_manifest(cfgfile, clobber=False)

        ##################
        # 6. Write manifest prior to step running into inputs

        self.set('arg', 'step', None, clobber=True)
        self.set('arg', 'index', None, clobber=True)
        os.makedirs('inputs', exist_ok=True)
        #self.write_manifest(f'inputs/{design}.pkg.json')

        ##################
        # 7. Reset metrics to zero
        # TODO: There should be no need for this, but need to fix
        # without it we need to be more careful with flows to make sure
        # things like the builtin functions don't look at None values
        for metric in self.getkeys('metric', 'default', 'default'):
            self.set('metric', step, index, metric, 'real', 0)

        ##################
        # 8. Select inputs

        args = self.get('flowgraph', flow, step, index, 'args')
        inputs = self.get('flowgraph', flow, step, index, 'input')

        sel_inputs = []
        score = 0

        if tool in self.builtin:
            self.logger.info(f"Running built in task '{tool}'")
            # Figure out which inputs to select
            if tool == 'minimum':
                (score, sel_inputs) = self.minimum(*inputs)
            elif tool == "maximum":
                (score, sel_inputs) = self.maximum(*inputs)
            elif tool == "mux":
                (score, sel_inputs) = self.mux(*inputs, selector=args)
            elif tool == "join":
                sel_inputs = self.join(*inputs)
            elif tool == "verify":
                if not self.verify(*inputs, assertion=args):
                    self._haltstep(step, index, active)
        else:
            sel_inputs = self.get('flowgraph', flow, step, index, 'input')

        if sel_inputs == None:
            self.logger.error(f'No inputs selected after running {tool}')
            self._haltstep(step, index, active)

        self.set('flowstatus', step, index, 'select', sel_inputs)

        ##################
        # 9. Copy (link) output data from previous steps

        if step == 'import':
            self._collect(step, index, active)

        if not self.get('flowgraph', flow, step, index,'input'):
            all_inputs = []
        elif not self.get('flowstatus', step, index, 'select'):
            all_inputs = self.get('flowgraph', flow, step, index,'input')
        else:
            all_inputs = self.get('flowstatus', step, index, 'select')
        for in_step, in_index in all_inputs:
            if self.get('flowstatus', in_step, in_index, 'error') == 1:
                self.logger.error(f'Halting step due to previous error in {in_step}{in_index}')
                self._haltstep(step, index, active)

            # Skip copying pkg.json files here, since we write the current chip
            # configuration into inputs/{design}.pkg.json earlier in _runstep.
            utils.copytree(f"../../../{in_job}/{in_step}/{in_index}/outputs", 'inputs/', dirs_exist_ok=True,
                ignore=[f'{design}.pkg.json'], link=True)

        ##################
        # 10. Copy Reference Scripts
        if tool not in self.builtin:
            if self.get('eda', tool, 'copy'):
                for refdir in self.find_files('eda', tool, 'refdir', step, index):
                    utils.copytree(refdir, ".", dirs_exist_ok=True)

        ##################
        # 11. Check manifest
        self.set('arg', 'step', step, clobber=True)
        self.set('arg', 'index', index, clobber=True)

        if not self.get('skipcheck'):
            if self.check_manifest():
                self.logger.error(f"Fatal error in check_manifest()! See previous errors.")
                self._haltstep(step, index, active)

        ##################
        # 12. Run preprocess step for tool
        if tool not in self.builtin:
            func = self.find_function(tool, "pre_process", 'tools')
            if func:
                func(self)
                if self.error:
                    self.logger.error(f"Pre-processing failed for '{tool}'")
                    self._haltstep(step, index, active)

        ##################
        # 13. Set environment variables

        # License file configuration.
        for item in self.getkeys('eda', tool, 'licenseserver'):
            license_file = self.get('eda', tool, 'licenseserver', item)
            if license_file:
                os.environ[item] = ':'.join(license_file)

        # Tool-specific environment variables for this task.
        if (step in self.getkeys('eda', tool, 'env')) and \
           (index in self.getkeys('eda', tool, 'env', step)):
            for item in self.getkeys('eda', tool, 'env', step, index):
                os.environ[item] = self.get('eda', tool, 'env', step, index, item)

        ##################
        # 14. Check exe version

        vercheck = self.get('vercheck')
        veropt = self.get('eda', tool, 'vswitch')
        exe = self._getexe(tool)
        version = None
        if veropt and (exe is not None):
            cmdlist = [exe]
            cmdlist.extend(veropt)
            proc = subprocess.run(cmdlist, stdout=PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
            parse_version = self.find_function(tool, 'parse_version', 'tools')
            if parse_version is None:
                self.logger.error(f'{tool} does not implement parse_version.')
                self._haltstep(step, index, active)
            version = parse_version(proc.stdout)
            self.logger.info(f"Checking executable. Tool '{exe}' found with version '{version}'")
            if vercheck:
                allowed_versions = self.get('eda', tool, 'version')
                if allowed_versions and version not in allowed_versions:
                    allowedstr = ', '.join(allowed_versions)
                    self.logger.error(f"Version check failed for {tool}. Check installation.")
                    self.logger.error(f"Found version {version}, expected one of [{allowedstr}].")
                    self._haltstep(step, index, active)

        ##################
        # 15. Write manifest (tool interface) (Don't move this!)
        suffix = self.get('eda', tool, 'format')
        if suffix:
            pruneopt = bool(suffix!='tcl')
            self.write_manifest(f"sc_manifest.{suffix}", prune=pruneopt, abspath=True)

        ##################
        # 16. Start CPU Timer
        self.logger.debug(f"Starting executable")
        cpu_start = time.time()

        ##################
        # 17. Run executable (or copy inputs to outputs for builtin functions)

        if tool in self.builtin:
            utils.copytree(f"inputs", 'outputs', dirs_exist_ok=True, link=True)
        elif not self.get('skipall'):
            cmdlist = self._makecmd(tool, step, index)
            cmdstr = ' '.join(cmdlist)
            self.logger.info("Running in %s", workdir)
            self.logger.info('%s', cmdstr)
            timeout = self.get('flowgraph', flow, step, index, 'timeout')
            logfile = step + '.log'
            if sys.platform in ('darwin', 'linux') and step in self.get('bkpt'):
                # When we break on a step, the tool often drops into a shell.
                # However, our usual subprocess scheme seems to break terminal
                # echo for some tools. On POSIX-compatible systems, we can use
                # pty to connect the tool to our terminal instead. This code
                # doesn't handle quiet/timeout logic, since we don't want either
                # of these features for an interactive session. Logic for
                # forwarding to file based on
                # https://docs.python.org/3/library/pty.html#example.
                with open(logfile, 'wb') as log_writer:
                    def read(fd):
                        data = os.read(fd, 1024)
                        log_writer.write(data)
                        return data
                    retcode = pty.spawn(cmdlist, read)
            else:
                with open(logfile, 'w') as log_writer, open(logfile, 'r') as log_reader:
                    # Use separate reader/writer file objects as hack to display
                    # live output in non-blocking way, so we can monitor the
                    # timeout. Based on https://stackoverflow.com/a/18422264.
                    cmd_start_time = time.time()
                    proc = subprocess.Popen(cmdstr,
                                            stdout=log_writer,
                                            stderr=subprocess.STDOUT,
                                            shell=True)
                    while proc.poll() is None:
                        # Loop until process terminates
                        if not quiet:
                            sys.stdout.write(log_reader.read())
                        if timeout is not None and time.time() - cmd_start_time > timeout:
                            self.logger.error(f'Step timed out after {timeout} seconds')
                            proc.terminate()
                            self._haltstep(step, index, active)
                        time.sleep(0.1)

                    # Read the remaining
                    if not quiet:
                        sys.stdout.write(log_reader.read())
                    retcode = proc.returncode

            if retcode != 0:
                self.logger.warning('Command failed with code %d. See log file %s', retcode, os.path.abspath(logfile))
                if not self.get('eda', tool, 'continue'):
                    self._haltstep(step, index, active)

        ##################
        # 18. Capture cpu runtime
        cpu_end = time.time()
        cputime = round((cpu_end - cpu_start),2)
        self.set('metric',step, index, 'exetime', 'real', cputime)

        ##################
        # 19. Post process (could fail)
        post_error = 0
        if (tool not in self.builtin) and (not self.get('skipall')) :
            func = self.find_function(tool, 'post_process', 'tools')
            if func:
                post_error = func(self)
                if post_error:
                    self.logger.error('Post-processing check failed')
                    self._haltstep(step, index, active)

        ##################
        # 20. Check log file (must be after post-process)
        if (tool not in self.builtin) and (not self.get('skipall')) :
            self.check_logfile(step=step, index=index, display=not quiet)

        ##################
        # 21. Hash files
        if self.get('hash') and (tool not in self.builtin):
            # hash all outputs
            self.hash_files('eda', tool, 'output', step, index)
            # hash all requirements
            if self.valid('eda', tool, 'require', step, index, quiet=True):
                for item in self.get('eda', tool, 'require', step, index):
                    args = item.split(',')
                    if 'file' in self.get(*args, field='type'):
                        self.hash_files(*args)

        ##################
        # 22. Capture wall runtime
        wall_end = time.time()
        walltime = round((wall_end - wall_start),2)
        self.set('metric',step, index, 'tasktime', 'real', walltime)

        ##################
        # 23. Make a record if tracking is enabled
        if self.get('track'):
            self._make_record(job, step, index, wall_start, wall_end, version)

        ##################
        # 24. Save a successful manifest
        self.set('flowstatus', step, str(index), 'error', 0)
        self.set('arg', 'step', None, clobber=True)
        self.set('arg', 'index', None, clobber=True)

        self.write_manifest("outputs/" + self.get('design') +'.pkg.json')

        ##################
        # 25. Clean up non-essential files
        if self.get('clean'):
            self.logger.error('Self clean not implemented')

        ##################
        # 26. return to original directory
        os.chdir(cwd)

        ##################
        # 27. clearing active and error bits
        # !!Do not move this code!!
        error[step + str(index)] = 0
        active[step + str(index)] = 0

    ###########################################################################
    def _haltstep(self, step, index, active, log=True):
        if log:
            self.logger.error(f"Halting step '{step}' index '{index}' due to errors.")
        active[step + str(index)] = 0
        sys.exit(1)

    ###########################################################################
    def run(self):
        '''
        Executes tasks in a flowgraph.

        The run function sets up tools and launches runs for every index
        in a step defined by a steplist. The steplist is taken from the schema
        steplist parameter if defined, otherwise the steplist is defined
        as the list of steps within the schema flowgraph dictionary. Before
        starting  the process, tool modules are loaded and setup up for each
        step and index based on on the schema eda dictionary settings.
        Once the tools have been set up, the manifest is checked using the
        check_manifest() function and files in the manifest are hashed based
        on the 'hashmode' schema setting.

        Once launched, each process waits for preceding steps to complete,
        as defined by the flowgraph 'inputs' parameter. Once a all inputs
        are ready, previous steps are checked for errors before the
        process entered a local working directory and starts to run
        a tool or to execute a built in Chip function.

        Fatal errors within a step/index process cause all subsequent
        processes to exit before start, returning control to the the main
        program which can then exit.

        Examples:
            >>> run()
            Runs the execution flow defined by the flowgraph dictionary.
        '''

        flow = self.get('flow')

        if not flow in self.getkeys('flowgraph'):
            # If not a pre-loaded flow, we'll assume that 'flow' specifies a
            # single-step tool run with flow being the name of the tool. Set up
            # a basic flowgraph for this tool with a no-op import and default
            # weights.
            tool = flow
            step = self.get('arg', 'step')
            if step is None:
                self.logger.error('arg, step must be specified for single tool flow.')

            self.set('flowgraph', flow, step, '0', 'tool', tool)
            self.set('flowgraph', flow, step, '0', 'weight', 'errors', 0)
            self.set('flowgraph', flow, step, '0', 'weight', 'warnings', 0)
            self.set('flowgraph', flow, step, '0', 'weight', 'runtime', 0)
            if step != 'import':
                self.set('flowgraph', flow, step, '0', 'input', ('import','0'))
                self.set('flowgraph', flow, 'import', '0', 'tool', 'nop')

            self.set('arg', 'step', None)

        # Run steps if set, otherwise run whole graph
        if self.get('arg', 'step'):
            steplist = [self.get('arg', 'step')]
        elif self.get('steplist'):
            steplist = self.get('steplist')
        else:
            steplist = self.list_steps()

            # If no step(list) was specified, the whole flow is being run
            # start-to-finish. Delete the build dir to clear stale results.
            cur_job_dir = f'{self.get("dir")}/{self.get("design")}/'\
                          f'{self.get("jobname")}'
            if os.path.isdir(cur_job_dir):
                shutil.rmtree(cur_job_dir)

        # List of indices to run per step. Precomputing this ensures we won't
        # have any problems if [arg, index] gets clobbered, and reduces logic
        # repetition.
        indexlist = {}
        for step in steplist:
            if self.get('arg', 'index'):
                indexlist[step] = [self.get('arg', 'index')]
            elif self.get('indexlist'):
                indexlist[step] = self.get('indexlist')
            else:
                indexlist[step] = self.getkeys('flowgraph', flow, step)

        # Set env variables
        for envvar in self.getkeys('env'):
            val = self.get('env', envvar)
            os.environ[envvar] = val

        # Remote workflow: Dispatch the Chip to a remote server for processing.
        if self.get('remote'):
            # Load the remote storage config into the status dictionary.
            if self.get('credentials'):
                # Use the provided remote credentials file.
                cfg_file = self.get('credentials')[-1]
                cfg_dir = os.path.dirname(cfg_file)
            else:
                # Use the default config file path.
                cfg_dir = os.path.join(Path.home(), '.sc')
                cfg_file = os.path.join(cfg_dir, 'credentials')
            if (not os.path.isdir(cfg_dir)) or (not os.path.isfile(cfg_file)):
                self.logger.error('Could not find remote server configuration - please run "sc-configure" and enter your server address and credentials.')
                sys.exit(1)
            with open(cfg_file, 'r') as cfgf:
                self.status['remote_cfg'] = json.loads(cfgf.read())
            if (not 'address' in self.status['remote_cfg']):
                self.logger.error('Improperly formatted remote server configuration - please run "sc-configure" and enter your server address and credentials.')
                sys.exit(1)

            # Pre-process: Run an 'import' stage locally, and upload the
            # in-progress build directory to the remote server.
            # Data is encrypted if user / key were specified.
            # run remote process
            remote_preprocess(self)

            # Run the job on the remote server, and wait for it to finish.
            remote_run(self)

            # Fetch results (and delete the job's data from the server).
            fetch_results(self)
        else:
            manager = multiprocessing.Manager()
            error = manager.dict()
            active = manager.dict()

            # Launch a thread for eact step in flowgraph
            # Use a shared even for errors
            # Use a manager.dict for keeping track of active processes
            # (one unqiue dict entry per process),
            # Set up tools and processes
            for step in self.getkeys('flowgraph', flow):
                for index in self.getkeys('flowgraph', flow, step):
                    stepstr = step + index
                    if step in steplist and index in indexlist[step]:
                        self.set('flowstatus', step, str(index), 'error', 1)
                        error[stepstr] = self.get('flowstatus', step, str(index), 'error')
                        active[stepstr] = 1
                        # Setting up tool is optional
                        tool = self.get('flowgraph', flow, step, index, 'tool')
                        if tool not in self.builtin:
                            self.set('arg','step', step)
                            self.set('arg','index', index)
                            func = self.find_function(tool, 'setup', 'tools')
                            if func is None:
                                self.logger.error(f'setup() not found for tool {tool}')
                                sys.exit(1)
                            func(self)
                            # Need to clear index, otherwise we will skip
                            # setting up other indices. Clear step for good
                            # measure.
                            self.set('arg','step', None)
                            self.set('arg','index', None)
                    else:
                        self.set('flowstatus', step, str(index), 'error', 0)
                        error[stepstr] = self.get('flowstatus', step, str(index), 'error')
                        active[stepstr] = 0

            # Implement auto-update of jobincrement
            try:
                alljobs = os.listdir(self.get('dir') + "/" + self.get('design'))
                if self.get('jobincr'):
                    jobid = 0
                    for item in alljobs:
                        m = re.match(self.get('jobname')+r'(\d+)', item)
                        if m:
                            jobid = max(jobid, int(m.group(1)))
                    self.set('jobid', str(jobid + 1))
            except:
                pass

            # Check validity of setup
            self.logger.info("Checking manifest before running.")
            if not self.get('skipcheck'):
                self.check_manifest()

            # Check if there were errors before proceeding with run
            if self.error:
                self.logger.error(f"Check failed. See previous errors.")
                sys.exit()

            # Create all processes
            processes = []
            for step in steplist:
                for index in indexlist[step]:
                    processes.append(multiprocessing.Process(target=self._runtask_safe,
                                                             args=(step, index, active, error,)))


            # We have to deinit the chip's logger before spawning the processes
            # since the logger object is not serializable. _runtask_safe will
            # reinitialize the logger in each new process, and we reinitialize
            # the primary chip's logger after the processes complete.
            self._deinit_logger()

            # Start all processes
            for p in processes:
                p.start()
            # Mandatory process cleanup
            for p in processes:
                p.join()

            self._init_logger()

            # Make a clean exit if one of the steps failed
            halt = 0
            for step in steplist:
                index_error = 1
                for index in indexlist[step]:
                    stepstr = step + index
                    index_error = index_error & error[stepstr]
                halt = halt + index_error
            if halt:
                self.logger.error('Run() failed, exiting! See previous errors.')
                sys.exit(1)

        # Clear scratchpad args since these are checked on run() entry
        self.set('arg', 'step', None, clobber=True)
        self.set('arg', 'index', None, clobber=True)

        # Merge cfg back from last executed runsteps.
        # Note: any information generated in steps that do not merge into the
        # last step will not be picked up in this chip object.
        laststep = steplist[-1]
        last_step_failed = True
        for index in indexlist[laststep]:
            lastdir = self._getworkdir(step=laststep, index=index)

            # This no-op listdir operation is important for ensuring we have a
            # consistent view of the filesystem when dealing with NFS. Without
            # this, this thread is often unable to find the final manifest of
            # runs performed on job schedulers, even if they completed
            # successfully. Inspired by: https://stackoverflow.com/a/70029046.
            os.listdir(os.path.dirname(lastdir))

            lastcfg = f"{lastdir}/outputs/{self.get('design')}.pkg.json"
            if os.path.isfile(lastcfg):
                last_step_failed = False
                local_dir = self.get('dir')
                self.read_manifest(lastcfg, clobber=True, clear=True)
                self.set('dir', local_dir)

        if last_step_failed:
            # Hack to find first failed step by checking for presence of output
            # manifests.
            failed_step = laststep
            for step in steplist[:-1]:
                step_has_cfg = False
                for index in indexlist[step]:
                    stepdir = self._getworkdir(step=step, index=index)
                    cfg = f"{stepdir}/outputs/{self.get('design')}.pkg.json"
                    if os.path.isfile(cfg):
                        step_has_cfg = True
                        break

                if not step_has_cfg:
                    failed_step = step
                    break

            stepdir = self._getworkdir(step=failed_step)[:-1]
            self.logger.error(f'Run() failed on step {failed_step}, exiting! '
                f'See logs in {stepdir} for error details.')
            sys.exit(1)

        # Store run in history
        self.cfghistory[self.get('jobname')] = copy.deepcopy(self.cfg)

    ###########################################################################
    def show(self, filename=None, extra_options=None):
        '''
        Opens a graphical viewer for the filename provided.

        The show function opens the filename specified using a viewer tool
        selected based on the file suffix and the 'showtool' schema setup.
        The 'showtool' parameter binds tools with file suffixes, enabling the
        automated dynamic loading of tool setup functions from
        siliconcompiler.tools.<tool>/<tool>.py. Display settings and
        technology settings for viewing the file are read from the
        in-memory chip object schema settings. All temporary render and
        display files are saved in the <build_dir>/_show directory.

        The show() command can also be used to display content from an SC
        schema .json filename provided. In this case, the SC schema is
        converted to html and displayed as a 'dashboard' in the browser.

        Filenames with .gz and .zip extensions are automatically unpacked
        before being displayed.

        Args:
            filename: Name of file to display

        Examples:
            >>> show('build/oh_add/job0/export/0/outputs/oh_add.gds')
            Displays gds file with a viewer assigned by 'showtool'
            >>> show('build/oh_add/job0/export/0/outputs/oh_add.pkg.json')
            Displays manifest in the browser
        '''

        if extra_options is None:
            extra_options = []

        # Finding last layout if no argument specified
        if filename is None:
            self.logger.info('Searching build directory for layout to show.')
            design = self.get('design')
            # TODO: consider a more flexible approach here. I tried doing a
            # reverse search through all steps, but when verification steps are
            # enabled this finds a DEF passed into LVS rather than the GDS
            # Perhaps we could have a way for flows to register their "final"
            # output.
            laststep = 'export'
            lastindex = '0'
            lastdir = self._getworkdir(step=laststep, index=lastindex)
            gds_file= f"{lastdir}/outputs/{design}.gds"
            def_file = f"{lastdir}/outputs/{design}.def"
            if os.path.isfile(gds_file):
                filename = gds_file
            elif os.path.isfile(def_file):
                filename = def_file

        if filename is None:
            self.logger.error('Unable to automatically find layout in build directory.')
            self.logger.error('Try passing in a full path to show() instead.')
            return 1

        self.logger.info('Showing file %s', filename)

        # Parsing filepath
        filepath = os.path.abspath(filename)
        basename = os.path.basename(filepath)
        localfile = basename.replace(".gz","")
        filetype = os.path.splitext(localfile)[1].lower().replace(".","")

        #Check that file exists
        if not os.path.isfile(filepath):
            self.logger.error(f"Invalid filepath {filepath}.")
            return 1

        # Opening file from temp directory
        cwd = os.getcwd()
        showdir = self.get('dir') + "/_show"
        os.makedirs(showdir, exist_ok=True)
        os.chdir(showdir)

        # Uncompress file if necessary
        if os.path.splitext(filepath)[1].lower() == ".gz":
            with gzip.open(filepath, 'rb') as f_in:
                with open(localfile, 'wb') as f_out:
                    shutil.copyfileobj(f_in, f_out)
        else:
            shutil.copy(filepath, localfile)

        #Figure out which tool to use for opening data
        if filetype in self.getkeys('showtool'):
            # Using env variable and manifest to pass arguments
            os.environ['SC_FILENAME'] = localfile
            # Setting up tool
            tool = self.get('showtool', filetype)
            step = 'show'+filetype
            index = "0"
            self.set('arg', 'step', step)
            self.set('arg', 'index', index)
            setup_tool = self.find_function(tool, 'setup', 'tools')
            setup_tool(self, mode='show')
            self.write_manifest("sc_manifest.tcl", abspath=True)
            self.write_manifest("sc_manifest.json", abspath=True)
            self.set('arg', 'step', None)
            self.set('arg', 'index', None)

            exe = self._getexe(tool)
            if shutil.which(exe) is None:
                self.logger.error(f'Executable {exe} not found.')
                success = False
            else:
                # Running command
                cmdlist = self._makecmd(tool, step, index, extra_options=extra_options)
                proc = subprocess.run(cmdlist)
                success = proc.returncode == 0
        else:
            self.logger.error(f"Filetype '{filetype}' not set up in 'showtool' parameter.")
            success = False

        # Returning to original directory
        os.chdir(cwd)
        return success

    def read_lef(self, path, stackup):
        '''Reads tech LEF and imports data into schema.

        This function reads layer information from a provided tech LEF and uses
        it to fill out the 'pdk', 'grid' keypaths of the current chip object.

        Args:
            path (str): Path to LEF file.
            stackup (str): Stackup associated with LEF file.
        '''
        data = leflib.parse(path)
        layer_index = 1
        for name, layer in data['layers'].items():
            if layer['type'] != 'ROUTING':
                # Skip non-routing layers
                continue

            sc_name = f'm{layer_index}'
            layer_index += 1
            self.set('pdk', 'grid', stackup, name, 'name', sc_name)

            direction = None
            if 'direction' in layer:
                direction = layer['direction'].lower()
                self.set('pdk', 'grid', stackup, name, 'dir', direction)

            if 'offset' in layer:
                offset = layer['offset']
                if isinstance(offset, float):
                    # Per LEF spec, a single offset value applies to the
                    # preferred routing direction. If one doesn't exist, we'll
                    # just ignore.
                    if direction == 'vertical':
                        self.set('pdk', 'grid', stackup, name, 'xoffset', offset)
                    elif direction == 'horizontal':
                        self.set('pdk', 'grid', stackup, name, 'yoffset', offset)
                else:
                    xoffset, yoffset = offset
                    self.set('pdk', 'grid', stackup, name, 'xoffset', xoffset)
                    self.set('pdk', 'grid', stackup, name, 'yoffset', yoffset)

            if 'pitch' in layer:
                pitch = layer['pitch']
                if isinstance(pitch, float):
                    # Per LEF spec, a single pitch value applies to both
                    # directions.
                    self.set('pdk', 'grid', stackup, name, 'xpitch', pitch)
                    self.set('pdk', 'grid', stackup, name, 'ypitch', pitch)
                else:
                    xpitch, ypitch = pitch
                    self.set('pdk', 'grid', stackup, name, 'xpitch', xpitch)
                    self.set('pdk', 'grid', stackup, name, 'ypitch', ypitch)

    ############################################################################
    # Chip helper Functions
    ############################################################################
    def _typecheck(self, cfg, leafkey, value):
        ''' Schema type checking
        '''
        ok = True
        valuetype = type(value)
        errormsg = ""
        if (not re.match(r'\[',cfg['type'])) & (valuetype==list):
            errormsg = "Value must be scalar."
            ok = False
            # Iterate over list
        else:
            # Create list for iteration
            if valuetype == list:
                valuelist = value
            else:
                valuelist = [value]
                # Make type python compatible
            cfgtype = re.sub(r'[\[\]]', '', cfg['type'])
            for item in valuelist:
                valuetype =  type(item)
                if ((cfgtype != valuetype.__name__) and (item is not None)):
                    tupletype = re.match(r'\([\w\,]+\)',cfgtype)
                    #TODO: check tuples!
                    if tupletype:
                        pass
                    elif cfgtype == 'bool':
                        if not item in ['true', 'false']:
                            errormsg = "Valid boolean values are True/False/'true'/'false'"
                            ok = False
                    elif cfgtype == 'file':
                        pass
                    elif cfgtype == 'dir':
                        pass
                    elif (cfgtype == 'float'):
                        try:
                            float(item)
                        except:
                            errormsg = "Type mismatch. Cannot cast item to float."
                            ok = False
                    elif (cfgtype == 'int'):
                        try:
                            int(item)
                        except:
                            errormsg = "Type mismatch. Cannot cast item to int."
                            ok = False
                    elif item is not None:
                        errormsg = "Type mismach."
                        ok = False

        # Logger message
        if type(value) == list:
            printvalue = ','.join(map(str, value))
        else:
            printvalue = str(value)
        errormsg = (errormsg +
                    " Key=" + str(leafkey) +
                    ", Expected Type=" + cfg['type'] +
                    ", Entered Type=" + valuetype.__name__ +
                    ", Value=" + printvalue)


        return (ok, errormsg)

    #######################################
    def _getexe(self, tool):
        path = self.get('eda', tool, 'path')
        exe = self.get('eda', tool, 'exe')
        if exe is None:
            return None

        if path:
            exe_with_path = os.path.join(path, exe)
        else:
            exe_with_path = exe

        fullexe = self._resolve_env_vars(exe_with_path)

        return fullexe

    #######################################
    def _makecmd(self, tool, step, index, extra_options=None):
        '''
        Constructs a subprocess run command based on eda tool setup.
        Creates a replay script in current directory.
        '''

        fullexe = self._getexe(tool)

        options = []
        is_posix = ('win' not in sys.platform)

        for option in self.get('eda', tool, 'option', step, index):
            options.extend(shlex.split(option, posix=is_posix))

        # Add scripts files
        if self.valid('eda', tool, 'script', step, index):
            scripts = self.find_files('eda', tool, 'script', step, index)
        else:
            scripts = []

        cmdlist = [fullexe]
        if extra_options:
            cmdlist.extend(extra_options)
        cmdlist.extend(options)
        cmdlist.extend(scripts)
        runtime_options = self.find_function(tool, 'runtime_options', 'tools')
        if runtime_options:
            for option in runtime_options(self):
                cmdlist.extend(shlex.split(shlex.quote(option), posix=is_posix))

        envvars = {}
        for key in self.getkeys('env'):
            envvars[key] = self.get('env', key)
        for item in self.getkeys('eda', tool, 'licenseserver'):
            license_file = self.get('eda', tool, 'licenseserver', item)
            if license_file:
                envvars[item] = ':'.join(license_file)
        if (step in self.getkeys('eda', tool, 'env') and
            index in self.getkeys('eda', tool, 'env', step)):
            for key in self.getkeys('eda', tool, 'env', step, index):
                envvars[key] = self.get('eda', tool, 'env', step, index, key)

        #create replay file
        is_posix = 'win' not in sys.platform
        script_name = 'replay.sh' if is_posix else 'replay.bat'
        with open(script_name, 'w') as f:
            if is_posix:
                print('#!/bin/bash', file=f)

            envvar_cmd = 'export' if is_posix else 'set'
            for key, val in envvars.items():
                print(f'{envvar_cmd} {key}={val}', file=f)

            print(' '.join(shlex.quote(arg) for arg in cmdlist), file=f)
        os.chmod(script_name, 0o755)

        return cmdlist

    #######################################
    def _get_cloud_region(self):
        # TODO: add logic to figure out if we're running on a remote cluster and
        # extract the region in a provider-specific way.
        return 'local'

    #######################################
    def _make_record(self, job, step, index, start, end, toolversion):
        '''
        Records provenance details for a runstep.
        '''

        start_date = datetime.datetime.fromtimestamp(start).strftime('%Y-%m-%d %H:%M:%S')
        end_date = datetime.datetime.fromtimestamp(end).strftime('%Y-%m-%d %H:%M:%S')

        userid = getpass.getuser()
        self.set('record', job, step, index, 'userid', userid)

        scversion = self.get('version', 'sc')
        self.set('record', job, step, index, 'version', 'sc', scversion)

        if toolversion:
            self.set('record', job, step, index, 'version', 'tool', toolversion)

        self.set('record', job, step, index, 'starttime', start_date)
        self.set('record', job, step, index, 'endtime', end_date)

        machine = platform.node()
        self.set('record', job, step, index, 'machine', machine)

        self.set('record', job, step, index, 'region', self._get_cloud_region())

        try:
            gateways = netifaces.gateways()
            ipaddr, interface = gateways['default'][netifaces.AF_INET]
            macaddr = netifaces.ifaddresses(interface)[netifaces.AF_LINK][0]['addr']
            self.set('record', job, step, index, 'ipaddr', ipaddr)
            self.set('record', job, step, index, 'macaddr', macaddr)
        except KeyError:
            self.logger.warning('Could not find default network interface info')

        system = platform.system()
        if system == 'Darwin':
            lower_sys_name = 'macos'
        else:
            lower_sys_name = system.lower()
        self.set('record', job, step, index, 'platform', lower_sys_name)

        if system == 'Linux':
            distro_name = distro.id()
            self.set('record', job, step, index, 'distro', distro_name)

        if system == 'Darwin':
            osversion, _, _ = platform.mac_ver()
        elif system == 'Linux':
            osversion = distro.version()
        else:
            osversion = platform.release()
        self.set('record', job, step, index, 'version', 'os', osversion)

        if system == 'Linux':
            kernelversion = platform.release()
        elif system == 'Windows':
            kernelversion = platform.version()
        elif system == 'Darwin':
            kernelversion = platform.release()
        else:
            kernelversion = None
        if kernelversion:
            self.set('record', job, step, index, 'version', 'kernel', kernelversion)

        arch = platform.machine()
        self.set('record', job, step, index, 'arch', arch)

    #######################################
    def _safecompare(self, value, op, goal):
        # supported relational oprations
        # >, >=, <=, <. ==, !=
        if op == ">":
            return(bool(value>goal))
        elif op == ">=":
            return(bool(value>=goal))
        elif op == "<":
            return(bool(value<goal))
        elif op == "<=":
            return(bool(value<=goal))
        elif op == "==":
            return(bool(value==goal))
        elif op == "!=":
            return(bool(value!=goal))
        else:
            self.error = 1
            self.logger.error(f"Illegal comparison operation {op}")


    #######################################
    def _getworkdir(self, jobname=None, step=None, index='0'):
        '''Create a step directory with absolute path
        '''

        if jobname is None:
            jobname = self.get('jobname')

        dirlist =[self.cwd,
                  self.get('dir'),
                  self.get('design'),
                  jobname]

        # Return jobdirectory if no step defined
        # Return index 0 by default
        if step is not None:
            dirlist.append(step)
            dirlist.append(index)

        return os.path.join(*dirlist)

    #######################################
    def _resolve_env_vars(self, filepath):
        resolved_path = os.path.expandvars(filepath)

        # variables that don't exist in environment get ignored by `expandvars`,
        # but we can do our own error checking to ensure this doesn't result in
        # silent bugs
        envvars = re.findall(r'\$(\w+)', resolved_path)
        for var in envvars:
            self.logger.warning(f'Variable {var} in {filepath} not defined in environment')

        return resolved_path

    #######################################
    def _get_imported_filename(self, pathstr):
        ''' Utility to map collected file to an unambigious name based on its path.

        The mapping looks like:
        path/to/file.ext => file_<md5('path/to/file.ext')>.ext
        '''
        path = pathlib.Path(pathstr)
        ext = ''.join(path.suffixes)

        # strip off all file suffixes to get just the bare name
        while path.suffix:
            path = pathlib.Path(path.stem)
        filename = str(path)

        pathhash = hashlib.sha1(pathstr.encode('utf-8')).hexdigest()

        return f'{filename}_{pathhash}{ext}'

###############################################################################
# Package Customization classes
###############################################################################

class YamlIndentDumper(yaml.Dumper):
    def increase_indent(self, flow=False, indentless=False):
        return super(YamlIndentDumper, self).increase_indent(flow, False)
