"""install/setup.sh: the first-entry menu, driven with stub `git` and `curl`.

It lists master and the 10 newest releases, downloads the installer of the version
picked, and hands it the version. An installer from before version pinning (no
SPLOUCH_PINS_REF) is only trusted with master and the newest release, which it can
reach as `latest`; and install.sh's own checkout resolves a tag, a branch or a commit.
"""

import os
import subprocess
from pathlib import Path

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SETUP = os.path.join(REPO, "install", "setup.sh")
INSTALLER = os.path.join(REPO, "install", "install.sh")

TAGS = [f"v2026.0{m}.{n}" for m in (6, 7, 8) for n in range(5)]  # 15 releases


def _stubs(tmp_path, pinning):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    refs = "".join(f"abc\trefs/tags/{t}\n" for t in [*TAGS, "not-a-release"])
    (bin_dir / "git").write_text(f"#!/bin/sh\nprintf '{refs}'\n")
    marker = "# SPLOUCH_PINS_REF" if pinning else ""
    # The fake installer reports the arguments it was given.
    (bin_dir / "curl").write_text(
        "#!/bin/sh\n"
        'while [ "$1" != "-o" ]; do shift; done\n'
        f'printf \'#!/bin/sh\\n{marker}\\necho "ARGS[$1|$2]"\\n\' > "$2"\n'
    )
    for f in bin_dir.iterdir():
        f.chmod(0o755)
    return {
        **os.environ,
        "TMPDIR": str(tmp_path),
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
    }


def _run(tmp_path, answers, pinning=True, args=()):
    out = subprocess.run(
        ["bash", SETUP, *args],
        input=answers,
        capture_output=True,
        text=True,
        env=_stubs(tmp_path, pinning),
        timeout=20,
        check=False,
    )
    return out.stdout + out.stderr


def test_scripts_are_valid_bash():
    subprocess.run(["bash", "-n", SETUP], check=True)


def test_the_menu_is_master_then_the_ten_newest_releases(tmp_path):
    out = _run(tmp_path, "13\n")  # Quit
    assert "1) master" in out
    assert "2) v2026.08.4 (latest release)" in out
    assert "11) v2026.07.0" in out
    assert "v2026.06.4" not in out and "not-a-release" not in out
    assert "12) Custom" in out
    assert "|____/" in out  # the logo


@pytest.mark.parametrize(
    "answers, expected",
    [
        ("1\n", "ARGS[|master]"),
        ("3\n", "ARGS[|v2026.08.3]"),
        ("12\nfeature/x\n", "ARGS[|feature/x]"),
    ],
)
def test_the_choice_is_passed_to_the_installer(tmp_path, answers, expected):
    assert expected in _run(tmp_path, answers)


def test_role_and_version_arguments_skip_the_menu(tmp_path):
    out = _run(tmp_path, "", args=("cloud", "v2026.07.1"))
    assert "ARGS[cloud|v2026.07.1]" in out
    assert "Which version" not in out


def test_an_old_installer_reaches_the_newest_release_as_latest(tmp_path):
    assert "ARGS[|latest]" in _run(tmp_path, "2\n", pinning=False)


def test_end_of_input_at_the_menu_exits(tmp_path):
    assert "ARGS[" not in _run(tmp_path, "")


def test_an_old_installer_is_refused_an_older_release(tmp_path):
    out = _run(tmp_path, "3\n13\n", pinning=False)
    assert "can only install master or the latest release" in out
    assert "ARGS[" not in out


def test_install_sh_carries_the_marker():
    assert "SPLOUCH_PINS_REF" in Path(INSTALLER).read_text(encoding="utf-8")


@pytest.fixture
def repo(tmp_path):
    """A clone with a tag, a branch and a commit only reachable by hash."""
    origin = tmp_path / "origin"
    git = [
        "git",
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@t",
        "-c",
        "init.defaultBranch=master",
    ]
    subprocess.run([*git, "init", "-q", str(origin)], check=True)

    def commit(msg):
        subprocess.run(
            [*git, "-C", str(origin), "commit", "-q", "--allow-empty", "-m", msg],
            check=True,
        )
        return subprocess.run(
            ["git", "-C", str(origin), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

    tagged = commit("one")
    subprocess.run(["git", "-C", str(origin), "tag", "v2026.01.0"], check=True)
    hashed = commit("two")
    subprocess.run(["git", "-C", str(origin), "branch", "feature/x"], check=True)
    commit("three")
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(origin), str(clone)], check=True)
    return clone, {"v2026.01.0": tagged, "feature/x": hashed, hashed[:10]: hashed}


def _checkout(clone, version):
    text = Path(INSTALLER).read_text(encoding="utf-8")
    fn = text[text.index("checkout_version() {") :]
    fn = fn[: fn.index("\n}\n") + 3]
    script = (
        'info() { :; }; warn() { :; }; error() { echo "$*" >&2; }\n'
        f"{fn}\nVERSION_CHOICE={version!r}\ncheckout_version {str(clone)!r}\n"
        f"git -C {str(clone)!r} rev-parse HEAD"
    )
    return subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, check=False
    )


@pytest.mark.parametrize("version", ["v2026.01.0", "feature/x", "hash"])
def test_checkout_version_pins_a_tag_a_branch_or_a_commit(repo, version):
    clone, heads = repo
    if version == "hash":
        version = next(k for k in heads if k not in ("v2026.01.0", "feature/x"))
    out = _checkout(clone, version)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip().endswith(heads[version])


def test_checkout_version_refuses_an_unknown_version(repo):
    clone, _ = repo
    out = _checkout(clone, "nope")
    assert out.returncode != 0
    assert "not a tag, branch or commit" in out.stderr
