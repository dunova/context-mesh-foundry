package main

import (
	"bufio"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"unicode/utf8"
)

// DefaultNoiseMarkers is the set of substrings that identify a text fragment
// as noise.  Each entry is compared case-insensitively against the candidate.
// Auto-generated from config/noise_markers.json — do not edit manually.
// Run scripts/check_noise_sync.py to verify sync with Python/Rust backends.
var DefaultNoiseMarkers = []string{
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
	"我先做\"全局一致性同步\"检查",
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
	"现在不是\"能不能跑\"的问题",
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
}

// DefaultNoisePrefixes contains line prefixes that identify a fragment as
// noise.  Each entry is compared case-insensitively against the candidate.
// Auto-generated from config/noise_markers.json — do not edit manually.
var DefaultNoisePrefixes = []string{
	"##",
	"```",
	"> ",
	"- [",
	"* ",
	"http",
	"https",
}

// defaultSnippetLimit is the maximum character length of an extracted snippet.
const defaultSnippetLimit = 180

// SessionScanner processes individual session files and extracts summaries.
type SessionScanner struct {
	noiseFilter  *NoiseFilter
	snippetLimit int
}

// TextCandidate holds a text fragment alongside the JSON field path it came from.
type TextCandidate struct {
	Field string
	Text  string
}

// NewSessionScanner creates a SessionScanner.  Nil filter falls back to
// DefaultNoiseMarkers; non-positive snippetLimit falls back to defaultSnippetLimit.
func NewSessionScanner(filter *NoiseFilter, snippetLimit int) *SessionScanner {
	if filter == nil {
		filter = NewNoiseFilter(DefaultNoiseMarkers)
	}
	if snippetLimit <= 0 {
		snippetLimit = defaultSnippetLimit
	}
	return &SessionScanner{
		noiseFilter:  filter,
		snippetLimit: snippetLimit,
	}
}

// ProcessFile reads item.Path, applies query matching, and returns a populated
// SessionSummary.  The boolean return is false when the file should be excluded
// (no match, or belongs to the active working directory session).
func (s *SessionScanner) ProcessFile(item WorkItem, query string) (SessionSummary, bool) {
	file, err := os.Open(item.Path)
	if err != nil {
		fmt.Fprintf(os.Stderr, "open %s: %v\n", item.Path, err)
		return SessionSummary{}, false
	}
	defer file.Close()

	stat, err := file.Stat()
	if err != nil {
		fmt.Fprintf(os.Stderr, "stat %s: %v\n", item.Path, err)
		return SessionSummary{}, false
	}

	summary := SessionSummary{
		Source:    item.Source,
		Path:      item.Path,
		SessionID: strings.TrimSuffix(filepath.Base(item.Path), filepath.Ext(item.Path)),
		SizeBytes: stat.Size(),
	}

	matcher := NewSnippetMatcher(query, s.noiseFilter, s.snippetLimit)
	matchFound := matcher.QueryEmpty()
	currentWorkdir := normalizedCurrentWorkdir()
	sessionCwd := ""

	sc := bufio.NewScanner(file)
	sc.Buffer(make([]byte, 0, 1024*1024), 32*1024*1024)
	for sc.Scan() {
		line := strings.TrimSpace(sc.Text())
		if line == "" {
			continue
		}
		summary.Lines++

		var payload map[string]any
		if err := json.Unmarshal([]byte(line), &payload); err == nil {
			if shouldSkipRecordType(payload) {
				continue
			}
			if sid := extractSessionID(payload); sid != "" {
				summary.SessionID = sid
			}
			if cwd := extractCwd(payload); cwd != "" {
				if sessionCwd == "" {
					sessionCwd = cwd
				}
				if !matcher.QueryEmpty() && currentWorkdir != "" && normalizePath(cwd) == currentWorkdir {
					return SessionSummary{}, false
				}
			}
			if ts := extractTimestamp(payload); ts != "" {
				if summary.FirstTimestamp == "" {
					summary.FirstTimestamp = ts
				}
				summary.LastTimestamp = ts
			}
			if !matcher.QueryEmpty() {
				for _, candidate := range extractTextCandidates(payload) {
					if snippet, ok := matcher.Match(candidate.Text); ok {
						score := candidateScore(candidate.Field, candidate.Text, matcher.queryLower)
						if score > summary.MatchScore {
							summary.Snippet = snippet
							summary.MatchField = candidate.Field
							summary.MatchScore = score
						}
						matchFound = true
					}
				}
			}
		} else if !matcher.QueryEmpty() {
			if snippet, ok := matcher.Match(line); ok {
				score := candidateScore("raw_line", line, matcher.queryLower)
				if score > summary.MatchScore {
					summary.Snippet = snippet
					summary.MatchField = "raw_line"
					summary.MatchScore = score
				}
				matchFound = true
			}
		}
	}
	if err := sc.Err(); err != nil {
		fmt.Fprintf(os.Stderr, "scan %s: %v\n", item.Path, err)
	}
	return summary, matchFound
}

