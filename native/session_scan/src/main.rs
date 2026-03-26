//! High-performance scanner for Codex/Claude session files.
//!
//! Walks `.json` and `.jsonl` session files in parallel using Rayon, extracts
//! structured metadata (timestamps, session IDs, text snippets), filters noise,
//! and emits either a human-readable summary or a machine-readable JSON report.
//!
//! # Usage
//!
//! ```text
//! session_scan --query "agent" --limit 20 --json
//! ```

use anyhow::{Context, Result};
use clap::Parser;
use rayon::prelude::*;
use serde_json::Value;
use shellexpand::tilde;
use std::collections::HashMap;
use std::fs::File;
use std::io::{BufRead, BufReader};
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};
use walkdir::WalkDir;

// ── sentinel strings used as lightweight error variants ──────────────────────

const SKIPPED_QUERY_MISS: &str = "query not matched";
const SKIPPED_CURRENT_WORKDIR: &str = "skip current workdir session";

// ── noise filter tables ───────────────────────────────────────────────────────

/// Substrings that mark a text fragment as noise.  Checked case-insensitively.
const NOISE_MARKERS: &[&str] = &[
    "# agents.md instructions",
    "### available skills",
    "prompt engineer and agent skill optimizer",
    "current skill name:",
    "base directory for this skill:",
    "hit-first query rules",
    "default mode is `hybrid`",
    "search past claude/codex sessions",
    "query_viking_memory",
    "onecontext search",
    "name: openviking-memory-sync",
    "name: recall",
    "use when explicit /notebooklm",
    "activates on explicit /notebooklm",
    "automate google notebooklm",
    "build_query_terms",
    "python3 scripts/context_cli.py native-scan",
    "guardian_truncated",
    "diff --git",
    "@@",
    "```bash",
    "```python",
    "launchctl list | egrep",
    "继续完成了，但这轮我做了一个重要判断",
    "实验性 native 热路径已经接上",
    "命中优先级（从高到低）",
    "首发查询用短关键词",
    "returns noisy snippets from benchmark/test/skill text",
    "native 搜索结果质量还不够好",
    "刚才那个 session / 上个终端 / 某次调研",
    "随后我又把本地安装态重新部署到",
    "通过了 `python3 scripts/context_cli.py smoke`",
    "go test ./...",
    "python3 -m benchmarks --mode both",
    "已预热",
    "样本定位",
    "不要改文件。输出",
    "只读。审查",
    "只读。定位为什么",
    "远端对齐确认",
    "未纳入本次提交",
    "已查看并收口当前子 agent",
    "状态汇总：",
    "已关闭且有有效产出",
    "我先按仓库要求做上下文预热",
    "我先做"全局一致性同步"检查",
    "主链不再是瓶颈",
    "现在真正该优化的是",
    "native 结果质量现状",
    "native 搜索结果质量",
    "no matches found in local session index.",
    "不是再融合，而是",
    "我继续的话，就沿这条质量线往下打",
    "把 rust `native-scan` 结果里的",
    "我继续直接提主链结果质量",
    "我先复跑主链",
    "再决定要不要进一步做字段级过滤",
    "现在不是"能不能跑"的问题",
    "让它质量更好，能替代旧逻辑",
    "我继续。",
    "我现在直接复跑主链",
    "我再强制重建一次索引",
    "skill.md",
    "python -m pytest",
    "benchmarks/run.py",
    "<instructions>",
    "chunk id:",
    "wall time:",
    "process exited with code",
    "original token count:",
    "\noutput:",
];

/// Line prefixes that mark a fragment as noise.  Checked case-insensitively.
const NOISE_PREFIXES: &[&str] = &["##", "```", "> ", "- [", "* ", "http", "https"];

/// Field name used when a match is found in an unparsed raw line.
const RAW_LINE_FIELD: &str = "raw_line";

// ── CLI ───────────────────────────────────────────────────────────────────────

#[derive(Parser)]
#[command(
    author,
    version,
    about = "High-performance Codex/Claude session scanner"
)]
struct Args {
    /// Root directory for Codex sessions (tilde-expanded)
    #[arg(long, default_value = "~/.codex/sessions")]
    codex_root: String,

    /// Root directory for Claude sessions (tilde-expanded)
    #[arg(long, default_value = "~/.claude/projects")]
    claude_root: String,

