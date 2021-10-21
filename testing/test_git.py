import os
import subprocess
import sys
from datetime import date
from datetime import datetime
from os.path import join as opj
from unittest.mock import Mock
from unittest.mock import patch

import pytest

from setuptools_scm import git
from setuptools_scm import integration
from setuptools_scm import NonNormalizedVersion
from setuptools_scm.file_finder_git import git_find_files
from setuptools_scm.pre_commit_hook import PreCommitHook
from setuptools_scm.utils import _always_strings
from setuptools_scm.utils import do
from setuptools_scm.utils import has_command
from setuptools_scm.utils import no_git_env


pytestmark = pytest.mark.skipif(
    not has_command("git", warn=False), reason="git executable not found"
)


@pytest.fixture
def wd(wd, monkeypatch):
    monkeypatch.delenv("HOME", raising=False)
    wd("git init")
    wd("git config user.email test@example.com")
    wd('git config user.name "a test"')
    wd.add_command = "git add ."
    wd.commit_command = "git commit -m test-{reason}"
    return wd


@pytest.mark.parametrize(
    "given, tag, number, node, dirty",
    [
        ("3.3.1-rc26-0-g9df187b", "3.3.1-rc26", 0, "g9df187b", False),
        ("17.33.0-rc-17-g38c3047c0", "17.33.0-rc", 17, "g38c3047c0", False),
    ],
)
def test_parse_describe_output(given, tag, number, node, dirty):
    parsed = git._git_parse_describe(given)
    assert parsed == (tag, number, node, dirty)


def test_root_relative_to(tmpdir, wd, monkeypatch):
    monkeypatch.delenv("SETUPTOOLS_SCM_DEBUG")
    p = wd.cwd.joinpath("sub/package")
    p.mkdir(parents=True)
    p.joinpath("setup.py").write_text(
        """from setuptools import setup
setup(use_scm_version={"root": "../..",
                       "relative_to": __file__})
"""
    )
    res = do((sys.executable, "setup.py", "--version"), p)
    assert res == "0.1.dev0"


def test_root_search_parent_directories(tmpdir, wd, monkeypatch):
    monkeypatch.delenv("SETUPTOOLS_SCM_DEBUG")
    p = wd.cwd.joinpath("sub/package")
    p.mkdir(parents=True)
    p.joinpath("setup.py").write_text(
        """from setuptools import setup
setup(use_scm_version={"search_parent_directories": True})
"""
    )
    res = do((sys.executable, "setup.py", "--version"), p)
    assert res == "0.1.dev0"


def test_git_gone(wd, monkeypatch):
    monkeypatch.setenv("PATH", str(wd.cwd / "not-existing"))
    with pytest.raises(EnvironmentError, match="'git' was not found"):
        git.parse(str(wd.cwd), git.DEFAULT_DESCRIBE)


@pytest.mark.issue("https://github.com/pypa/setuptools_scm/issues/298")
@pytest.mark.issue(403)
def test_file_finder_no_history(wd, caplog):
    file_list = git_find_files(str(wd.cwd))
    assert file_list == []

    assert "listing git files failed - pretending there aren't any" in caplog.text


@pytest.mark.issue("https://github.com/pypa/setuptools_scm/issues/281")
def test_parse_call_order(wd):
    git.parse(str(wd.cwd), git.DEFAULT_DESCRIBE)


def test_version_from_git(wd):
    assert wd.version == "0.1.dev0"

    assert git.parse(str(wd.cwd), git.DEFAULT_DESCRIBE).branch == "master"

    wd.commit_testfile()
    assert wd.version.startswith("0.1.dev1+g")
    assert not wd.version.endswith("1-")

    wd("git tag v0.1")
    assert wd.version == "0.1"

    wd.write("test.txt", "test2")
    assert wd.version.startswith("0.2.dev0+g")

    wd.commit_testfile()
    assert wd.version.startswith("0.2.dev1+g")

    wd("git tag version-0.2")
    assert wd.version.startswith("0.2")

    wd.commit_testfile()
    wd("git tag version-0.2.post210+gbe48adfpost3+g0cc25f2")
    with pytest.warns(
        UserWarning, match="tag '.*' will be stripped of its suffix '.*'"
    ):
        assert wd.version.startswith("0.2")

    wd.commit_testfile()
    wd("git tag 17.33.0-rc")
    assert wd.version == "17.33.0rc0"

    # custom normalization
    assert wd.get_version(normalize=False) == "17.33.0-rc"
    assert wd.get_version(version_cls=NonNormalizedVersion) == "17.33.0-rc"
    assert (
        wd.get_version(version_cls="setuptools_scm.NonNormalizedVersion")
        == "17.33.0-rc"
    )


