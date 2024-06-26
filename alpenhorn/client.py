"""Alpenhorn client interface."""

# === Start Python 2/3 compatibility
from __future__ import absolute_import, division, print_function
from future.builtins import *  # noqa  pylint: disable=W0401, W0614
from future.builtins.disabled import *  # noqa  pylint: disable=W0401, W0614

# === End Python 2/3 compatibility


import sys
import time
import os
import glob
import datetime
import time
import socket
import subprocess

import click
import peewee as pw

import chimedb.core as db
import chimedb.data_index as di


def normalize(name):
    return name.replace("_", "-")


# Pass token_normalize_func to context to allow commands with underscores
@click.group(context_settings={"token_normalize_func": normalize})
def cli():
    """Client interface for alpenhorn. Use to request transfers, mount drives,
    check status etc."""
    pass


@cli.command()
@click.argument("node_name", metavar="NODE")
@click.argument("group_name", metavar="GROUP")
@click.option(
    "--acq", help="Sync only this acquisition.", metavar="ACQ", type=str, default=None
)
@click.option("--force", "-f", help="proceed without confirmation", is_flag=True)
@click.option("--nice", "-n", help="nice level for transfer", default=0)
@click.option(
    "--target",
    metavar="TARGET_GROUP",
    default=None,
    type=str,
    help="Only transfer files not available on this group.",
)
@click.option(
    "--transport",
    "-t",
    is_flag=True,
    help="[DEPRECATED] transport mode: only copy if fewer than two archived copies exist.",
)
@click.option("--show_acq", help="Summarise acquisitions to be copied.", is_flag=True)
@click.option("--show_files", help="Show files to be copied.", is_flag=True)
def sync(
    node_name, group_name, acq, force, nice, target, transport, show_acq, show_files
):
    """Copy all files from NODE to GROUP that are not already present.

    We can also use the --target option to only transfer files that are not
    available on both the destination group, and the TARGET_GROUP. This is
    useful for transferring data to a staging location before going to a final
    archive (e.g. HPSS, transport disks).
    """

    # Make sure we connect RW
    db.connect(read_write=True)

    try:
        from_node = di.StorageNode.get(name=node_name)
    except pw.DoesNotExist:
        raise db.NotFoundError('Node "%s" does not exist in the DB.' % node_name)
    try:
        to_group = di.StorageGroup.get(name=group_name)
    except pw.DoesNotExist:
        raise db.NotFoundError('Group "%s" does not exist in the DB.' % group_name)

    # Construct list of file copies that are available on the source node, and
    # not available on any nodes at the destination. This query is quite complex
    # so I've broken it up...

    # First get the nodes at the destination...
    nodes_at_dest = di.StorageNode.select().where(di.StorageNode.group == to_group)

    # Then use this to get a list of all files at the destination...
    files_at_dest = (
        di.ArchiveFile.select()
        .join(di.ArchiveFileCopy)
        .where(
            di.ArchiveFileCopy.node << nodes_at_dest, di.ArchiveFileCopy.has_file == "Y"
        )
    )

    # Then combine to get all file(copies) that are available at the source but
    # not at the destination...
    copy = di.ArchiveFileCopy.select().where(
        di.ArchiveFileCopy.node == from_node,
        di.ArchiveFileCopy.has_file == "Y",
        ~(di.ArchiveFileCopy.file << files_at_dest),
    )

    # If the target option has been specified, only copy nodes also not
    # available there...
    if target is not None:
        # Fetch a reference to the target group
        try:
            target_group = di.StorageGroup.get(name=target)
        except pw.DoesNotExist:
            raise db.NotFoundError(
                'Target group "%s" does not exist in the DB.' % target
            )

        # First get the nodes at the destination...
        nodes_at_target = di.StorageNode.select().where(
            di.StorageNode.group == target_group
        )

        # Then use this to get a list of all files at the destination...
        files_at_target = (
            di.ArchiveFile.select()
            .join(di.ArchiveFileCopy)
            .where(
                di.ArchiveFileCopy.node << nodes_at_target,
                di.ArchiveFileCopy.has_file == "Y",
            )
        )

        # Only match files that are also not available at the target
        copy = copy.where(~(di.ArchiveFileCopy.file << files_at_target))

    # In transport mode (DEPRECATED) we only move files that don't have an
    # archive copy elsewhere...
    if transport:
        import warnings

        warnings.warn("Transport mode is deprecated. Try to use --target instead.")

        # Get list of other archive nodes
        other_archive_nodes = di.StorageNode.select().where(
            di.StorageNode.storage_type == "A", di.StorageNode.id != from_node
        )

        files_in_archive = (
            di.ArchiveFile.select()
            .join(di.ArchiveFileCopy)
            .where(
                di.ArchiveFileCopy.node << other_archive_nodes,
                di.ArchiveFileCopy.has_file == "Y",
            )
        )

        copy = copy.where(~(di.ArchiveFileCopy.file << files_in_archive))

    # Join onto ArchiveFile for later query parts
    copy = copy.join(di.ArchiveFile)

    # If requested, limit query to a specific acquisition...
    if acq is not None:
        # Fetch acq if specified
        try:
            acq = di.ArchiveAcq.get(name=acq)
        except pw.DoesNotExist:
            raise db.NotFoundError('Acquisition "%s" does not exist in the DB.' % acq)

        # Restrict files to be in the acquisition
        copy = copy.where(di.ArchiveFile.acq == acq)

    if not copy.count():
        print("No files to copy from node %s." % (node_name))
        return

    # Show acquisitions based summary of files to be copied
    if show_acq:
        acqs = [c.file.acq.name for c in copy]

        import collections

        for acq, count in collections.Counter(acqs).items():
            print("%s [%i files]" % (acq, count))

    # Show all files to be copied
    if show_files:
        for c in copy:
            print("%s/%s" % (c.file.acq.name, c.file.name))

    size_bytes = copy.select(pw.fn.Sum(di.ArchiveFile.size_b)).scalar()
    size_gb = int(size_bytes) / 1073741824.0

    print(
        "Will request that %d files (%.1f GB) be copied from node %s to group %s."
        % (copy.count(), size_gb, node_name, group_name)
    )

    if not (force or click.confirm("Do you want to proceed?")):
        print("Aborted.")
        return

    dtnow = datetime.datetime.now()

    # Perform update in a transaction to avoid any clobbering from concurrent updates
    with di.ArchiveFileCopyRequest._meta.database.atomic():
        # Get a list of all the file ids for the copies we should perform
        files_ids = [c.file_id for c in copy]

        # Get a list of all the file ids for exisiting requests
        requests = di.ArchiveFileCopyRequest.select().where(
            di.ArchiveFileCopyRequest.group_to == to_group,
            di.ArchiveFileCopyRequest.node_from == from_node,
        )
        req_file_ids = [req.file_id for req in requests]

        # Separate the files into ones that already have requests and ones that don't
        files_in = [x for x in files_ids if x in req_file_ids]
        files_out = [x for x in files_ids if x not in req_file_ids]

        sys.stdout.write(
            "Updating %i existing requests and inserting %i new ones.\n"
            % (len(files_in), len(files_out))
        )

        # Perform an update of all the existing copy requests
        if len(files_in) > 0:
            update = di.ArchiveFileCopyRequest.update(
                nice=nice,
                completed=False,
                cancelled=False,
                timestamp=dtnow,
                n_requests=di.ArchiveFileCopyRequest.n_requests + 1,
            )

            update = update.where(
                di.ArchiveFileCopyRequest.file << files_in,
                di.ArchiveFileCopyRequest.group_to == to_group,
                di.ArchiveFileCopyRequest.node_from == from_node,
            )
            update.execute()

        # Insert any new requests
        if len(files_out) > 0:
            # Construct a list of all the rows to insert
            insert = [
                {
                    "file": fid,
                    "node_from": from_node,
                    "nice": 0,
                    "group_to": to_group,
                    "completed": False,
                    "n_requests": 1,
                    "timestamp": dtnow,
                }
                for fid in files_out
            ]

            # Do a bulk insert of these new rows
            di.ArchiveFileCopyRequest.insert_many(insert).execute()