    /// Number of Rayon worker threads
    #[arg(long, default_value_t = 4)]
    threads: usize,

    /// Return only results whose text contains this substring
    #[arg(long, default_value = "")]
    query: String,

    /// Maximum number of results to emit
    #[arg(long, default_value_t = 20)]
    limit: usize,

    /// Emit machine-readable JSON instead of a human summary
    #[arg(long, default_value_t = false)]
    json: bool,
}

// ── core data types ───────────────────────────────────────────────────────────

/// A single file to process, annotated with its logical source label.
struct WorkItem {
    source: &'static str,
    path: PathBuf,
}

/// A text candidate extracted from a parsed JSON record.
#[derive(Clone, Debug)]
struct MatchDetail {
    field: &'static str,
    text: String,
}

/// In-memory summary produced from one session file.
struct SessionSummary {
    source: &'static str,
    path: PathBuf,
    session_id: String,
    lines: usize,
    size_bytes: u64,
    first_timestamp: Option<String>,
    last_timestamp: Option<String>,
    snippet: Option<String>,
    match_field: Option<String>,
    match_score: i32,
}

// ── JSON-serialisable output types ────────────────────────────────────────────

#[derive(serde::Serialize)]
struct SerializableSummary {
    source: String,
    path: String,
    session_id: String,
    lines: usize,
    size_bytes: u64,
    first_timestamp: Option<String>,
    last_timestamp: Option<String>,
    snippet: Option<String>,
    match_field: Option<String>,
}

#[derive(serde::Serialize)]
struct JsonSample {
    session_id: String,
    path: String,
    first_timestamp: Option<String>,
    last_timestamp: Option<String>,
    snippet: Option<String>,
    match_field: Option<String>,
}

#[derive(serde::Serialize)]
struct JsonRootAggregate {
    label: String,
    session_count: usize,
    total_lines: usize,
    total_bytes: u64,
    sample: Option<JsonSample>,
}

/// Top-level JSON output emitted when `--json` is passed.
#[derive(serde::Serialize)]
struct JsonReport {
    files_scanned: usize,
    query: String,
    duration_ms: u128,
    aggregates: Vec<JsonRootAggregate>,
    matches: Vec<SerializableSummary>,
    errors: Vec<String>,
}

// ── scanner internals ─────────────────────────────────────────────────────────

struct ScannerReport {
    total_files: usize,
    summaries: Vec<SessionSummary>,
    errors: Vec<anyhow::Error>,
    duration: Duration,
}

struct SourceRoot {
    label: &'static str,
    path: PathBuf,
}

struct Scanner {
    roots: Vec<SourceRoot>,
}

impl Scanner {
    fn from_args(args: &Args) -> Self {
        let expand = |raw: &str| -> PathBuf { PathBuf::from(tilde(raw).as_ref()) };
        let roots = vec![
            SourceRoot {
                label: "codex_session",
                path: expand(&args.codex_root),
            },
            SourceRoot {
                label: "codex_session",
                path: expand("~/.codex/archived_sessions"),
            },
            SourceRoot {
                label: "claude_session",
                path: expand(&args.claude_root),
            },
        ];
        Self { roots }
    }

    fn collect_work_items(&self) -> Vec<WorkItem> {
        self.roots
            .iter()
            .filter(|root| {
                if root.path.exists() {
                    true
                } else {
                    eprintln!(
                        "Directory not found, skipping: {} -> {}",
                        root.label,
                        root.path.display()
                    );
                    false
                }
            })
            .flat_map(|root| {
                WalkDir::new(&root.path)
                    .into_iter()
                    .filter_map(|entry| match entry {
                        Ok(entry)
                            if entry.file_type().is_file()
                                && is_valid_extension(entry.path()) =>
                        {
                            Some(WorkItem {
                                source: root.label,
                                path: entry.into_path(),
                            })
                        }
                        _ => None,
                    })
                    .collect::<Vec<_>>()
            })
            .collect()
    }

    /// Returns the unique set of source labels, preserving encounter order.
    fn root_labels(&self) -> Vec<&'static str> {
        let mut labels: Vec<&'static str> = Vec::new();
        for root in &self.roots {
            if !labels.contains(&root.label) {
                labels.push(root.label);
            }
        }
        labels
    }

    fn scan(
        &self,
        work_items: &[WorkItem],
        query: &str,
    ) -> (Vec<SessionSummary>, Vec<anyhow::Error>) {
        let results: Vec<_> = work_items
            .par_iter()
            .map(|item| process_file(item, query))
            .collect();

        let mut summaries = Vec::with_capacity(results.len());
        let mut errors = Vec::new();
        for result in results {
            match result {
                Ok(summary) => summaries.push(summary),
                Err(err) if should_report_error(&err) => errors.push(err),
                Err(_) => {}
            }
        }
        (summaries, errors)
    }
}