@pytest.mark.parametrize("with_class", [False, type, str])
def test_git_version_unnormalized_setuptools(with_class, tmpdir, wd, monkeypatch):
    """
    Test that when integrating with setuptools without normalization,
    the version is not normalized in write_to files,
    but still normalized by setuptools for the final dist metadata.
    """
    monkeypatch.delenv("SETUPTOOLS_SCM_DEBUG")
    p = wd.cwd

    # create a setup.py
    dest_file = str(tmpdir.join("VERSION.txt")).replace("\\", "/")
    if with_class is False:
        # try normalize = False
        setup_py = """
from setuptools import setup
setup(use_scm_version={'normalize': False, 'write_to': '%s'})
"""
    elif with_class is type:
        # custom non-normalizing class
        setup_py = """
from setuptools import setup

class MyVersion:
    def __init__(self, tag_str: str):
        self.version = tag_str

    def __repr__(self):
        return self.version

setup(use_scm_version={'version_cls': MyVersion, 'write_to': '%s'})
"""
    elif with_class is str:
        # non-normalizing class referenced by name
        setup_py = """from setuptools import setup
setup(use_scm_version={
    'version_cls': 'setuptools_scm.NonNormalizedVersion',
    'write_to': '%s'
})
"""

    # finally write the setup.py file
    p.joinpath("setup.py").write_text(setup_py % dest_file)

    # do git operations and tag
    wd.commit_testfile()
    wd("git tag 17.33.0-rc1")

    # setuptools still normalizes using packaging.Version (removing the dash)
    res = do((sys.executable, "setup.py", "--version"), p)
    assert res == "17.33.0rc1"

    # but the version tag in the file is non-normalized (with the dash)
    assert tmpdir.join("VERSION.txt").read() == "17.33.0-rc1"


@pytest.mark.issue(179)
def test_unicode_version_scheme(wd):
    scheme = b"guess-next-dev".decode("ascii")
    assert wd.get_version(version_scheme=scheme)


@pytest.mark.issue(108)
@pytest.mark.issue(109)
def test_git_worktree(wd):
    wd.write("test.txt", "test2")
    # untracked files dont change the state
    assert wd.version == "0.1.dev0"
    wd("git add test.txt")
    assert wd.version.startswith("0.1.dev0+d")


@pytest.mark.issue(86)
@pytest.mark.parametrize("today", [False, True])
def test_git_dirty_notag(today, wd, monkeypatch):
    if today:
        monkeypatch.delenv("SOURCE_DATE_EPOCH", raising=False)
    wd.commit_testfile()
    wd.write("test.txt", "test2")
    wd("git add test.txt")
    assert wd.version.startswith("0.1.dev1")
    if today:
        # the date on the tag is in UTC
        tag = datetime.utcnow().date().strftime(".d%Y%m%d")
    else:
        tag = ".d20090213"
    # we are dirty, check for the tag
    assert tag in wd.version


@pytest.mark.issue(193)
@pytest.mark.xfail(reason="sometimes relative path results")
def test_git_worktree_support(wd, tmp_path):
    wd.commit_testfile()
    worktree = tmp_path / "work_tree"
    wd("git worktree add -b work-tree %s" % worktree)

    res = do([sys.executable, "-m", "setuptools_scm", "ls"], cwd=worktree)
    assert "test.txt" in res
    assert str(worktree) in res


