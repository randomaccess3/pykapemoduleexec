#!/usr/bin/env bash
# Install native prerequisites without replacing the distribution's Python.
set -euo pipefail

PYTHON_MINOR=3.13
DOTNET_CHANNEL=10.0
PREFIX="${XDG_DATA_HOME:-$HOME/.local/share}/pykapemoduleexec"
ASSUME_YES=false
CHECK_ONLY=false
WORK=""

usage() {
    cat <<'EOF'
Usage: bash setup_wsl.sh [--yes | --check] [--prefix DIRECTORY]

Check native Python 3.13, .NET 10, and PyYAML. Ask before installing missing
components. --yes accepts installation prompts; --check makes no changes.
Python is built from python.org sources; .NET uses apt-get to install
aspnetcore-runtime-10.0 (which includes the .NET runtime needed by EZTools).
Build dependencies come from Ubuntu's package repositories and may need sudo.
Nothing replaces system Python or edits your shell startup files.
EOF
}

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
trap 'status=$?; if [[ -n "$WORK" ]]; then rm -rf -- "$WORK"; fi; exit "$status"' EXIT

while (( $# )); do
    case "$1" in
        --yes) ASSUME_YES=true; shift ;;
        --check) CHECK_ONLY=true; shift ;;
        --prefix)
            (( $# >= 2 )) || die "--prefix requires a directory"
            PREFIX=$2; shift 2 ;;
        --help|-h) usage; exit 0 ;;
        *) usage >&2; die "Unknown argument: $1" ;;
    esac
