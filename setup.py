from setuptools import setup, find_packages

setup(
    name='alpenhorn',
    version=0.1,

    packages=find_packages(),

    install_requires=['ch_util', 'h5py', 'mysqlclient', 'peewee >= 2.7.0',
                      'tabulate',
                      'bitshuffle', 'netifaces', 'PyYAML', 'configobj', 'watchdog',
                      'ConcurrentLogHandler', 'Click'],
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
