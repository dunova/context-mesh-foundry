package main

import (
	"bufio"
	"bytes"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
)

// mmapThreshold is the minimum file size in bytes for using memory-mapped I/O.
// Files smaller than this are read with a pooled bufio.Scanner instead.
const mmapThreshold = 1024 * 1024 // 1 MB

// scannerBufSize is the fixed buffer size used for bufio.Scanner.  It must be
// >= the max token size (32 MiB) so that Scanner never needs to grow the
// buffer, which would invalidate the pool aliasing assumption.
const scannerBufSize = 32 * 1024 * 1024

// scannerBufPool pools the large scanner buffers used in ProcessFile to reduce
// GC pressure when many files are processed in parallel.
var scannerBufPool = sync.Pool{
	New: func() any {
		b := make([]byte, scannerBufSize)
		return &b
	},
}

// runeSlicePool pools []rune scratch slices used in clipRuneWindow / clipSnippet.
// Slices are reset to length 0 before being put back so that they can be grown
// as needed by the next caller.
var runeSlicePool = sync.Pool{
	New: func() any {
		s := make([]rune, 0, 512)
		return &s
	},
}

// Note: bufio.Scanner cannot be Reset to a new reader (no Reset method in Go stdlib),
// so we create a new Scanner per call but reuse the underlying byte buffer via
// scannerBufPool.  This avoids the dominant allocation (the 1MB scan buffer).

// DefaultNoiseMarkers is the set of substrings that identify a text fragment
// as noise.  Each entry is compared case-insensitively against the candidate.
// Kept in sync with config/noise_markers.json.
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
}

// DefaultNoisePrefixes contains line prefixes that identify a fragment as
// noise.  Each entry is compared case-insensitively against the candidate.
// Kept in sync with config/noise_markers.json.
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

// mmapFile is defined in mmap_unix.go / mmap_windows.go via build tags.

// processLines iterates over newline-delimited lines in data (a []byte) and
// calls fn for each non-empty, trimmed line.  Avoids string conversion of the
// whole buffer.
func processLines(data []byte, fn func(line []byte)) {
	for len(data) > 0 {
		idx := bytes.IndexByte(data, '\n')
		var line []byte
		if idx < 0 {
			line = data
			data = data[len(data):]
		} else {
			line = data[:idx]
			data = data[idx+1:]
		}
		// Trim CR for Windows-style line endings.
		if len(line) > 0 && line[len(line)-1] == '\r' {
			line = line[:len(line)-1]
		}
		// Inline trim of leading/trailing spaces without allocation.
		start := 0
		for start < len(line) && (line[start] == ' ' || line[start] == '\t') {
			start++
		}
		end := len(line)
		for end > start && (line[end-1] == ' ' || line[end-1] == '\t') {
			end--
		}
		line = line[start:end]
		if len(line) > 0 {
			fn(line)
		}
	}
}

