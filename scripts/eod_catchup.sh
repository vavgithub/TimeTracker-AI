#!/bin/bash
# Run on login — posts EOD for any missed days

cd /home/khyathi/VAV-projects/_Active_projects/time_tracker
source .venv/bin/activate

# Check last 3 weekdays
for days_ago in 1 2 3; do
    date=$(date -d "-${days_ago} days" +%Y-%m-%d)
    eod_file="out/eod_${date}.json"
    sent_marker="out/eod_sent_${date}.flag"
    
    # If EOD file exists but not sent yet
    if [ -f "$eod_file" ] && [ ! -f "$sent_marker" ]; then
        echo "Sending missed EOD for $date"
        DATE=$date bash scripts/eod_post.sh
        touch "$sent_marker"
    fi
done

