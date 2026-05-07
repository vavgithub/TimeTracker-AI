#!/bin/bash
ROOT=/home/khyathi/VAV-projects/_Active_projects/time_tracker
cd "$ROOT/backend"
source "$ROOT/.venv/bin/activate"
DATE=${1:-${DATE:-$(date +%Y-%m-%d)}}
"$ROOT/.venv/bin/python3" -c "
import config  # loads repo-root .env
from pipeline.eod.summary_writer import (
    generate_eod_summary, 
    format_eod_clickup_message,
    post_to_clickup_channel
)
import os

eod = generate_eod_summary('$DATE', os.getenv('USER_EMAIL'), skip_ai=True)
msg = format_eod_clickup_message(eod)
print(msg)

channel_id = os.getenv('EOD_CLICKUP_CHANNEL_ID', '')
if channel_id:
    success = post_to_clickup_channel(msg, channel_id)
    print('Posted to ClickUp:', success)
else:
    print('EOD_CLICKUP_CHANNEL_ID not set — skipping post')
" >> ~/.eod.log 2>&1