impl ScannerReport {
    fn write_stdout(&self, scanner: &Scanner) {
        println!(
            "Scan complete: {} files in {:.2?}.",
            self.total_files, self.duration
        );
        let aggregates = summarize_by_source(&self.summaries);
        for label in scanner.root_labels() {
            if let Some(aggregate) = aggregates.get(label) {
                println!(
                    "  {} -> {} sessions, {} lines, {} bytes",
                    aggregate.label,
                    aggregate.session_count,
                    aggregate.total_lines,
                    aggregate.total_bytes
                );
                if let Some(sample) = aggregate.sample {
                    println!(
                        "    sample: {} | {} -> {} | {} | [{}]",
                        sample.session_id,
                        sample.first_timestamp.as_deref().unwrap_or("?"),
                        sample.last_timestamp.as_deref().unwrap_or("?"),
                        sample.path.display(),
                        sample.match_field.as_deref().unwrap_or("unknown")
                    );
                }
            }
        }
        if !self.errors.is_empty() {
            println!(
                "  {} parse error(s) encountered (see stderr for details).",
                self.errors.len()
            );
            for err in self.errors.iter().take(5) {
                println!("    - {err}");
            }
        }
    }

    fn json_payload(&self, scanner: &Scanner, query: &str) -> JsonReport {
        let aggregates = summarize_by_source(&self.summaries);
        let roots = scanner
            .root_labels()
            .into_iter()
            .map(|label| {
                aggregates.get(label).map_or_else(
                    || JsonRootAggregate {
                        label: label.to_string(),
                        session_count: 0,
                        total_lines: 0,
                        total_bytes: 0,
                        sample: None,
                    },
                    |aggregate| JsonRootAggregate {
                        label: aggregate.label.to_string(),
                        session_count: aggregate.session_count,
                        total_lines: aggregate.total_lines,
                        total_bytes: aggregate.total_bytes,
                        sample: aggregate.sample.map(|summary| JsonSample {
                            session_id: summary.session_id.clone(),
                            path: summary.path.display().to_string(),
                            first_timestamp: summary.first_timestamp.clone(),
                            last_timestamp: summary.last_timestamp.clone(),
                            snippet: summary.snippet.clone(),
                            match_field: summary.match_field.clone(),
                        }),
                    },
                )
            })
            .collect();

        JsonReport {
            files_scanned: self.total_files,
            query: query.to_string(),
            duration_ms: self.duration.as_millis(),
            aggregates: roots,
            matches: self
                .summaries
                .iter()
                .map(|item| SerializableSummary {
                    source: item.source.to_string(),
                    path: item.path.display().to_string(),
                    session_id: item.session_id.clone(),
                    lines: item.lines,
                    size_bytes: item.size_bytes,
                    first_timestamp: item.first_timestamp.clone(),
                    last_timestamp: item.last_timestamp.clone(),
                    snippet: item.snippet.clone(),
                    match_field: item.match_field.clone(),
                })
                .collect(),
            errors: self.errors.iter().map(|err| err.to_string()).collect(),
        }
    }
}

// ── source aggregation ────────────────────────────────────────────────────────

struct SourceAggregate<'a> {
    label: &'static str,
    session_count: usize,
    total_lines: usize,
    total_bytes: u64,
    sample: Option<&'a SessionSummary>,
}

fn summarize_by_source<'a>(
    summaries: &'a [SessionSummary],
) -> HashMap<&'static str, SourceAggregate<'a>> {
    let mut map: HashMap<&'static str, SourceAggregate<'a>> = HashMap::new();
    for summary in summaries {
        let entry = map
            .entry(summary.source)
            .or_insert_with(|| SourceAggregate {
                label: summary.source,
                session_count: 0,
                total_lines: 0,
                total_bytes: 0,
                sample: None,
            });
        entry.session_count += 1;
        entry.total_lines += summary.lines;
        entry.total_bytes += summary.size_bytes;
        if entry.sample.is_none() {
            entry.sample = Some(summary);
        }
    }
    map
}

