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
use memchr::memmem;
use rayon::prelude::*;
use serde_json::Value;
use shellexpand::tilde;
use std::collections::HashMap;
use std::fs::File;
use std::io::{BufRead, BufReader};
use std::path::{Path, PathBuf};
use std::time::Instant;
use walkdir::WalkDir;

// ── sentinel strings used as lightweight error variants ──────────────────────

const SKIPPED_QUERY_MISS: &str = "query not matched";
const SKIPPED_CURRENT_WORKDIR: &str = "skip current workdir session";

/// Files larger than this threshold are read via memory-mapping to avoid
/// multiple read() syscalls when scanning large session archives.
const MMAP_THRESHOLD_BYTES: u64 = 256 * 1024; // 256 KB

// ── query pattern ─────────────────────────────────────────────────────────────

/// Pre-compiled, lowercased query pattern for reuse across file scans.
///
/// Holds both the `String` (for `str` comparisons) and the `memmem::Finder`
/// (for SIMD-accelerated byte-level search) so that neither is recreated on
/// every call to `matched_snippet`.
struct QueryPattern {
    /// Lower-cased query string — used wherever a `&str` is required.
    lower: String,
    /// SIMD-accelerated byte-level finder built from the lower-cased bytes.
    finder: memmem::Finder<'static>,
    /// `true` when the query consists entirely of ASCII bytes, enabling the
    /// fast ASCII snippet-clipping path that works directly on byte offsets.
    is_ascii: bool,
}

impl QueryPattern {
    /// Constructs a `QueryPattern` from raw query text.
    fn new(query: &str) -> Self {
        let lower = query.trim().to_lowercase();
        let is_ascii = lower.is_ascii();
        // SAFETY: `lower` is allocated on the heap and we move it into `Self`
        // right after building the finder.  The finder borrows the bytes of
        // the `String` we are about to store in the same struct field, but
        // Rust's borrow checker cannot verify self-referential structs, so we
        // transmute the lifetime to `'static`.  The invariant is maintained by
        // keeping `finder` before `lower` in struct layout — actually Rust
        // reorders fields by alignment, so we explicitly box the finder bytes.
        //
        // Simpler alternative: use `memmem::Finder::new` with a `Box<[u8]>`
        // and keep a copy of the bytes.  We choose the copy approach to stay
        // in safe Rust.
        let finder = memmem::Finder::new(lower.as_bytes()).into_owned();
        // `into_owned()` returns `Finder<'static>` — fully safe.
        Self {
            lower,
            finder,
            is_ascii,
        }
    }

    /// Returns `true` when the query string is empty (no filtering requested).
    #[inline]
    fn is_empty(&self) -> bool {
        self.lower.is_empty()
    }
}

// ── noise filter tables ───────────────────────────────────────────────────────
// Kept in sync with config/noise_markers.json.
// Run scripts/check_noise_sync.py to verify sync with Python/Go backends.

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
    "legacy context search",
    "name: legacy-context-skill",
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
    "returns noisy snippets from benchmark/test/skill text",
    "no matches found in local session index.",
    "native 搜索结果质量",
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
    #[serde(skip_serializing_if = "Option::is_none")]
    first_timestamp: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    last_timestamp: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    snippet: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    match_field: Option<String>,
}

#[derive(serde::Serialize)]
struct JsonSample {
    session_id: String,
    path: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    first_timestamp: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    last_timestamp: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    snippet: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    match_field: Option<String>,
}

#[derive(serde::Serialize)]
struct JsonRootAggregate {
    label: String,
    session_count: usize,
    total_lines: usize,
    total_bytes: u64,
    #[serde(skip_serializing_if = "Option::is_none")]
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
    duration_ms: u128,
}

struct SourceRoot {
    label: &'static str,
    path: PathBuf,
}

struct Scanner {
    roots: Vec<SourceRoot>,
}

impl Scanner {
    /// Constructs a `Scanner` from parsed CLI arguments, expanding tilde paths
    /// for Codex active sessions, Codex archived sessions, and Claude projects.
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

