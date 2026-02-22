#!/usr/bin/env bash
set -euo pipefail

show_help() {
  cat <<'EOF'
Convert .mov (or other video) to compressed 720p MP4 (H.264 + AAC).

Usage:
  scripts/transcode_mov_to_mp4.sh -i INPUT.mov [-o OUTPUT.mp4] [options]

Options:
  -i, --input PATH         Input video path (required)
  -o, --output PATH        Output MP4 path (default: same dir/name with .mp4)
  --crf N                  Video quality (lower=better quality, bigger file).
                           Default: 28
  --preset NAME            x264 preset: ultrafast|superfast|veryfast|faster|
                           fast|medium|slow|slower|veryslow. Default: slow
  --audio-bitrate RATE     AAC bitrate, e.g. 96k, 128k. Default: 96k
  --overwrite              Overwrite output if exists
  -h, --help               Show this help

Notes:
  - Keeps aspect ratio and limits height to max 720px (no upscale).
  - Adds +faststart for better web playback.
EOF
}

require_binary() {
  local binary_name="$1"
  if ! command -v "$binary_name" >/dev/null 2>&1; then
    echo "Error: '$binary_name' is not installed or not in PATH." >&2
    exit 1
  fi
}

INPUT=""
OUTPUT=""
CRF="28"
PRESET="slow"
AUDIO_BITRATE="96k"
OVERWRITE="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    -i|--input)
      INPUT="${2:-}"
      shift 2
      ;;
    -o|--output)
      OUTPUT="${2:-}"
      shift 2
      ;;
    --crf)
      CRF="${2:-}"
      shift 2
      ;;
    --preset)
      PRESET="${2:-}"
      shift 2
      ;;
    --audio-bitrate)
      AUDIO_BITRATE="${2:-}"
      shift 2
      ;;
    --overwrite)
      OVERWRITE="true"
      shift
      ;;
    -h|--help)
      show_help
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      show_help
      exit 1
      ;;
  esac
done

if [[ -z "$INPUT" ]]; then
  echo "Error: input is required." >&2
  show_help
  exit 1
fi

if [[ ! -f "$INPUT" ]]; then
  echo "Error: input file not found: $INPUT" >&2
  exit 1
fi

if [[ -z "$OUTPUT" ]]; then
  input_dir="$(dirname "$INPUT")"
  input_name="$(basename "$INPUT")"
  input_stem="${input_name%.*}"
  OUTPUT="$input_dir/$input_stem.mp4"
fi

if [[ "$INPUT" == "$OUTPUT" ]]; then
  echo "Error: input and output paths are the same." >&2
  exit 1
fi

if [[ -f "$OUTPUT" && "$OVERWRITE" != "true" ]]; then
  echo "Error: output already exists: $OUTPUT" >&2
  echo "Use --overwrite to replace it." >&2
  exit 1
fi

require_binary ffmpeg

mkdir -p "$(dirname "$OUTPUT")"

overwrite_flag="-n"
if [[ "$OVERWRITE" == "true" ]]; then
  overwrite_flag="-y"
fi

echo "Transcoding:"
echo "  input:  $INPUT"
echo "  output: $OUTPUT"
echo "  crf:    $CRF"
echo "  preset: $PRESET"
echo "  audio:  $AUDIO_BITRATE"

ffmpeg \
  "$overwrite_flag" \
  -i "$INPUT" \
  -map 0:v:0 -map 0:a? \
  -c:v libx264 \
  -preset "$PRESET" \
  -crf "$CRF" \
  -pix_fmt yuv420p \
  -vf "scale=-2:min(720\,ih)" \
  -c:a aac \
  -b:a "$AUDIO_BITRATE" \
  -ac 2 \
  -ar 48000 \
  -movflags +faststart \
  "$OUTPUT"

echo "Done: $OUTPUT"
