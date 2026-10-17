#!/usr/bin/env bash
# Push the committed HEAD to a Hugging Face Docker Space.
# Usage: scripts/deploy_hf.sh <hf-username> [space-name]
# Git asks for your Hugging Face username and an access token (write scope)
# as the password. The token is never stored by this script.
set -euo pipefail
USER_NAME="${1:?usage: scripts/deploy_hf.sh <hf-username> [space-name]}"
SPACE="${2:-milaan}"
ROOT="$(git rev-parse --show-toplevel)"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

git -C "$ROOT" archive HEAD | tar -x -C "$STAGE"
# The Space needs its own README front matter. Hugging Face rejects binary
# files pushed without Xet/LFS, and the demo needs none of them.
cp "$ROOT/deploy/huggingface/README.md" "$STAGE/README.md"
rm -rf "$STAGE/data" "$STAGE/docs"
find "$STAGE" -type f \( -name '*.png' -o -name '*.jpg' -o -name '*.gif' \) -delete

cd "$STAGE"
git init -q -b main
git add -A
git -c user.name="deploy" -c user.email="deploy@localhost" \
  commit -q -m "Deploy $(git -C "$ROOT" rev-parse --short HEAD)"
git push --force "https://huggingface.co/spaces/$USER_NAME/$SPACE" main
echo "Pushed. Build log: https://huggingface.co/spaces/$USER_NAME/$SPACE?logs=build"