@cli.command()
@click.option(
    "--all", help="Show the status of all nodes, not just active ones.", is_flag=True
)
def status(all):
    """Summarise the status of alpenhorn storage nodes."""

    import tabulate

    db.connect()

    # Data to fetch from the database (node name, total files, total size)
    query_info = (
        di.StorageNode.name,
        pw.fn.Count(di.ArchiveFileCopy.id).alias("count"),
        pw.fn.Sum(di.ArchiveFile.size_b).alias("total_size"),
        di.StorageNode.host,
        di.StorageNode.root,
    )

    # Per node totals
    nodes = (
        di.StorageNode.select(*query_info)
        .join(di.ArchiveFileCopy)
        .where(di.ArchiveFileCopy.has_file == "Y")
        .join(di.ArchiveFile)
        .group_by(di.StorageNode)
        .order_by(di.StorageNode.name)
    )

    if not all:
        nodes = nodes.where(di.StorageNode.active)

    # Totals for the whole archive
    tot = di.ArchiveFile.select(
        pw.fn.Count(di.ArchiveFile.id).alias("count"),
        pw.fn.Sum(di.ArchiveFile.size_b).alias("total_size"),
    ).scalar(as_tuple=True)

    data = [
        [
            node[0],
            int(node[1]),
            int(node[2]) / 2**40.0,
            100.0 * int(node[1]) / int(tot[0]),
            100.0 * int(node[2]) / int(tot[1]),
            "%s:%s" % (node[3], node[4]),
        ]
        for node in nodes.tuples()
    ]

    headers = ["Node", "Files", "Size [TB]", "Files [%]", "Size [%]", "Path"]

    print(tabulate.tabulate(data, headers=headers, floatfmt=".1f"))