@pytest.fixture
def shallow_wd(wd, tmpdir):
    wd.commit_testfile()
    wd.commit_testfile()
    wd.commit_testfile()
    target = tmpdir.join("wd_shallow")
    do(["git", "clone", "file://%s" % wd.cwd, str(target), "--depth=1"])
    return target


def test_git_parse_shallow_warns(shallow_wd, recwarn):
    git.parse(str(shallow_wd))
    msg = recwarn.pop()
    assert "is shallow and may cause errors" in str(msg.message)


def test_git_parse_shallow_fail(shallow_wd):
    with pytest.raises(ValueError) as einfo:
        git.parse(str(shallow_wd), pre_parse=git.fail_on_shallow)

    assert "git fetch" in str(einfo.value)


def test_git_shallow_autocorrect(shallow_wd, recwarn):
    git.parse(str(shallow_wd), pre_parse=git.fetch_on_shallow)
    msg = recwarn.pop()
    assert "git fetch was used to rectify" in str(msg.message)
    git.parse(str(shallow_wd), pre_parse=git.fail_on_shallow)


def test_find_files_stop_at_root_git(wd):
    wd.commit_testfile()
    project = wd.cwd / "project"
    project.mkdir()
    project.joinpath("setup.cfg").touch()
    assert integration.find_files(str(project)) == []


@pytest.mark.issue(128)
def test_parse_no_worktree(tmpdir):
    ret = git.parse(str(tmpdir))
    assert ret is None


def test_alphanumeric_tags_match(wd):
    wd.commit_testfile()
    wd("git tag newstyle-development-started")
    assert wd.version.startswith("0.1.dev1+g")


def test_git_archive_export_ignore(wd, monkeypatch):
    wd.write("test1.txt", "test")
    wd.write("test2.txt", "test")
    wd.write(
        ".git/info/attributes",
        # Explicitly include test1.txt so that the test is not affected by
        # a potentially global gitattributes file on the test machine.
        "/test1.txt -export-ignore\n/test2.txt export-ignore",
    )
    wd("git add test1.txt test2.txt")
    wd.commit()
    monkeypatch.chdir(wd.cwd)
    assert integration.find_files(".") == [opj(".", "test1.txt")]


@pytest.mark.issue(228)
def test_git_archive_subdirectory(wd, monkeypatch):
    os.mkdir(wd.cwd / "foobar")
    wd.write("foobar/test1.txt", "test")
    wd("git add foobar")
    wd.commit()
    monkeypatch.chdir(wd.cwd)
    assert integration.find_files(".") == [opj(".", "foobar", "test1.txt")]


@pytest.mark.issue(251)
def test_git_archive_run_from_subdirectory(wd, monkeypatch):
    os.mkdir(wd.cwd / "foobar")
    wd.write("foobar/test1.txt", "test")
    wd("git add foobar")
    wd.commit()
    monkeypatch.chdir(wd.cwd / "foobar")
    assert integration.find_files(".") == [opj(".", "test1.txt")]


def test_git_feature_branch_increments_major(wd):
    wd.commit_testfile()
    wd("git tag 1.0.0")
    wd.commit_testfile()
    assert wd.get_version(version_scheme="python-simplified-semver").startswith("1.0.1")
    wd("git checkout -b feature/fun")
    wd.commit_testfile()
    assert wd.get_version(version_scheme="python-simplified-semver").startswith("1.1.0")


@pytest.mark.issue("https://github.com/pypa/setuptools_scm/issues/303")
def test_not_matching_tags(wd):
    wd.commit_testfile()
    wd("git tag apache-arrow-0.11.1")
    wd.commit_testfile()
    wd("git tag apache-arrow-js-0.9.9")
    wd.commit_testfile()
    assert wd.get_version(
        tag_regex=r"^apache-arrow-([\.0-9]+)$",
        git_describe_command="git describe --dirty --tags --long --exclude *js* ",
    ).startswith("0.11.2")


@pytest.mark.issue("https://github.com/pypa/setuptools_scm/issues/411")
@pytest.mark.xfail(reason="https://github.com/pypa/setuptools_scm/issues/449")
def test_non_dotted_version(wd):
    wd.commit_testfile()
    wd("git tag apache-arrow-1")
    wd.commit_testfile()
    assert wd.get_version().startswith("2")


