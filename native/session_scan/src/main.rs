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

const SKIPPED_QUERY_MISS: &str = "query not matched";
const SKIPPED_CURRENT_WORKDIR: &str = "skip current workdir session";

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
    "我先做“全局一致性同步”检查",
    "主链不再是瓶颈",
    "现在真正该优化的是",
    "native 搜索结果质量",
    "不是再融合，而是",
    "我继续的话，就沿这条质量线往下打",
    "把 rust `native-scan` 结果里的",
    "我继续直接提主链结果质量",
    "我先复跑主链",
    "再决定要不要进一步做字段级过滤",
    "现在不是“能不能跑”的问题",
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

const NOISE_PREFIXES: &[&str] = &["##", "```", "> ", "- [", "* ", "http", "https"];
const RAW_LINE_FIELD: &str = "raw_line";

#[derive(Parser)]
#[command(author, version, about = "高性能 Codex / Claude 会话扫描原型")]
struct Args {
    #[arg(long, default_value = "~/.codex/sessions", help = "Codex 会话根目录")]
    codex_root: String,

    #[arg(long, default_value = "~/.claude/projects", help = "Claude 会话根目录")]
    claude_root: String,

    #[arg(long, default_value_t = 4, help = "Rayon 并行线程数")]
    threads: usize,

    #[arg(long, default_value = "", help = "仅保留包含 query 的结果")]
    query: String,

    #[arg(long, default_value_t = 20, help = "最多输出结果数")]
    limit: usize,

    #[arg(long, default_value_t = false, help = "输出 JSON")]
    json: bool,
}

struct WorkItem {
    source: &'static str,
    path: PathBuf,
}

#[derive(Clone, Debug)]
struct MatchDetail {
    field: &'static str,
    text: String,
}

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

#[derive(serde::Serialize)]
struct JsonReport {
    files_scanned: usize,
    query: String,
    duration_ms: u128,
    aggregates: Vec<JsonRootAggregate>,
    matches: Vec<SerializableSummary>,
    errors: Vec<String>,
}

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

impl SourceRoot {
    fn new(label: &'static str, path: PathBuf) -> Self {
        Self { label, path }
    }
}

struct Scanner {
    roots: Vec<SourceRoot>,
}

impl Scanner {
    fn from_args(args: &Args) -> Self {
        let expand = |raw: &str| tilde(raw).into_owned();
        let roots = vec![
            SourceRoot::new("codex_session", PathBuf::from(expand(&args.codex_root))),
            SourceRoot::new(
                "codex_session",
                PathBuf::from(expand("~/.codex/archived_sessions")),
            ),
            SourceRoot::new("claude_session", PathBuf::from(expand(&args.claude_root))),
        ];
        Self { roots }
    }

    fn collect_work_items(&self) -> Vec<WorkItem> {
        self.roots
            .iter()
            .filter_map(|root| {
                if !root.path.exists() {
                    eprintln!(
                        "目录不存在，跳过：{} -> {}",
                        root.label,
                        root.path.display()
                    );
                    None
                } else {
                    Some(root)
                }
            })
            .flat_map(|root| {
                WalkDir::new(&root.path)
                    .into_iter()
                    .filter_map(|entry| match entry {
                        Ok(entry)
                            if entry.file_type().is_file() && is_valid_extension(entry.path()) =>
                        {
                            Some(WorkItem {
                                source: root.label,
                                path: entry.path().to_path_buf(),
                            })
                        }
                        _ => None,
                    })
                    .collect::<Vec<_>>()
            })
            .collect()
    }

    fn root_labels(&self) -> Vec<&'static str> {
        let mut labels = Vec::new();
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
        let mut summaries = Vec::new();
        let mut errors = Vec::new();
        for result in results {
            match result {
                Ok(summary) => summaries.push(summary),
                Err(err) => {
                    if should_report_error(&err) {
                        errors.push(err);
                    }
                }
            }
        }
        (summaries, errors)
    }
}