@cli.command()
@click.argument("node_name", metavar="NODE")
@click.option("--md5", help="perform full check against md5sum", is_flag=True)
@click.option(
    "--fixdb", help="fix up the database to be consistent with reality", is_flag=True
)
@click.option(
    "--acq",
    metavar="ACQ",
    multiple=True,
    help="Limit verification to specified acquisitions. Use repeated --acq flags to specify multiple acquisitions.",
)
def verify(node_name, md5, fixdb, acq):
    """Verify the archive on NODE against the database."""

    db.connect()

    try:
        this_node = di.StorageNode.get(di.StorageNode.name == node_name)
    except pw.DoesNotExist:
        print("Specified node does not exist.")
        return

    ## Use a complicated query with a tuples construct to fetch everything we
    ## need in a single query. This massively speeds up the whole process versus
    ## fetching all the FileCopy's then querying for Files and Acqs.
    lfiles = (
        di.ArchiveFile.select(
            di.ArchiveFile.name,
            di.ArchiveAcq.name,
            di.ArchiveFile.size_b,
            di.ArchiveFile.md5sum,
            di.ArchiveFileCopy.id,
        )
        .join(di.ArchiveAcq)
        .switch(di.ArchiveFile)
        .join(di.ArchiveFileCopy)
        .where(di.ArchiveFileCopy.node == this_node, di.ArchiveFileCopy.has_file == "Y")
        .tuples()
    )

    missing_files = []
    corrupt_files = []

    missing_ids = []
    corrupt_ids = []

    nfiles = 0

    with click.progressbar(lfiles, label="Scanning files") as lfiles_iter:
        for filename, acqname, filesize, md5sum, fc_id in lfiles_iter:
            # Skip if not in specified acquisitions
            if len(acq) > 0 and acqname not in acq:
                continue

            nfiles += 1

            filepath = this_node.root + "/" + acqname + "/" + filename

            # Check if file is plain missing
            if not os.path.exists(filepath):
                missing_files.append(filepath)
                missing_ids.append(fc_id)
                continue

            if md5:
                file_md5 = di.util.md5sum_file(filepath)
                corrupt = file_md5 != md5sum
            else:
                corrupt = os.path.getsize(filepath) != filesize

            if corrupt:
                corrupt_files.append(filepath)
                corrupt_ids.append(fc_id)
                continue

    if len(missing_files) > 0:
        print()
        print("=== Missing files ===")
        for fname in missing_files:
            print(fname)

    if len(corrupt_files) > 0:
        print()
        print("=== Corrupt files ===")
        for fname in corrupt_files:
            print(fname)

    print()
    print("=== Summary ===")
    print("  %i total files" % nfiles)
    print("  %i missing files" % len(missing_files))
    print("  %i corrupt files" % len(corrupt_files))
    print()

    # Fix up the database by marking files as missing, and marking
    # corrupt files for verification by alpenhornd.
    if fixdb:
        # Make sure we connect RW
        db.connect(read_write=True)

        if (len(missing_files) > 0) and click.confirm("Fix missing files"):
            missing_count = (
                di.ArchiveFileCopy.update(has_file="N")
                .where(di.ArchiveFileCopy.id << missing_ids)
                .execute()
            )
            print("  %i marked as missing" % missing_count)

        if (len(corrupt_files) > 0) and click.confirm("Fix corrupt files"):
            corrupt_count = (
                di.ArchiveFileCopy.update(has_file="M")
                .where(di.ArchiveFileCopy.id << corrupt_ids)
                .execute()
            )
            print("  %i corrupt files marked for verification" % corrupt_count)


