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
	"skill.md",
	"python -m pytest",
	"benchmarks/run.py",
	"<instructions>",
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
		var payload map[string]any
		if err := json.Unmarshal([]byte(line), &payload); err == nil {
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
		if summary.Snippet == "" && !matcher.QueryEmpty() {
			if snippet, ok := matcher.Match(line); ok {
				summary.Snippet = snippet
				matchFound = true
			}
		}
	}
	return summary, matchFound
}

type NoiseFilter struct {
	markers []string
}

func NewNoiseFilter(markers []string) *NoiseFilter {
	normalized := make([]string, 0, len(markers))
	for _, marker := range markers {
		marker = strings.ToLower(strings.TrimSpace(marker))
		if marker != "" {
			normalized = append(normalized, marker)
		}
	}
	return &NoiseFilter{markers: normalized}
}

func (f *NoiseFilter) IsNoise(line string) bool {
	if f == nil || len(f.markers) == 0 {
		return false
	}
	line = strings.ToLower(line)
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
	if !strings.Contains(lower, m.queryLower) || (m.filter != nil && m.filter.IsNoise(lower)) {
		return "", false
	}
	if m.snippetLimit > 0 && len(trimmed) > m.snippetLimit {
		return trimmed[:m.snippetLimit], true
	}
	return trimmed, true
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
	for _, key := range []string{"message", "display", "text", "input", "prompt", "output", "content"} {
		appendIfString(payload[key])
	}
	if nested, ok := payload["payload"].(map[string]any); ok {
		for _, key := range []string{"message", "display", "text", "input", "prompt", "output"} {
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
