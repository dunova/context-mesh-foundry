#!/usr/bin/env bash
# Install ContextGO skills to ~/.claude/skills/
# 将 ContextGO skills 安装到 ~/.claude/skills/
#
# Usage: bash skills/install.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIR
readonly TARGET_DIR="${HOME}/.claude/skills"

# Locate skills
shopt -s nullglob
skill_dirs=("${SCRIPT_DIR}"/contextgo-*/)
shopt -u nullglob

if [ ${#skill_dirs[@]} -eq 0 ]; then
    echo "ERROR: no skills found under ${SCRIPT_DIR}/contextgo-*/" >&2
    exit 1
fi

# Create target directory if it doesn't exist
mkdir -p "${TARGET_DIR}"

echo "Installing ContextGO skills to ${TARGET_DIR}/ ..."

for skill_dir in "${skill_dirs[@]}"; do
    [ -d "${skill_dir}" ] || continue
    skill_name="$(basename "${skill_dir}")"
    target="${TARGET_DIR}/${skill_name}"

    # Verify the skill has a SKILL.md
    if [ ! -f "${skill_dir}/SKILL.md" ]; then
        echo "  SKIP ${skill_name} (no SKILL.md found)" >&2
        continue
    fi

    if [ -d "${target}" ]; then
        echo "  updating  ${skill_name}"
        rm -rf "${target}"
    else
        echo "  installing ${skill_name}"
    fi

    mkdir -p "${target}"
    cp -r "${skill_dir}"* "${target}/"
done

echo ""
echo "Installed skills:"
for skill_dir in "${TARGET_DIR}"/contextgo-*/; do
    [ -d "${skill_dir}" ] || continue
    name="$(basename "${skill_dir}")"
    version="$(grep -m1 '^version:' "${skill_dir}/SKILL.md" 2>/dev/null \
        | sed 's/version:[[:space:]]*"\?\([^"]*\)"\?.*/\1/' \
        || echo "?")"
    echo "  ${name} v${version}"
done

echo ""
echo "Verify : contextgo health"
echo "Try    : /contextgo-gsd in Claude Code"