@cli.command()
@click.argument("node_name", metavar="NODE")
@click.option(
    "--days", "-d", help="clean files older than <days>", type=int, default=None
)
@click.option(
    "--size",
    "-s",
    help="clean the earliest registered <size> GiB of files",
    type=int,
    default=None,
)
@click.option("--force", "-f", help="force cleaning on an archive node", is_flag=True)
@click.option("--now", "-n", help="force immediate removal", is_flag=True)
@click.option(
    "--target",
    metavar="TARGET_GROUP",
    default=None,
    type=str,
    help="Only clean files already available in this group.",
)
@click.option(
    "--acq", metavar="ACQ", default=None, type=str, help="Limit removal to acquisition"
)
def clean(node_name, days, size, force, now, target, acq):
    """Clean up NODE by marking files as potentially removable.

    If --target is specified we will only remove files already available in the
    TARGET_GROUP. This is useful for cleaning out intermediate locations such as
    transport disks.

    Using the --days flag will only clean correlator and housekeeping
    files which have a timestamp associated with them. It will not
    touch other types. If no --days flag is given, all files will be
    considered for removal.

    The size specified with --size is always rounded up depending on the size
    of the files marked for removal.  Files in this mode are ordered by
    registration time (i.e. database order), not by acquisition time, and so
    it will mark all files, not just those with a timestamp.  If more than
    <size> GiB of files are already marked for removal, no new files will be
    marked.

    The --size and --days flags are mutually exclusive.
    """

    db.connect(read_write=True)

    # Check for clashing arguments
    if days is not None and size is not None:
        raise ValueError("Parameter error: you cannot specify both --days and --size")

    # Ignore weird values
    if size is not None and size <= 0:
        print("Nothing selected for cleaning.")
        return

    try:
        this_node = di.StorageNode.get(di.StorageNode.name == node_name)
    except pw.DoesNotExist:
        print("Specified node does not exist.")
        return

    # Check to see if we are on an archive node
    if this_node.storage_type == "A":
        if force or click.confirm("DANGER: run clean on archive node?"):
            print("%s is an archive node. Forcing clean." % node_name)
        else:
            print("Cannot clean archive node %s without forcing." % node_name)
            return

    # Select FileCopys on this node.

    files = (
        di.ArchiveFileCopy.select(
            di.ArchiveFileCopy.id, di.ArchiveFileCopy.wants_file, di.ArchiveFile.size_b
        )
        .join(di.ArchiveFile)
        .where(di.ArchiveFileCopy.node == this_node)
        .order_by(di.ArchiveFile.id)
    )

    # If size is specified, we select files that are currently on the node,
    # and ignore wants_file.  Otherwise, we select all files destined for
    # this node (wants_file == 'Y'), whether or not they're already on it
    if size is None:
        files = files.where(di.ArchiveFileCopy.wants_file == "Y")
    else:
        files = files.where(di.ArchiveFileCopy.has_file == "Y")

    # Limit to acquisition
    if acq is not None:
        try:
            acq = di.ArchiveAcq.get(name=acq)
        except pw.DoesNotExit:
            raise db.NotFoundError("Specified acquisition %s does not exist" % acq)

        files_in_acq = di.ArchiveFile.select().where(di.ArchiveFile.acq == acq)

        files = files.where(di.ArchiveFileCopy.file << files_in_acq)

    # If the target option has been specified, only clean files also available there...
    if target is not None:
        # Fetch a reference to the target group
        try:
            target_group = di.StorageGroup.get(name=target)
        except pw.DoesNotExist:
            raise db.NotFoundError(
                'Target group "%s" does not exist in the DB.' % target
            )

        # First get the nodes at the destination...
        nodes_at_target = di.StorageNode.select().where(
            di.StorageNode.group == target_group
        )

        # Then use this to get a list of all files at the destination...
        files_at_target = (
            di.ArchiveFile.select()
            .join(di.ArchiveFileCopy)
            .where(
                di.ArchiveFileCopy.node << nodes_at_target,
                di.ArchiveFileCopy.has_file == "Y",
            )
        )

        # Only match files that are also available at the target
        files = files.where(di.ArchiveFileCopy.file << files_at_target)

    # If --days has been set we need to restrict to files older than the given
    # time. This only works for a few particular file types
    if days is not None and days > 0:
        # Get the time for the oldest files to keep
        oldest_unix = time.time() - 24 * 3600 * days

        # List of filetypes we want to update, needs a human readable name and a
        # FileInfo table.
        filetypes = [["correlation", di.CorrFileInfo], ["housekeeping", di.HKFileInfo]]

        file_ids = []

        # Iterate over file types for cleaning
        for name, infotable in filetypes:
            # Filter to fetch only ones with a start time older than `oldest`
            oldfiles = files.join(infotable).where(infotable.start_time < oldest_unix)

            local_file_ids = list(oldfiles)

            # Get number of correlation files
            count = oldfiles.count()

            if count > 0:
                size_bytes = (
                    di.ArchiveFileCopy.select(pw.fn.Sum(di.ArchiveFile.size_b))
                    .join(di.ArchiveFile)
                    .where(di.ArchiveFileCopy.id << local_file_ids)
                    .scalar()
                )

                size_gb = int(size_bytes) / 2**30.0

                print(
                    "Cleaning up %i %s files (%.1f GB) from %s "
                    % (count, name, size_gb, node_name)
                )

                file_ids += local_file_ids

    # If size is set, iterate through files until we've satisfied the size given
    elif size is not None:
        # Convert to bytes
        size *= 2**30

        # Iterate though the file list until we've found enough files
        marked_size = 0
        count = 0
        file_ids = list()
        for copy in files:
            # Add the file to the list to be marked only if necessary.
            # We can escallate wants_file = 'M' to 'N' here
            if copy.wants_file == "Y" or (now and copy.wants_file == "M"):
                file_ids.append(copy)
                marked_size += copy.file.size_b
                count += 1

            # Check if we're done.  The size subtracton happens even if
            # the file wasn't added to the list
            size -= copy.file.size_b
            if size <= 0:
                break

        if count > 0:
            print(
                "Cleaning up %i files (%.1f GB) from %s "
                % (count, marked_size / 2**30, node_name)
            )
        else:
            print(
                "Size parameter already satisfied.  No new files marked for cleaning."
            )
            return

    # If neither days nor size is not set, then just select all files that
    # meet the requirements so far
    else:
        file_ids = list(files)
        count = files.count()

        if count > 0:
            size_bytes = (
                di.ArchiveFileCopy.select(pw.fn.Sum(di.ArchiveFile.size_b))
                .join(di.ArchiveFile)
                .where(di.ArchiveFileCopy.id << file_ids)
                .scalar()
            )

            size_gb = int(size_bytes) / 1073741824.0

            print(
                "Cleaning up %i files (%.1f GB) from %s " % (count, size_gb, node_name)
            )

    # If there are any files to clean, ask for confirmation and the mark them in
    # the database for removal
    if len(file_ids) > 0:
        if force or click.confirm("  Are you sure?"):
            print("  Marking files for cleaning.")

            state = "N" if now else "M"

            update = di.ArchiveFileCopy.update(wants_file=state).where(
                di.ArchiveFileCopy.id << file_ids
            )

            n = update.execute()

            print("Marked %i files for cleaning" % n)

        else:
            print("  Cancelled")
    else:
        print("No files selected for cleaning on %s." % node_name)


