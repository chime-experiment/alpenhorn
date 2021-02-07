# This tests the small-file group features of the
# alpenhorn client

import pytest
import peewee as pw
from click.testing import CliRunner
from alpenhorn.client import cli

import chimedb.core as db
import chimedb.data_index as di


# Check for a pending ArchiveFileCopyRequest.  Raises
# ArchiveFileCopyRequestDoesNotExist if something is missing
def check_afcr(file_id, node, group):
    return di.ArchiveFileCopyRequest.get(
        di.ArchiveFileCopyRequest.file_id == file_id,
        di.ArchiveFileCopyRequest.node_from == node,
        di.ArchiveFileCopyRequest.group_to == group,
        di.ArchiveFileCopyRequest.completed == False,
        di.ArchiveFileCopyRequest.cancelled == False,
    )


# Don't run tests against the production database:
# turn on chimedb's test-safe mode
db.test_enable()


# Instantiate a click CLI runner
@pytest.fixture
def runner():
    return CliRunner(mix_stderr=False)


# Create (and later destroy) an anonymous, in-memory SQLite database
# (which is what you get if you turn on test-safe mode but then don't
# provide any connection data).
@pytest.fixture
def create_database():
    db.connect(read_write=True)
    db.orm.create_tables("chimedb.data_index")

    # Populate
    with db.proxy.atomic():
        # groups
        di.StorageGroup.insert_many(
            [
                {"id": 1, "name": "source_group"},
                {"id": 2, "name": "simple_group"},
                {"id": 3, "name": "large_group", "small_size": 2 ** 30},
                {"id": 4, "name": "small_group"},
            ]
        ).execute()

        # add ref
        small = di.StorageGroup.get(di.StorageGroup.name == "small_group")
        di.StorageGroup.update(small_group=small).where(
            di.StorageGroup.name == "large_group"
        ).execute()

        # nodes
        di.StorageNode.insert_many(
            [
                {"id": 1, "name": "source_node", "group_id": 1, "min_avail_gb": 0},
                {"id": 2, "name": "simple_node", "group_id": 2, "min_avail_gb": 0},
                {"id": 3, "name": "large_node", "group_id": 3, "min_avail_gb": 0},
                {"id": 4, "name": "small_node", "group_id": 4, "min_avail_gb": 0},
            ]
        ).execute()

        # acquisition
        di.ArchiveAcq.insert_many(
            [{"id": 1, "name": "acqname", "inst_id": 1, "type_id": 1}]
        ).execute()

        # files
        def acq_file(id_, big):
            if big:
                return {
                    "id": id_,
                    "acq_id": 1,
                    "type_id": 1,
                    "name": "big" + str(id_),
                    "size_b": 2 ** 31,
                    "md5sum": 0,
                }
            else:
                return {
                    "id": id_,
                    "acq_id": 1,
                    "type_id": 1,
                    "name": "small" + str(id_),
                    "size_b": 2 ** 29,
                    "md5sum": 0,
                }

        di.ArchiveFile.insert_many(
            [
                acq_file(1, True),
                acq_file(2, True),
                acq_file(3, True),
                acq_file(4, True),
                acq_file(5, True),
                acq_file(6, False),
                acq_file(7, False),
                acq_file(8, False),
                acq_file(9, False),
                acq_file(10, False),
            ]
        ).execute()

        # copies
        def file_copy(file_, node):
            return {
                "file_id": file_,
                "node_id": node,
                "has_file": "Y",
                "wants_file": "Y",
            }

        di.ArchiveFileCopy.insert_many(
            [
                file_copy(1, 1),
                file_copy(2, 2),
                file_copy(3, 3),
                file_copy(4, 4),
                file_copy(6, 1),
                file_copy(7, 2),
                file_copy(8, 3),
                file_copy(9, 4),
                # Duplicates
                file_copy(5, 3),
                file_copy(5, 4),
                file_copy(10, 3),
                file_copy(10, 4),
            ]
        ).execute()

        # Cancelled copy requests (to test updating vs adding)
        def copy_req(file_, to, from_):
            return {
                "file_id": file_,
                "group_to_id": to,
                "node_from_id": from_,
                "nice": 0,
                "completed": False,
                "cancelled": True,
                "n_requests": 1,
                "timestamp": 0,
            }

        di.ArchiveFileCopyRequest.insert_many(
            [
                copy_req(1, 2, 1),
                copy_req(1, 3, 1),
                copy_req(1, 4, 1),
                copy_req(3, 4, 1),
            ]
        ).execute()

    yield  # Do the test

    db.close()


def test_smallfile_check_bad(create_database, runner):
    '''Test "alpenhorn smallfile non_existent"'''

    result = runner.invoke(cli, ["smallfile", "non_existent"])
    assert result.exit_code == 1
    assert 'Group "non_existent" does not exist in the DB' in result.stdout


def test_smallfile_check_simple(create_database, runner):
    """Test "alpenhorn smallfile simple_group"

    The user can use the "smallfile" command on a non-small-file-enabled group.
    This is not an error.
    """

    result = runner.invoke(cli, ["smallfile", "simple_group"])
    assert result.exit_code == 0
    assert "has no associated small-file group" in result.stdout


