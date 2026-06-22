#!/bin/bash

# 1. Start Podman DB Container
echo "Starting Database Container..."
podman start climb

# 2. Kill old session if it still exists in the background
tmux kill-session -t vbs 2>/dev/null

# 3. Create a new tmux session named 'vbs'
echo "Creating tmux session with side-by-side panes..."
tmux new-session -d -s vbs -n 'VBS-Ecosystem'

# Source conda definition to make the 'conda' command available inside the script
# Usually located at ~/.bashrc or ~/miniconda3/etc/profile.d/conda.sh
if [ -f "$HOME/miniconda3/etc/profile.d/conda.sh" ]; then
    CONDA_PATH="$HOME/miniconda3/etc/profile.d/conda.sh"
elif [ -f "$HOME/anaconda3/etc/profile.d/conda.sh" ]; then
    CONDA_PATH="$HOME/anaconda3/etc/profile.d/conda.sh"
else
    CONDA_PATH="/etc/profile.d/conda.sh"
fi

# Pane 1 (Left): AI Pipeline
tmux send-keys -t vbs "conda activate climb && cd video_processing/src && python main.py -start" C-m

# Pane 2 (Middle): Split horizontally to create the middle pane for Backend
tmux split-window -h -t vbs
tmux send-keys -t vbs 'conda activate climb && cd backend && [ ! -d "node_modules" ] && npm install; npm start' C-m

# Pane 3 (Right): Split horizontally again from the right side for Frontend
tmux split-window -h -t vbs
tmux send-keys -t vbs 'conda activate climb && cd frontend && [ ! -d "node_modules" ] && npm install; npm run dev' C-m

# 4. Equalize the width of all three panes so they look clean
tmux select-layout -t vbs even-horizontal

# 5. Attach to the tmux session
echo "Attaching to tmux session..."
tmux attach-session -t vbs