@cli.command()
@click.option(
    "--host",
    "-H",
    help="use specified host rather than local machine",
    type=str,
    default=None,
)
def active(host):
    """list the nodes active on this, or another specified, machine"""

    db.connect()

    if host is None:
        host = socket.gethostname().split(".")[0]
    zero = True
    for node in di.StorageNode.select().where(
        di.StorageNode.host == host, di.StorageNode.active == True
    ):
        n_file = (
            di.ArchiveFileCopy.select().where(di.ArchiveFileCopy.node == node).count()
        )
        print("%-25s %-30s %5d files" % (node.name, node.root, n_file))
        zero = False
    if zero:
        print("No nodes are active on host %s." % host)


@cli.command()
@click.option(
    "--host",
    "-H",
    help="use specified host rather than local machine",
    type=str,
    default=None,
)
def mounted(host):
    """Legacy alias to list the nodes active on this, or another specified, machine"""

    active(host)


@cli.command()
@click.argument("serial_num")
def format_transport(serial_num):
    """Interactive routine for formatting a transport disc as a storage
    node; formats and labels the disc as necessary, the adds to the
    database. The disk is specified using the manufacturers
    SERIAL_NUM, which is printed on the disk.
    """

    if os.getuid() != 0:
        print("You must be root to run mount on a transport disc. I quit.")
        return

    # Make sure we connect RW
    db.connect(read_write=True)

    # Find the disc.
    dev = glob.glob("/dev/disk/by-id/*%s" % serial_num)
    if len(dev) == 0:
        print("No disc with that serial number is attached.")
        return
    elif len(dev) > 1:
        print("Confused: found more than one device matching that serial number:")
        for d in dev:
            print("  %s" % dev)
        print("Aborting.")
        return
    dev = dev[0]
    dev_part = "%s-part1" % dev

    # Figure out if it is formatted.
    print("Checking to see if disc is formatted. Please wait.")
    fp = os.popen("parted -s %s print" % dev)
    formatted = False
    part_start = False
    while True:
        l = fp.readline()
        if not l:
            break
        if l.find("Number") == 0 and l.find("Start") > 0 and l.find("File system") > 0:
            part_start = True
        elif l.strip() != "" and part_start:
            formatted = True
    fp.close()

    if not formatted:
        if not click.confirm("Disc is not formatted. Should I format it?"):
            return
        print("Creating partition. Please wait.")
        os.system(
            "parted -s -a optimal %s mklabel gpt -- mkpart primary 0%% 100%%" % dev
        )
        print("Formatting disc. Please wait.")
        time.sleep(5)  # Sleep for a few seconds to allow the partition to appear
        os.system("mkfs.ext4 %s -m 0 -L CH-%s" % (dev_part, serial_num))
    else:
        print("Disc is already formatted.")

    e2label = get_e2label(dev_part)
    name = "CH-%s" % serial_num
    if e2label and e2label != name:
        print(
            "Disc label %s does not conform to labelling standard, "
            "which is CH-<serialnum>."
        )
        exit
    elif not e2label:
        print('Labelling the disc as "%s" (using e2label) ...' % (name))
        assert dev_part is not None
        assert len(name) <= MAX_E2LABEL_LEN
        stat = os.system("/sbin/e2label %s %s" % (dev_part, name))
        if stat:
            print("Failed to e2label! Stat = %s. I quit." % (stat))
            exit()

    # Ensure the mount path exists.
    root = "/mnt/%s" % name
    if not os.path.isdir(root):
        print("Creating mount point %s." % root)
        os.mkdir(root)

    # Check to see if the disc is active.
    fp = os.popen("df")
    active = False
    dev_part_abs = os.path.realpath(dev_part)
    while 1:
        l = fp.readline()
        if not l:
            break
        if l.find(root) > 0:
            if l[: len(dev_part)] == dev or l[: len(dev_part_abs)] == dev_part_abs:
                active = True
            else:
                print(
                    "%s is a mount point, but %s is already active there."(
                        root, l.split()[0]
                    )
                )
    fp.close()

    try:
        node = di.StorageNode.get(name=name)
    except pw.DoesNotExist:
        print(
            "This disc has not been registered yet as a storage node. "
            "Registering now."
        )
        try:
            group = di.StorageGroup.get(name="transport")
        except pw.DoesNotExist:
            print('Hmmm. Storage group "transport" does not exist. I quit.')
            exit()

        # We need to write to the database.
        db.connect(read_write=True)
        node = di.StorageNode.create(
            name=name, root=root, group=group, storage_type="T", min_avail_gb=1
        )

        print("Successfully created storage node.")

    print("Node created but not active. Run alpenhorn mount_transport for that.")