// ── entry point ───────────────────────────────────────────────────────────────

fn main() -> Result<()> {
    let args = Args::parse();
    rayon::ThreadPoolBuilder::new()
        .num_threads(args.threads)
        .build_global()
        .context("Failed to configure Rayon thread pool")?;

    let start = Instant::now();
    let scanner = Scanner::from_args(&args);
    let work_items = scanner.collect_work_items();
    let total_files = work_items.len();

    let (mut summaries, errors) = scanner.scan(&work_items, &args.query);
    summaries.sort_by(|left, right| {
        right
            .match_score
            .cmp(&left.match_score)
            .then_with(|| right.last_timestamp.cmp(&left.last_timestamp))
            .then_with(|| right.first_timestamp.cmp(&left.first_timestamp))
            .then_with(|| left.path.cmp(&right.path))
    });
    if args.limit > 0 && summaries.len() > args.limit {
        summaries.truncate(args.limit);
    }

    let duration = start.elapsed();
    let report = ScannerReport {
        total_files,
        summaries,
        errors,
        duration,
    };

    if args.json {
        let payload = report.json_payload(&scanner, &args.query);
        println!("{}", serde_json::to_string_pretty(&payload)?);
        return Ok(());
    }
    report.write_stdout(&scanner);
    Ok(())
}

// ── file processing ───────────────────────────────────────────────────────────

fn process_file(item: &WorkItem, query: &str) -> Result<SessionSummary> {
    let file = File::open(&item.path)
        .with_context(|| format!("Cannot open session file {}", item.path.display()))?;
    let metadata = file
        .metadata()
        .with_context(|| format!("Cannot read metadata for {}", item.path.display()))?;
    let modified_epoch = metadata
        .modified()
        .ok()
        .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
        .map(|d| d.as_secs())
        .unwrap_or(0);

    let mut session_id = item
        .path
        .file_stem()
        .and_then(|s| s.to_str())
        .unwrap_or("session")
        .to_string();

    let mut first_timestamp: Option<String> = None;
    let mut last_timestamp: Option<String> = None;
    let mut session_cwd: Option<String> = None;
    let mut lines = 0usize;
    let query_lower = query.trim().to_lowercase();
    let mut matched = query_lower.is_empty();
    let mut best_match: Option<(i32, String, String)> = None;
    let current_workdir = active_workdir();

    let reader = BufReader::new(file);
    for raw in reader.lines() {
        let line = match raw {
            Ok(l) => l,
            Err(err) => {
                eprintln!("Read error in {}: {err}", item.path.display());
                continue;
            }
        };
        if line.trim().is_empty() {
            continue;
        }
        lines += 1;

        if let Ok(json) = serde_json::from_str::<Value>(&line) {
            if should_skip_record_type(&json) {
                continue;
            }
            if let Some(id) = extract_session_id(&json) {
                session_id = id;
            }
            if let Some(cwd) = extract_cwd(&json) {
                if session_cwd.is_none() {
                    session_cwd = Some(cwd.clone());
                }
                if !query_lower.is_empty() {
                    if let Some(workdir) = current_workdir.as_deref() {
                        let norm = std::path::Path::new(&cwd)
                            .canonicalize()
                            .map(|p| p.to_string_lossy().into_owned())
                            .unwrap_or(cwd);
                        if norm == workdir {
                            anyhow::bail!(SKIPPED_CURRENT_WORKDIR);
                        }
                    }
                }
            }
            if let Some(ts) = extract_timestamp(&json) {
                if first_timestamp.is_none() {
                    first_timestamp = Some(ts.clone());
                }
                last_timestamp = Some(ts);
            }
            if !query_lower.is_empty() {
                for detail in extract_text_candidates(&json) {
                    if should_skip_meta_text(
                        current_workdir.as_deref(),
                        session_cwd.as_deref(),
                        &detail.text,
                    ) {
                        continue;
                    }
                    if let Some(candidate) = matched_snippet(&detail.text, &query_lower, 180) {
                        if should_skip_meta_candidate(&candidate)
                            || is_noise_line(&candidate.to_lowercase())
                        {
                            continue;
                        }
                        matched = true;
                        let score = candidate_score(detail.field, &detail.text, &query_lower);
                        let replace = best_match
                            .as_ref()
                            .map_or(true, |(best_score, _, _)| score > *best_score);
                        if replace {
                            best_match = Some((score, candidate, detail.field.to_string()));
                        }
                    }
                }
            }
        } else if !query_lower.is_empty() {
            if let Some(candidate) = matched_snippet(&line, &query_lower, 180) {
                if !should_skip_meta_candidate(&candidate)
                    && !is_noise_line(&candidate.to_lowercase())
                {
                    matched = true;
                    let score = candidate_score(RAW_LINE_FIELD, &line, &query_lower);
                    let replace = best_match
                        .as_ref()
                        .map_or(true, |(best_score, _, _)| score > *best_score);
                    if replace {
                        best_match = Some((score, candidate, RAW_LINE_FIELD.to_string()));
                    }
                }
            }
        }
    }

    if !matched {
        anyhow::bail!(SKIPPED_QUERY_MISS);
    }

    Ok(SessionSummary {
        source: item.source,
        path: item.path.clone(),
        session_id,
        lines,
        size_bytes: metadata.len(),
        first_timestamp,
        last_timestamp,
        snippet: best_match.as_ref().map(|(_, snip, _)| snip.clone()),
        match_field: best_match.as_ref().map(|(_, _, field)| field.clone()),
        match_score: best_match.as_ref().map(|(score, _, _)| *score).unwrap_or(0),
    })
}

