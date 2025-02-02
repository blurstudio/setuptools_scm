import pytest

from setuptools_scm import format_version
from setuptools_scm import integration
from setuptools_scm.config import Configuration
from setuptools_scm.hg import archival_to_version
from setuptools_scm.hg import parse
from setuptools_scm.utils import has_command


pytestmark = pytest.mark.skipif(
    not has_command("hg", warn=False), reason="hg executable not found"
)


@pytest.fixture
def wd(wd):
    wd("hg init")
    wd.add_command = "hg add ."
    wd.commit_command = 'hg commit -m test-{reason} -u test -d "0 0"'
    return wd


archival_mapping = {
    "1.0": {"tag": "1.0"},
    "1.1.dev3+h000000000000": {
        "latesttag": "1.0",
        "latesttagdistance": "3",
        "node": "0" * 20,
    },
    "0.0": {"node": "0" * 20},
    "1.2.2": {"tag": "release-1.2.2"},
    "1.2.2.dev0": {"tag": "release-1.2.2.dev"},
}


@pytest.mark.parametrize("expected,data", sorted(archival_mapping.items()))
def test_archival_to_version(expected, data):
    config = Configuration()
    version = archival_to_version(data, config=config)
    assert (
        format_version(
            version, version_scheme="guess-next-dev", local_scheme="node-and-date"
        )
        == expected
    )


def test_hg_gone(wd, monkeypatch):
    monkeypatch.setenv("PATH", str(wd.cwd / "not-existing"))
    with pytest.raises(EnvironmentError, match="'hg' was not found"):
        parse(str(wd.cwd))


def test_find_files_stop_at_root_hg(wd, monkeypatch):
    wd.commit_testfile()
    project = wd.cwd / "project"
    project.mkdir()
    project.joinpath("setup.cfg").touch()
    # setup.cfg has not been committed
    assert integration.find_files(str(project)) == []
    # issue 251
    wd.add_and_commit()
    monkeypatch.chdir(project)
    assert integration.find_files() == ["setup.cfg"]


# XXX: better tests for tag prefixes
def test_version_from_hg_id(wd):
    assert wd.version == "0.0"

    wd.commit_testfile()
    assert wd.version.startswith("0.1.dev2+")

    # tagging commit is considered the tag
    wd('hg tag v0.1 -u test -d "0 0"')
    assert wd.version == "0.1"

    wd.commit_testfile()
    assert wd.version.startswith("0.2.dev2")

    wd("hg up v0.1")
    assert wd.version == "0.1"

    # commit originating from the tagged revision
    # that is not a actual tag
    wd.commit_testfile()
    assert wd.version.startswith("0.2.dev1+")

    # several tags
    wd("hg up")
    wd('hg tag v0.2 -u test -d "0 0"')
    wd('hg tag v0.3 -u test -d "0 0" -r v0.2')
    assert wd.version == "0.3"


def test_version_from_archival(wd):
    # entrypoints are unordered,
    # cleaning the wd ensure this test won't break randomly
    wd.cwd.joinpath(".hg").rename(wd.cwd / ".nothg")
    wd.write(".hg_archival.txt", "node: 000000000000\n" "tag: 0.1\n")
    assert wd.version == "0.1"

    wd.write(
        ".hg_archival.txt",
        "node: 000000000000\n" "latesttag: 0.1\n" "latesttagdistance: 3\n",
    )

    assert wd.version == "0.2.dev3+h000000000000"


@pytest.mark.issue("#72")
def test_version_in_merge(wd):
    wd.commit_testfile()
    wd.commit_testfile()
    wd("hg up 0")
    wd.commit_testfile()
    wd("hg merge --tool :merge")
    assert wd.version is not None


@pytest.mark.issue(128)
def test_parse_no_worktree(tmpdir):
    ret = parse(str(tmpdir))
    assert ret is None


@pytest.fixture
def version_1_0(wd):
    wd("hg branch default")
    wd.commit_testfile()
    wd('hg tag 1.0.0 -u test -d "0 0"')
    return wd


@pytest.fixture
def pre_merge_commit_after_tag(wd, version_1_0):
    wd("hg branch testbranch")
    wd.write("branchfile", "branchtext")
    wd(wd.add_command)
    wd.commit()
    wd("hg update default")
    wd("hg merge testbranch")
    return wd


@pytest.mark.usefixtures("pre_merge_commit_after_tag")
def test_version_bump_before_merge_commit(wd):
    assert wd.version.startswith("1.0.1.dev1+")


@pytest.mark.issue(219)
@pytest.mark.usefixtures("pre_merge_commit_after_tag")
def test_version_bump_from_merge_commit(wd):
    wd.commit()
    assert wd.version.startswith("1.0.1.dev3+")  # issue 219


@pytest.mark.usefixtures("version_1_0")
def test_version_bump_from_commit_including_hgtag_mods(wd):
    """Test the case where a commit includes changes to .hgtags and other files"""
    with wd.cwd.joinpath(".hgtags").open("ab") as tagfile:
        tagfile.write(b"0  0\n")
    wd.write("branchfile", "branchtext")
    wd(wd.add_command)
    assert wd.version.startswith("1.0.1.dev1+")  # bump from dirty version
    wd.commit()  # commits both the testfile _and_ .hgtags
    assert wd.version.startswith("1.0.1.dev2+")


@pytest.mark.issue(229)
@pytest.mark.usefixtures("version_1_0")
def test_latest_tag_detection(wd):
    """Tests that tags not containing a "." are ignored, the same as for git.
    Note that will be superseded by the fix for pypa/setuptools_scm/issues/235
    """
    wd('hg tag some-random-tag -u test -d "0 0"')
    assert wd.version == "1.0.0"


@pytest.mark.usefixtures("version_1_0")
def test_feature_branch_increments_major(wd):

    wd.commit_testfile()
    assert wd.get_version(version_scheme="python-simplified-semver").startswith("1.0.1")
    wd("hg branch feature/fun")
    assert wd.get_version(version_scheme="python-simplified-semver").startswith("1.1.0")
