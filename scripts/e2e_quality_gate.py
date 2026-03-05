#!/usr/bin/env python3
"""
SCF 1.000 E2E quality gate.

Scope:
1) Yesterday sessions listing + session-id continuation.
2) Yesterday all-chat summary.
3) Broad semantic retrieval.
4) MCP health and memory save/query.
5) 20-parallel recall stress probe.
"""

from __future__ import annotations

import concurrent.futures
import datetime as dt
import importlib.util
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
ALINE_DB_PATH = Path.home() / ".aline" / "db" / "aline.db"
MCP_PATH = REPO_ROOT / "scripts" / "openviking_mcp.py"
REPORT_DIR = REPO_ROOT / "docs"


@dataclass
class CaseResult:
    name: str
    passed: bool
    detail: str
    data: dict[str, Any]
    elapsed_sec: float


def run_cmd(args: list[str], timeout: int = 20) -> tuple[int, str, str]:
    proc = subprocess.run(
        args,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def onecontext_search(query: str, search_type: str = "all", limit: int = 10, no_regex: bool = True) -> tuple[int, str, str]:
    args = ["onecontext", "search", query, "-t", search_type, "-l", str(limit)]
    if no_regex:
        args.append("--no-regex")
    return run_cmd(args, timeout=30)


def load_mcp_module(enable_semantic: bool):
    module_name = f"openviking_mcp_e2e_{int(time.time() * 1000)}"
    old_sem = os.environ.get("OPENVIKING_ENABLE_SEMANTIC_QUERY")
    os.environ["OPENVIKING_ENABLE_SEMANTIC_QUERY"] = "1" if enable_semantic else "0"
    try:
        spec = importlib.util.spec_from_file_location(module_name, MCP_PATH)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Cannot load module from {MCP_PATH}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # type: ignore[union-attr]
        return module
    finally:
        if old_sem is None:
            os.environ.pop("OPENVIKING_ENABLE_SEMANTIC_QUERY", None)
        else:
            os.environ["OPENVIKING_ENABLE_SEMANTIC_QUERY"] = old_sem


def http_post_json(url: str, payload: dict[str, Any], timeout: int = 8) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", errors="replace")
    return json.loads(body)


def get_yesterday_sessions() -> list[sqlite3.Row]:
    if not ALINE_DB_PATH.exists():
        raise FileNotFoundError(f"Aline DB not found: {ALINE_DB_PATH}")
    yesterday = (dt.date.today() - dt.timedelta(days=1)).isoformat()
    conn = sqlite3.connect(str(ALINE_DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT id, session_type, COALESCE(session_title, '') AS session_title,
                   COALESCE(session_summary, '') AS session_summary,
                   started_at, last_activity_at, COALESCE(workspace_path, '') AS workspace_path
            FROM sessions
            WHERE substr(started_at, 1, 10) = ? OR substr(last_activity_at, 1, 10) = ?
            ORDER BY last_activity_at DESC
            """,
            (yesterday, yesterday),
        ).fetchall()
        return rows
    finally:
        conn.close()


def summarize_yesterday(rows: list[sqlite3.Row]) -> dict[str, Any]:
    by_ai = Counter()
    workspaces = Counter()
    titles = []
    for r in rows:
        by_ai[r["session_type"] or "unknown"] += 1
        if r["workspace_path"]:
            workspaces[r["workspace_path"]] += 1
        if r["session_title"]:
            titles.append(r["session_title"].strip())
    top_titles = [t for t, _ in Counter(titles).most_common(8)]
    return {
        "session_total": len(rows),
        "by_ai": dict(by_ai),
        "top_workspaces": workspaces.most_common(6),
        "top_titles": top_titles,
    }


def case_onecontext_yesterday() -> CaseResult:
    t0 = time.time()
    yesterday = (dt.date.today() - dt.timedelta(days=1)).isoformat()
    code, out, err = onecontext_search(yesterday, search_type="all", limit=20, no_regex=True)
    ok = code == 0 and ("Found " in out or "ID:" in out)
    detail = f"query={yesterday}, rc={code}, out_head={out[:160].replace(chr(10), ' ')}"
    if err.strip():
        detail += f", err_head={err[:120].replace(chr(10), ' ')}"
    return CaseResult("昨天会话检索（onecontext）", ok, detail, {"query": yesterday}, time.time() - t0)


def case_session_list_and_continue() -> CaseResult:
    t0 = time.time()
    rows = get_yesterday_sessions()
    if not rows:
        return CaseResult("会话ID枚举+续聊", False, "昨天会话数=0，无法续聊测试", {}, time.time() - t0)
    target_id = rows[0]["id"]
    try:
        mcp_mod = load_mcp_module(enable_semantic=False)
        out = mcp_mod.search_onecontext_history(target_id, search_type="content", limit=8, no_regex=True)
        ok = (
            isinstance(out, str)
            and target_id in out
            and "No matching messages found" not in out
            and "No matches found after fallback chain" not in out
        )
        detail = f"sessions={len(rows)}, target_id={target_id}, out_head={out[:200].replace(chr(10), ' ')}"
        data = {"yesterday_sessions": len(rows), "target_session_id": target_id}
        return CaseResult("会话ID枚举+续聊", ok, detail, data, time.time() - t0)
    except Exception as exc:
        return CaseResult("会话ID枚举+续聊", False, f"mcp continuation query failed: {exc}", {}, time.time() - t0)


def case_yesterday_summary() -> CaseResult:
    t0 = time.time()
    rows = get_yesterday_sessions()
    if not rows:
        return CaseResult("昨天全量总结", False, "昨天无会话数据", {}, time.time() - t0)
    summary = summarize_yesterday(rows)
    ok = summary["session_total"] > 0 and len(summary["by_ai"]) > 0
    detail = (
        f"session_total={summary['session_total']}, "
        f"ai={summary['by_ai']}, top_workspace={summary['top_workspaces'][:2]}"
    )
    return CaseResult("昨天全量总结", ok, detail, summary, time.time() - t0)


def case_openviking_health() -> CaseResult:
    t0 = time.time()
    try:
        with urllib.request.urlopen("http://127.0.0.1:8090/health", timeout=3) as resp:
            code = resp.getcode()
            body = resp.read().decode("utf-8", errors="replace")
        ok = code == 200
        detail = f"http={code}, body_head={body[:80]}"
    except urllib.error.URLError as exc:
        ok = False
        detail = f"health probe failed: {exc}"
    return CaseResult("OpenViking健康探针", ok, detail, {}, time.time() - t0)


def case_semantic_search() -> CaseResult:
    t0 = time.time()
    payload = {"query": "现在策略的主要参数", "target_uri": "viking://resources", "limit": 5}
    try:
        data = http_post_json("http://127.0.0.1:8090/api/v1/search/find", payload, timeout=10)
        resources = data.get("result", {}).get("resources", [])
        ok = data.get("status") == "ok" and isinstance(resources, list) and len(resources) > 0
        detail = f"status={data.get('status')}, resources={len(resources)}"
        return CaseResult("大范围语义检索", ok, detail, {"resource_sample": resources[:2]}, time.time() - t0)
    except Exception as exc:
        return CaseResult("大范围语义检索", False, f"semantic query failed: {exc}", {}, time.time() - t0)


def case_mcp_health() -> CaseResult:
    t0 = time.time()
    try:
        mcp_mod = load_mcp_module(enable_semantic=False)
        payload = json.loads(mcp_mod.context_system_health())
        ok = bool(payload.get("all_ok"))
        detail = (
            f"all_ok={payload.get('all_ok')}, "
            f"recall_ok={payload.get('recall_lite', {}).get('ok')}, "
            f"onecontext_ok={payload.get('onecontext_compat', {}).get('ok')}"
        )
        return CaseResult("MCP健康总览", ok, detail, payload, time.time() - t0)
    except Exception as exc:
        return CaseResult("MCP健康总览", False, f"context_system_health failed: {exc}", {}, time.time() - t0)


def case_mcp_memory_save_and_query() -> CaseResult:
    t0 = time.time()
    marker = f"e2e-marker-{int(time.time())}"
    title = f"E2E Memory {marker}"
    content = f"quality gate marker: {marker}"
    try:
        mcp_mod = load_mcp_module(enable_semantic=True)
        save_ret = mcp_mod.save_conversation_memory(title=title, content=content, tags=["e2e", "qa"])
        query_ret = mcp_mod.query_viking_memory(marker, limit=3)
        ok = ("Successfully saved memory" in save_ret or "Saved to local file" in save_ret) and (
            "LOCAL MEMORY MATCHES" in query_ret or marker in query_ret or "FOUND RESOURCES" in query_ret
        )
        detail = f"save={save_ret[:90]}, query_head={query_ret[:140].replace(chr(10), ' ')}"
        return CaseResult("MCP写入并检索记忆", ok, detail, {"marker": marker}, time.time() - t0)
    except Exception as exc:
        return CaseResult("MCP写入并检索记忆", False, f"mcp save/query failed: {exc}", {}, time.time() - t0)


def case_parallel_recall_stress() -> CaseResult:
    t0 = time.time()
    yesterday = (dt.date.today() - dt.timedelta(days=1)).isoformat()
    workers = 20

    def worker(_: int) -> tuple[bool, float]:
        s = time.time()
        rc, out, _ = onecontext_search(yesterday, search_type="content", limit=3, no_regex=True)
        ok = rc == 0 and "No matching messages found" not in out
        return ok, time.time() - s

    oks = []
    latencies = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(worker, i) for i in range(workers)]
        for fut in concurrent.futures.as_completed(futs, timeout=120):
            ok, lat = fut.result()
            oks.append(ok)
            latencies.append(lat)
    success = sum(1 for x in oks if x)
    min_ok = max(18, int(workers * 0.9))
    ok = success >= min_ok
    p95 = sorted(latencies)[int(len(latencies) * 0.95) - 1] if latencies else 999
    detail = f"workers={workers}, success={success}, p95={p95:.2f}s"
    data = {"workers": workers, "success": success, "latencies_sec": latencies}
    return CaseResult("20并发检索压力测试", ok, detail, data, time.time() - t0)


def render_report(results: list[CaseResult], report_path: Path) -> None:
    passed = sum(1 for r in results if r.passed)
    failed = len(results) - passed
    ts = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "# SCF 1.000 E2E QA Report",
        "",
        f"- Generated at: {ts}",
        f"- Repo: `{REPO_ROOT}`",
        f"- Aline DB: `{ALINE_DB_PATH}`",
        f"- Pass: `{passed}` / `{len(results)}`",
        f"- Fail: `{failed}`",
        "",
        "## Result Table",
        "",
        "| Case | Status | Detail | Elapsed(s) |",
        "|---|---|---|---|",
    ]
    for r in results:
        status = "PASS" if r.passed else "FAIL"
        detail = re.sub(r"\s+", " ", r.detail).strip()
        if len(detail) > 180:
            detail = detail[:177] + "..."
        lines.append(f"| {r.name} | {status} | {detail} | {r.elapsed_sec:.2f} |")
    lines.append("")
    lines.append("## Structured Data")
    lines.append("")
    for r in results:
        lines.append(f"### {r.name}")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(r.data, ensure_ascii=False, indent=2))
        lines.append("```")
        lines.append("")

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    cases = [
        case_onecontext_yesterday,
        case_session_list_and_continue,
        case_yesterday_summary,
        case_openviking_health,
        case_semantic_search,
        case_mcp_health,
        case_mcp_memory_save_and_query,
        case_parallel_recall_stress,
    ]
    results: list[CaseResult] = []
    for fn in cases:
        result = fn()
        results.append(result)
        flag = "PASS" if result.passed else "FAIL"
        print(f"[{flag}] {result.name} - {result.detail}")

    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = REPORT_DIR / f"QA_REPORT_{ts}_1dot000.md"
    render_report(results, report_path)
    print(f"\nReport written to: {report_path}")

    failed = [r for r in results if not r.passed]
    if failed:
        print("\nFailed cases:")
        for r in failed:
            print(f"- {r.name}: {r.detail}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