// shouldSkipRecordType returns true for record types that contain no useful
// user-visible text (tool call outputs, token counts, etc.).
func shouldSkipRecordType(payload map[string]any) bool {
	topType, _ := payload["type"].(string)
	switch topType {
	case "turn_context", "custom_tool_call":
		return true
	case "response_item":
		if nested, ok := payload["payload"].(map[string]any); ok {
			pt, _ := nested["type"].(string)
			switch pt {
			case "function_call_output", "function_call", "reasoning":
				return true
			}
		}
	case "event_msg":
		if nested, ok := payload["payload"].(map[string]any); ok {
			pt, _ := nested["type"].(string)
			switch pt {
			case "token_count", "task_started":
				return true
			}
		}
	}
	return false
}

// NoiseFilter decides whether a text fragment should be excluded from results.
type NoiseFilter struct {
	markers  []string
	prefixes []string
}

// NewNoiseFilter builds a NoiseFilter from the given marker list.
// All markers and DefaultNoisePrefixes are normalised to lowercase.
func NewNoiseFilter(markers []string) *NoiseFilter {
	normalized := make([]string, 0, len(markers))
	for _, m := range markers {
		m = strings.ToLower(strings.TrimSpace(m))
		if m != "" {
			normalized = append(normalized, m)
		}
	}
	prefixes := make([]string, 0, len(DefaultNoisePrefixes))
	for _, p := range DefaultNoisePrefixes {
		p = strings.ToLower(strings.TrimSpace(p))
		if p != "" {
			prefixes = append(prefixes, p)
		}
	}
	return &NoiseFilter{markers: normalized, prefixes: prefixes}
}

// IsNoise reports whether line (already lower-cased) matches any noise pattern.
func (f *NoiseFilter) IsNoise(line string) bool {
	if f == nil {
		return false
	}
	line = strings.ToLower(strings.TrimSpace(line))
	for _, p := range f.prefixes {
		if p != "" && strings.HasPrefix(line, p) {
			return true
		}
	}
	for _, m := range f.markers {
		if m != "" && strings.Contains(line, m) {
			return true
		}
	}

	// Heuristic: many short spaceless tokens suggest a directory listing or
	// skill manifest.
	shortTokens := 0
	for _, part := range strings.Split(line, "\n") {
		part = strings.TrimSpace(part)
		if part == "" {
			continue
		}
		if len(part) <= 40 && !strings.Contains(part, " ") &&
			strings.Count(part, "/") < 2 && strings.Count(part, "-") <= 3 {
			shortTokens++
		}
	}
	if shortTokens >= 5 {
		return true
	}
	if strings.Contains(line, "drwx") ||
		strings.Contains(line, "rwxr-xr-x") ||
		strings.Contains(line, "\ntotal ") {
		return true
	}
	if strings.Contains(line, "notebooklm") &&
		strings.Contains(line, "search") &&
		strings.Contains(line, "session_index") &&
		strings.Contains(line, "native-scan") {
		return true
	}
	if (strings.Contains(line, "我先") || strings.Contains(line, "我继续")) &&
		(strings.Contains(line, "search") ||
			strings.Contains(line, "native-scan") ||
			strings.Contains(line, "session_index")) {
		return true
	}
	return false
}

// SnippetMatcher extracts and validates query-matching snippets from text.
type SnippetMatcher struct {
	queryLower   string
	filter       *NoiseFilter
	snippetLimit int
}