// ── snippet helpers ───────────────────────────────────────────────────────────

/// Returns a window of `limit` characters centred on the first match of
/// `query_lower` inside `text`, or `None` when no match exists.
fn matched_snippet(text: &str, query_lower: &str, limit: usize) -> Option<String> {
    let trimmed = text.trim();
    if trimmed.is_empty() || query_lower.is_empty() {
        return None;
    }
    let lower = trimmed.to_lowercase();
    let idx = lower.find(query_lower)?;
    Some(clip_snippet(trimmed, idx, query_lower.len(), limit))
}

/// Clips `text` to at most `limit` characters, keeping the matched region
/// centred in the window.  Operates on Unicode scalar values (chars), not
/// bytes, to avoid splitting multi-byte sequences.
fn clip_snippet(text: &str, index: usize, query_len: usize, limit: usize) -> String {
    let total_chars = text.chars().count();
    if limit == 0 || total_chars <= limit {
        return text.to_string();
    }
    let start_chars = text[..index].chars().count();
    let query_chars = text[index..index + query_len].chars().count();
    let radius = limit / 2;
    let start = start_chars.saturating_sub(radius);
    let end = (start_chars + query_chars + radius).min(total_chars);
    text.chars()
        .skip(start)
        .take(end.saturating_sub(start))
        .collect()
}

// ── noise filtering ───────────────────────────────────────────────────────────

/// Returns `true` when `line` (already lower-cased) matches any noise marker
/// or heuristic pattern.
fn is_noise_line(line: &str) -> bool {
    let trimmed = line.trim();
    if trimmed.is_empty() {
        return true;
    }
    if NOISE_MARKERS.iter().any(|m| trimmed.contains(m)) {
        return true;
    }
    if NOISE_PREFIXES.iter().any(|p| trimmed.starts_with(p)) {
        return true;
    }
    // Heuristic: a block with many short, spaceless tokens looks like a
    // directory listing or skill manifest.
    let short_token_lines = trimmed
        .lines()
        .map(str::trim)
        .filter(|l| {
            !l.is_empty()
                && l.len() <= 40
                && !l.contains(' ')
                && l.matches('/').count() < 2
                && l.matches('-').count() <= 3
        })
        .count();
    if short_token_lines >= 5 {
        return true;
    }
    if trimmed.contains("drwx")
        || trimmed.contains("rwxr-xr-x")
        || trimmed.contains("\ntotal ")
    {
        return true;
    }
    // Filter active-session meta commentary that leaked into session files.
    if (trimmed.contains("我先") || trimmed.contains("我继续"))
        && (trimmed.contains("search")
            || trimmed.contains("native-scan")
            || trimmed.contains("session_index"))
    {
        return true;
    }
    trimmed.contains("notebooklm")
        && trimmed.contains("search")
        && trimmed.contains("session_index")
        && trimmed.contains("native-scan")
}

