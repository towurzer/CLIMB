#!/bin/bash

PIDS=() # remember background processes in case we don't support your terminal emulator
USED_FALLBACK=false

# function to kill background processes on exit
cleanup() {
    echo -e "\nShutting down background CLIMB services..."
    for pid in "${PIDS[@]}"; do
        if kill -0 "$pid" 2>/dev/null; then
            kill "$pid" 2>/dev/null || true
        fi
    done
    podman stop climb_caching 2>/dev/null
    podman stop climb 2>/dev/null
    echo "All background processes stopped."
    exit 0
}

# Determine Python command
PYTHON_CMD="python"
if command -v python3 >/dev/null 2>&1; then
    PYTHON_CMD="python3"
fi

# Run a command in a new terminal window or fallback to the current one
run_in_new_terminal() {
    local title="$1"
    local dir="$2"
    local cmd="$3"

    # Absolute path to prevent directory resolution errors
    local abs_dir="$(pwd)/$dir"

    # mac
    if [[ "$OSTYPE" == "darwin"* ]]; then
        osascript -e "tell application \"Terminal\" to do script \"cd '$abs_dir' && $cmd\""
        return
    fi

    # linux
    if command -v kitty >/dev/null 2>&1; then
        kitty --title "$title" bash -c "cd '$abs_dir' && $cmd; exec bash" &
    elif command -v alacritty >/dev/null 2>&1; then
        alacritty --title "$title" -e bash -c "cd '$abs_dir' && $cmd; exec bash" &
    elif command -v wezterm >/dev/null 2>&1; then
        wezterm start --title "$title" -- bash -c "cd '$abs_dir' && $cmd; exec bash" &
    elif command -v gnome-terminal >/dev/null 2>&1; then
        gnome-terminal --title="$title" -- bash -c "cd '$abs_dir' && $cmd; exec bash"
    elif command -v konsole >/dev/null 2>&1; then
        konsole -e bash -c "cd '$abs_dir' && $cmd; exec bash" &
    elif command -v xfce4-terminal >/dev/null 2>&1; then
        xfce4-terminal --title="$title" -e "bash -c \"cd '$abs_dir' && $cmd; exec bash\"" &
    elif command -v xterm >/dev/null 2>&1; then
        xterm -title "$title" -e "bash -c \"cd '$abs_dir' && $cmd; exec bash\"" &
    else
        # Sorry we don't support your terminal: Run in background of the current terminal and mix outputs
        echo "No compatible terminal emulator found. Running '$title' in current terminal..."
        USED_FALLBACK=true
        cd "$abs_dir"
        eval "$cmd" &
        PIDS+=($!)
        cd - > /dev/null
    fi
}

# Start Podman Containers
echo "Starting Podman containers..."
podman start climb_caching 2>/dev/null || echo "Notice: 'climb_caching' container not found or already running."
podman start climb 2>/dev/null || echo "Notice: 'climb_db' container not found or already running."

# Wait briefly for them to spin up
sleep 1

# Launch Search Engine
if [ -d "video_processing/src" ]; then
    run_in_new_terminal "CLIMB Search Engine" "video_processing/src" "$PYTHON_CMD main.py --startSearchEngine"
else
    echo "Error: Directory 'video_processing/src' not found."
fi

# Launch Backend
if [ -d "backend" ]; then
    backend_cmd="if [ ! -d node_modules ]; then npm install; fi; npm start"
    run_in_new_terminal "CLIMB Backend" "backend" "$backend_cmd"
else
    echo "Error: Directory 'backend' not found."
fi

# Launch Frontend
if [ -d "frontend" ]; then
    frontend_cmd="if [ ! -d node_modules ]; then npm install; fi; npm run dev"
    run_in_new_terminal "CLIMB Frontend" "frontend" "$frontend_cmd"
else
    echo "Error: Directory 'frontend' not found."
fi

# Handle Execution Flow
if [ "$USED_FALLBACK" = true ]; then
    echo "Services are running in the current terminal."
    echo "Press Ctrl+C to shut down all services."
    trap cleanup SIGINT SIGTERM EXIT # clean up background processes on ctr+c or sigterm
    wait
else
    echo "All services have been dispatched to separate terminal windows."
    echo "Closing this terminal in 2 seconds..."
    sleep 2
    exit 0
fi