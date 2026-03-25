package main

import (
	"bufio"
	"encoding/json"
	"os"
	"path/filepath"
	"sort"
	"strings"
)

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

var DefaultNoisePrefixes = []string{
	"##",
	"```",
	"> ",
	"- [",
	"* ",
	"http",
	"https",
}

const defaultSnippetLimit = 220

type SessionScanner struct {
	noiseFilter  *NoiseFilter
	snippetLimit int
}

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

func (s *SessionScanner) ProcessFile(item WorkItem, query string) (SessionSummary, bool) {
	file, err := os.Open(item.Path)
	if err != nil {
		return SessionSummary{}, false
	}
	defer file.Close()
	stat, err := file.Stat()
	if err != nil {
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

	scanner := bufio.NewScanner(file)
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" {
			continue
		}
		summary.Lines++
		parsedJSON := false
		var payload map[string]any
		if err := json.Unmarshal([]byte(line), &payload); err == nil {
			parsedJSON = true
			if sid := extractSessionID(payload); sid != "" {
				summary.SessionID = sid
			}
			if summary.Snippet == "" && !matcher.QueryEmpty() {
				for _, text := range extractTextCandidates(payload) {
					if snippet, ok := matcher.Match(text); ok {
						summary.Snippet = snippet
						matchFound = true
						break
					}
				}
			}
		}
		if summary.Snippet == "" && !matcher.QueryEmpty() && !parsedJSON {
			if snippet, ok := matcher.Match(line); ok {
				summary.Snippet = snippet
				matchFound = true
			}
		}
	}
	return summary, matchFound
}

type NoiseFilter struct {
	markers  []string
	prefixes []string
}

func NewNoiseFilter(markers []string) *NoiseFilter {
	normalized := make([]string, 0, len(markers))
	for _, marker := range markers {
		marker = strings.ToLower(strings.TrimSpace(marker))
		if marker != "" {
			normalized = append(normalized, marker)
		}
	}
	prefixes := make([]string, 0, len(DefaultNoisePrefixes))
	for _, prefix := range DefaultNoisePrefixes {
		prefix = strings.ToLower(strings.TrimSpace(prefix))
		if prefix != "" {
			prefixes = append(prefixes, prefix)
		}
	}
	return &NoiseFilter{markers: normalized, prefixes: prefixes}
}

func (f *NoiseFilter) IsNoise(line string) bool {
	if f == nil {
		return false
	}
	line = strings.ToLower(strings.TrimSpace(line))
	for _, prefix := range f.prefixes {
		if prefix != "" && strings.HasPrefix(line, prefix) {
			return true
		}
	}
	for _, marker := range f.markers {
		if marker != "" && strings.Contains(line, marker) {
			return true
		}
	}
	return false
}

type SnippetMatcher struct {
	queryLower   string
	filter       *NoiseFilter
	snippetLimit int
}

func NewSnippetMatcher(query string, filter *NoiseFilter, limit int) *SnippetMatcher {
	return &SnippetMatcher{
		queryLower:   strings.ToLower(strings.TrimSpace(query)),
		filter:       filter,
		snippetLimit: limit,
	}
}

func (m *SnippetMatcher) QueryEmpty() bool {
	return m == nil || m.queryLower == ""
}

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

func clipSnippet(text string, index int, queryLen int, limit int) string {
	if limit <= 0 || len(text) <= limit {
		return text
	}
	if queryLen < 0 {
		queryLen = 0
	}
	radius := limit / 2
	start := index - radius
	if start < 0 {
		start = 0
	}
	end := start + limit
	if end > len(text) {
		end = len(text)
		start = max(0, end-limit)
	}
	return text[start:end]
}

func max(a int, b int) int {
	if a > b {
		return a
	}
	return b
}

func extractTextCandidates(payload map[string]any) []string {
	out := make([]string, 0, 8)
	appendIfString := func(value any) {
		if text, ok := value.(string); ok {
			text = strings.TrimSpace(text)
			if text != "" {
				out = append(out, text)
			}
		}
	}
	for _, key := range []string{"message", "display", "text", "prompt", "output", "content"} {
		appendIfString(payload[key])
	}
	if nested, ok := payload["payload"].(map[string]any); ok {
		for _, key := range []string{"message", "display", "text", "prompt", "output", "user_instructions", "last_agent_message"} {
			appendIfString(nested[key])
		}
		if content, ok := nested["content"].([]any); ok {
			for _, item := range content {
				if m, ok := item.(map[string]any); ok {
					appendIfString(m["text"])
				}
			}
		}
	}
	if message, ok := payload["message"].(map[string]any); ok {
		appendIfString(message["content"])
		if content, ok := message["content"].([]any); ok {
			for _, item := range content {
				if m, ok := item.(map[string]any); ok {
					appendIfString(m["text"])
				}
			}
		}
	}
	appendIfString(payload["user_instructions"])
	appendIfString(payload["last_agent_message"])
	return out
}

func extractSessionID(payload map[string]any) string {
	if sessionID, ok := payload["sessionId"].(string); ok && sessionID != "" {
		return sessionID
	}
	if nested, ok := payload["payload"].(map[string]any); ok {
		if id, ok := nested["id"].(string); ok && id != "" {
			return id
		}
	}
	return ""
}

type Aggregate struct {
	Source     string
	Count      int
	TotalLines int
	TotalSize  int64
}

func summarize(results []SessionSummary) []Aggregate {
	m := map[string]*Aggregate{}
	for _, result := range results {
		agg, ok := m[result.Source]
		if !ok {
			agg = &Aggregate{Source: result.Source}
			m[result.Source] = agg
		}
		agg.Count++
		agg.TotalLines += result.Lines
		agg.TotalSize += result.SizeBytes
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

func (o ScanOutput) Aggregates() []Aggregate {
	return summarize(o.Matches)
}