/// Returns `true` when the text is project-internal meta commentary that
/// should not surface as a search result even if it contains the query.
fn should_skip_meta_text(
    current_workdir: Option<&str>,
    session_cwd: Option<&str>,
    text: &str,
) -> bool {
    let (Some(workdir), Some(cwd)) = (current_workdir, session_cwd) else {
        return false;
    };
    let normalized_cwd = std::path::Path::new(cwd)
        .canonicalize()
        .map(|p| p.to_string_lossy().into_owned())
        .unwrap_or_else(|| cwd.to_string());
    let trimmed = text.trim();
    let looks_like_meta = (trimmed.starts_with('我')
        || trimmed.starts_with("我继续")
        || trimmed.starts_with("我现在")
        || trimmed.starts_with("已")
        || trimmed.starts_with("好，")
        || trimmed.starts_with("现在")
        || trimmed.starts_with("继续"))
        && (trimmed.contains("search")
            || trimmed.contains("native-scan")
            || trimmed.contains("session_index")
            || trimmed.contains("索引"));
    looks_like_meta && normalized_cwd == workdir
}

fn should_skip_meta_candidate(text: &str) -> bool {
    let trimmed = text.trim();
    (trimmed.starts_with('我')
        || trimmed.starts_with("我继续")
        || trimmed.starts_with("我现在")
        || trimmed.starts_with("已")
        || trimmed.starts_with("继续"))
        && (trimmed.contains("search")
            || trimmed.contains("native-scan")
            || trimmed.contains("session_index")
            || trimmed.contains("索引"))
}

// ── record-type filtering ─────────────────────────────────────────────────────

fn should_skip_record_type(value: &Value) -> bool {
    let top_level = value.get("type").and_then(Value::as_str).unwrap_or("");
    if matches!(top_level, "turn_context" | "custom_tool_call") {
        return true;
    }
    let nested_type = |key: &str| {
        value
            .get(key)
            .and_then(|v| v.get("type"))
            .and_then(Value::as_str)
            .unwrap_or("")
    };
    if top_level == "response_item" {
        let pt = nested_type("payload");
        if matches!(pt, "function_call_output" | "function_call" | "reasoning") {
            return true;
        }
    }
    if top_level == "event_msg" {
        let pt = nested_type("payload");
        if matches!(pt, "token_count" | "task_started") {
            return true;
        }
    }
    false
}

// ── scoring ───────────────────────────────────────────────────────────────────

fn field_priority(field: &str) -> i32 {
    match field {
        "message.content.text" | "payload.content.text" => 120,
        "message" | "message.content" | "payload.message" | "root.text" | "payload.text" => 100,
        "root.content" | "root.display" | "payload.display" | "root.last_agent_message"
        | "payload.last_agent_message" => 70,
        "root.prompt"
        | "payload.prompt"
        | "root.user_instructions"
        | "payload.user_instructions" => 20,
        RAW_LINE_FIELD => 10,
        _ => 40,
    }
}

fn candidate_score(field: &str, text: &str, query_lower: &str) -> i32 {
    let hits = text.to_lowercase().matches(query_lower).count() as i32;
    field_priority(field) + hits * 25
}

// ── text extraction ───────────────────────────────────────────────────────────

fn extract_text_candidates(value: &Value) -> Vec<MatchDetail> {
    const ROOT_FIELDS: &[(&str, &str)] = &[
        ("root.display", "display"),
        ("root.text", "text"),
        ("root.prompt", "prompt"),
        ("root.content", "content"),
        ("root.user_instructions", "user_instructions"),
        ("root.last_agent_message", "last_agent_message"),
    ];
    const PAYLOAD_FIELDS: &[(&str, &str)] = &[
        ("payload.message", "message"),
        ("payload.display", "display"),
        ("payload.text", "text"),
        ("payload.prompt", "prompt"),
        ("payload.user_instructions", "user_instructions"),
        ("payload.last_agent_message", "last_agent_message"),
    ];

    let mut out = Vec::new();
    collect_text_candidate("message", value.get("message"), &mut out);
    for &(field, key) in ROOT_FIELDS {
        collect_text_candidate(field, value.get(key), &mut out);
    }
    if let Some(payload) = value.get("payload") {
        for &(field, key) in PAYLOAD_FIELDS {
            collect_text_candidate(field, payload.get(key), &mut out);
        }
        if let Some(items) = payload.get("content").and_then(Value::as_array) {
            for item in items {
                collect_text_candidate("payload.content.text", item.get("text"), &mut out);
            }
        }
    }
    if let Some(message) = value.get("message") {
        collect_text_candidate("message.content", message.get("content"), &mut out);
        if let Some(items) = message.get("content").and_then(Value::as_array) {
            for item in items {
                collect_text_candidate("message.content.text", item.get("text"), &mut out);
            }
        }
    }
    out
}