@cli.command()
@click.pass_context
@click.argument("node")
@click.option("--user", help="username to access this node.", type=str, default=None)
@click.option(
    "--address", help="address for remote access to this node.", type=str, default=None
)
def mount_transport(ctx, node, user, address):
    """Mount a transport disk into the system and then make it available to alpenhorn."""

    mnt_point = "/mnt/%s" % node

    print("Mounting disc at %s" % mnt_point)

    if os.system("mount %s" % mnt_point) != 0:
        print("Could not mount disk in OS.")
        return

    ctx.invoke(mount, name=node, path=mnt_point, user=user, address=address)


@cli.command()
@click.pass_context
@click.argument("node")
def unmount_transport(ctx, node):
    """Unmount a transport disk from the system."""

    mnt_point = "/mnt/%s" % node

    print("Unmounting disc at %s" % mnt_point)
    os.system("umount %s" % mnt_point)

    ctx.invoke(unmount, root_or_name=node)


@cli.command()
@click.argument("name")
@click.option("--path", help="Root path for this node", type=str, default=None)
@click.option("--user", help="username to access this node.", type=str, default=None)
@click.option(
    "--address", help="address for remote access to this node.", type=str, default=None
)
@click.option(
    "--hostname",
    help="hostname running the alpenhornd instance for this node (set to this hostname by default).",
    type=str,
    default=None,
)
def activate(name, path, user, address, hostname):
    """Interactive routine for activating a storage node located at ROOT."""

    # We need to write to the database.
    db.connect(read_write=True)

    try:
        node = di.StorageNode.get(name=name)
    except pw.DoesNotExist:
        print('Storage node "%s" does not exist. I quit.' % name)

    if node.active:
        print('Node "%s" is already active.' % name)
        return

    # Set the default hostname if required
    if hostname is None:
        hostname = socket.gethostname().split(".")[0]
        print('I will set the host to "%s".' % hostname)

    # Set the parameters of this node
    node.username = user
    node.address = address
    node.active = True
    node.host = hostname

    if path is not None:
        node.root = path

    node.save()

    print('Successfully activated "%s".' % name)


