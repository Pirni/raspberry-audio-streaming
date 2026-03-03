#!/bin/sh

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
PROJECT_ROOT="$(realpath "$SCRIPT_DIR/..")"

# Hardcoded list of template files
FILES="
radio-station-controller.example.service
radio-station-stream.example.service
radio-station-web.example.service
"

for file in $FILES; do
  src="$SCRIPT_DIR/$file"
  if [ ! -f "$src" ]; then
    echo "Skipping: $src (not found)"
    continue
  fi

  out="$SCRIPT_DIR/${file%.example.service}.service"
  sed "s|FOLDER_NAME|$PROJECT_ROOT|g" "$src" > "$out"
  echo "Generated: $out"
done
