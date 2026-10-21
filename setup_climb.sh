#!/bin/bash
# Sets CLIMB up on this machine. Run from anywhere; safe to run again, what exists is kept.
#   1. .env, copied from .env.example the first time, to be filled in before going on
#   2. video_processing/.venv with requirements.txt. Where nvidia-smi finds a GPU: torch for CUDA
#      12.6 and paddlepaddle-gpu, the CUDA that faster-whisper and Paddle need too (README 1.0)
#   3. the cache and database containers, with podman or docker
#   4. the schema: dataset/climb_db.dump restored if there is one and the database is empty,
#      then `main.py migrate`
# The backend and frontend install their node modules on their first start (start_climb.sh).

set -e # exit if nonzero exit status
cd "$(dirname "$0")"

if [ ! -f .env ]; then
    cp .env.example .env
    echo "Created .env from .env.example. Set POSTGRES_PASSWORD and CLIMB_SOURCE_ROOT in it, then run this again."
    exit 1
fi

# One value from .env, without sourcing it: some values (the origin regex) are not valid shell.
env_value() {
    local value
    value=$(grep -E "^$1=" .env | tail -n 1 | cut -d= -f2-)
    echo "${value:-$2}"
}
POSTGRES_DB_NAME=$(env_value POSTGRES_DB_NAME CLIMB_DB)
DB_USER=$(env_value POSTGRES_USER postgres)
DB_CONTAINER=$(env_value CLIMB_DB_CONTAINER climb)
CACHE_CONTAINER=$(env_value CLIMB_CACHE_CONTAINER climb_caching)
REDIS_PORT=$(env_value REDIS_URL redis://localhost:6379 | sed -E 's#.*:([0-9]+)(/.*)?$#\1#')
DUMP_FILE="dataset/climb_db.dump"

CONTAINERS=$(command -v podman || command -v docker || true)
if [ -z "$CONTAINERS" ]; then
    echo "Error: neither podman nor docker is installed."
    exit 1
fi
CONTAINERS=$(basename "$CONTAINERS")

# --- Python ----------------------------------------------------------------------------------
PYTHON=$(command -v python3.13 || command -v python3)
VENV_PYTHON=video_processing/.venv/bin/python
if [ ! -x "$VENV_PYTHON" ]; then
    echo "Creating video_processing/.venv with $("$PYTHON" --version)..."
    "$PYTHON" -m venv video_processing/.venv
fi
if ! "$VENV_PYTHON" -c 'import sys; sys.exit(sys.version_info >= (3, 14))'; then
    echo "Note: Python 3.14 has no paddlepaddle, so OCR falls back to rapidocr (README 1.1)."
fi
# The GPU wheels run to gigabytes, more than a /tmp in memory may hold; pip unpacks them here.
export TMPDIR="$PWD/dataset/work/pip-tmp"
mkdir -p "$TMPDIR"
"$VENV_PYTHON" -m pip install --upgrade pip
if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1; then
    echo "NVIDIA GPU found: installing torch for CUDA 12.6, shared with faster-whisper and Paddle."
    # Named with +cu126 so that a torch already installed for another CUDA is replaced.
    "$VENV_PYTHON" -m pip install -r video_processing/requirements.txt \
        "torch==2.12.1+cu126" "torchvision==0.27.1+cu126" \
        --extra-index-url https://download.pytorch.org/whl/cu126
    if "$VENV_PYTHON" -c 'import sys; sys.exit(sys.version_info >= (3, 14))'; then
        # Paddle's download server can be slow from Europe; pip's default timeout is 15 s.
        "$VENV_PYTHON" -m pip install paddlepaddle-gpu==3.3.1 --timeout 120 --retries 10 \
            -i https://www.paddlepaddle.org.cn/packages/stable/cu126/
        # paddlepaddle-gpu pins older NCCL, cuDNN and cuSPARSELt than torch and replaces torch's:
        # with Paddle's NCCL torch does not import, with Paddle's cuDNN it refuses its first
        # convolution. torch's go back in; cuDNN 9 and NCCL 2 run code built for an older minor
        # version, so Paddle runs on them too (pip still lists Paddle's pins as conflicts).
        "$VENV_PYTHON" -m pip install $("$VENV_PYTHON" -c 'import importlib.metadata as m; print(" ".join(r.split(";")[0].replace(" ", "") for r in m.requires("torch") if r.startswith("nvidia-")))')
    fi
else
    "$VENV_PYTHON" -m pip install -r video_processing/requirements.txt
fi
rm -rf "$TMPDIR"
unset TMPDIR

# --- Containers ------------------------------------------------------------------------------
container_exists() {
    $CONTAINERS ps -a --format '{{.Names}}' | grep -qx "$1"
}

if container_exists "$CACHE_CONTAINER"; then
    echo "Starting the cache container '$CACHE_CONTAINER'..."
    $CONTAINERS start "$CACHE_CONTAINER" >/dev/null
else
    echo "Creating the cache container '$CACHE_CONTAINER' on port $REDIS_PORT..."
    $CONTAINERS run --name "$CACHE_CONTAINER" -p "$REDIS_PORT":6379 -d docker.io/library/redis:7
fi

if container_exists "$DB_CONTAINER"; then
    echo "Starting the database container '$DB_CONTAINER'..."
    $CONTAINERS start "$DB_CONTAINER" >/dev/null
else
    echo "Creating the database container '$DB_CONTAINER'..."
    # The same command `main.py schema` prints, so the two cannot drift apart.
    eval "$(cd video_processing/src && ../.venv/bin/python -c \
        "from db_setup import container_command; print(container_command('$CONTAINERS'))")"
fi

echo "Waiting for PostgreSQL to accept connections..."
until $CONTAINERS exec "$DB_CONTAINER" pg_isready -U "$DB_USER" -d "$POSTGRES_DB_NAME" >/dev/null 2>&1; do
    sleep 2
done

# --- Schema ----------------------------------------------------------------------------------
migrated=$($CONTAINERS exec "$DB_CONTAINER" psql -U "$DB_USER" -d "$POSTGRES_DB_NAME" -tAc \
    "SELECT to_regclass('public.schema_migrations') IS NOT NULL")
if [ -f "$DUMP_FILE" ] && [ "$migrated" != "t" ]; then
    echo "Restoring $DUMP_FILE into the empty database..."
    $CONTAINERS exec -i "$DB_CONTAINER" pg_restore -U "$DB_USER" -d "$POSTGRES_DB_NAME" --no-owner < "$DUMP_FILE"
fi
(cd video_processing/src && ../.venv/bin/python main.py migrate)

echo
echo "Set up. ./start_climb.sh starts CLIMB; 'python main.py check' in video_processing/src checks"
echo "everything a pipeline run needs (README 1.0)."