def test_non_dotted_version_with_updated_regex(wd):
    wd.commit_testfile()
    wd("git tag apache-arrow-1")
    wd.commit_testfile()
    assert wd.get_version(tag_regex=r"^apache-arrow-([\.0-9]+)$").startswith("2")


def test_non_dotted_tag_no_version_match(wd):
    wd.commit_testfile()
    wd("git tag apache-arrow-0.11.1")
    wd.commit_testfile()
    wd("git tag apache-arrow")
    wd.commit_testfile()
    assert wd.get_version().startswith("0.11.2.dev2")


@pytest.mark.issue("https://github.com/pypa/setuptools_scm/issues/381")
def test_gitdir(monkeypatch, wd):
    """ """
    wd.commit_testfile()
    normal = wd.version
    # git hooks set this and break subsequent setuptools_scm unless we clean
    monkeypatch.setenv("GIT_DIR", __file__)
    assert wd.version == normal


def test_git_getdate(wd):
    # TODO: case coverage for git wd parse
    today = date.today()

    def parse_date():
        return git.parse(os.fspath(wd.cwd)).node_date

    git_wd = git.GitWorkdir(os.fspath(wd.cwd))
    assert git_wd.get_head_date() is None
    assert parse_date() == today

    wd.commit_testfile()
    assert git_wd.get_head_date() == today
    meta = git.parse(os.fspath(wd.cwd))
    assert meta.node_date == today


def test_git_getdate_badgit(
    wd,
):
    wd.commit_testfile()
    git_wd = git.GitWorkdir(os.fspath(wd.cwd))
    with patch.object(git_wd, "do_ex", Mock(return_value=("%cI", "", 0))):
        assert git_wd.get_head_date() is None


def create_pip_package(wd):
    """Creates a simple pip package in this git repo supporting setuptools_scm"""
    wd.cwd.joinpath("setup.py").write_text(
        "\n".join(("from setuptools import setup", "setup(use_scm_version=True)", ""))
    )
    wd.cwd.joinpath("pyproject.toml").write_text(
        "\n".join(
            (
                "[build-system]",
                'requires = ["setuptools>=45", "wheel", "setuptools_scm>=6.2"]',
                "[tool.setuptools_scm]",
                'write_to = "version.py"',
            )
        )
    )
    wd.cwd.joinpath("setup.cfg").write_text(
        "\n".join(("[metadata]", "name = test_package"))
    )
    wd("git add setup.py setup.cfg pyproject.toml")
    wd.commit()


def do_interactive(cwd, branch="master"):
    """Force git into an interactive rebase state."""
    # Remove all the git variables from the environment so we can add a required one.
    env = _always_strings(
        dict(
            no_git_env(os.environ),
            # os.environ,
            # try to disable i18n
            LC_ALL="C",
            LANGUAGE="",
            HGPLAIN="1",
        )
    )

    # Force git into an interactive rebase state
    # Adapted from https://stackoverflow.com/a/15394837
    env["GIT_SEQUENCE_EDITOR"] = "sed -i -re 's/^noop/e HEAD/'"

    proc = subprocess.Popen(
        f"git rebase -i {branch}",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(cwd),
        env=env,
    )
    proc.wait()


def test_is_git_rebasing(wd):
    hook = PreCommitHook(wd.cwd, [])
    wd.commit_testfile()

    # git_dir = hook.work_dir.get_git_dir()
    assert hook.is_git_rebasing() is False
    do_interactive(wd.cwd)
    assert hook.is_git_rebasing() is True