impl ScannerReport {
    fn write_stdout(&self, scanner: &Scanner) {
        println!(
            "扫描完毕：{} 文件，耗时 {:.2?}。",
            self.total_files, self.duration
        );
        let aggregates = summarize_by_source(&self.summaries);
        for label in scanner.root_labels() {
            if let Some(aggregate) = aggregates.get(label) {
                println!(
                    "  {} -> {} sessions, {} 行, {} 字节",
                    aggregate.label,
                    aggregate.session_count,
                    aggregate.total_lines,
                    aggregate.total_bytes
                );
                if let Some(sample) = aggregate.sample {
                    println!(
                        "    示例：{} | {} -> {} | {} | [{}]",
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
            println!("  解析时出现 {} 个错误（仅日志输出）。", self.errors.len());
            for err in self.errors.iter().take(5) {
                println!("    - {}", err);
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

struct SourceAggregate<'a> {
    label: &'static str,
    session_count: usize,
    total_lines: usize,
    total_bytes: u64,
    sample: Option<&'a SessionSummary>,
}

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

fn main() -> Result<()> {
    let args = Args::parse();
    rayon::ThreadPoolBuilder::new()
        .num_threads(args.threads)
        .build_global()
        .context("配置 Rayon 线程池失败")?;

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

fn process_file(item: &WorkItem, query: &str) -> Result<SessionSummary> {
    let file = File::open(&item.path)
        .with_context(|| format!("无法打开会话文件 {}", item.path.display()))?;
    let metadata = file
        .metadata()
        .with_context(|| format!("无法读取元数据 {}", item.path.display()))?;
    let modified_epoch = metadata
        .modified()
        .ok()
        .and_then(|value| value.duration_since(std::time::UNIX_EPOCH).ok())
        .map(|value| value.as_secs())
        .unwrap_or(0);

    let mut session_id = item
        .path
        .file_stem()
        .and_then(|s| s.to_str())
        .unwrap_or("session")
        .to_string();

    let mut first_timestamp = None;
    let mut last_timestamp = None;
    let mut session_cwd: Option<String> = None;
    let mut lines = 0usize;
    let query_lower = query.trim().to_lowercase();
    let mut matched = query_lower.is_empty();
    let mut best_match: Option<(i32, String, String)> = None;
    let current_workdir = active_workdir();
    let reader = BufReader::new(file);
    for line in reader.lines() {
        let line = match line {
            Ok(line) => line,
            Err(err) => {
                eprintln!("读取 {} 时出错：{}", item.path.display(), err);
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
                    if let Some(current_workdir) = current_workdir.as_deref() {
                        let normalized_session_cwd = std::path::Path::new(&cwd)
                            .canonicalize()
                            .ok()
                            .map(|path| path.to_string_lossy().to_string())
                            .unwrap_or(cwd);
                        if normalized_session_cwd == current_workdir {
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
                        modified_epoch,
                        &detail.text,
                    ) {
                        continue;
                    }
                    if let Some(candidate) = matched_snippet(&detail.text, &query_lower, 180) {
                        if should_skip_meta_candidate(&candidate) {
                            continue;
                        }
                        if is_noise_line(&candidate.to_lowercase()) {
                            continue;
                        }
                        matched = true;
                        let score = candidate_score(detail.field, &detail.text, &query_lower);
                        let replace = best_match
                            .as_ref()
                            .map(|(best_score, _, _)| score > *best_score)
                            .unwrap_or(true);
                        if replace {
                            best_match = Some((score, candidate, detail.field.to_string()));
                        }
                    }
                }
            }
        } else if !query_lower.is_empty() {
            if let Some(candidate) = matched_snippet(&line, &query_lower, 180) {
                if should_skip_meta_candidate(&candidate) {
                    continue;
                }
                if is_noise_line(&candidate.to_lowercase()) {
                    continue;
                }
                matched = true;
                let score = candidate_score(RAW_LINE_FIELD, &line, &query_lower);
                let replace = best_match
                    .as_ref()
                    .map(|(best_score, _, _)| score > *best_score)
                    .unwrap_or(true);
                if replace {
                    best_match = Some((score, candidate, RAW_LINE_FIELD.to_string()));
                }
            }
        }
    }

    if !matched {
        anyhow::bail!(SKIPPED_QUERY_MISS)
    }

    Ok(SessionSummary {
        source: item.source,
        path: item.path.clone(),
        session_id,
        lines,
        size_bytes: metadata.len(),
        first_timestamp,
        last_timestamp,
        snippet: best_match.as_ref().map(|(_, snippet, _)| snippet.clone()),
        match_field: best_match.as_ref().map(|(_, _, field)| field.clone()),
        match_score: best_match.as_ref().map(|(score, _, _)| *score).unwrap_or(0),
    })
}

fn matched_snippet(text: &str, query_lower: &str, limit: usize) -> Option<String> {
    let trimmed = text.trim();
    if trimmed.is_empty() || query_lower.is_empty() {
        return None;
    }
    let lower = trimmed.to_lowercase();
    let idx = lower.find(query_lower)?;
    Some(clip_snippet(trimmed, idx, query_lower.len(), limit))
}

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

fn is_noise_line(line: &str) -> bool {
    let trimmed = line.trim();
    if trimmed.is_empty() {
        return true;
    }
    let lower = trimmed.to_lowercase();
    if NOISE_MARKERS.iter().any(|marker| lower.contains(marker)) {
        return true;
    }
    if NOISE_PREFIXES
        .iter()
        .any(|prefix| lower.starts_with(prefix))
    {
        return true;
    }
    let lines = lower
        .lines()
        .map(str::trim)
        .filter(|line| !line.is_empty())
        .collect::<Vec<_>>();
    let short_token_lines = lines
        .iter()
        .filter(|line| line.len() <= 40 && !line.contains(' ') && line.matches('/').count() < 2 && line.matches('-').count() <= 3)
        .count();
    if short_token_lines >= 5 {
        return true;
    }
    if lower.contains("drwx") || lower.contains("rwxr-xr-x") || lower.contains("\ntotal ") {
        return true;
    }
    if (lower.contains("我先") || lower.contains("我继续"))
        && (lower.contains("search") || lower.contains("native-scan") || lower.contains("session_index"))
    {
        return true;
    }
    lower.contains("notebooklm")
        && lower.contains("search")
        && lower.contains("session_index")
        && lower.contains("native-scan")
}

fn should_skip_meta_text(
    _current_workdir: Option<&str>,
    session_cwd: Option<&str>,
    _modified_epoch: u64,
    text: &str,
) -> bool {
    let Some(current_workdir) = _current_workdir else {
        return false;
    };
    let Some(session_cwd) = session_cwd else {
        return false;
    };
    let normalized_session_cwd = std::path::Path::new(session_cwd)
        .canonicalize()
        .ok()
        .map(|path| path.to_string_lossy().to_string())
        .unwrap_or_else(|| session_cwd.to_string());
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
    looks_like_meta && normalized_session_cwd == current_workdir
}

fn should_skip_record_type(value: &Value) -> bool {
    let top_level = value.get("type").and_then(|v| v.as_str()).unwrap_or("");
    if matches!(top_level, "turn_context" | "custom_tool_call") {
        return true;
    }
    if top_level == "response_item" {
        let payload_type = value
            .get("payload")
            .and_then(|v| v.get("type"))
            .and_then(|v| v.as_str())
            .unwrap_or("");
        if matches!(payload_type, "function_call_output" | "function_call" | "reasoning") {
            return true;
        }
    }
    if top_level == "event_msg" {
        let payload_type = value
            .get("payload")
            .and_then(|v| v.get("type"))
            .and_then(|v| v.as_str())
            .unwrap_or("");
        if matches!(payload_type, "token_count" | "task_started") {
            return true;
        }
    }
    false
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

fn field_priority(field: &str) -> i32 {
    match field {
        "message.content.text" | "payload.content.text" => 120,
        "message" | "message.content" | "payload.message" | "root.text" | "payload.text" => 100,
        "root.content" | "root.display" | "payload.display" | "root.last_agent_message"
        | "payload.last_agent_message" => 70,
        "root.prompt" | "payload.prompt" | "root.user_instructions" | "payload.user_instructions" => 20,
        RAW_LINE_FIELD => 10,
        _ => 40,
    }
}

fn candidate_score(field: &str, text: &str, query_lower: &str) -> i32 {
    let lower = text.to_lowercase();
    let hits = lower.matches(query_lower).count() as i32;
    field_priority(field) + hits * 25
}

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
        if let Some(items) = payload.get("content").and_then(|v| v.as_array()) {
            for item in items {
                collect_text_candidate("payload.content.text", item.get("text"), &mut out);
            }
        }
    }
    if let Some(message) = value.get("message") {
        collect_text_candidate("message.content", message.get("content"), &mut out);
        if let Some(items) = message.get("content").and_then(|v| v.as_array()) {
            for item in items {
                collect_text_candidate("message.content.text", item.get("text"), &mut out);
            }
        }
    }
    out
}

fn collect_text_candidate(field: &'static str, value: Option<&Value>, out: &mut Vec<MatchDetail>) {
    if let Some(text) = value.and_then(|v| v.as_str()) {
        let trimmed = text.trim();
        if !trimmed.is_empty() {
            out.push(MatchDetail {
                field,
                text: trimmed.to_string(),
            });
        }
    }
}

fn extract_session_id(value: &Value) -> Option<String> {
    nested_str(value, &["payload", "id"])
        .or_else(|| nested_str(value, &["sessionId"]))
        .or_else(|| nested_str(value, &["session_id"]))
        .map(|s| s.to_string())
}

fn extract_timestamp(value: &Value) -> Option<String> {
    nested_str(value, &["payload", "timestamp"])
        .or_else(|| nested_str(value, &["createdAt"]))
        .or_else(|| nested_str(value, &["created_at"]))
        .or_else(|| nested_str(value, &["time"]))
        .map(|s| s.to_string())
}

fn extract_cwd(value: &Value) -> Option<String> {
    nested_str(value, &["payload", "cwd"])
        .or_else(|| nested_str(value, &["cwd"]))
        .map(|s| s.to_string())
}

fn active_workdir() -> Option<String> {
    if let Ok(explicit) = std::env::var("CONTEXTGO_ACTIVE_WORKDIR") {
        let trimmed = explicit.trim();
        if !trimmed.is_empty() {
            return std::path::Path::new(trimmed)
                .canonicalize()
                .ok()
                .map(|path| path.to_string_lossy().to_string())
                .or_else(|| Some(trimmed.to_string()));
        }
    }
    std::env::current_dir()
        .ok()
        .and_then(|path| path.canonicalize().ok().or(Some(path)))
        .map(|path| path.to_string_lossy().to_string())
}

fn nested_str<'a>(value: &'a Value, keys: &[&str]) -> Option<&'a str> {
    let mut current = value;
    for key in keys {
        current = current.get(*key)?;
    }
    current.as_str()
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use std::path::PathBuf;

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
        assert!(candidates.iter().any(
            |candidate| candidate.field == "message.content.text" && candidate.text == "hello"
        ));
        assert!(
            candidates
                .iter()
                .any(|candidate| candidate.field == "message.content.text"
                    && candidate.text == "世界")
        );
        assert!(candidates
            .iter()
            .any(|candidate| candidate.field == "payload.text" && candidate.text == "payload"));
    }

    #[test]
    fn is_noise_line_filters_known_markers() {
        let marker = "# agents.md instructions";
        assert!(is_noise_line(marker));
        assert!(!is_noise_line("a normal line"));
    }

    #[test]
    fn is_noise_line_attack_prefixes() {
        assert!(is_noise_line("## 目录"));
        assert!(is_noise_line("```rust"));
    }

    #[test]
    fn should_skip_path_filters_skills_sources() {
        assert!(should_skip_path("/users/dunova/.codex/skills/notebooklm/skill.md"));
        assert!(should_skip_path("/users/dunova/.claude/projects/-users-dunova-skills-repo/a.jsonl"));
        assert!(!should_skip_path("/users/dunova/.codex/sessions/2026/03/test.jsonl"));
    }

    #[test]
    fn is_noise_line_filters_meta_chatter() {
        assert!(is_noise_line(
            "我继续沿结果质量这条线打，不回到命名层。先复看当前工作树和主链 search NotebookLM 的命中。"
        ));
    }

    #[test]
    fn summarize_by_source_keeps_first_sample() {
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
        let prompt_score = candidate_score(
            "payload.prompt",
            "NotebookLM integration outline",
            "notebooklm",
        );
        let content_score = candidate_score(
            "message.content.text",
            "NotebookLM integration outline",
            "notebooklm",
        );
        assert!(content_score > prompt_score);
    }

    #[test]
    fn active_workdir_prefers_explicit_env() {
        let previous = std::env::var("CONTEXTGO_ACTIVE_WORKDIR").ok();
        std::env::set_var("CONTEXTGO_ACTIVE_WORKDIR", "/tmp/contextgo-explicit");
        let cwd = active_workdir().unwrap();
        assert!(cwd.ends_with("/tmp/contextgo-explicit"));
        if let Some(value) = previous {
            std::env::set_var("CONTEXTGO_ACTIVE_WORKDIR", value);
        } else {
            std::env::remove_var("CONTEXTGO_ACTIVE_WORKDIR");
        }
    }

    #[test]
    fn should_report_error_suppresses_expected_skips() {
        assert!(!should_report_error(&anyhow::anyhow!(SKIPPED_QUERY_MISS)));
        assert!(!should_report_error(&anyhow::anyhow!(SKIPPED_CURRENT_WORKDIR)));
        assert!(should_report_error(&anyhow::anyhow!("real parse failure")));
    }
}

fn summarize_by_source<'a>(
    summaries: &'a [SessionSummary],
) -> HashMap<&'static str, SourceAggregate<'a>> {
    let mut map = HashMap::new();
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