fn collect_text_candidate(field: &'static str, value: Option<&Value>, out: &mut Vec<MatchDetail>) {
    if let Some(text) = value.and_then(Value::as_str) {
        let trimmed = text.trim();
        if !trimmed.is_empty() {
            out.push(MatchDetail {
                field,
                text: trimmed.to_string(),
            });
        }
    }
}

// ── field extractors ──────────────────────────────────────────────────────────

fn extract_session_id(value: &Value) -> Option<String> {
    nested_str(value, &["payload", "id"])
        .or_else(|| nested_str(value, &["sessionId"]))
        .or_else(|| nested_str(value, &["session_id"]))
        .map(str::to_string)
}

fn extract_timestamp(value: &Value) -> Option<String> {
    nested_str(value, &["payload", "timestamp"])
        .or_else(|| nested_str(value, &["createdAt"]))
        .or_else(|| nested_str(value, &["created_at"]))
        .or_else(|| nested_str(value, &["time"]))
        .map(str::to_string)
}

fn extract_cwd(value: &Value) -> Option<String> {
    nested_str(value, &["payload", "cwd"])
        .or_else(|| nested_str(value, &["cwd"]))
        .map(str::to_string)
}

/// Returns the canonical path of the active working directory, preferring the
/// `CONTEXTGO_ACTIVE_WORKDIR` environment variable when set.
fn active_workdir() -> Option<String> {
    if let Ok(explicit) = std::env::var("CONTEXTGO_ACTIVE_WORKDIR") {
        let trimmed = explicit.trim();
        if !trimmed.is_empty() {
            return std::path::Path::new(trimmed)
                .canonicalize()
                .map(|p| p.to_string_lossy().into_owned())
                .ok()
                .or_else(|| Some(trimmed.to_string()));
        }
    }
    std::env::current_dir()
        .ok()
        .and_then(|p| p.canonicalize().ok().or(Some(p)))
        .map(|p| p.to_string_lossy().into_owned())
}

fn nested_str<'a>(value: &'a Value, keys: &[&str]) -> Option<&'a str> {
    let mut current = value;
    for key in keys {
        current = current.get(*key)?;
    }
    current.as_str()
}

// ── path filtering ────────────────────────────────────────────────────────────

const VALID_EXTENSIONS: &[&str] = &["json", "jsonl"];

fn is_valid_extension(path: &Path) -> bool {
    let lower_path = path.to_string_lossy().to_lowercase();
    if should_skip_path(&lower_path) {
        return false;
    }
    path.extension()
        .and_then(|ext| ext.to_str())
        .map(|ext| VALID_EXTENSIONS.contains(&ext))
        .unwrap_or(false)
}

fn should_skip_path(lower_path: &str) -> bool {
    lower_path.contains("/skills/") || lower_path.contains("skills-repo")
}

fn should_report_error(err: &anyhow::Error) -> bool {
    let text = err.to_string();
    text != SKIPPED_QUERY_MISS && text != SKIPPED_CURRENT_WORKDIR
}

