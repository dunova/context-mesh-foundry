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
	"native 结果质量现状",
	"native 搜索结果质量",
	"no matches found in local session index.",
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

type TextCandidate struct {
	Field string
	Text  string
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
	currentWorkdir := normalizedCurrentWorkdir()
	sessionCwd := ""

	scanner := bufio.NewScanner(file)
	scanner.Buffer(make([]byte, 0, 1024*1024), 32*1024*1024)
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
		}
		if !matcher.QueryEmpty() && !parsedJSON {
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
	return summary, matchFound
}

func shouldSkipRecordType(payload map[string]any) bool {
	topLevelType, _ := payload["type"].(string)
	if topLevelType == "turn_context" || topLevelType == "custom_tool_call" {
		return true
	}
	if topLevelType == "response_item" {
		if nested, ok := payload["payload"].(map[string]any); ok {
			payloadType, _ := nested["type"].(string)
			if payloadType == "function_call_output" || payloadType == "function_call" || payloadType == "reasoning" {
				return true
			}
		}
	}
	if topLevelType == "event_msg" {
		if nested, ok := payload["payload"].(map[string]any); ok {
			payloadType, _ := nested["type"].(string)
			if payloadType == "token_count" || payloadType == "task_started" {
				return true
			}
		}
	}
	return false
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
	lines := strings.Split(line, "\n")
	shortTokenLines := 0
	for _, item := range lines {
		item = strings.TrimSpace(item)
		if item == "" {
			continue
		}
		if len(item) <= 40 && !strings.Contains(item, " ") && strings.Count(item, "/") < 2 && strings.Count(item, "-") <= 3 {
			shortTokenLines++
		}
	}
	if shortTokenLines >= 5 {
		return true
	}
	if strings.Contains(line, "drwx") || strings.Contains(line, "rwxr-xr-x") || strings.Contains(line, "\ntotal ") {
		return true
	}
	if strings.Contains(line, "notebooklm") &&
		strings.Contains(line, "search") &&
		strings.Contains(line, "session_index") &&
		strings.Contains(line, "native-scan") {
		return true
	}
	if (strings.Contains(line, "我先") || strings.Contains(line, "我继续")) &&
		(strings.Contains(line, "search") || strings.Contains(line, "native-scan") || strings.Contains(line, "session_index")) {
		return true
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

func fieldPriority(field string) int {
	switch field {
	case "message.content.text", "payload.content.text":
		return 120
	case "message", "message.content", "payload.message", "root.text", "payload.text":
		return 100
	case "root.content", "root.display", "payload.display", "root.last_agent_message", "payload.last_agent_message":
		return 70
	case "root.prompt", "payload.prompt", "root.user_instructions", "payload.user_instructions":
		return 20
	case "raw_line":
		return 10
	default:
		return 40
	}
}

func candidateScore(field string, text string, queryLower string) int {
	hits := 0
	if queryLower != "" {
		hits = strings.Count(strings.ToLower(text), queryLower)
	}
	return fieldPriority(field) + hits*25
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

func extractTextCandidates(payload map[string]any) []TextCandidate {
	out := make([]TextCandidate, 0, 8)
	appendIfString := func(field string, value any) {
		if text, ok := value.(string); ok {
			text = strings.TrimSpace(text)
			if text != "" {
				out = append(out, TextCandidate{Field: field, Text: text})
			}
		}
	}
	for _, item := range []struct {
		Field string
		Key   string
	}{
		{Field: "message", Key: "message"},
		{Field: "root.display", Key: "display"},
		{Field: "root.text", Key: "text"},
		{Field: "root.prompt", Key: "prompt"},
		{Field: "root.output", Key: "output"},
		{Field: "root.content", Key: "content"},
	} {
		appendIfString(item.Field, payload[item.Key])
	}
	if nested, ok := payload["payload"].(map[string]any); ok {
		for _, item := range []struct {
			Field string
			Key   string
		}{
			{Field: "payload.message", Key: "message"},
			{Field: "payload.display", Key: "display"},
			{Field: "payload.text", Key: "text"},
			{Field: "payload.prompt", Key: "prompt"},
			{Field: "payload.output", Key: "output"},
			{Field: "payload.user_instructions", Key: "user_instructions"},
			{Field: "payload.last_agent_message", Key: "last_agent_message"},
		} {
			appendIfString(item.Field, nested[item.Key])
		}
		if content, ok := nested["content"].([]any); ok {
			for _, item := range content {
				if m, ok := item.(map[string]any); ok {
					appendIfString("payload.content.text", m["text"])
				}
			}
		}
	}
	if message, ok := payload["message"].(map[string]any); ok {
		appendIfString("message.content", message["content"])
		if content, ok := message["content"].([]any); ok {
			for _, item := range content {
				if m, ok := item.(map[string]any); ok {
					appendIfString("message.content.text", m["text"])
				}
			}
		}
	}
	appendIfString("root.user_instructions", payload["user_instructions"])
	appendIfString("root.last_agent_message", payload["last_agent_message"])
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

func normalizedCurrentWorkdir() string {
	cwd, err := os.Getwd()
	if err != nil {
		return ""
	}
	return normalizePath(cwd)
}

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
