# === Start Python 2/3 compatibility
from __future__ import (absolute_import, division,
                        print_function, unicode_literals)
from future.builtins import *  # noqa  pylint: disable=W0401, W0614
from future.builtins.disabled import *  # noqa  pylint: disable=W0401, W0614
# === End Python 2/3 compatibility

from setuptools import setup, find_packages

setup(
    name='alpenhorn',
    version=0.2,

    packages=find_packages(),

    install_requires=[
        'chimedb @ git+ssh://git@github.com/chime-experiment/chimedb.git',
        'chimedb.data_index @ git+ssh://git@github.com/chime-experiment/chimedb_di.git',
        'h5py', 'mysqlclient', 'peewee >= 2.7.0, <3', 'tabulate',
        'bitshuffle', 'netifaces', 'PyYAML', 'configobj', 'watchdog',
        'ConcurrentLogHandler', 'Click'
        ],
    entry_points="""
        [console_scripts]
        alpenhorn=alpenhorn.client:cli
        alpenhornd=alpenhorn.service:cli
        alpenhorn_hpss=alpenhorn.hpss_callback:cli
    """,

    scripts=['scripts/alpenhorn_ensure_running.sh'],

    # metadata for upload to PyPI
    author="CHIME collaboration",
    author_email="richard@phas.ubc.ca",
    description="Data archive management software.",
    license="GPL v3.0",
    url="https://bitbucket.org/chime/alpenhorn"
)
