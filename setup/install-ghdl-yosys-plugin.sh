#!/bin/sh

mkdir -p deps
cd deps
git clone https://github.com/ghdl/ghdl-yosys-plugin.git
cd ghdl-yosys-plugin

if ! command -v ghdl > /dev/null
then
    echo "ERROR: ghdl not found - install ghdl first"
    exit 1
fi

if ! command -v yosys > /dev/null
then
    echo "ERROR: yosys not found - install yosys first"
    exit 1
fi

make
make install
cd -