done
[[ "$PREFIX" = /* && "$PREFIX" != / ]] || die "--prefix must be an absolute, non-root path"
if $ASSUME_YES && $CHECK_ONLY; then die "--yes and --check cannot be combined"; fi
[[ "$(uname -s)" == Linux ]] || die "Run this script inside WSL/Linux, not Windows"

confirm() {
    local answer
    if $CHECK_ONLY; then return 1; fi
    if $ASSUME_YES; then printf '%s [accepted by --yes]\n' "$1"; return 0; fi
    printf '%s [y/N] ' "$1"
    if ! IFS= read -r answer; then printf '\n' >&2; return 1; fi
    [[ "$answer" == y || "$answer" == Y || "$answer" == yes || "$answer" == YES ]]
}

download() {
    curl --fail --location --silent --show-error --retry 3 \
        --connect-timeout 30 --max-time 900 --proto '=https' --tlsv1.2 "$1" -o "$2"
}

ensure_workspace() {
    if [[ -z "$WORK" ]]; then
        command -v curl >/dev/null || die "Install curl and ca-certificates from your distribution first"
        WORK=$(mktemp -d)
        mkdir -p "$PREFIX"
    fi
}

python_ok() {
    [[ -x "$1" ]] && "$1" -c \
        "import sys; sys.exit(0 if sys.platform == 'linux' and sys.version_info[:2] == (3, 13) else 1)" \
        >/dev/null 2>&1
}

dotnet_ok() {
    local runtimes
    [[ -x "$1" ]] || return 1
    [[ "$1" != *.exe ]] || return 1
    runtimes=$("$1" --list-runtimes) || return 1
    grep -Eq '^Microsoft\.NETCore\.App 10\.0\.[0-9]+ \[/' <<< "$runtimes"
}

apt_install() {
    command -v apt-get >/dev/null || die "Automatic installation requires apt-get"
    local -a elevate=()
    if (( EUID != 0 )); then
        elevate=(sudo)
        if $ASSUME_YES; then elevate+=(-n); fi
    fi
    "${elevate[@]}" apt-get update &&
    "${elevate[@]}" apt-get install -y --no-install-recommends "$@"
}

PYTHON=""
for candidate in "$PREFIX/venv/bin/python" "$PREFIX/python/bin/python3.13" \
    "$(command -v python3.13 || true)" "$(command -v python3 || true)"; do
    if python_ok "$candidate"; then PYTHON=$candidate; break; fi
done
DOTNET=""
for candidate in "$PREFIX/dotnet/dotnet" "$(command -v dotnet || true)" "$HOME/.dotnet/dotnet"; do
    if dotnet_ok "$candidate"; then DOTNET=$candidate; break; fi
done

if [[ -n "$PYTHON" ]]; then
    printf 'Found Python: %s (%s)\n' "$("$PYTHON" --version)" "$PYTHON"
else
    printf 'Missing recommended native Python %s.\n' "$PYTHON_MINOR"
fi
if [[ -n "$DOTNET" ]]; then
    printf 'Found native .NET %s runtime: %s\n' "$DOTNET_CHANNEL" "$DOTNET"
else
    printf 'Missing native .NET %s runtime.\n' "$DOTNET_CHANNEL"
fi

if [[ -z "$PYTHON" ]] && confirm "Build Python $PYTHON_MINOR from official python.org sources (plus Ubuntu build dependencies)?"; then
    command -v apt-get >/dev/null || die "Automatic Python build dependencies currently require Ubuntu/Debian"
    ensure_workspace
    dependencies=(
        build-essential pkg-config ca-certificates libssl-dev zlib1g-dev
        libbz2-dev libreadline-dev libsqlite3-dev libffi-dev liblzma-dev
        libncursesw5-dev uuid-dev
    )
    missing=()
    for package in "${dependencies[@]}"; do
        if [[ "$(dpkg-query -W -f='${Status}' "$package" 2>/dev/null || true)" != "install ok installed" ]]; then
            missing+=("$package")
        fi
    done
    if (( ${#missing[@]} )); then
        apt_install "${missing[@]}"
    fi
    download https://www.python.org/ftp/python/ "$WORK/releases.html"
    versions=$(grep -oE 'href="3\.13\.[0-9]+/"' "$WORK/releases.html" | cut -d '"' -f 2 | tr -d '/' | sort -Vu)
    version=$(tail -n 1 <<< "$versions")
    [[ "$version" =~ ^3\.13\.[0-9]+$ ]] || die "Could not determine latest stable Python $PYTHON_MINOR release"
    printf 'Building Python %s in %s (this can take several minutes).\n' "$version" "$PREFIX/python"
    download "https://www.python.org/ftp/python/$version/Python-$version.tar.xz" "$WORK/python.tar.xz"
    tar -xJf "$WORK/python.tar.xz" -C "$WORK"
    (
        cd "$WORK/Python-$version"
        ./configure --prefix="$PREFIX/python" --with-ensurepip=install
        make -j2
        make altinstall
    )
    PYTHON="$PREFIX/python/bin/python3.13"
    python_ok "$PYTHON" || die "Installed Python failed its version/platform check"
    "$PYTHON" -c 'import ssl, sqlite3, bz2, lzma, ctypes, venv' \
        || die "Installed Python is missing required standard-library components"
fi

YAML_OK=false
if [[ -n "$PYTHON" ]]; then
    if "$PYTHON" -c 'import yaml; assert tuple(map(int, yaml.__version__.split(".")[:1])) >= (6,)' >/dev/null 2>&1; then
        YAML_OK=true
        printf 'Found PyYAML >= 6.0.\n'
    elif confirm "Create a user-local Python environment and install PyYAML from its official PyPI release?"; then
        "$PYTHON" -m venv "$PREFIX/venv"
        PYTHON="$PREFIX/venv/bin/python"
        "$PYTHON" -m pip install --index-url https://pypi.org/simple 'PyYAML>=6.0'
        "$PYTHON" -c 'import yaml; assert int(yaml.__version__.split(".")[0]) >= 6'
        YAML_OK=true
    else
        printf 'Missing PyYAML >= 6.0.\n' >&2
    fi
fi

if [[ -z "$DOTNET" ]] && confirm "Install aspnetcore-runtime-$DOTNET_CHANNEL using apt-get (requires sudo)?"; then
    if ! apt_install "aspnetcore-runtime-$DOTNET_CHANNEL"; then
        die "APT could not install aspnetcore-runtime-$DOTNET_CHANNEL. Check repository/network errors above (including sudo authentication; run sudo -v before --yes). Ubuntu 20.04's default repositories do not supply .NET 10; configure an officially supported feed or use a supported Ubuntu release."
    fi
    DOTNET="$(command -v dotnet || true)"
    dotnet_ok "$DOTNET" || die "Installed .NET failed its runtime check; inspect missing native-library errors"
fi

if [[ -z "$PYTHON" || -z "$DOTNET" ]] || ! $YAML_OK; then
    die "Setup incomplete; missing or declined prerequisites. Rerun to install them."
fi
printf '\nPrerequisites ready. Use these commands in this WSL shell:\n'
printf 'export DOTNET_ROOT=%q\n' "$(dirname "$(readlink -f "$DOTNET")")"
printf 'export PATH=%q:"$PATH"\n' "$(dirname "$DOTNET")"
printf '%q kape_modules.py --help\n' "$PYTHON"
printf '\n.NET 10 matches the installed EZTools net10.0 runtime configs.\n'
printf 'Some EZTools still require Windows libraries or access to unlocked artifacts.\n'
