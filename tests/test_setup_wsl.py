"""Exercise setup prompts and APT failures without installing anything."""

import os
import subprocess
from pathlib import Path

import pytest


pytestmark = pytest.mark.skipif(os.name == "nt", reason="Requires native Linux Bash")
SCRIPT = Path(__file__).resolve().parents[1] / "setup_wsl.sh"


@pytest.fixture
def setup_environment(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    prefix = tmp_path / "local setup"
    home = tmp_path / "home"
    home.mkdir()
    log = tmp_path / "commands.log"
    ready = tmp_path / "dotnet-ready"

    def executable(path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#!/bin/bash\n" + text)
        path.chmod(0o755)

    executable(
        prefix / "python" / "bin" / "python3.13",
        'if [[ "$1" == --version ]]; then echo "Python 3.13.1"; fi\nexit 0\n',
    )
    executable(
        bin_dir / "dotnet",
        '[[ -f "$SETUP_READY" ]] || exit 1\n'
        'echo "Microsoft.NETCore.App 10.0.15 [/usr/share/dotnet/shared/Microsoft.NETCore.App]"\n',
    )
    executable(
        bin_dir / "apt-get",
        'echo "$*" >> "$SETUP_LOG"\n'
        'if [[ "${SETUP_APT_FAIL:-}" == "$1" ]]; then exit 100; fi\n'
        'if [[ "$1" == install ]]; then touch "$SETUP_READY"; fi\n',
    )
    executable(bin_dir / "sudo", 'if [[ "$1" == -n ]]; then shift; fi\nexec "$@"\n')
    # Shadow system interpreters so detection remains independent of the test host.
    executable(bin_dir / "python3", "exit 1\n")
    executable(bin_dir / "python3.13", "exit 1\n")
    env = dict(
        os.environ, HOME=str(home), PATH=f"{bin_dir}:/usr/bin:/bin",
        SETUP_LOG=str(log), SETUP_READY=str(ready),
    )

    def run(*args, input=""):
        return subprocess.run(
            ["bash", str(SCRIPT), "--prefix", str(prefix), *args],
            input=input, text=True, capture_output=True, env=env, timeout=15,
        )

    return run, env, prefix, log, ready


def test_ready_check_does_not_install(setup_environment):
    run, _, _, log, ready = setup_environment
    ready.touch()
    result = run("--check")
    assert result.returncode == 0, result.stderr
    assert "Prerequisites ready" in result.stdout
    assert not log.exists()


def test_missing_check_does_not_install(setup_environment):
    run, _, _, log, _ = setup_environment
    result = run("--check")
    assert result.returncode == 1
    assert "Missing native .NET 10.0" in result.stdout
    assert not log.exists()


def test_missing_python_check_does_not_create_installation(setup_environment):
    run, _, prefix, log, _ = setup_environment
    (prefix / "python" / "bin" / "python3.13").unlink()
    result = run("--check")
    assert result.returncode == 1
    assert "Missing recommended native Python 3.13" in result.stdout
    assert not (prefix / "venv").exists()
    assert not log.exists()


def test_yes_reuses_present_installations(setup_environment):
    run, _, _, log, ready = setup_environment
    ready.touch()
    result = run("--yes")
    assert result.returncode == 0, result.stderr
    assert not log.exists()


def test_declined_install_does_not_run_apt(setup_environment):
    run, _, _, log, _ = setup_environment
    result = run(input="n\n")
    assert result.returncode == 1
    assert "[y/N]" in result.stdout
    assert not log.exists()


def test_yes_installs_aspnetcore_runtime(setup_environment):
    run, _, _, log, _ = setup_environment
    result = run("--yes")
    assert result.returncode == 0, result.stderr
    assert log.read_text().splitlines() == [
        "update", "install -y --no-install-recommends aspnetcore-runtime-10.0",
    ]


@pytest.mark.parametrize("failure", ["update", "install"])
def test_apt_failure_is_explicit_and_does_not_fall_back(setup_environment, failure):
    run, env, _, log, _ = setup_environment
    env["SETUP_APT_FAIL"] = failure
    result = run("--yes")
    assert result.returncode == 1
    assert "APT could not install aspnetcore-runtime-10.0" in result.stderr
    commands = log.read_text().splitlines()
    assert len(commands) == (1 if failure == "update" else 2)


def test_conflicting_flags_are_rejected(setup_environment):
    run, _, _, log, _ = setup_environment
    result = run("--yes", "--check")
    assert result.returncode == 1
    assert "cannot be combined" in result.stderr
    assert not log.exists()


def test_missing_python_uses_official_latest_stable_source(setup_environment, tmp_path):
    run, env, prefix, log, ready = setup_environment
    ready.touch()
    python = prefix / "python" / "bin" / "python3.13"
    template = tmp_path / "python-template"
    template.write_bytes(python.read_bytes())
    python.unlink()
    bin_dir = Path(env["PATH"].split(":")[0])
    env.update(SETUP_PREFIX=str(prefix), SETUP_TEMPLATE=str(template))
    scripts = {
        "dpkg-query": "printf 'install ok installed'\n",
        "curl": (
            'echo "$*" >> "$SETUP_LOG"\n'
            'while (( $# )); do\n'
            '  if [[ "$1" == -o ]]; then output=$2; break; fi\n'
            '  shift\n'
            'done\n'
            "printf '%s\\n' 'href=\"3.13.9/\"' 'href=\"3.13.12/\"' "
            "'href=\"3.13.13rc1/\"' > \"$output\"\n"
        ),
        "tar": (
            'while (( $# )); do\n'
            '  if [[ "$1" == -C ]]; then cd "$2"; break; fi\n'
            '  shift\n'
            'done\n'
            'mkdir -p Python-3.13.12\n'
            "printf '#!/bin/bash\\nexit 0\\n' > Python-3.13.12/configure\n"
            'chmod +x Python-3.13.12/configure\n'
        ),
        "make": (
            'echo "make $*" >> "$SETUP_LOG"\n'
            'if [[ "$1" == altinstall ]]; then\n'
            '  mkdir -p "$SETUP_PREFIX/python/bin"\n'
            '  cp "$SETUP_TEMPLATE" "$SETUP_PREFIX/python/bin/python3.13"\n'
            '  chmod +x "$SETUP_PREFIX/python/bin/python3.13"\n'
            'fi\n'
        ),
    }
    for name, body in scripts.items():
        path = bin_dir / name
        path.write_text("#!/bin/bash\nset -eu\n" + body)
        path.chmod(0o755)
    result = run("--yes")
    assert result.returncode == 0, result.stderr
    commands = log.read_text()
    assert "https://www.python.org/ftp/python/3.13.12/Python-3.13.12.tar.xz" in commands
    assert "make -j2" in commands
    assert "make altinstall" in commands
    assert python.is_file()
