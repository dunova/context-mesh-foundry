#!/usr/bin/env python3
"""ContextGO AutoResearch baseline/evaluation runner."""

from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import subprocess
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_ROOT = REPO_ROOT / "artifacts" / "autoresearch"
LOG_PATH = ARTIFACT_ROOT / "contextgo_autoresearch.tsv"
STATE_PATH = ARTIFACT_ROOT / "contextgo_autoresearch_latest.json"
DEFAULT_MAX_ROUNDS = 100
DEFAULT_QUERY = "NotebookLM"


def run_cmd(args: list[str], timeout: int = 180) -> tuple[int, str, str]:
    proc = subprocess.run(
        args,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return proc.returncode, proc.stdout or "", proc.stderr or ""


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


def _smoke_eval() -> tuple[int, dict, int]:
    for _ in range(2):
        rc, out, err = run_cmd(
            [sys.executable, str(REPO_ROOT / "scripts" / "context_cli.py"), "smoke"]
        )
        payload = _json_from_text(out or err)
        summary = payload.get("summary") or {}
        if rc == 0 and summary.get("status") == "pass":
            return rc, payload, len((out or err).encode("utf-8"))
    return rc, payload, len((out or err).encode("utf-8"))


def evaluate(query: str) -> dict:
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

    native_bytes = rust["bytes"] + go["bytes"]
    token_efficiency = 100
    if smoke_bytes > 2000:
        token_efficiency -= min(40, (smoke_bytes - 2000) // 100)
    if native_bytes > 3500:
        token_efficiency -= min(40, (native_bytes - 3500) // 150)
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
            "smoke_ok": smoke_rc == 0 and (smoke_payload.get("summary") or {}).get("status") == "pass",
            "search_ok": search_rc == 0,
            "search_bytes": len((search_out or search_err).encode("utf-8")),
            "smoke_bytes": smoke_bytes,
            "native_total_bytes": native_bytes,
            "rust": rust,
            "go": go,
        },
    }


def append_log(round_no: int, payload: dict, decision: str, note: str) -> None:
    ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
    if not LOG_PATH.exists():
        LOG_PATH.write_text(
            "round\ttimestamp\tstability\trecall\ttoken_efficiency\ttotal_score\tdecision\tnote\n",
            encoding="utf-8",
        )
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
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(row + "\n")
    STATE_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="ContextGO AutoResearch runner")
    parser.add_argument("--round", type=int, default=1, help="当前轮次编号")
    parser.add_argument("--max-rounds", type=int, default=DEFAULT_MAX_ROUNDS, help="总轮次目标")
    parser.add_argument("--query", default=DEFAULT_QUERY, help="召回质量评估查询")
    parser.add_argument("--note", default="baseline", help="本轮说明")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = evaluate(args.query)
    payload["task_name"] = "ContextGO AutoResearch"
    payload["target_score"] = 95
    payload["max_rounds"] = args.max_rounds
    payload["round"] = args.round
    decision = "KEEP" if payload["total_score"] >= 80 else "ITERATE"
    append_log(args.round, payload, decision, args.note)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
