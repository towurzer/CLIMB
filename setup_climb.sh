#!/bin/bash

set -e # exit if nonzero exit status

# load environment variables and database dump
if [ ! -f .env ]; then
    echo "Error: .env file not found in the root directory."
    exit 1
fi

export $(grep -v '^#' .env | xargs)

DB_PORT=${DB_PORT:-5432}
POSTGRES_DB_NAME=${POSTGRES_DB_NAME:-CLIMB_DB}
POSTGRES_PASSWORD=${POSTGRES_PASSWORD:-password}


PROJECT_DIR=$(pwd)
DUMP_FILE="dataset/climb_db.dump"

if [ ! -f "$DUMP_FILE" ]; then
    echo "Error: Database dump file not found at $DUMP_FILE"
    exit 1
fi

# Redis Container
echo "Checking Redis container..."
if podman ps -a --format '{{.Names}}' | grep -Eq "^climb_caching$"; then
    echo "Container 'climb_caching' already exists. Starting it..."
    podman start climb_caching
else
    echo "Creating and starting new 'climb_caching' container..."
    podman run --name climb_caching -p 6379:6379 -d docker.io/library/redis:7
fi

# Postgres Container
echo "Checking PostgreSQL container..."
if podman ps -a --format '{{.Names}}' | grep -Eq "^climb_db$"; then
    echo "Container 'climb_db' already exists. Starting it..."
    podman start climb_db
else
    echo "Creating and starting new 'climb_db' container..."
    podman run --name climb_db \
        -e POSTGRES_PASSWORD="$POSTGRES_PASSWORD" \
        -e POSTGRES_DB="$POSTGRES_DB_NAME" \
        -v postgres_data:/var/lib/postgresql/data \
        -p "$DB_PORT":5432 \
        -d docker.io/ankane/pgvector:latest
fi

# Connect to DB
echo "Waiting for PostgreSQL database to accept connections..."
until podman exec climb_db pg_isready -U postgres -d "$POSTGRES_DB_NAME" >/dev/null 2>&1; do
    sleep 2
done
echo "PostgreSQL is ready."

#Enable the vector extension
echo "Ensuring 'vector' extension is created..."
podman exec climb_db psql -U postgres -d "$POSTGRES_DB_NAME" -c "CREATE EXTENSION IF NOT EXISTS vector;"

# Apply the Database Dump
echo "Restoring database dump..."
if [ "$(head -c 5 "$DUMP_FILE")" = "PGDMP" ]; then
    echo "Detected custom format dump. Running pg_restore..."
    podman exec -i climb_db pg_restore -U postgres -d "$POSTGRES_DB_NAME" < "$DUMP_FILE" || true # || true to ignor warnings
else
    echo "Detected plain-text SQL format. Running psql..."
    podman exec -i climb_db psql -U postgres -d "$POSTGRES_DB_NAME" < "$DUMP_FILE"
fi

# Update image paths
TARGET_PATH="${PROJECT_DIR}/dataset/keyframes/"
echo "Updating file paths in 'shots' table to: $TARGET_PATH"

# Replaces the path prefix leading up to 'dataset/keyframes/' with the current absolute path
podman exec -i climb_db psql -U postgres -d "$POSTGRES_DB_NAME" -c \
    "UPDATE shots SET image_path = REGEXP_REPLACE(image_path, '^.*/dataset/keyframes/', '${TARGET_PATH}');"

# Install Python dependencies
echo "Installing Python dependencies..."
if [ -d "video_processing" ]; then
    cd video_processing

    PYTHON_CMD="python"
    if command -v python3 >/dev/null 2>&1; then
        PYTHON_CMD="python3"
    fi

    # Activate virtual environment if one is present
    if [ -d ".venv" ]; then
        echo "Activating virtual environment (.venv)..."
        source .venv/bin/activate
    fi

    if [ -f "requirements.txt" ]; then
        $PYTHON_CMD -m pip install -r requirements.txt
    else
        echo "Warning: requirements.txt not found in video_processing/"
    fi

    cd - > /dev/null
else
    echo "Warning: 'video_processing' directory not found. Skipping installation."
fi

echo "Setup process completed."