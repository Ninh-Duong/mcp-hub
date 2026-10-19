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

if (( need_uv )); then
    mkdir -p "$uv_directory"
    export UV_INSTALL_DIR="$uv_directory"
    export UV_NO_MODIFY_PATH=1
    if command -v curl >/dev/null 2>&1; then
        printf '%s\n' "[mcp-hub] Installing uv into the repository-local .tools directory." >&2
        if ! curl -LsSf https://astral.sh/uv/install.sh | sh >&2; then
            printf '%s\n' "Bootstrap failed: could not install uv." >&2
            exit 1
        fi
    elif command -v wget >/dev/null 2>&1; then
        printf '%s\n' "[mcp-hub] Installing uv into the repository-local .tools directory." >&2
        if ! wget -qO- https://astral.sh/uv/install.sh | sh >&2; then
            printf '%s\n' "Bootstrap failed: could not install uv." >&2
            exit 1
        fi
    fi
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
