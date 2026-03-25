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

#[derive(Parser)]
#[command(author, version, about = "高性能 Codex / Claude 会话扫描原型")]
struct Args {
    #[arg(long, default_value = "~/.codex/sessions", help = "Codex 会话根目录")]
    codex_root: String,

    #[arg(long, default_value = "~/.claude/projects", help = "Claude 会话根目录")]
    claude_root: String,

    #[arg(long, default_value_t = 4, help = "Rayon 并行线程数")]
    threads: usize,
}

struct WorkItem {
    source: &'static str,
    path: PathBuf,
}

struct SessionSummary {
    source: &'static str,
    path: PathBuf,
    session_id: String,
    lines: usize,
    size_bytes: u64,
    first_timestamp: Option<String>,
    last_timestamp: Option<String>,
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

    fn scan(&self, work_items: &[WorkItem]) -> (Vec<SessionSummary>, Vec<anyhow::Error>) {
        let results: Vec<_> = work_items
            .par_iter()
            .map(|item| process_file(item))
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

    fn report(
        &self,
        total_files: usize,
        summaries: &[SessionSummary],
        errors: &[anyhow::Error],
        duration: Duration,
    ) {
        println!("扫描完毕：{} 文件，耗时 {:.2?}。", total_files, duration);
        let aggregates = summarize_by_source(summaries);
        for root in &self.roots {
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
                            "    示例：{} | {} -> {} | {}",
                            sample.session_id,
                            sample.first_timestamp.as_deref().unwrap_or("?"),
                            sample.last_timestamp.as_deref().unwrap_or("?"),
                            sample.path.display()
                        );
                    }
                }
                None => {
                    println!("  {} -> 0 sessions", root.label);
                }
            }
        }

        if !errors.is_empty() {
            println!("  解析时出现 {} 个错误（仅日志输出）。", errors.len());
            for err in errors.iter().take(5) {
                println!("    - {}", err);
            }
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

    let (summaries, errors) = scanner.scan(&work_items);
    let duration = start.elapsed();
    scanner.report(total_files, &summaries, &errors, duration);
    Ok(())
}

fn process_file(item: &WorkItem) -> Result<SessionSummary> {
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
        }
    }

    Ok(SessionSummary {
        source: item.source,
        path: item.path.clone(),
        session_id,
        lines,
        size_bytes: metadata.len(),
        first_timestamp,
        last_timestamp,
    })
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
