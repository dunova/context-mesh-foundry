#!/usr/bin/env python3
"""ContextGO AutoResearch baseline/evaluation runner."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_ROOT = REPO_ROOT / "artifacts" / "autoresearch"
LOG_PATH = ARTIFACT_ROOT / "contextgo_autoresearch.tsv"
STATE_PATH = ARTIFACT_ROOT / "contextgo_autoresearch_latest.json"
METRICS_PATH = ARTIFACT_ROOT / "contextgo_autoresearch_metrics.json"
BEST_PATH = ARTIFACT_ROOT / "contextgo_autoresearch_best.json"
DEFAULT_MAX_ROUNDS = 100
DEFAULT_QUERY = "NotebookLM"
MAX_METRIC_HISTORY = 20


def run_cmd(args: list[str], timeout: int = 180) -> tuple[int, str, str]:
    """Run a subprocess and return (returncode, stdout, stderr)."""
    proc = subprocess.run(
        args,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def current_git_commit() -> str:
    """Return the short HEAD git commit hash, or empty string on failure."""
    rc, out, _ = run_cmd(["git", "rev-parse", "--short", "HEAD"], timeout=30)
    return out.strip() if rc == 0 and out.strip() else ""


def _json_from_text(text: str) -> dict:
    return json.loads((text or "").strip() or "{}")


def _native_eval(backend: str, query: str) -> dict:
    rc, out, err = run_cmd(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "context_cli.py"),
            "native-scan",
            "--backend",
            backend,
            "--threads",
            "4",
            "--query",
            query,
            "--limit",
            "3",
            "--json",
        ],
        timeout=180,
    )
    payload = _json_from_text(out or err)
    matches = payload.get("matches") or []
    return {
        "backend": backend,
        "rc": rc,
        "match_count": len(matches),
        "bytes": len((out or err).encode("utf-8")),
        "ok": rc == 0 and bool(matches),
    }


def _native_text_eval(backend: str, query: str) -> dict:
    rc, out, err = run_cmd(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "context_cli.py"),
            "native-scan",
            "--backend",
            backend,
            "--threads",
            "4",
            "--query",
            query,
            "--limit",
            "3",
        ],
        timeout=180,
    )
    return {
        "backend": backend,
        "rc": rc,
        "bytes": len((out or err).encode("utf-8")),
        "ok": rc == 0,
    }


def _smoke_eval() -> tuple[int, dict, int]:
    for _ in range(2):
        rc, out, err = run_cmd([sys.executable, str(REPO_ROOT / "scripts" / "context_cli.py"), "smoke"])
        payload = _json_from_text(out or err)
        summary = payload.get("summary") or {}
        if rc == 0 and summary.get("status") == "pass":
            return rc, payload, len((out or err).encode("utf-8"))
    return rc, payload, len((out or err).encode("utf-8"))


def evaluate(query: str) -> dict:
    """Run all evaluation probes and return a scored metrics payload."""
    health_rc, health_out, health_err = run_cmd(
        [sys.executable, str(REPO_ROOT / "scripts" / "context_cli.py"), "health"]
    )
    smoke_rc, smoke_payload, smoke_bytes = _smoke_eval()
    search_rc, search_out, search_err = run_cmd(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "context_cli.py"),
            "search",
            query,
            "--type",
            "all",
            "--limit",
            "3",
            "--literal",
        ]
    )
    rust = _native_eval("rust", query)
    go = _native_eval("go", query)
    rust_text = _native_text_eval("rust", query)
    go_text = _native_text_eval("go", query)

    health_payload = _json_from_text(health_out or health_err)
    stability = 0
    if health_rc == 0 and health_payload.get("all_ok"):
        stability += 50
    if smoke_rc == 0 and (smoke_payload.get("summary") or {}).get("status") == "pass":
        stability += 50

    recall = 0
    if search_rc == 0:
        recall += 40
    if rust["ok"]:
        recall += 30
    if go["ok"]:
        recall += 30

    health_bytes = len((health_out or health_err).encode("utf-8"))
    native_bytes = rust["bytes"] + go["bytes"]
    native_text_bytes = rust_text["bytes"] + go_text["bytes"]
    token_efficiency = 100
    if health_bytes > 600:
        token_efficiency -= min(20, (health_bytes - 600) // 50)
    if smoke_bytes > 2000:
        token_efficiency -= min(40, (smoke_bytes - 2000) // 100)
    if native_bytes > 3500:
        token_efficiency -= min(40, (native_bytes - 3500) // 150)
    if native_text_bytes > 600:
        token_efficiency -= min(10, (native_text_bytes - 600) // 40)
    token_efficiency = max(0, token_efficiency)

    total_score = round(stability * 0.45 + recall * 0.35 + token_efficiency * 0.20, 2)
    return {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "query": query,
        "dimensions": {
            "stability": stability,
            "recall": recall,
            "token_efficiency": token_efficiency,
        },
        "total_score": total_score,
        "signals": {
            "health_ok": health_rc == 0 and bool(health_payload.get("all_ok")),
            "health_bytes": health_bytes,
            "smoke_ok": smoke_rc == 0 and (smoke_payload.get("summary") or {}).get("status") == "pass",
            "search_ok": search_rc == 0,
            "search_bytes": len((search_out or search_err).encode("utf-8")),
            "smoke_bytes": smoke_bytes,
            "native_total_bytes": native_bytes,
            "native_text_bytes": native_text_bytes,
            "rust": rust,
            "go": go,
            "rust_text": rust_text,
            "go_text": go_text,
        },
    }


def append_log(round_no: int, payload: dict, decision: str, note: str) -> None:
    """Append a round result to the TSV log and update JSON state/metrics/best files."""
    ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
    row = "\t".join(
        [
            f"R{round_no:03d}",
            payload["timestamp"],
            str(payload["dimensions"]["stability"]),
            str(payload["dimensions"]["recall"]),
            str(payload["dimensions"]["token_efficiency"]),
            str(payload["total_score"]),
            decision,
            note,
        ]
    )
    header = "round\ttimestamp\tstability\trecall\ttoken_efficiency\ttotal_score\tdecision\tnote"
    lines = [header]
    if LOG_PATH.exists():
        existing = [line.rstrip("\n") for line in LOG_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]
        if existing:
            lines = [existing[0]]
            target_prefix = f"R{round_no:03d}\t"
            for existing_line in existing[1:]:
                if existing_line.startswith(target_prefix):
                    continue
                lines.append(existing_line)
    lines.append(row)
    LOG_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    STATE_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    metrics = []
    signals = payload.get("signals") or {}
    for line in lines[1:]:
        round_id = line.split("\t", 1)[0]
        if round_id != f"R{round_no:03d}":
            continue
        metrics.append(
            {
                "round": payload.get("round", round_no),
                "timestamp": payload["timestamp"],
                "git_commit": payload.get("git_commit", ""),
                "note": payload.get("note", note),
                "total_score": payload["total_score"],
                "stability": payload["dimensions"]["stability"],
                "recall": payload["dimensions"]["recall"],
                "token_efficiency": payload["dimensions"]["token_efficiency"],
                "health_bytes": signals.get("health_bytes"),
                "search_bytes": signals.get("search_bytes"),
                "smoke_bytes": signals.get("smoke_bytes"),
                "native_total_bytes": signals.get("native_total_bytes"),
                "native_text_bytes": signals.get("native_text_bytes"),
            }
        )
    existing_metrics = []
    if METRICS_PATH.exists():
        try:
            existing_metrics = json.loads(METRICS_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            existing_metrics = []
    current_round = payload.get("round", round_no)
    existing_metrics = [
        item for item in existing_metrics if item.get("round") != current_round and item.get("health_bytes") is not None
    ]
    existing_metrics.extend(metrics)
    existing_metrics.sort(key=lambda item: item.get("round", 0))
    if len(existing_metrics) > MAX_METRIC_HISTORY:
        existing_metrics = existing_metrics[-MAX_METRIC_HISTORY:]
    METRICS_PATH.write_text(json.dumps(existing_metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    if existing_metrics:

        def _metric_value(item: dict, key: str, default: int) -> int:
            value = item.get(key)
            return default if value is None else int(value)

        best = max(
            existing_metrics,
            key=lambda item: (
                item.get("total_score", 0),
                item.get("token_efficiency", 0),
                item.get("round", 0),
                -_metric_value(item, "health_bytes", 10**9),
                -_metric_value(item, "search_bytes", 10**9),
                -_metric_value(item, "smoke_bytes", 10**9),
                -_metric_value(item, "native_total_bytes", 10**9),
                -_metric_value(item, "native_text_bytes", 10**9),
            ),
        )
        best_payload = dict(best)
        best_payload["generated_at"] = datetime.now().isoformat(timespec="seconds")
        best_payload["target_score"] = payload.get("target_score")
        BEST_PATH.write_text(json.dumps(best_payload, ensure_ascii=False, indent=2), encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    """Build and return the CLI argument parser for the AutoResearch runner."""
    parser = argparse.ArgumentParser(description="ContextGO AutoResearch runner")
    parser.add_argument("--round", type=int, default=1, help="current round number")
    parser.add_argument("--max-rounds", type=int, default=DEFAULT_MAX_ROUNDS, help="total number of rounds")
    parser.add_argument("--query", default=DEFAULT_QUERY, help="recall quality evaluation query")
    parser.add_argument("--note", default="baseline", help="description for this round")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Evaluate the ContextGO runtime for one round and print results as JSON."""
    args = build_parser().parse_args(argv)
    payload = evaluate(args.query)
    payload["task_name"] = "ContextGO AutoResearch"
    payload["target_score"] = 95
    payload["max_rounds"] = args.max_rounds
    payload["round"] = args.round
    payload["note"] = args.note
    payload["git_commit"] = current_git_commit()
    decision = "KEEP" if payload["total_score"] >= 80 else "ITERATE"
    append_log(args.round, payload, decision, args.note)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
