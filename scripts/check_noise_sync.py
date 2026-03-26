#!/usr/bin/env python3
"""Verify that Rust and Go noise marker arrays are in sync with config/noise_markers.json.

Usage:
    python3 scripts/check_noise_sync.py

Exits 0 when all backends match the config file, non-zero when drift is detected.
This script can be added to CI to prevent accidental divergence between the
JSON source-of-truth and the compiled Rust/Go constants.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
CONFIG_PATH = REPO_ROOT / "config" / "noise_markers.json"
RUST_PATH = REPO_ROOT / "native" / "session_scan" / "src" / "main.rs"
GO_PATH = REPO_ROOT / "native" / "session_scan_go" / "scanner.go"


def load_config() -> dict:
    """Load the canonical noise marker config."""
    if not CONFIG_PATH.exists():
        print(f"ERROR: config file not found: {CONFIG_PATH}", file=sys.stderr)
        sys.exit(2)
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f)
    except json.JSONDecodeError as exc:
        print(f"ERROR: invalid JSON in {CONFIG_PATH}: {exc}", file=sys.stderr)
        sys.exit(2)


def _extract_string_array(source: str, start_pattern: str) -> list[str]:
    """Extract a list of quoted strings from a source block starting at start_pattern.

    The start_pattern must end with the opening bracket character ([ or {).
    Scans forward until the matching closing bracket, collecting all
    double-quoted string literals it encounters.
    """
    idx = source.find(start_pattern)
    if idx < 0:
        return []
    # The opening bracket is the last character of start_pattern.
    # Do NOT search for the next "[" because that may land inside the pattern
    # on a different bracket (e.g. "[&str]" in a Rust type annotation).
    block_start = idx + len(start_pattern) - 1
    if block_start >= len(source) or source[block_start] not in ("[", "{"):
        return []
    open_ch = source[block_start]
    close_ch = "]" if open_ch == "[" else "}"
    depth = 0
    pos = block_start
    end = len(source)
    while pos < end:
        ch = source[pos]
        if ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                block = source[block_start : pos + 1]
                # Match all double-quoted string literals, including escape sequences.
                # We use a simple pattern that handles \" inside strings.
                return re.findall(r'"((?:[^"\\]|\\.)*)"', block)
        pos += 1
    return []


def extract_rust_markers(source: str) -> list[str]:
    """Parse NOISE_MARKERS from Rust source."""
    return _extract_string_array(source, "const NOISE_MARKERS: &[&str] = &[")


def extract_rust_prefixes(source: str) -> list[str]:
    """Parse NOISE_PREFIXES from Rust source."""
    # NOISE_PREFIXES is a single-line array literal
    match = re.search(r"const NOISE_PREFIXES: &\[&str\] = &\[([^\]]*)\]", source)
    if not match:
        return []
    inner = match.group(1)
    return re.findall(r'"((?:[^"\\]|\\.)*)"', inner)


def extract_go_markers(source: str) -> list[str]:
    """Parse DefaultNoiseMarkers from Go source."""
    return _extract_string_array(source, "var DefaultNoiseMarkers = []string{")


def extract_go_prefixes(source: str) -> list[str]:
    """Parse DefaultNoisePrefixes from Go source."""
    return _extract_string_array(source, "var DefaultNoisePrefixes = []string{")


def _decode_string_escapes(s: str) -> str:
    """Decode common Rust/Go string escape sequences to their logical characters.

    Handles the subset of escape sequences likely to appear in noise marker strings:
    \\n, \\t, \\r, \\\\, and \\".
    """
    return s.replace('\\"', '"').replace("\\n", "\n").replace("\\t", "\t").replace("\\r", "\r").replace("\\\\", "\\")


def _normalize_extracted(strings: list[str]) -> list[str]:
    """Decode escape sequences in extracted source strings for comparison."""
    return [_decode_string_escapes(s) for s in strings]


def compare(label: str, config_list: list[str], extracted: list[str]) -> list[str]:
    """Return a list of human-readable drift messages, empty when in sync."""
    config_set = set(config_list)
    extracted_set = set(_normalize_extracted(extracted))
    messages: list[str] = []
    missing = config_set - extracted_set
    extra = extracted_set - config_set
    if missing:
        for m in sorted(missing):
            messages.append(f"  {label}: in config but MISSING from source: {m!r}")
    if extra:
        for m in sorted(extra):
            messages.append(f"  {label}: in source but NOT in config: {m!r}")
    return messages


def main() -> int:
    """Check Rust and Go noise markers are in sync with the JSON config."""
    config = load_config()
    config_native = config.get("native_noise_markers", [])
    config_prefixes = config.get("noise_prefixes", [])

    rust_source = RUST_PATH.read_text(encoding="utf-8")
    go_source = GO_PATH.read_text(encoding="utf-8")

    rust_markers = extract_rust_markers(rust_source)
    rust_prefixes = extract_rust_prefixes(rust_source)
    go_markers = extract_go_markers(go_source)
    go_prefixes = extract_go_prefixes(go_source)

    all_messages: list[str] = []
    all_messages.extend(compare("Rust NOISE_MARKERS vs native_noise_markers", config_native, rust_markers))
    all_messages.extend(compare("Rust NOISE_PREFIXES vs noise_prefixes", config_prefixes, rust_prefixes))
    all_messages.extend(compare("Go DefaultNoiseMarkers vs native_noise_markers", config_native, go_markers))
    all_messages.extend(compare("Go DefaultNoisePrefixes vs noise_prefixes", config_prefixes, go_prefixes))

    if all_messages:
        print("NOISE MARKER DRIFT DETECTED — backends are out of sync with config/noise_markers.json:")
        for msg in all_messages:
            print(msg)
        print()
        print(
            "To fix: update config/noise_markers.json then propagate changes to "
            "native/session_scan/src/main.rs and native/session_scan_go/scanner.go."
        )
        return 1

    print("OK — Rust and Go noise markers are in sync with config/noise_markers.json.")
    print(f"  native_noise_markers : {len(config_native)} entries")
    print(f"  noise_prefixes       : {len(config_prefixes)} entries")
    return 0


if __name__ == "__main__":
    sys.exit(main())