// ProcessFile reads item.Path, applies query matching, and returns a populated
// SessionSummary.  The boolean return is false when the file should be excluded
// (no match, or belongs to the active working directory session).
func (s *SessionScanner) ProcessFile(item WorkItem, query string) (SessionSummary, bool) {
	file, err := os.Open(item.Path)
	if err != nil {
		if !os.IsPermission(err) {
			fmt.Fprintf(os.Stderr, "open %s: %v\n", item.Path, err)
		}
		return SessionSummary{}, false
	}
	defer file.Close()

	stat, err := file.Stat()
	if err != nil {
		fmt.Fprintf(os.Stderr, "stat %s: %v\n", item.Path, err)
		return SessionSummary{}, false
	}

	fileSize := stat.Size()

	summary := SessionSummary{
		Source:    item.Source,
		Path:      item.Path,
		SessionID: strings.TrimSuffix(filepath.Base(item.Path), filepath.Ext(item.Path)),
		SizeBytes: fileSize,
	}

	queryLower := strings.ToLower(strings.TrimSpace(query))
	// Pre-compute []byte form of query for faster binary search on raw lines.
	queryBytes := []byte(queryLower)
	matcher := NewSnippetMatcher(queryLower, s.noiseFilter, s.snippetLimit)
	matchFound := matcher.QueryEmpty()
	currentWorkdir := normalizedCurrentWorkdir()
	sessionCwd := ""

	// Estimate initial candidate capacity from file size: assume ~200 bytes per line
	// and ~10% of lines carry meaningful text fields.
	// Cap the intermediate int64 value before converting to int to prevent
	// overflow on 32-bit platforms where int is 32 bits.
	estLinesI64 := fileSize/200 + 1
	if estLinesI64 > 1024 {
		estLinesI64 = 1024
	}
	estLines := int(estLinesI64)

	// processOneLine handles one raw line (as []byte).  Using a closure keeps
	// the hot path inlined without repeating the outer variable captures.
	// Returns (continueProcessing bool).
	processOneLine := func(lineBytes []byte) bool {
		// Try to detect binary content: if line contains a NUL byte it is
		// almost certainly binary — skip the whole file by signalling stop.
		if bytes.IndexByte(lineBytes, 0) >= 0 {
			return false // binary file indicator
		}

		summary.Lines++

		// Fast-path: if the query is non-empty and the raw line does not
		// contain the query bytes at all (case-insensitive), skip JSON parsing
		// entirely.  containsFoldASCII is used for ASCII queries; for
		// non-ASCII queries we fall back to bytes.Contains on the lowercased
		// copy only when needed.
		queryMatches := matcher.QueryEmpty()
		if !queryMatches {
			if isASCII(queryBytes) {
				queryMatches = containsFoldASCII(lineBytes, queryBytes)
			} else {
				lineLower := bytes.ToLower(lineBytes)
				queryMatches = bytes.Contains(lineLower, queryBytes)
			}
		}

		var payload map[string]any
		if err := json.Unmarshal(lineBytes, &payload); err == nil {
			if shouldSkipRecordType(payload) {
				return true
			}
			if sid := extractSessionID(payload); sid != "" {
				summary.SessionID = sid
			}
			if cwd := extractCwd(payload); cwd != "" {
				if sessionCwd == "" {
					sessionCwd = cwd
				}
				if !matcher.QueryEmpty() && currentWorkdir != "" && normalizePath(cwd) == currentWorkdir {
					return false // signal: skip this file
				}
			}
			if ts := extractTimestamp(payload); ts != "" {
				if summary.FirstTimestamp == "" {
					summary.FirstTimestamp = ts
				}
				summary.LastTimestamp = ts
			}
			if !matcher.QueryEmpty() && queryMatches {
				// Pre-allocate candidates slice based on estimated capacity.
				candidates := extractTextCandidatesWithCap(payload, estLines/10+4)
				for _, candidate := range candidates {
					if snippet, ok := matcher.Match(candidate.Text); ok {
						score := candidateScore(candidate.Field, candidate.Text, queryLower)
						if score > summary.MatchScore {
							summary.Snippet = snippet
							summary.MatchField = candidate.Field
							summary.MatchScore = score
						}
						matchFound = true
					}
				}
			}
		} else if !matcher.QueryEmpty() && queryMatches {
			// Raw (non-JSON) line: convert to string only when needed for
			// snippet extraction (avoids allocation on every JSON line).
			lineStr := string(lineBytes)
			if snippet, ok := matcher.Match(lineStr); ok {
				score := candidateScore("raw_line", lineStr, queryLower)
				if score > summary.MatchScore {
					summary.Snippet = snippet
					summary.MatchField = "raw_line"
					summary.MatchScore = score
				}
				matchFound = true
			}
		}
		return true
	}

	// Choose I/O strategy based on file size.
	if fileSize >= mmapThreshold {
		// Large file: use memory-mapped I/O to avoid a user-space copy.
		data, unmap, mmapErr := mmapFile(file, fileSize)
		if mmapErr == nil && data != nil {
			defer unmap()
			abort := false
			processLines(data, func(line []byte) {
				if abort {
					return
				}
				if !processOneLine(line) {
					abort = true
				}
			})
			if abort {
				// Either binary file or active-workdir match — exclude.
				return SessionSummary{}, false
			}
		} else {
			// mmap failed: fall back to buffered reading.
			if _, seekErr := file.Seek(0, 0); seekErr != nil {
				fmt.Fprintf(os.Stderr, "seek %s: %v\n", item.Path, seekErr)
				return SessionSummary{}, false
			}
			if skip := s.processWithScanner(file, item.Path, processOneLine); skip {
				return SessionSummary{}, false
			}
		}
	} else {
		// Small file: use pooled bufio.Scanner.
		if skip := s.processWithScanner(file, item.Path, processOneLine); skip {
			return SessionSummary{}, false
		}
	}

	return summary, matchFound
}