@cli.command()
@click.argument("name")
@click.option("--path", help="Root path for this node", type=str, default=None)
@click.option("--user", help="username to access this node.", type=str, default=None)
@click.option(
    "--address", help="address for remote access to this node.", type=str, default=None
)
@click.option(
    "--hostname",
    help="hostname running the alpenhornd instance for this node (set to this hostname by default).",
    type=str,
    default=None,
)
def mount(name, path, user, address, hostname):
    """Legacy alias for activating a storage node located at ROOT."""

    activate(name, path, user, address, hostname)


@cli.command()
@click.argument("root_or_name")
def deactivate(root_or_name):
    """Deactivate a storage node with location or named ROOT_OR_NAME."""

    # We need to write to the database.
    db.connect(read_write=True)

    try:
        node = di.StorageNode.get(name=root_or_name)
    except pw.DoesNotExist:
        if root_or_name[-1] == "/":
            root_or_name = root_or_name[: len(root_or_name) - 1]

        if not os.path.exists(root_or_name):
            print("That is neither a node name, nor a path on this host. " "I quit.")
            exit()
        try:
            node = di.StorageNode.get(
                root=root_or_name, host=socket.gethostname().split(".")[0]
            )
        except pw.DoesNotExist:
            print(
                "That is neither a node name nor a root name that is " "known. I quit."
            )
            exit()

    if not node.active:
        print("There is no node active there any more.")
    else:
        node.active = False
        node.save()
        print("Node successfully deactivated.")


