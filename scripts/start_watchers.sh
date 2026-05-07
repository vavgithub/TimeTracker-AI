#!/bin/bash
# Start input watcher
nohup /home/khyathi/VAV-projects/_Active_projects/time_tracker/.venv/bin/python3 -c "
import sys
sys.path.insert(0, '/home/khyathi/aw-watcher-input/src')
from aw_watcher_input.main import main
main()
" \
  > ~/.aw-input.log 2>&1 &
echo "Input watcher started: $!"