// processWithScanner reads lines from f using a bufio.Scanner backed by a
// pooled byte buffer and calls fn for each non-empty line.  Returns true if
// processing should be aborted (fn returned false, indicating the file should
// be excluded).
func (s *SessionScanner) processWithScanner(f *os.File, path string, fn func([]byte) bool) (abort bool) {
	// Borrow a reusable buffer from the pool to avoid the dominant large
	// allocation (32 MiB scan buffer) on every call.
	// The buffer is pre-allocated at scannerBufSize (>= maxTokenSize) so that
	// bufio.Scanner never needs to grow it; this prevents pool aliasing bugs
	// where Scanner's internal growth would make our stored slice header stale.
	bufPtr := scannerBufPool.Get().(*[]byte)
	buf := *bufPtr
	if cap(buf) < scannerBufSize {
		// Should not happen given the New func above, but guard defensively.
		buf = make([]byte, scannerBufSize)
	}

	// bufio.Scanner has no Reset method; create a new instance backed by the
	// pooled buffer so only the small Scanner struct is allocated per call.
	// Pass buf[:0] (len=0, cap=scannerBufSize) so Scanner uses buf as-is and
	// never allocates a new backing array.
	sc := bufio.NewScanner(f)
	sc.Buffer(buf[:0], scannerBufSize)

	for sc.Scan() {
		raw := sc.Bytes()
		// Trim in-place without allocation.
		trimmed := bytes.TrimSpace(raw)
		if len(trimmed) == 0 {
			continue
		}
		// Make a copy: sc.Bytes() is only valid until the next Scan call.
		line := make([]byte, len(trimmed))
		copy(line, trimmed)
		if !fn(line) {
			abort = true
			break
		}
	}
	if err := sc.Err(); err != nil {
		fmt.Fprintf(os.Stderr, "scan %s: %v\n", path, err)
	}

	// Return the original buffer to the pool unchanged.  Because cap(buf) >=
	// scannerBufSize the Scanner never reallocated, so buf still points to the
	// same backing array we borrowed.
	*bufPtr = buf
	scannerBufPool.Put(bufPtr)
	return abort
}

// isASCII reports whether b contains only bytes in [0x00, 0x7F].
func isASCII(b []byte) bool {
	for _, c := range b {
		if c > 0x7F {
			return false
		}
	}
	return true
}

