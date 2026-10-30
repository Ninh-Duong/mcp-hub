#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd -- "$(dirname -- "$0")/.." && pwd)
cd "$repo_root"

python_version=$(<"$repo_root/.python-version")
if [[ ! "$python_version" =~ ^[0-9]+\.[0-9]+$ ]]; then
    printf '%s\n' "Invalid .python-version; expected MAJOR.MINOR." >&2
    exit 2
fi

python_major=${python_version%%.*}
python_minor=${python_version#*.}
# The uv version installed when none is found, and the SHA-256 of its installer script; update both together.
uv_version="0.12.3"
uv_installer_sha256="a7e3924ea1cd06bf1518c577d635c624ae2e2db030e0fc8ff8cf426224384e17"
uv_directory="$repo_root/.tools"
local_uv="$uv_directory/uv"
uv_bin=""

if [[ -x "$local_uv" ]]; then
    uv_bin="$local_uv"
elif command -v uv >/dev/null 2>&1; then
    uv_bin=$(command -v uv)
fi

python_version_available() {
    local candidate

    if [[ -n "$uv_bin" ]] && "$uv_bin" python find "$python_version" >/dev/null 2>&1; then
        return 0
    fi

    for candidate in "python$python_version" python3 python; do
        if command -v "$candidate" >/dev/null 2>&1 &&
            "$candidate" -c "import sys; raise SystemExit(sys.version_info[:2] != ($python_major, $python_minor))" >/dev/null 2>&1; then
            return 0
        fi
    done

    return 1
}

need_uv=0
need_python=0
if [[ -z "$uv_bin" ]]; then
    need_uv=1
fi
if ! python_version_available; then
    need_python=1
fi

if (( need_uv )) && ! command -v curl >/dev/null 2>&1 && ! command -v wget >/dev/null 2>&1; then
    printf '%s\n' "Cannot install uv: curl or wget is required." >&2
    exit 2
fi

sha256_of() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | cut -d ' ' -f 1
    elif command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "$1" | cut -d ' ' -f 1
    else
        openssl dgst -sha256 -r "$1" | cut -d ' ' -f 1
    fi
}

download_uv_installer() {
    local url="https://astral.sh/uv/$uv_version/install.sh"
    if command -v curl >/dev/null 2>&1; then
        curl -LsSf -o "$1" "$url"
    else
        wget -qO "$1" "$url"
    fi
}

if (( need_uv )); then
    mkdir -p "$uv_directory"
    uv_installer="$uv_directory/uv-installer.sh"
    printf '%s\n' "[mcp-hub] Installing uv $uv_version into the repository-local .tools directory." >&2
    if ! download_uv_installer "$uv_installer"; then
        rm -f "$uv_installer"
        printf '%s\n' "Bootstrap failed: could not download the uv installer." >&2
        exit 1
    fi
    if [[ "$(sha256_of "$uv_installer")" != "$uv_installer_sha256" ]]; then
        rm -f "$uv_installer"
        printf '%s\n' "Bootstrap failed: the uv $uv_version installer does not match its pinned SHA-256; it was not run." >&2
        exit 1
    fi
    export UV_INSTALL_DIR="$uv_directory"
    export UV_NO_MODIFY_PATH=1
    if ! sh "$uv_installer" >&2; then
        rm -f "$uv_installer"
        printf '%s\n' "Bootstrap failed: could not install uv." >&2
        exit 1
    fi
    rm -f "$uv_installer"
    unset UV_INSTALL_DIR UV_NO_MODIFY_PATH
    if [[ ! -x "$local_uv" ]]; then
        printf '%s\n' "The uv installer did not create .tools/uv." >&2
        exit 1
    fi
    uv_bin="$local_uv"
fi

if ! python_version_available; then
    printf '[mcp-hub] Installing Python %s through uv.\n' "$python_version" >&2
    if ! "$uv_bin" python install "$python_version" >&2; then
        printf 'Bootstrap failed: could not install Python %s.\n' "$python_version" >&2
        exit 1
    fi
fi

set +e
"$uv_bin" run --locked --python "$python_version" mcp-hub
status=$?
exit "$status"
