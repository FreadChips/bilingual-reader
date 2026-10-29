#!/bin/sh
# Install the bilingual-reader skill on macOS or Linux for Claude Code and Codex.
#
#   ./install.sh             link the skill into ~/.claude/skills and ~/.codex/skills,
#                            and create .venv here with the Python packages (about 100 MB)
#   ./install.sh --no-venv   only link the skill
#
# Run it again after moving this folder. The links are symbolic links, so the agents always use this
# copy: update it with `git pull`.
set -e
repo=$(cd "$(dirname "$0")" && pwd)

for agent in claude codex; do
    dir="$HOME/.$agent/skills"
    mkdir -p "$dir"
    link="$dir/bilingual-reader"
    if [ -L "$link" ]; then
        rm "$link"
    elif [ -e "$link" ]; then
        echo "warning: $link exists and is not a link; left alone"
        continue
    fi
    ln -s "$repo" "$link"
    echo "linked $link -> $repo"
done

if [ "$1" != "--no-venv" ]; then
    command -v python3 >/dev/null 2>&1 || { echo "Python 3.10 or later is needed"; exit 1; }
    [ -x "$repo/.venv/bin/python" ] || python3 -m venv "$repo/.venv"
    "$repo/.venv/bin/python" -m pip install --upgrade pip >/dev/null
    "$repo/.venv/bin/python" -m pip install -r "$repo/requirements.txt"
    "$repo/.venv/bin/python" -I -c "import pymupdf.layout, yaml; print('python packages ok')"
    echo "Python environment: $repo/.venv/bin/python"
fi
echo "Done. In Claude Code or Codex, open a folder with your PDF, EPUB or HTML filing and ask for a bilingual study edition."