// ── tests ─────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn nested_str_navigates_nested_structures() {
        let value = json!({ "payload": { "id": "session-1" } });
        assert_eq!(nested_str(&value, &["payload", "id"]), Some("session-1"));
    }

    #[test]
    fn extract_session_id_supports_multiple_keys() {
        assert_eq!(
            extract_session_id(&json!({ "session_id": "legacy" })),
            Some("legacy".to_string())
        );
        assert_eq!(
            extract_session_id(&json!({ "payload": { "id": "payload-id" } })),
            Some("payload-id".to_string())
        );
    }

    #[test]
    fn extract_timestamp_prefers_payload_timestamp() {
        let value = json!({
            "createdAt": "2025-01-01T00:00:00Z",
            "payload": { "timestamp": "2025-01-01T01:00:00Z" }
        });
        assert_eq!(
            extract_timestamp(&value),
            Some("2025-01-01T01:00:00Z".to_string())
        );
    }

    #[test]
    fn collect_text_candidates_trims_and_gathers() {
        let value = json!({
            "message": {
                "content": [
                    { "text": " hello " },
                    { "text": "世界" }
                ]
            },
            "payload": {
                "text": " payload "
            }
        });
        let candidates = extract_text_candidates(&value);
        assert!(candidates
            .iter()
            .any(|c| c.field == "message.content.text" && c.text == "hello"));
        assert!(candidates
            .iter()
            .any(|c| c.field == "message.content.text" && c.text == "世界"));
        assert!(candidates
            .iter()
            .any(|c| c.field == "payload.text" && c.text == "payload"));
    }

    #[test]
    fn is_noise_line_filters_known_markers() {
        assert!(is_noise_line("# agents.md instructions"));
        assert!(!is_noise_line("a normal line"));
    }

    #[test]
    fn is_noise_line_blocks_noisy_prefixes() {
        assert!(is_noise_line("## heading"));
        assert!(is_noise_line("```rust"));
    }

    #[test]
    fn should_skip_path_filters_skills_sources() {
        assert!(should_skip_path(
            "/users/testuser/.codex/skills/notebooklm/skill.md"
        ));
        assert!(should_skip_path(
            "/users/testuser/.claude/projects/-users-testuser-skills-repo/a.jsonl"
        ));
        assert!(!should_skip_path(
            "/users/testuser/.codex/sessions/2026/03/test.jsonl"
        ));
    }

    #[test]
    fn is_noise_line_filters_meta_chatter() {
        let line = "我继续沿结果质量这条线打，不回到命名层。先复看当前工作树和主链 search NotebookLM 的命中。";
        assert!(is_noise_line(&line.to_lowercase()));
    }

    #[test]
    fn summarize_by_source_aggregates_correctly() {
        let summaries = vec![
            SessionSummary {
                source: "alpha",
                path: PathBuf::from("/tmp/first.json"),
                session_id: "first".into(),
                lines: 5,
                size_bytes: 123,
                first_timestamp: Some("t1".into()),
                last_timestamp: Some("t2".into()),
                snippet: None,
                match_field: None,
                match_score: 0,
            },
            SessionSummary {
                source: "alpha",
                path: PathBuf::from("/tmp/second.json"),
                session_id: "second".into(),
                lines: 3,
                size_bytes: 45,
                first_timestamp: None,
                last_timestamp: None,
                snippet: Some("sample".into()),
                match_field: None,
                match_score: 0,
            },
        ];
        let aggregate = summarize_by_source(&summaries);
        let entry = aggregate.get("alpha").unwrap();
        assert_eq!(entry.session_count, 2);
        assert_eq!(entry.total_lines, 8);
        assert_eq!(entry.total_bytes, 168);
        assert_eq!(entry.sample.unwrap().session_id, "first");
    }

    #[test]
    fn candidate_score_prefers_message_content_over_prompt() {
        let prompt_score =
            candidate_score("payload.prompt", "NotebookLM integration outline", "notebooklm");
        let content_score = candidate_score(
            "message.content.text",
            "NotebookLM integration outline",
            "notebooklm",
        );
        assert!(content_score > prompt_score);
    }

    #[test]
    fn active_workdir_prefers_explicit_env() {
        // Use a serial-safe approach: store, set, test, restore.
        // This test is intentionally not run in parallel with others that
        // mutate the same env var.
        let previous = std::env::var("CONTEXTGO_ACTIVE_WORKDIR").ok();
        // SAFETY: single-threaded test binary; no other thread reads this var.
        unsafe {
            std::env::set_var("CONTEXTGO_ACTIVE_WORKDIR", "/tmp");
        }
        let cwd = active_workdir().unwrap();
        // /tmp may be a symlink on macOS; accept any path ending with "tmp".
        assert!(cwd.ends_with("tmp"), "unexpected cwd: {cwd}");
        unsafe {
            match previous {
                Some(v) => std::env::set_var("CONTEXTGO_ACTIVE_WORKDIR", v),
                None => std::env::remove_var("CONTEXTGO_ACTIVE_WORKDIR"),
            }
        }
    }

    #[test]
    fn should_report_error_suppresses_expected_skips() {
        assert!(!should_report_error(&anyhow::anyhow!(SKIPPED_QUERY_MISS)));
        assert!(!should_report_error(&anyhow::anyhow!(
            SKIPPED_CURRENT_WORKDIR
        )));
        assert!(should_report_error(&anyhow::anyhow!("real parse failure")));
    }
}
