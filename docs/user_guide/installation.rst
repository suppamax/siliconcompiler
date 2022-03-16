Installation
===================================


Python
------

Before installing the SiliconCompiler package you will need to set up a Python environment. Currently Python 3.6-3.10 is supported.

Ubuntu (>=18.04)
^^^^^^^^^^^^^^^^
Open up a terminal and enter the following command sequence.

.. code-block:: bash

    python3 --version                                      # check for Python 3.6 - 3.10
    sudo apt update                                        # update package information
    sudo apt install python3-dev python3-pip python3-venv  # install dependencies
    python3 -m venv  ./venv                                # create a virtual env
    source ./venv/bin/activate                             # active virtual env (bash/zsh)

RHEL (>=RHEL 7)
^^^^^^^^^^^^^^^^^^^
Open up a terminal and enter the following command sequence.

..  Note: when testing on AWS I had to use a different repository name in the first command:
.. sudo subscription-manager repos --enable rhel-server-rhui-rhscl-7-rpms
.. However, that seemed AWS-specific, and the command used in the docs comes from Red Hat itself:
.. https://developers.redhat.com/blog/2018/08/13/install-python3-rhel#

.. code-block:: bash

   sudo subscription-manager repos --enable rhel-server-rhscl-7-rpms  # enable Red Hat Software Collections repository
   sudo yum -y install rh-python36                                    # install Python 3.6
   scl enable rh-python36 bash                                        # enable Python in current environment
   python3 --version                                                  # check for Python 3.6 - 3.10
   python3 -m venv ./venv                                             # create a virtual env
   source ./venv/bin/activate                                         # active virtual env (bash/zsh)
   pip install --upgrade pip                                          # upgrade Pip


macOS (>=10.15)
^^^^^^^^^^^^^^^
Open up a terminal and enter the following command sequence.

.. code-block:: bash

   /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
   export PATH="/usr/local/opt/python/libexec/bin:$PATH"
   brew update
   brew install python
   python3 --version                                      # check for Python 3.6 - 3.10
   python3 -m venv  ./venv                                # create a virtual env
   source ./venv/bin/activate                             # active virtual env

Windows (>= Windows 10)
^^^^^^^^^^^^^^^^^^^^^^^^

Install the latest Python package from `Python.org <https://www.python.org/downloads>`_ using the Windows installer. Open up a Windows shell by:

1. Pressing the 'Windows' key
2. Typing 'cmd', and pressing enter.

From the command shell, enter the following sequence to create and activate a virtual environment.

.. code-block:: doscon

  python -m venv  .\venv
  .\venv\Scripts\activate

SiliconCompiler
---------------

SiliconCompiler is installed directly from `pypi.org <https://pypi.org>`_ using pip. Activate your `Python Virtual Environment <https://docs.python.org/3/library/venv.html>`_ and follow the instructions below. (identical for Windows, Linux, and macOS).

.. code-block:: bash

 (venv) pip install --upgrade pip                # upgrade pip in virtual env
 (venv) pip list                                 # show installed packages in venv
 (venv) pip install --upgrade siliconcompiler    # install SiliconCompiler in venv
 (venv) python -m pip show siliconcompiler       # will display  SiliconCompiler package information
 (venv) python -c "import siliconcompiler;chip=siliconcompiler.Chip();print(chip.get('version','sc'))"

The expected version should be printed to the display:

.. parsed-literal::

   \ |release|

To exit the Python virtual environment, type 'deactivate' and hit enter.

You can also install SiliconCompiler from the latest `SiliconCompiler GitHub Repository <https://github.com/siliconcompiler/siliconcompiler>`_. This option is currently
only supported on Linux/MacOS platforms.

.. code-block:: bash

   git clone https://github.com/siliconcompiler/siliconcompiler
   cd siliconcompiler
   git submodule update --init --recursive third_party/tools/openroad
   pip install -r requirements.txt
   python -m pip install -e .

Offline Install (Linux only)
^^^^^^^^^^^^^^^^^^^^^^^^^^^^

We also provide packages that bundle SC with all of its Python dependencies to enable installation on machines without an external internet connection. They can be found under the "Artifacts" section of any passing nightly or release build on our `builds page <https://github.com/siliconcompiler/siliconcompiler/actions/workflows/wheels.yml>`_. The packages are named ``scdeps-<pyversion>.tar.gz``, depending on which Python version they are associated with.

To install from a bundle, create a Python virtual environment following the instructions above, then perform the following commands.

.. code-block:: bash

   tar -xzvf scdeps-<pyversion>.tar.gz
   pip install siliconcompiler --no-index --find-links scdeps

Cloud Access
--------------

Remote server access requires a credentials text file located at ~/.sc/credentials on Linux or macOS, or at C:\\Users\\USERNAME\\.sc\\credentials on Windows. The credentials file is a JSON formatted file containing information about the remote server address, username, and password.

.. code-block:: json

   {
   "address": "your-server",
   "username": "your-username",
   "password": "your-key"
   }

Use a text editor to create the credentials file. Alternatively you can use 'sc-configure' app to generate it from the command line.

.. code-block:: console

  (venv) sc-configure
  Remote server address: your-server
  Remote username: your-username
  Remote password: your-key
  Remote configuration saved to: /home/<USER>/.sc/credentials

To verify that your credentials file and server is configured correctly, run the `sc-ping` command.

.. code-block:: console

  (venv) sc-ping
  User myname validated successfully!
  Remaining compute time: 1440.00 minutes
  Remaining results bandwidth: 5242880 KiB

Once you have verified that your remote configuration works, try compiling a simple design:

.. code-block:: bash

   (venv) curl https://raw.githubusercontent.com/siliconcompiler/siliconcompiler/main/docs/user_guide/examples/heartbeat.v > heartbeat.v
   (venv) sc heartbeat.v -remote

Layout Viewer
-------------

To view IC layout files (DEF, GDSII) we recommend installing the open source multi-platform 'klayout' viewer (available for Windows, Linux, and macOS). Installation instructions for klayout can be found `HERE <https://www.klayout.de/build.html>`_.

To test the klayout installation, run the 'sc-show' to display the 'heartbeat' layout:

.. code-block:: bash

   (venv) sc-show -design heartbeat

External Tools
--------------

To run compilation locally (instead of remotely), you will need to install a number of tools. For reference, we have provided install scripts for many of these tools. Unless otherwise specified in the script name, these scripts target Ubuntu 20.04.

.. installscripts::

In addition, links to installation documentation written by the original authors of all supported tools can be found in the tools directory of the reference manual :ref:`here<Tools directory>`.
