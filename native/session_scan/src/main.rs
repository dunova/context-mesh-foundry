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

const NOISE_MARKERS: &[&str] = &[
    "# agents.md instructions",
    "### available skills",
    "prompt engineer and agent skill optimizer",
    "current skill name:",
    "skill.md",
    "python -m pytest",
    "benchmarks/run.py",
    "<instructions>",
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
                Err(err) => errors.push(err),
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
        for root in &scanner.roots {
            match aggregates.get(root.label) {
                Some(aggregate) => {
                    println!(
                        "  {} -> {} sessions, 总行数 {}, 占用 {} 字节",
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
                None => {
                    println!("  {} -> 0 sessions", root.label);
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
            .roots
            .iter()
            .map(|root| {
                aggregates.get(root.label).map_or_else(
                    || JsonRootAggregate {
                        label: root.label.to_string(),
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
    path.extension()
        .and_then(|ext| ext.to_str())
        .map(|ext| VALID_EXTENSIONS.contains(&ext))
        .unwrap_or(false)
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

    let mut session_id = item
        .path
        .file_stem()
        .and_then(|s| s.to_str())
        .unwrap_or("session")
        .to_string();

    let mut first_timestamp = None;
    let mut last_timestamp = None;
    let mut lines = 0usize;
    let query_lower = query.trim().to_lowercase();
    let mut snippet = None;
    let mut matched = query_lower.is_empty();
    let mut match_field = None;

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
            if let Some(id) = extract_session_id(&json) {
                session_id = id;
            }
            if let Some(ts) = extract_timestamp(&json) {
                if first_timestamp.is_none() {
                    first_timestamp = Some(ts.clone());
                }
                last_timestamp = Some(ts);
            }
            if !query_lower.is_empty() && snippet.is_none() {
                for detail in extract_text_candidates(&json) {
                    let lowered = detail.text.to_lowercase();
                    if lowered.contains(&query_lower) && !is_noise_line(&lowered) {
                        matched = true;
                        snippet = Some(detail.text.chars().take(220).collect::<String>());
                        match_field = Some(detail.field.to_string());
                        break;
                    }
                }
            }
        } else if !query_lower.is_empty() && snippet.is_none() {
            let line_lower = line.to_lowercase();
            if line_lower.contains(&query_lower) && !is_noise_line(&line_lower) {
                matched = true;
                snippet = Some(line.chars().take(220).collect::<String>());
                match_field = Some(RAW_LINE_FIELD.to_string());
            }
        }
    }

    if !matched {
        anyhow::bail!("query not matched")
    }

    Ok(SessionSummary {
        source: item.source,
        path: item.path.clone(),
        session_id,
        lines,
        size_bytes: metadata.len(),
        first_timestamp,
        last_timestamp,
        snippet,
        match_field,
    })
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
    NOISE_PREFIXES
        .iter()
        .any(|prefix| lower.starts_with(prefix))
}

fn extract_text_candidates(value: &Value) -> Vec<MatchDetail> {
    const ROOT_FIELDS: &[(&str, &str)] = &[
        ("root.display", "display"),
        ("root.text", "text"),
        ("root.input", "input"),
        ("root.prompt", "prompt"),
        ("root.output", "output"),
        ("root.content", "content"),
    ];
    const PAYLOAD_FIELDS: &[(&str, &str)] = &[
        ("payload.message", "message"),
        ("payload.display", "display"),
        ("payload.text", "text"),
        ("payload.input", "input"),
        ("payload.prompt", "prompt"),
        ("payload.output", "output"),
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
            },
        ];
        let aggregate = summarize_by_source(&summaries);
        let entry = aggregate.get("alpha").unwrap();
        assert_eq!(entry.session_count, 2);
        assert_eq!(entry.total_lines, 8);
        assert_eq!(entry.total_bytes, 168);
        assert_eq!(entry.sample.unwrap().session_id, "first");
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