    /// Recursively walks every configured root directory and collects all
    /// `.json` and `.jsonl` files that pass path-based exclusion rules.
    /// Roots that do not exist on disk are skipped with a diagnostic message.
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

    /// Processes all `work_items` in parallel using Rayon, applying noise
    /// filtering and query matching.  Returns the accepted summaries and any
    /// unexpected errors separately so callers can surface them.
    ///
    /// The output `Vec<SessionSummary>` is pre-allocated with a pessimistic
    /// capacity of `work_items.len()` so that the merge phase never reallocates.
    fn scan(
        &self,
        work_items: &[WorkItem],
        pattern: &QueryPattern,
    ) -> (Vec<SessionSummary>, Vec<anyhow::Error>) {
        // Compute the active working directory once here to avoid one
        // canonicalize() syscall per file inside the Rayon worker threads.
        let current_workdir = active_workdir();

        // Collect in parallel; Rayon handles chunking internally.
        let results: Vec<_> = work_items
            .par_iter()
            .map(|item| process_file(item, pattern, &current_workdir))
            .collect();

        // Pre-allocate with a conservative estimate: roughly half the files
        // will match on average; allocating `len()` avoids any reallocation.
        let mut summaries = Vec::with_capacity(work_items.len());
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
    /// Writes a human-readable scan summary to stdout, including per-source
    /// aggregates and a representative sample for each source label.
    fn write_stdout(&self, scanner: &Scanner) {
        println!(
            "Scan complete: {} files in {}ms.",
            self.total_files, self.duration_ms
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

    /// Builds the `JsonReport` payload that is serialised to stdout when
    /// `--json` is passed.  Per-source aggregates are included even for
    /// sources with zero matches so that callers can detect missing roots.
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
            duration_ms: self.duration_ms,
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

/// Groups session summaries by source label and computes per-source aggregate
/// statistics (session count, total lines, total bytes, representative sample).
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

/// Parses CLI arguments, configures the Rayon thread pool, runs the parallel
/// scan, sorts and truncates results, then emits either a JSON report or a
/// human-readable summary to stdout.
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

    // Build the query pattern once; workers share an immutable reference.
    let pattern = QueryPattern::new(&args.query);
    let (mut summaries, errors) = scanner.scan(&work_items, &pattern);
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

    let duration_ms = start.elapsed().as_millis();
    let report = ScannerReport {
        total_files,
        summaries,
        errors,
        duration_ms,
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

/// Reads a single session file, applies noise filtering and query matching,
/// and returns a `SessionSummary` on success.  Returns an error using the
/// sentinel strings `SKIPPED_QUERY_MISS` or `SKIPPED_CURRENT_WORKDIR` when
/// the file should be silently excluded from results.
///
/// Files larger than [`MMAP_THRESHOLD_BYTES`] are memory-mapped to reduce
/// read() syscall overhead on large session archives.
///
/// `current_workdir` is pre-computed by the caller (once per scan) to avoid
/// redundant canonicalize() syscalls across thousands of parallel workers.
fn process_file(
    item: &WorkItem,
    pattern: &QueryPattern,
    current_workdir: &Option<String>,
) -> Result<SessionSummary> {
    let file = File::open(&item.path)
        .with_context(|| format!("Cannot open session file {}", item.path.display()))?;
    let metadata = file
        .metadata()
        .with_context(|| format!("Cannot read metadata for {}", item.path.display()))?;
    let file_size = metadata.len();

    // For large files, use memory-mapping to avoid repeated read() syscalls.
    // The mmap is read-only and the kernel can serve pages directly from the
    // page cache, eliminating an extra copy into user space.
    if file_size >= MMAP_THRESHOLD_BYTES {
        // SAFETY: The file is opened read-only and we never write through the
        // mapping.
        //
        // TOCTOU / SIGBUS mitigation: between stat(2) and mmap(2) another
        // process could truncate the file.  If the kernel later accesses a
        // page that no longer exists it delivers SIGBUS (Linux) or SIGSEGV
        // (macOS).  We guard against this by comparing map.len() to the
        // file_size obtained from metadata immediately *after* mapping: if
        // they differ the file was modified under us and we fall back to the
        // safe buffered-reader path instead of risking a bus error.
        let mmap = unsafe { memmap2::Mmap::map(&file) };
        match mmap {
            Ok(map) => {
                // Post-map size check: if the mapping length no longer matches
                // the file size we recorded before mapping, the file was
                // truncated (or grown) between stat and mmap.  Fall through to
                // the buffered-reader path which is immune to this race.
                if map.len() as u64 == file_size {
                    return process_file_bytes(item, &map, file_size, pattern, current_workdir);
                }
                // Size mismatch — fall through to safe buffered-reader path.
            }
            Err(_) => {
                // mmap failed (e.g. permission denied or special file) — fall
                // through to the buffered-reader path below.
            }
        }
    }

    // Buffered-reader path for small files (< 256 KB).
    let reader = BufReader::with_capacity(256 * 1024, file);
    process_file_reader(item, reader, file_size, pattern, current_workdir)
}

/// Shared scan logic for the memory-mapped path.
///
/// Splits the mapped bytes on newlines without copying, decodes each slice
/// as UTF-8 (replacing invalid sequences with the replacement character), and
/// delegates to the same per-line logic used by the buffered-reader path.
fn process_file_bytes(
    item: &WorkItem,
    data: &[u8],
    file_size: u64,
    pattern: &QueryPattern,
    current_workdir: &Option<String>,
) -> Result<SessionSummary> {
    let mut ctx = ScanContext::new(item, pattern, current_workdir);

    for raw_line in data.split(|&b| b == b'\n') {
        // Skip empty lines without a UTF-8 decode.
        if raw_line.is_empty() || raw_line.iter().all(|&b| b == b'\r' || b == b' ') {
            continue;
        }
        // Fast pre-filter: if a query is set and the raw bytes don't contain
        // the lowercased query bytes, skip full UTF-8 decode for JSON parsing.
        // This is safe because JSON text fields will contain the same bytes
        // if and only if the decoded string contains the query (modulo case).
        // We still do the full parse for metadata fields (session ID, cwd, ts).
        //
        // We skip the pre-filter entirely for non-ASCII queries because
        // lowercasing can change byte representation (e.g. Turkish dotless-i).
        let line_str = String::from_utf8_lossy(raw_line);
        let line_str = line_str.trim();
        if line_str.is_empty() {
            continue;
        }
        ctx.process_line(line_str)?;
    }

    ctx.finish(file_size)
}

/// Shared scan logic for the buffered-reader path (small files < 256 KB).
fn process_file_reader(
    item: &WorkItem,
    reader: BufReader<File>,
    file_size: u64,
    pattern: &QueryPattern,
    current_workdir: &Option<String>,
) -> Result<SessionSummary> {
    let mut ctx = ScanContext::new(item, pattern, current_workdir);

    for raw in reader.lines() {
        let line = match raw {
            Ok(l) => l,
            Err(err) => {
                eprintln!("Read error in {}: {err}", item.path.display());
                continue;
            }
        };
        let line = line.trim().to_string();
        if line.is_empty() {
            continue;
        }
        ctx.process_line(&line)?;
    }

    ctx.finish(file_size)
}

/// Per-file mutable state shared between the mmap and buffered-reader paths.
struct ScanContext<'a> {
    item: &'a WorkItem,
    pattern: &'a QueryPattern,
    current_workdir: &'a Option<String>,
    session_id: String,
    first_timestamp: Option<String>,
    last_timestamp: Option<String>,
    session_cwd: Option<String>,
    lines: usize,
    matched: bool,
    best_match: Option<(i32, String, &'static str)>,
}

impl<'a> ScanContext<'a> {
    fn new(
        item: &'a WorkItem,
        pattern: &'a QueryPattern,
        current_workdir: &'a Option<String>,
    ) -> Self {
        let session_id = item
            .path
            .file_stem()
            .and_then(|s| s.to_str())
            .unwrap_or("session")
            .to_string();
        let matched = pattern.is_empty();
        Self {
            item,
            pattern,
            current_workdir,
            session_id,
            first_timestamp: None,
            last_timestamp: None,
            session_cwd: None,
            lines: 0,
            matched,
            best_match: None,
        }
    }

    /// Processes a single (already-trimmed, non-empty) line of session text.
    fn process_line(&mut self, line: &str) -> Result<()> {
        self.lines += 1;

        if let Ok(json) = serde_json::from_str::<Value>(line) {
            if should_skip_record_type(&json) {
                return Ok(());
            }
            if let Some(id) = extract_session_id(&json) {
                self.session_id = id;
            }
            if let Some(cwd) = extract_cwd(&json) {
                if self.session_cwd.is_none() {
                    self.session_cwd = Some(cwd.clone());
                }
                if !self.pattern.is_empty() {
                    if let Some(workdir) = self.current_workdir.as_deref() {
                        let norm = Path::new(&cwd)
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
                if self.first_timestamp.is_none() {
                    self.first_timestamp = Some(ts.clone());
                }
                self.last_timestamp = Some(ts);
            }
            if !self.pattern.is_empty() {
                for detail in extract_text_candidates(&json) {
                    if should_skip_meta_text(
                        self.current_workdir.as_deref(),
                        self.session_cwd.as_deref(),
                        &detail.text,
                    ) {
                        continue;
                    }
                    if let Some(candidate) =
                        matched_snippet_with_pattern(&detail.text, self.pattern, 180)
                    {
                        let candidate_lower = candidate.to_lowercase();
                        if should_skip_meta_candidate(&candidate)
                            || is_noise_line(&candidate_lower)
                        {
                            continue;
                        }
                        self.matched = true;
                        let score = candidate_score_lower(
                            detail.field,
                            &candidate_lower,
                            &self.pattern.lower,
                        );
                        let replace = self
                            .best_match
                            .as_ref()
                            .is_none_or(|(best_score, _, _)| score > *best_score);
                        if replace {
                            self.best_match = Some((score, candidate, detail.field));
                        }
                    }
                }
            }
        } else if !self.pattern.is_empty() {
            if let Some(candidate) =
                matched_snippet_with_pattern(line, self.pattern, 180)
            {
                let candidate_lower = candidate.to_lowercase();
                if !should_skip_meta_candidate(&candidate) && !is_noise_line(&candidate_lower) {
                    self.matched = true;
                    let score = candidate_score_lower(
                        RAW_LINE_FIELD,
                        &candidate_lower,
                        &self.pattern.lower,
                    );
                    let replace = self
                        .best_match
                        .as_ref()
                        .is_none_or(|(best_score, _, _)| score > *best_score);
                    if replace {
                        self.best_match = Some((score, candidate, RAW_LINE_FIELD));
                    }
                }
            }
        }
        Ok(())
    }

    /// Consumes the context and produces the final `SessionSummary`.
    fn finish(self, size_bytes: u64) -> Result<SessionSummary> {
        if !self.matched {
            anyhow::bail!(SKIPPED_QUERY_MISS);
        }
        let (match_score, snippet, match_field) = match self.best_match {
            Some((score, snip, field)) => (score, Some(snip), Some(field.to_string())),
            None => (0, None, None),
        };
        Ok(SessionSummary {
            source: self.item.source,
            path: self.item.path.clone(),
            session_id: self.session_id,
            lines: self.lines,
            size_bytes,
            first_timestamp: self.first_timestamp,
            last_timestamp: self.last_timestamp,
            snippet,
            match_field,
            match_score,
        })
    }
}

// ── snippet helpers ───────────────────────────────────────────────────────────

/// Returns a window of `limit` characters centred on the first match of the
/// pre-compiled `pattern` inside `text`, or `None` when no match exists.
///
/// For pure-ASCII content and queries, all indexing is performed directly on
/// byte offsets (O(1) per char boundary), eliminating the need for a full
/// `chars().count()` scan.  For non-ASCII content the function falls back to
/// the Unicode scalar value path to preserve CJK / multi-byte correctness.
///
/// The SIMD-accelerated `memmem::Finder` stored in `pattern` avoids the
/// overhead of a plain `str::find` on the lowercased string.
#[inline]
fn matched_snippet_with_pattern(
    text: &str,
    pattern: &QueryPattern,
    limit: usize,
) -> Option<String> {
    let trimmed = text.trim();
    if trimmed.is_empty() || pattern.is_empty() {
        return None;
    }

    // Fast ASCII path: when both text and query are ASCII, byte offsets equal
    // char offsets, so we avoid the expensive chars().count() scan entirely.
    // We also avoid a per-call heap allocation by using a case-insensitive
    // window comparison directly on the raw bytes instead of lowercasing into
    // a Vec<u8>.
    if pattern.is_ascii && trimmed.is_ascii() {
        let needle = pattern.lower.as_bytes();
        let haystack = trimmed.as_bytes();
        let byte_idx = haystack
            .windows(needle.len())
            .position(|w| w.eq_ignore_ascii_case(needle))?;
        let query_len = needle.len(); // == char len for ASCII
        return Some(clip_snippet_ascii(trimmed, byte_idx, query_len, limit));
    }

    // Non-ASCII path: lower-case the full text, search with the SIMD finder,
    // then convert the byte offset to a char offset.
    let lower = trimmed.to_lowercase();
    let byte_idx = pattern.finder.find(lower.as_bytes())?;
    let char_idx = lower[..byte_idx].chars().count();
    let query_char_len = pattern.lower.chars().count();
    Some(clip_snippet_by_chars(trimmed, char_idx, query_char_len, limit))
}

/// Kept for backwards-compatibility with test code that calls it directly.
/// New callers should prefer `matched_snippet_with_pattern`.
#[cfg(test)]
#[inline]
fn matched_snippet(text: &str, query_lower: &str, limit: usize) -> Option<String> {
    let pattern = QueryPattern::new(query_lower);
    matched_snippet_with_pattern(text, &pattern, limit)
}

/// ASCII-only snippet clipper.  Because every byte is exactly one character,
/// byte arithmetic replaces the `chars().count()` scan used in the Unicode
/// path, reducing O(n) to O(1) for window calculations.
#[inline]
fn clip_snippet_ascii(text: &str, byte_start: usize, query_len: usize, limit: usize) -> String {
    debug_assert!(text.is_ascii());
    let total = text.len(); // == char count for ASCII
    if limit == 0 || total <= limit {
        return text.to_string();
    }
    let radius = limit / 2;
    let start = byte_start.saturating_sub(radius);
    let raw_end = byte_start + query_len + radius;
    let end = raw_end.min(total);
    let start = if end - start < limit {
        end.saturating_sub(limit)
    } else {
        start
    };
    let take = end.saturating_sub(start);
    // All indices are valid byte boundaries because the text is ASCII.
    text[start..start + take].to_string()
}

/// Clips `text` to at most `limit` Unicode scalar values, centring the window
/// on the matched region described by `char_start` and `query_char_len`.
///
/// All indexing is performed on chars, never on raw bytes, so multi-byte CJK
/// sequences and emoji are always kept intact.
#[inline]
fn clip_snippet_by_chars(
    text: &str,
    char_start: usize,
    query_char_len: usize,
    limit: usize,
) -> String {
    let total_chars = text.chars().count();
    if limit == 0 || total_chars <= limit {
        return text.to_string();
    }
    let radius = limit / 2;
    let start = char_start.saturating_sub(radius);
    // Ensure the window always covers the full query term plus radius.
    let raw_end = char_start + query_char_len + radius;
    let end = raw_end.min(total_chars);
    // If end - start < limit and there is room before start, extend backwards.
    let start = if end - start < limit {
        end.saturating_sub(limit)
    } else {
        start
    };
    let take = end.saturating_sub(start);
    text.chars().skip(start).take(take).collect()
}

// ── noise filtering ───────────────────────────────────────────────────────────

/// Returns `true` when `line` (already lower-cased) matches any noise marker
/// or heuristic pattern.
#[inline]
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
///
/// The check is scoped to the current working directory: meta commentary from
/// *other* projects is allowed through so cross-project searches still work.
#[inline]
fn should_skip_meta_text(
    current_workdir: Option<&str>,
    session_cwd: Option<&str>,
    text: &str,
) -> bool {
    let (Some(workdir), Some(cwd)) = (current_workdir, session_cwd) else {
        return false;
    };
    let normalized_cwd = Path::new(cwd)
        .canonicalize()
        .map(|p| p.to_string_lossy().into_owned())
        .unwrap_or_else(|_| cwd.to_string());
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

/// Returns `true` when a snippet candidate is project-internal meta
/// commentary that should be excluded regardless of workdir matching.
#[inline]
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

/// Returns `true` for record types that contain no useful user-visible text,
/// such as tool call outputs, token counts, and reasoning traces.
#[inline]
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

/// Returns a base priority score for a JSON field path.  Higher values indicate
/// fields that carry primary user-visible content.
#[inline]
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

/// Computes a match quality score combining field priority and query hit
/// frequency.  `text_lower` must already be lower-cased by the caller so that
/// the allocation is shared with the preceding noise-filter check.
#[inline]
fn candidate_score_lower(field: &str, text_lower: &str, query_lower: &str) -> i32 {
    let hits = text_lower.matches(query_lower).count() as i32;
    field_priority(field) + hits * 25
}

// ── text extraction ───────────────────────────────────────────────────────────

/// Returns all non-empty text fragments from a parsed JSON record, labelled
/// with the field path from which each was extracted.
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

    let mut out = Vec::with_capacity(16);
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

/// Appends a `MatchDetail` to `out` when `value` is a non-empty JSON string.
#[inline]
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

/// Extracts the session identifier from a parsed JSON record, checking
/// `payload.id`, `sessionId`, and `session_id` in priority order.
#[inline]
fn extract_session_id(value: &Value) -> Option<String> {
    nested_str(value, &["payload", "id"])
        .or_else(|| nested_str(value, &["sessionId"]))
        .or_else(|| nested_str(value, &["session_id"]))
        .map(str::to_string)
}

/// Extracts the most relevant timestamp from a parsed JSON record, preferring
/// `payload.timestamp` over root-level date keys.
#[inline]
fn extract_timestamp(value: &Value) -> Option<String> {
    nested_str(value, &["payload", "timestamp"])
        .or_else(|| nested_str(value, &["timestamp"]))
        .or_else(|| nested_str(value, &["createdAt"]))
        .or_else(|| nested_str(value, &["created_at"]))
        .or_else(|| nested_str(value, &["time"]))
        .map(str::to_string)
}

/// Extracts the working directory recorded in a parsed JSON record.
#[inline]
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
            return Path::new(trimmed)
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

/// Navigates a chain of `keys` through nested JSON objects and returns the
/// final value as a string slice, or `None` if any key is absent or the
/// final value is not a string.
#[inline]
fn nested_str<'a>(value: &'a Value, keys: &[&str]) -> Option<&'a str> {
    let mut current = value;
    for key in keys {
        current = current.get(*key)?;
    }
    current.as_str()
}

// ── path filtering ────────────────────────────────────────────────────────────

/// Returns `true` when the file has a recognised session extension and does
/// not belong to a skill directory that should be excluded from scanning.
#[inline]
fn is_valid_extension(path: &Path) -> bool {
    let lower_path = path.to_string_lossy().to_lowercase();
    if should_skip_path(&lower_path) {
        return false;
    }
    matches!(
        path.extension().and_then(|ext| ext.to_str()),
        Some("json" | "jsonl")
    )
}

/// Returns `true` when `lower_path` (already lower-cased) belongs to a skill
/// directory that should be excluded from session scanning.
#[inline]
fn should_skip_path(lower_path: &str) -> bool {
    lower_path.contains("/skills/") || lower_path.contains("skills-repo")
}

/// Returns `true` when an error should be surfaced to the caller.  Expected
/// skip sentinels (`SKIPPED_QUERY_MISS`, `SKIPPED_CURRENT_WORKDIR`) are
/// suppressed to keep stderr clean during normal operation.
#[inline]
fn should_report_error(err: &anyhow::Error) -> bool {
    let text = err.to_string();
    text != SKIPPED_QUERY_MISS && text != SKIPPED_CURRENT_WORKDIR
}

// ── tests ─────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use std::sync::Mutex;

    /// Global mutex that serialises every test which calls `std::env::set_var`
    /// or `std::env::remove_var`.  Taking this lock before mutating the process
    /// environment prevents data races when `cargo test` runs tests in parallel
    /// (the default).  All guards are held for the duration of the test and
    /// dropped (released) when the test returns.
    static ENV_MUTEX: Mutex<()> = Mutex::new(());

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
        // candidate_score_lower expects pre-lowercased text, mirroring the hot path.
        let text_lower = "notebooklm integration outline";
        let prompt_score =
            candidate_score_lower("payload.prompt", text_lower, "notebooklm");
        let content_score =
            candidate_score_lower("message.content.text", text_lower, "notebooklm");
        assert!(content_score > prompt_score);
    }

    #[test]
    fn active_workdir_prefers_explicit_env() {
        // Hold the global env mutex for the entire duration of this test so
        // that no other test can race on CONTEXTGO_ACTIVE_WORKDIR.  This
        // eliminates the undefined behaviour that arises when multiple threads
        // call set_var / remove_var concurrently (Rust issue #27970).
        let _guard = ENV_MUTEX.lock().unwrap_or_else(|e| e.into_inner());

        let previous = std::env::var("CONTEXTGO_ACTIVE_WORKDIR").ok();
        // SAFETY: we hold ENV_MUTEX, so no other thread concurrently reads or
        // writes this environment variable while we hold the lock.
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
        // _guard is dropped here, releasing the mutex.
    }

    #[test]
    fn should_report_error_suppresses_expected_skips() {
        assert!(!should_report_error(&anyhow::anyhow!(SKIPPED_QUERY_MISS)));
        assert!(!should_report_error(&anyhow::anyhow!(
            SKIPPED_CURRENT_WORKDIR
        )));
        assert!(should_report_error(&anyhow::anyhow!("real parse failure")));
    }

    /// Verifies that `matched_snippet` correctly centres a window around a CJK
    /// query term without corrupting multi-byte scalar boundaries.
    #[test]
    fn matched_snippet_handles_cjk_query() {
        let text = "这是一段包含关键词测试内容的中文句子，用于验证多字节边界安全性。";
        let result = matched_snippet(text, "关键词", 20);
        assert!(result.is_some(), "expected a match for CJK query");
        let snippet = result.unwrap();
        assert!(
            snippet.contains("关键词"),
            "snippet should contain query: {snippet}"
        );
    }

    /// Verifies that `clip_snippet_by_chars` never panics when the window
    /// extends past the end of a string containing multi-byte characters.
    #[test]
    fn clip_snippet_by_chars_clamps_to_end() {
        let text = "短文本";
        let result = clip_snippet_by_chars(text, 1, 1, 100);
        assert_eq!(result, text);
    }

    #[test]
    fn clip_snippet_by_chars_fills_window_from_start() {
        // When match is near end, window should extend backwards to fill `limit`.
        let text: String = "abcdefghij".to_string(); // 10 chars
        let result = clip_snippet_by_chars(&text, 8, 1, 6);
        assert_eq!(result.len(), 6, "window should be exactly limit chars");
    }
}
