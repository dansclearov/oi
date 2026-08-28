#!/usr/bin/env bash
# Record demo.gif from a real terminal: a fullscreen Ghostty window runs the
# scripted TUI session (demo/tui_demo.py) while gpu-screen-recorder captures
# the monitor; ffmpeg turns the video into the GIF. A real terminal is
# required — VHS's headless one has no kitty graphics, so no math rendering.
# Needs: ghostty, gpu-screen-recorder, ffmpeg. Extra args go to `oi`.
set -euo pipefail
cd "$(dirname "$0")/.."
video=$(mktemp --suffix=.mp4)
monitor=$(gpu-screen-recorder --list-monitors | head -1 | cut -d'|' -f1)

ghostty --title=oi-demo --fullscreen=true --font-size=22 \
  -e uv run python demo/tui_demo.py -P concise "$@" &
ghostty_pid=$!
sleep 1.5
gpu-screen-recorder -w "$monitor" -f 30 -cursor no -encoder cpu -q very_high \
  -o "$video" >/dev/null 2>&1 &
recorder_pid=$!

wait "$ghostty_pid"
kill -INT "$recorder_pid"
wait "$recorder_pid" || true

ffmpeg -y -loglevel error -i "$video" \
  -vf "fps=15,scale=1200:-1:flags=lanczos,split[a][b];[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=none:diff_mode=rectangle" \
  demo.gif
rm -f "$video"
echo "wrote demo.gif ($(ffprobe -v error -show_entries format=duration -of csv=p=0 demo.gif)s)"
