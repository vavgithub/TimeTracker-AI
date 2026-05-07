#!/bin/bash
# Only run AW during work hours (10am - 7pm IST, Mon-Fri)

HOUR=$(date +%H)
DAY=$(date +%u)  # 1=Monday, 5=Friday

# Weekday only
if [ "$DAY" -gt 5 ]; then
    exit 0
fi

# Between 10am and 7pm
if [ "$HOUR" -ge 10 ] && [ "$HOUR" -lt 19 ]; then
    # Start AW if not running
    if ! pgrep -f "activitywatch" > /dev/null; then
        /home/khyathi/Applications/activitywatch-linux-x86_64_6dfd2126d3720bfe688db15f2daa52a6.AppImage &
    fi
    
    # Start input watcher if not running
    if ! pgrep -f "aw-watcher-input" > /dev/null; then
        nohup /home/khyathi/VAV-projects/_Active_projects/time_tracker/.venv/bin/python3 -c "
import sys
sys.path.insert(0, '/home/khyathi/aw-watcher-input/src')
from aw_watcher_input.main import main
main()
" \
            > ~/.aw-input.log 2>&1 &
    fi
else
    # Outside work hours — stop AW
    pkill -f "activitywatch" 2>/dev/null
    pkill -f "aw-watcher-input" 2>/dev/null
fi