def test_pre_commit_hook(wd, monkeypatch):
    build_command = ["python", "setup.py", "egg_info"]
    hook = PreCommitHook(wd.cwd, build_command)
    success_message = f"update-egg-info: Running command: {build_command}"
    git_wd = git.GitWorkdir(os.fspath(wd.cwd))

    # 1. Test the various skip methods
    out, ret = hook.update_egg_info()
    # All skipped hooks should not return a error level
    assert ret == 0
    assert out == "update-egg-info: Skipping, no setup script found."

    create_pip_package(wd)

    # At this point there is a valid config but it has not been "editable installed"
    out, ret = hook.update_egg_info()
    assert ret == 0
    assert out == "update-egg-info: Skipping, no .egg-info directory found."

    # Simulate an editable install by creating the egg_info folder
    git_wd.do_ex("python setup.py egg_info")
    version_path = wd.cwd.joinpath("version.py")

    # Remove the version.py file so we can verify that build_command was run
    version_path.unlink()

    # Check that "SETUPTOOLS_SCM_SKIP_UPDATE_EGG_INFO" env var is respected
    monkeypatch.setenv("SETUPTOOLS_SCM_SKIP_UPDATE_EGG_INFO", "1")
    out, ret = hook.update_egg_info()
    assert ret == 0
    assert out == (
        'update-egg-info: Skipping, "SETUPTOOLS_SCM_SKIP_UPDATE_EGG_INFO" '
        "env var set."
    )
    assert not version_path.exists()

    monkeypatch.setenv("SETUPTOOLS_SCM_SKIP_UPDATE_EGG_INFO", "0")
    out, ret = hook.update_egg_info()
    assert ret == 0
    assert success_message in out
    assert version_path.exists()
    monkeypatch.delenv("SETUPTOOLS_SCM_SKIP_UPDATE_EGG_INFO")

    # 2. Test a correctly configured setuptools_scm working copy
    version_path.unlink()

    # Simulate post-commit, post-checkout or post-merge hook call
    out, ret = hook.update_egg_info()
    assert ret == 0
    assert success_message in out
    assert version_path.exists()
    version_path.unlink()

    # Use an interactive rebase to check that the hook skips processing if
    # `--post-rewrite` is not passed
    do_interactive(wd.cwd)

    out, ret = hook.update_egg_info()
    assert ret == 0
    assert out == "update-egg-info: Skipping, rebase in progress."
    assert not version_path.exists()

    # If `--post-rewrite` is passed by the post-rewrite hook, build_command is run
    # even if git is currently rebasing
    out, ret = hook.update_egg_info(force_on_rebase=True)
    assert ret == 0
    assert success_message in out
    assert version_path.exists()

    # After the rebase is finished, the hook runs again
    version_path.unlink()
    git_wd.do_ex("git rebase --continue")
    out, ret = hook.update_egg_info()
    assert ret == 0
    assert success_message in out
    assert version_path.exists()

    # 3. Check that if there is a error calling build_command it is reported to git
    with wd.cwd.joinpath("setup.py").open("a") as f:
        f.write("\nsyntax error")
    out, ret = hook.update_egg_info()
    assert ret == 1
    assert "update-egg-info: Error running build_command:" in out


def test_pre_commit_hook_cli(wd):
    # Create a working pip package with the egg_info folder
    create_pip_package(wd)
    git_wd = git.GitWorkdir(os.fspath(wd.cwd))
    git_wd.do_ex("python setup.py egg_info")
    version_path = wd.cwd.joinpath("version.py")
    # Remove version.py created by egg_info command
    version_path.unlink()

    build_command = ["python", "setup.py", "egg_info"]
    success_message = "update-egg-info: Running command: {}"

    # Test post-commit, post-checkout or post-merge hook call
    out, _, ret = git_wd.do_ex("python -m setuptools_scm.pre_commit_hook")
    assert ret == 0
    assert success_message.format(build_command) in out
    assert version_path.exists()

    # Test that the post-rewrite flag is respected
    version_path.unlink()
    out, _, ret = git_wd.do_ex(
        "python -m setuptools_scm.pre_commit_hook --post-rewrite"
    )
    assert ret == 0
    assert success_message.format(build_command) in out
    assert version_path.exists()

    # Test that passing a custom command evaluates correctly
    out, err, ret = git_wd.do_ex(
        "python -m setuptools_scm.pre_commit_hook touch test.txt"
    )
    assert ret == 0
    assert success_message.format(["touch", "test.txt"]) in out
    assert wd.cwd.joinpath("test.txt").exists()