// NewSnippetMatcher creates a SnippetMatcher for the given query string.
func NewSnippetMatcher(query string, filter *NoiseFilter, limit int) *SnippetMatcher {
	return &SnippetMatcher{
		queryLower:   strings.ToLower(strings.TrimSpace(query)),
		filter:       filter,
		snippetLimit: limit,
	}
}

// QueryEmpty reports whether the query is empty, meaning all files match.
func (m *SnippetMatcher) QueryEmpty() bool {
	return m == nil || m.queryLower == ""
}

// Match returns the best snippet from text centred on the first query
// occurrence, and true when the text is a non-noise match.
func (m *SnippetMatcher) Match(text string) (string, bool) {
	if m == nil || m.QueryEmpty() {
		return "", false
	}
	trimmed := strings.TrimSpace(text)
	if trimmed == "" {
		return "", false
	}
	lower := strings.ToLower(trimmed)
	idx := strings.Index(lower, m.queryLower)
	if idx < 0 {
		return "", false
	}
	snippet := trimmed
	if m.snippetLimit > 0 {
		snippet = clipSnippet(trimmed, idx, len(m.queryLower), m.snippetLimit)
	}
	if m.filter != nil && m.filter.IsNoise(strings.ToLower(snippet)) {
		return "", false
	}
	return snippet, true
}

// fieldPriority returns a base score for a JSON field path.  Higher-priority
// fields carry user-visible content; lower-priority fields carry meta text.
func fieldPriority(field string) int {
	switch field {
	case "message.content.text", "payload.content.text":
		return 120
	case "message", "message.content", "payload.message", "root.text", "payload.text":
		return 100
	case "root.content", "root.display", "payload.display",
		"root.last_agent_message", "payload.last_agent_message":
		return 70
	case "root.prompt", "payload.prompt",
		"root.user_instructions", "payload.user_instructions":
		return 20
	case "raw_line":
		return 10
	default:
		return 40
	}
}

// candidateScore computes a match quality score combining field priority and
// hit frequency.
func candidateScore(field, text, queryLower string) int {
	hits := 0
	if queryLower != "" {
		hits = strings.Count(strings.ToLower(text), queryLower)
	}
	return fieldPriority(field) + hits*25
}

// clipSnippet returns a substring of text of at most limit runes, centred on
// the match at byte position index.  It is rune-safe: all slicing is performed
// on the []rune representation so that multi-byte UTF-8 characters (including
// CJK codepoints) are never split.
//
// index is the byte offset of the query match within text (as returned by
// strings.Index).  queryLen is the byte length of the query term (used only to
// keep the result non-negative; pass 0 if unknown).  limit is the maximum
// number of runes in the returned string.
func clipSnippet(text string, index, queryLen, limit int) string {
	if limit <= 0 {
		return text
	}
	runes := []rune(text)
	total := len(runes)
	if total <= limit {
		return text
	}
	if queryLen < 0 {
		queryLen = 0
	}

	// Convert the byte offset 'index' to a rune index.  We iterate over rune
	// start positions (as produced by range-over-string) and count how many
	// runes precede byte offset 'index'.  This is O(n) but avoids allocating
	// a second string just for index arithmetic.
	runeIdx := 0
	for bytePos := range text {
		if bytePos >= index {
			break
		}
		runeIdx++
	}

	// Centre the window on the match.
	radius := limit / 2
	start := runeIdx - radius
	if start < 0 {
		start = 0
	}
	end := start + limit
	if end > total {
		end = total
		start = end - limit
		if start < 0 {
			start = 0
		}
	}
	return string(runes[start:end])
}

