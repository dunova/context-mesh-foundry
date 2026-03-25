use anyhow::{Context, Result};
use clap::Parser;
use rayon::prelude::*;
use serde_json::Value;
use shellexpand::tilde;
use std::collections::HashMap;
use std::fs::File;
use std::io::{BufRead, BufReader};
use std::path::PathBuf;
use std::time::Instant;
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

fn main() -> Result<()> {
    let args = Args::parse();
    rayon::ThreadPoolBuilder::new()
        .num_threads(args.threads)
        .build_global()
        .context("配置 Rayon 线程池失败")?;

    let start = Instant::now();
    let plan = collect_plan(&args);
    let work_items = collect_files(&plan);
    let total_files = work_items.len();

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

    let duration = start.elapsed();
    report(total_files, &summaries, &errors, duration);
    Ok(())
}

fn collect_plan(args: &Args) -> Vec<(&'static str, PathBuf)> {
    let expand = |raw: &str| tilde(raw).into_owned();
    vec![
        ("codex_session", PathBuf::from(expand(&args.codex_root))),
        ("claude_session", PathBuf::from(expand(&args.claude_root))),
    ]
}

fn collect_files(plan: &[(&'static str, PathBuf)]) -> Vec<WorkItem> {
    plan.iter()
        .filter_map(|(source, root)| {
            if !root.exists() {
                eprintln!("目录不存在，跳过：{} -> {}", source, root.display());
                return None;
            }
            Some((source, root))
        })
        .flat_map(|(source, root)| {
            WalkDir::new(root)
                .into_iter()
                .filter_map(|entry| match entry {
                    Ok(entry) if entry.file_type().is_file() => {
                        let ext = entry
                            .path()
                            .extension()
                            .and_then(|e| e.to_str())
                            .unwrap_or("");
                        if matches!(ext, "jsonl" | "json") {
                            Some(WorkItem {
                                source,
                                path: entry.path().to_path_buf(),
                            })
                        } else {
                            None
                        }
                    }
                    _ => None,
                })
                .collect::<Vec<_>>()
        })
        .collect()
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

fn report(
    total_files: usize,
    summaries: &[SessionSummary],
    errors: &[anyhow::Error],
    duration: std::time::Duration,
) {
    println!("扫描完毕：{} 文件，耗时 {:.2?}。", total_files, duration);

    let mut per_source: HashMap<&str, Vec<&SessionSummary>> = HashMap::new();
    for summary in summaries {
        per_source.entry(summary.source).or_default().push(summary);
    }

    for (source, batch) in per_source {
        let total_lines: usize = batch.iter().map(|s| s.lines).sum();
        let total_size: u64 = batch.iter().map(|s| s.size_bytes).sum();
        println!(
            "  {} -> {} sessions, 总行数 {}, 占用 {} 字节",
            source,
            batch.len(),
            total_lines,
            total_size
        );
        if let Some(head) = batch.first() {
            println!(
                "    示例：{} | {} -> {} | {}",
                head.session_id,
                head.first_timestamp.as_deref().unwrap_or("?"),
                head.last_timestamp.as_deref().unwrap_or("?"),
                head.path.display()
            );
        }
    }

    if !errors.is_empty() {
        println!("  解析时出现 {} 个错误（仅日志输出）。", errors.len());
        for err in errors.iter().take(5) {
            println!("    - {}", err);
        }
    }
}