@cli.command()
@click.argument("root_or_name")
def unmount(root_or_name):
    """Legacy alias for deactivating a storage node with location or named ROOT_OR_NAME."""

    deactivate(root_or_name)


@cli.command()
@click.argument("node_name", metavar="NODE")
@click.option("-v", "--verbose", count=True)
@click.option(
    "--acq",
    help="Limit import to specified acquisition directories",
    multiple=True,
    default=None,
)
@click.option("--dry", "-d", help="Dry run. Do not modify database.", is_flag=True)
def import_files(node_name, verbose, acq, dry):
    """Scan the current directory for known acquisition files and add them into the database for NODE.

    This command is useful for manually maintaining an archive where we can run
    alpenhornd in the usual manner.
    """
    db.connect(read_write=True)

    # Construct list of acqs to scan
    if acq is None:
        acqs = glob.glob("*")
    else:
        acqs = acq

    # Keep track of state as we process the files
    added_files = []  # Files we have added to the database
    corrupt_files = []  # Known files which are corrupt
    registered_files = []  # Files already registered in the database
    unknown_files = []  # Files not known in the database
    not_acqs = []  # Directories which were not known acquisitions

    # Fetch a reference to the node
    try:
        node = di.StorageNode.select().where(di.StorageNode.name == node_name).get()
    except pw.DoesNotExist:
        print("Unknown node.")
        return

    with click.progressbar(acqs, label="Scanning acquisitions") as acq_iter:
        for acq_name in acq_iter:
            try:
                di.util.parse_acq_name(acq_name)
            except db.ValidationError:
                not_acqs.append(acq_name)
                continue

            try:
                acq = di.ArchiveAcq.select().where(di.ArchiveAcq.name == acq_name).get()
            except pw.DoesNotExist:
                not_acqs.append(acq_name)
                continue

            files = glob.glob(acq_name + "/*")

            # Fetch lists of all files in this acquisition, and all
            # files in this acq with local copies
            file_names = [f.name for f in acq.files]
            local_file_names = [
                f.name
                for f in acq.files.join(di.ArchiveFileCopy).where(
                    di.ArchiveFileCopy.node == node
                )
            ]

            for fn in files:
                f_name = os.path.split(fn)[1]

                # Check if file exists in database
                if f_name not in file_names:
                    unknown_files.append(fn)
                    continue

                # Check if file is already registered on this node
                if f_name in local_file_names:
                    registered_files.append(fn)
                else:
                    archive_file = (
                        di.ArchiveFile.select()
                        .where(di.ArchiveFile.name == f_name, di.ArchiveFile.acq == acq)
                        .get()
                    )

                    if os.path.getsize(fn) != archive_file.size_b:
                        corrupt_files.append(fn)
                        continue

                    added_files.append(fn)
                    if not dry:
                        di.ArchiveFileCopy.create(
                            file=archive_file, node=node, has_file="Y", wants_file="Y"
                        )

    print("\n==== Summary ====")
    print()
    print("Added %i files" % len(added_files))
    print()
    print("%i corrupt files." % len(corrupt_files))
    print("%i files already registered." % len(registered_files))
    print("%i files not known" % len(unknown_files))
    print("%i directories were not acquisitions." % len(not_acqs))

    if verbose > 0:
        print()
        print("Added files:")
        print()

        for fn in added_files:
            print(fn)

    if verbose > 1:
        print("Corrupt:")
        for fn in corrupt_files:
            print(fn)
        print()

        print("Unknown files:")
        for fn in unknown_files:
            print(fn)
        print()

        print("Unknown acquisitions:")
        for fn in not_acqs:
            print(fn)
        print()


# A few utitly routines for dealing with filesystems
MAX_E2LABEL_LEN = 16


def get_e2label(dev):
    try:
        return subprocess.check_output(
            args=["/sbin/e2label", dev],
            text=True,
        ).strip()
    except subprocess.CalledProcessError:
        return None


def get_mount_device(path):
    p = os.popen("mount", "r")
    res = p.read()
    p.close()
    dev = None
    for l in res.split("\n"):
        if not len(l):
            continue
        s = l.split()
        assert s[1] == "on"
        if s[2] == os.path.abspath(path):
            dev = s[0]
    return dev