// extractTextCandidates returns all non-empty text fields from a parsed JSON
// record, labelled with their field path.
func extractTextCandidates(payload map[string]any) []TextCandidate {
	out := make([]TextCandidate, 0, 8)
	appendStr := func(field string, value any) {
		if text, ok := value.(string); ok {
			if text = strings.TrimSpace(text); text != "" {
				out = append(out, TextCandidate{Field: field, Text: text})
			}
		}
	}

	rootFields := []struct{ field, key string }{
		{"message", "message"},
		{"root.display", "display"},
		{"root.text", "text"},
		{"root.prompt", "prompt"},
		{"root.output", "output"},
		{"root.content", "content"},
	}
	for _, f := range rootFields {
		appendStr(f.field, payload[f.key])
	}

	if nested, ok := payload["payload"].(map[string]any); ok {
		payloadFields := []struct{ field, key string }{
			{"payload.message", "message"},
			{"payload.display", "display"},
			{"payload.text", "text"},
			{"payload.prompt", "prompt"},
			{"payload.output", "output"},
			{"payload.user_instructions", "user_instructions"},
			{"payload.last_agent_message", "last_agent_message"},
		}
		for _, f := range payloadFields {
			appendStr(f.field, nested[f.key])
		}
		if items, ok := nested["content"].([]any); ok {
			for _, item := range items {
				if m, ok := item.(map[string]any); ok {
					appendStr("payload.content.text", m["text"])
				}
			}
		}
	}

	if message, ok := payload["message"].(map[string]any); ok {
		appendStr("message.content", message["content"])
		if items, ok := message["content"].([]any); ok {
			for _, item := range items {
				if m, ok := item.(map[string]any); ok {
					appendStr("message.content.text", m["text"])
				}
			}
		}
	}

	appendStr("root.user_instructions", payload["user_instructions"])
	appendStr("root.last_agent_message", payload["last_agent_message"])
	return out
}

// extractSessionID returns the session identifier from a parsed JSON record,
// checking payload.id and root sessionId fields.
func extractSessionID(payload map[string]any) string {
	if nested, ok := payload["payload"].(map[string]any); ok {
		if id, ok := nested["id"].(string); ok && id != "" {
			return id
		}
	}
	if id, ok := payload["sessionId"].(string); ok && id != "" {
		return id
	}
	if id, ok := payload["session_id"].(string); ok && id != "" {
		return id
	}
	return ""
}

// extractTimestamp returns the most relevant timestamp from a parsed JSON
// record.  It prefers payload.timestamp, then falls back to root-level keys.
func extractTimestamp(payload map[string]any) string {
	if nested, ok := payload["payload"].(map[string]any); ok {
		if ts, ok := nested["timestamp"].(string); ok && ts != "" {
			return ts
		}
	}
	for _, key := range []string{"createdAt", "created_at", "timestamp", "time"} {
		if ts, ok := payload[key].(string); ok && ts != "" {
			return ts
		}
	}
	return ""
}

// extractCwd returns the working directory recorded in a parsed JSON record.
func extractCwd(payload map[string]any) string {
	if nested, ok := payload["payload"].(map[string]any); ok {
		if cwd, ok := nested["cwd"].(string); ok && cwd != "" {
			return cwd
		}
	}
	if cwd, ok := payload["cwd"].(string); ok && cwd != "" {
		return cwd
	}
	return ""
}

// normalizedCurrentWorkdir returns the canonical path of the active working
// directory.  It honours CONTEXTGO_ACTIVE_WORKDIR when set.
func normalizedCurrentWorkdir() string {
	if explicit := strings.TrimSpace(os.Getenv("CONTEXTGO_ACTIVE_WORKDIR")); explicit != "" {
		return normalizePath(explicit)
	}
	cwd, err := os.Getwd()
	if err != nil {
		return ""
	}
	return normalizePath(cwd)
}

// normalizePath resolves symlinks and returns an absolute path.
func normalizePath(path string) string {
	if path == "" {
		return ""
	}
	if resolved, err := filepath.EvalSymlinks(path); err == nil {
		path = resolved
	}
	if abs, err := filepath.Abs(path); err == nil {
		return abs
	}
	return path
}

// Aggregate holds per-source statistics computed from a result set.
type Aggregate struct {
	Source     string
	Count      int
	TotalLines int
	TotalSize  int64
}

// summarize groups results by source and computes aggregate statistics.
func summarize(results []SessionSummary) []Aggregate {
	m := make(map[string]*Aggregate, 4)
	for i := range results {
		r := &results[i]
		agg, ok := m[r.Source]
		if !ok {
			agg = &Aggregate{Source: r.Source}
			m[r.Source] = agg
		}
		agg.Count++
		agg.TotalLines += r.Lines
		agg.TotalSize += r.SizeBytes
	}
	out := make([]Aggregate, 0, len(m))
	for _, agg := range m {
		out = append(out, *agg)
	}
	sort.Slice(out, func(i, j int) bool {
		return out[i].Source < out[j].Source
	})
	return out
}

