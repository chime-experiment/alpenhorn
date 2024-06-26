"""Alpenhorn service."""

# === Start Python 2/3 compatibility
from __future__ import absolute_import, division, print_function, unicode_literals
from future.builtins import *  # noqa  pylint: disable=W0401, W0614
from future.builtins.disabled import *  # noqa  pylint: disable=W0401, W0614

# === End Python 2/3 compatibility


import sys
import socket

import click

from alpenhorn import logger
import chimedb.core as db
import chimedb.data_index as di
from alpenhorn import update, auto_import

log = logger.get_log()


# Register Hook to Log Exception
# ==============================


def log_exception(*args):
    log.error("Fatal error!", exc_info=args)


sys.excepthook = log_exception


@click.command()
def cli():
    """Alpenhorn data management service."""

    # We need write access to the DB.
    db.connect(read_write=True)

    # Get the name of this host
    host = socket.gethostname().split(".")[0]

    # Get the list of nodes currently mounted
    node_list = list(
        di.StorageNode.select().where(
            di.StorageNode.host == host, di.StorageNode.active
        )
    )

    # Warn if there are no mounted nodes. We used to exit here, but actually
    # it's useful to keep alpenhornd running for nodes where we exclusively use
    # transport disks (e.g. jingle)
    if len(node_list) == 0:
        log.warn('No nodes on this host ("%s") registered in the DB!' % host)

    # Load the cache of already imported files
    auto_import.load_import_cache()

    # Setup the observers to watch the nodes for new files
    auto_import.setup_observers(node_list)

    # Enter main loop performing node updates
    try:
        update.update_loop(host)

    # Exit cleanly on a keyboard interrupt
    except KeyboardInterrupt:
        log.info("Exiting...")
        auto_import.stop_observers()

    # Wait for watchdog threads to terminate
    auto_import.join_observers()
