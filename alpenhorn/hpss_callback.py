"""Call backs for the HPSS interface."""

# === Start Python 2/3 compatibility
from __future__ import absolute_import, division, print_function, unicode_literals
from future.builtins import *  # noqa  pylint: disable=W0401, W0614
from future.builtins.disabled import *  # noqa  pylint: disable=W0401, W0614

# === End Python 2/3 compatibility


import peewee as pw
import click

import chimedb.core as db
import chimedb.data_index.orm as di

from . import logger  # Import logger here to avoid connection

# messages for transfer

# Get a reference to the log
log = logger.get_log()

# Connect to the database read/write
db.connect(read_write=True)


def normalize(name):
    return name.replace("_", "-")


# Pass token_normalize_func to context to allow commands with underscores
@click.group(context_settings={"token_normalize_func": normalize})
def cli():
    """Call back commands for updating the database from a shell script after an
    HPSS transfer."""
    pass


@cli.command()
@click.argument("file_id", type=int)
@click.argument("node_id", type=int)
def push_failed(file_id, node_id):
    """Update the database to reflect that the HPSS transfer failed.

    INTERNAL COMMAND. NOT FOR HUMAN USE!
    """
    afile = di.ArchiveFile.select().where(di.ArchiveFile.id == file_id).get()
    node = di.StorageNode.select().where(di.StorageNode.id == node_id).get()

    log.warn(
        "Failed push: %s/%s into node %s" % (afile.acq.name, afile.name, node.name)
    )

    # We don't really need to do anything other than log this (we could reattempt)


@cli.command()
@click.argument("file_id", type=int)
@click.argument("node_id", type=int)
def pull_failed(file_id, node_id):
    """Update the database to reflect that the HPSS transfer failed.

    INTERNAL COMMAND. NOT FOR HUMAN USE!
    """
    afile = di.ArchiveFile.select().where(di.ArchiveFile.id == file_id).get()
    node = di.StorageNode.select().where(di.StorageNode.id == node_id).get()

    log.warn(
        "Failed pull: %s/%s onto node %s" % (afile.acq.name, afile.name, node.name)
    )

    # We don't really need to do anything other than log this (we could reattempt)


@cli.command()
@click.argument("file_id", type=int)
@click.argument("node_id", type=int)
def push_success(file_id, node_id):
    """Update the database to reflect that the HPSS transfer succeeded.

    INTERNAL COMMAND. NOT FOR HUMAN USE!
    """

    afile = di.ArchiveFile.select().where(di.ArchiveFile.id == file_id).get()
    node = di.StorageNode.select().where(di.StorageNode.id == node_id).get()

    # Update the FileCopy (if exists), or insert a new FileCopy
    try:
        fcopy = (
            di.ArchiveFileCopy.select()
            .where(di.ArchiveFileCopy.file == afile, di.ArchiveFileCopy.node == node)
            .get()
        )

        fcopy.has_file = "Y"
        fcopy.wants_file = "Y"
        fcopy.save()

    except pw.DoesNotExist:
        di.ArchiveFileCopy.insert(
            file=afile, node=node, has_file="Y", wants_file="Y"
        ).execute()

    log.info(
        "Successful push: %s/%s onto node %s" % (afile.acq.name, afile.name, node.name)
    )


@cli.command()
@click.argument("file_id", type=int)
@click.argument("node_id", type=int)
def pull_success(file_id, node_id):
    """Update the database to reflect that the HPSS transfer succeeded.

    INTERNAL COMMAND. NOT FOR HUMAN USE!
    """

    afile = di.ArchiveFile.select().where(di.ArchiveFile.id == file_id).get()
    node = di.StorageNode.select().where(di.StorageNode.id == node_id).get()

    # Update the FileCopy (if exists), or insert a new FileCopy
    try:
        fcopy = (
            di.ArchiveFileCopy.select()
            .where(di.ArchiveFileCopy.file == afile, di.ArchiveFileCopy.node == node)
            .get()
        )

        fcopy.has_file = "Y"
        fcopy.wants_file = "Y"
        fcopy.save()

    except pw.DoesNotExist:
        di.ArchiveFileCopy.insert(
            file=afile, node=node, has_file="Y", wants_file="Y"
        ).execute()

    log.info(
        "Successful pull: %s/%s into node %s" % (afile.acq.name, afile.name, node.name)
    )