// containsFoldASCII reports whether haystack contains needle using
// ASCII-only case-folding (a-z / A-Z).  needle must already be lower-cased.
// This avoids a full strings.ToLower allocation on every line.
func containsFoldASCII(haystack, needle []byte) bool {
	if len(needle) == 0 {
		return true
	}
	if len(haystack) < len(needle) {
		return false
	}
	first := needle[0]
	firstUp := first
	if first >= 'a' && first <= 'z' {
		firstUp = first - 32
	}
	for i := 0; i <= len(haystack)-len(needle); i++ {
		c := haystack[i]
		if c != first && c != firstUp {
			continue
		}
		match := true
		for j := 1; j < len(needle); j++ {
			hc := haystack[i+j]
			nc := needle[j]
			ncUp := nc
			if nc >= 'a' && nc <= 'z' {
				ncUp = nc - 32
			}
			if hc != nc && hc != ncUp {
				match = false
				break
			}
		}
		if match {
			return true
		}
	}
	return false
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

// IsNoiseLower reports whether line (already lower-cased and trimmed) matches
// any noise pattern.  The caller is responsible for lowercasing before calling.
func (f *NoiseFilter) IsNoiseLower(line string) bool {
	if f == nil {
		return false
	}
	if line == "" {
		return true
	}
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
	// skill manifest.  Only split on "\n" when the line actually contains one
	// to avoid allocating a slice for the common single-line case.
	shortTokens := 0
	if strings.Contains(line, "\n") {
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
	} else {
		part := strings.TrimSpace(line)
		if part != "" && len(part) <= 40 && !strings.Contains(part, " ") &&
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

// IsNoise is a convenience wrapper that lower-cases and trims line before
// delegating to IsNoiseLower.
func (f *NoiseFilter) IsNoise(line string) bool {
	return f.IsNoiseLower(strings.ToLower(strings.TrimSpace(line)))
}

// SnippetMatcher extracts and validates query-matching snippets from text.
type SnippetMatcher struct {
	// queryLower is already lower-cased and trimmed.
	queryLower   string
	filter       *NoiseFilter
	snippetLimit int
}

// NewSnippetMatcher creates a SnippetMatcher for the given query string.
// queryLower must already be lower-cased and trimmed by the caller.
func NewSnippetMatcher(queryLower string, filter *NoiseFilter, limit int) *SnippetMatcher {
	return &SnippetMatcher{
		queryLower:   queryLower,
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
		// clipSnippet converts byte offset to rune index internally.  Use
		// lower as the source for offset conversion so the byte index idx
		// (which was obtained from lower via strings.Index) is always valid,
		// even when strings.ToLower changes the byte length of a character
		// (e.g. Turkish İ U+0130 → i).  The rune window computed from lower
		// is then applied to trimmed to preserve the original casing.
		runeStart, runeEnd := clipRuneWindow(lower, idx, len(m.queryLower), m.snippetLimit)
		// Use the pooled rune slice to avoid per-call heap allocation.
		rsPtr := runeSlicePool.Get().(*[]rune)
		rs := (*rsPtr)[:0]
		for _, r := range trimmed {
			rs = append(rs, r)
		}
		if runeEnd > len(rs) {
			runeEnd = len(rs)
		}
		if runeStart > runeEnd {
			runeStart = runeEnd
		}
		snippet = string(rs[runeStart:runeEnd])
		*rsPtr = rs[:0]
		runeSlicePool.Put(rsPtr)
	}
	// Re-use the already-lowercased snippet for the noise check.
	snippetLower := strings.ToLower(snippet)
	if m.filter != nil && m.filter.IsNoiseLower(snippetLower) {
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
// hit frequency.  queryLower must be pre-lowercased.
func candidateScore(field, text, queryLower string) int {
	hits := 0
	if queryLower != "" {
		hits = strings.Count(strings.ToLower(text), queryLower)
	}
	return fieldPriority(field) + hits*25
}

// clipRuneWindow computes the [start, end) rune index window of at most limit
// runes centred on the match at byte position index within text.  It is the
// building block used by both clipSnippet and SnippetMatcher.Match.
//
// index is the byte offset of the query match within text (as returned by
// strings.Index).  queryLen is the byte length of the query term.  Both must
// be non-negative; negative values are clamped to 0.
func clipRuneWindow(text string, index, queryLen, limit int) (start, end int) {
	if index < 0 {
		index = 0
	}
	if queryLen < 0 {
		queryLen = 0
	}

	// Count runes without allocating a []rune slice; just count rune-start bytes.
	total := 0
	for range text {
		total++
	}
	if total <= limit {
		return 0, total
	}

	// Convert the byte offset 'index' to a rune index by counting rune start
	// positions that precede byte offset 'index'.
	runeIdx := 0
	for bytePos := range text {
		if bytePos >= index {
			break
		}
		runeIdx++
	}

	// Centre the window on the match.
	radius := limit / 2
	start = runeIdx - radius
	if start < 0 {
		start = 0
	}
	end = start + limit
	if end > total {
		end = total
		// Extend backwards to fill the full window if possible.
		start = end - limit
		if start < 0 {
			start = 0
		}
	}
	return start, end
}

// clipSnippet returns a substring of text of at most limit runes, centred on
// the match at byte position index.  It is rune-safe: all slicing is performed
// on the []rune representation so that multi-byte UTF-8 characters (including
// CJK codepoints) are never split.
//
// index is the byte offset of the query match within text (as returned by
// strings.Index).  queryLen is the byte length of the query term.
// limit is the maximum number of runes in the returned string.
func clipSnippet(text string, index, queryLen, limit int) string {
	if limit <= 0 {
		return text
	}
	start, end := clipRuneWindow(text, index, queryLen, limit)
	if start == 0 {
		// Count total runes to decide whether trimming is needed.
		total := 0
		for range text {
			total++
		}
		if total <= limit {
			return text
		}
	}
	// Borrow a pooled []rune to avoid allocation on every call.
	rsPtr := runeSlicePool.Get().(*[]rune)
	rs := (*rsPtr)[:0]
	for _, r := range text {
		rs = append(rs, r)
	}
	if end > len(rs) {
		end = len(rs)
	}
	if start > end {
		start = end
	}
	result := string(rs[start:end])
	*rsPtr = rs[:0]
	runeSlicePool.Put(rsPtr)
	return result
}

// extractTextCandidates returns all non-empty text fields from a parsed JSON
// record, labelled with their field path.
func extractTextCandidates(payload map[string]any) []TextCandidate {
	return extractTextCandidatesWithCap(payload, 16)
}

// extractTextCandidatesWithCap is like extractTextCandidates but pre-allocates
// the output slice with the provided capacity hint.
func extractTextCandidatesWithCap(payload map[string]any, capHint int) []TextCandidate {
	if capHint < 4 {
		capHint = 4
	}
	out := make([]TextCandidate, 0, capHint)
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