def test_smallfile_check_small(create_database, runner):
    """Test "alpenhorn smallfile small_group"

    The small-file group is, itself, not small-file enabled, so
    running "smallfile" on it acts like it does with a "normal" group.
    """

    result = runner.invoke(cli, ["smallfile", "small_group"])
    assert result.exit_code == 0
    assert "has no associated small-file group" in result.stdout


def test_smallfile_check_large(create_database, runner):
    '''Test "alpenhorn smallfile large_group"'''

    result = runner.invoke(cli, ["smallfile", "large_group"])

    assert result.exit_code == 0
    assert "1 (2.0 GiB) duplicated files in group large_group" in result.stdout
    assert "1 (0.5 GiB) duplicated files in group small_group" in result.stdout
    assert (
        "1 (0.5 GiB) small files in group large_group to move to group small_group"
        in result.stdout
    )
    assert (
        "1 (2.0 GiB) large files in group small_group to move to group large_group"
        in result.stdout
    )


def test_smallfile_check_large(create_database, runner):
    '''Test "alpenhorn smallfile large_group"'''

    result = runner.invoke(cli, ["smallfile", "large_group"])

    assert result.exit_code == 0
    assert "1 (2.0 GiB) duplicated files in group large_group" in result.stdout
    assert "1 (0.5 GiB) duplicated files in group small_group" in result.stdout
    assert (
        "1 (0.5 GiB) small files in group large_group to move to group small_group"
        in result.stdout
    )
    assert (
        "1 (2.0 GiB) large files in group small_group to move to group large_group"
        in result.stdout
    )


def test_smallfile_fix(create_database, runner):
    '''Test "alpenhorn smallfile large_group --fix"'''

    result = runner.invoke(cli, ["smallfile", "large_group", "--fix", "--force"])

    # Checking all the results from test_smallfile_check_large is not necessary

    assert result.exit_code == 0
    assert (
        "Updating 0 existing requests and inserting 1 new ones for destination small_group"
        in result.output
    )
    assert (
        "Updating 0 existing requests and inserting 1 new ones for destination large_group"
        in result.output
    )
    assert "Marked 1 files for cleaning from group large_group" in result.output
    assert "Marked 1 files for cleaning from group small_group" in result.output

    # Check copy requests
    check_afcr(
        4,
        di.StorageNode.get(name="small_node"),
        di.StorageGroup.get(name="large_group"),
    )
    check_afcr(
        8,
        di.StorageNode.get(name="large_node"),
        di.StorageGroup.get(name="small_group"),
    )

    # Check deletions
    di.ArchiveFileCopy.get(
        di.ArchiveFileCopy.file_id == 5,
        di.ArchiveFileCopy.node_id == 3,
        di.ArchiveFileCopy.wants_file == "N",
    )
    di.ArchiveFileCopy.get(
        di.ArchiveFileCopy.file_id == 10,
        di.ArchiveFileCopy.node_id == 4,
        di.ArchiveFileCopy.wants_file == "N",
    )


def test_sync_simple(create_database, runner):
    """Test syncing from source_node to simple_group.

    This should work the same as always."""

    result = runner.invoke(cli, ["sync", "source_node", "simple_group", "--force"])

    assert (
        "Updating 1 existing requests and inserting 1 new ones for destination simple_group"
        in result.output
    )
    assert result.exit_code == 0

    # Check copy requests
    from_node = di.StorageNode.get(name="source_node")
    to_group = di.StorageGroup.get(name="simple_group")
    check_afcr(1, from_node, to_group)
    check_afcr(6, from_node, to_group)


def test_sync_small(create_database, runner):
    """Test syncing from source_node to small_group.

    An explicit sync to a small-file group works like a simple sync"""

    result = runner.invoke(cli, ["sync", "source_node", "small_group", "--force"])

    assert (
        "Updating 1 existing requests and inserting 1 new ones for destination small_group"
        in result.output
    )
    assert result.exit_code == 0

    # Check copy requests
    from_node = di.StorageNode.get(name="source_node")
    to_group = di.StorageGroup.get(name="small_group")
    check_afcr(1, from_node, to_group)
    check_afcr(6, from_node, to_group)


def test_sync_large_no_delegation(create_database, runner):
    """Test syncing from source_node to large_group with no delegation.

    In this case, all files will be transfered to large_group, regardless of size."""

    result = runner.invoke(
        cli, ["sync", "source_node", "large_group", "--no-delegation", "--force"]
    )

    assert (
        "Updating 1 existing requests and inserting 1 new ones for destination large_group"
        in result.output
    )
    assert result.exit_code == 0

    # Check copy requests
    from_node = di.StorageNode.get(name="source_node")
    to_group = di.StorageGroup.get(name="large_group")
    check_afcr(1, from_node, to_group)
    check_afcr(6, from_node, to_group)


def test_sync_large(create_database, runner):
    """Test syncing from source_node to large_group."""

    result = runner.invoke(cli, ["sync", "source_node", "large_group", "--force"])

    assert (
        "Updating 1 existing requests and inserting 0 new ones for destination large_group"
        in result.output
    )
    assert (
        "Updating 0 existing requests and inserting 1 new ones for destination small_group"
        in result.output
    )
    assert result.exit_code == 0

    # Check copy requests
    from_node = di.StorageNode.get(name="source_node")
    small_group = di.StorageGroup.get(name="small_group")
    large_group = di.StorageGroup.get(name="large_group")
    check_afcr(1, from_node, large_group)
    check_afcr(6, from_node, small_group)
