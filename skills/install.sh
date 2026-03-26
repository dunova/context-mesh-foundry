#!/usr/bin/env bash
set -euo pipefail

# Install ContextGO skills to ~/.claude/skills/
# Usage: bash skills/install.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIR
readonly TARGET_DIR="${HOME}/.claude/skills"

echo "Installing ContextGO skills to ${TARGET_DIR}/ ..."

for skill_dir in "${SCRIPT_DIR}"/contextgo-*/; do
    [ -d "${skill_dir}" ] || continue
    skill_name="$(basename "${skill_dir}")"
    target="${TARGET_DIR}/${skill_name}"

    if [ -d "${target}" ]; then
        echo "  updating ${skill_name}"
        rm -rf "${target}"
    else
        echo "  installing ${skill_name}"
    fi

    mkdir -p "${target}"
    cp -r "${skill_dir}"/* "${target}/"
done

echo "Done. Installed skills:"
for skill_dir in "${TARGET_DIR}"/contextgo-*/; do
    [ -d "${skill_dir}" ] || continue
    name="$(basename "${skill_dir}")"
    version=$(grep -m1 'version:' "${skill_dir}/SKILL.md" 2>/dev/null | sed 's/.*"\(.*\)".*/\1/' || echo "?")
    echo "  ${name} v${version}"
done

echo ""
echo "Verify: contextgo health"
echo "Try:    /contextgo-gsd in Claude Code"
