package main

import (
	"bufio"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"text/tabwriter"
	"time"
)

var noiseMarkers = []string{
	"# agents.md instructions",
	"### available skills",
	"prompt engineer and agent skill optimizer",
	"current skill name:",
	"skill.md",
	"python -m pytest",
	"benchmarks/run.py",
	"<instructions>",
}

const snippetLimit = 220

type WorkItem struct {
	Source string
	Path   string
}

type SessionSummary struct {
	Source    string `json:"source"`
	Path      string `json:"path"`
	SessionID string `json:"session_id"`
	Lines     int    `json:"lines"`
	SizeBytes int64  `json:"size_bytes"`
	Snippet   string `json:"snippet,omitempty"`
}

type ScanOutput struct {
	FilesScanned int              `json:"files_scanned"`
	Query        string           `json:"query,omitempty"`
	Matches      []SessionSummary `json:"matches"`
	Truncated    bool             `json:"truncated,omitempty"`
}

func main() {
	codexRoot := flag.String("codex-root", filepath.Join(os.Getenv("HOME"), ".codex", "sessions"), "Codex 会话根目录")
	claudeRoot := flag.String("claude-root", filepath.Join(os.Getenv("HOME"), ".claude", "projects"), "Claude 会话根目录")
	threads := flag.Int("threads", 4, "并发 worker 数")
	query := flag.String("query", "", "仅保留包含 query 的结果")
	limit := flag.Int("limit", 20, "最多输出结果数")
	jsonOutput := flag.Bool("json", false, "输出 JSON")
	flag.Parse()

	start := time.Now()
	work := collectFiles([]WorkItem{
		{Source: "codex_session", Path: *codexRoot},
		{Source: "claude_session", Path: *claudeRoot},
	})

	results, truncated := scan(work, *threads, *query, *limit)
	sort.Slice(results, func(i, j int) bool {
		if results[i].Source != results[j].Source {
			return results[i].Source < results[j].Source
		}
		return results[i].Path < results[j].Path
	})
	if *jsonOutput {
		payload := ScanOutput{
			FilesScanned: len(work),
			Query:        *query,
			Matches:      results,
			Truncated:    truncated,
		}
		raw, _ := json.MarshalIndent(payload, "", "  ")
		fmt.Println(string(raw))
		return
	}
	fmt.Printf("扫描完毕：%d 文件，匹配 %d 条，耗时 %s。\n", len(work), len(results), time.Since(start).Round(time.Millisecond))
	aggs := summarize(results)
	w := tabwriter.NewWriter(os.Stdout, 0, 0, 2, ' ', 0)
	fmt.Fprintln(w, "来源\t匹配数\t总行数\t总字节")
	for _, item := range aggs {
		fmt.Fprintf(w, "%s\t%d\t%d\t%d\n", item.Source, item.Count, item.TotalLines, item.TotalSize)
	}
	w.Flush()
	if truncated && *limit > 0 {
		fmt.Printf("结果已按 limit %d 截断，可能存在更多匹配\n", *limit)
	}
}

func collectFiles(roots []WorkItem) []WorkItem {
	items := make([]WorkItem, 0)
	for _, root := range roots {
		if _, err := os.Stat(root.Path); err != nil {
			continue
		}
		_ = filepath.Walk(root.Path, func(path string, info os.FileInfo, err error) error {
			if err != nil || info == nil || info.IsDir() {
				return nil
			}
			ext := strings.ToLower(filepath.Ext(path))
			if ext == ".jsonl" || ext == ".json" {
				items = append(items, WorkItem{Source: root.Source, Path: path})
			}
			return nil
		})
	}
	return items
}

func scan(items []WorkItem, threads int, query string, limit int) ([]SessionSummary, bool) {
	if threads < 1 {
		threads = 1
	}
	workCh := make(chan WorkItem)
	resultCh := make(chan SessionSummary)
	var wg sync.WaitGroup
	for i := 0; i < threads; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for item := range workCh {
				if summary, ok := processFile(item, query); ok {
					resultCh <- summary
				}
			}
		}()
	}
	go func() {
		for _, item := range items {
			workCh <- item
		}
		close(workCh)
		wg.Wait()
		close(resultCh)
	}()

	results := make([]SessionSummary, 0, len(items))
	limitHit := false
	for result := range resultCh {
		if limit > 0 && len(results) >= limit {
			limitHit = true
			continue
		}
		results = append(results, result)
		if limit > 0 && len(results) >= limit {
			limitHit = true
		}
	}
	return results, limitHit
}

func processFile(item WorkItem, query string) (SessionSummary, bool) {
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
	queryLower := strings.ToLower(strings.TrimSpace(query))
	matchFound := queryLower == ""

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
			if summary.Snippet == "" {
				for _, text := range extractTextCandidates(payload) {
					if snippet, ok := matchSnippet(text, queryLower); ok {
						summary.Snippet = snippet
						matchFound = true
						break
					}
				}
			}
		}
		if summary.Snippet == "" {
			if snippet, ok := matchSnippet(line, queryLower); ok {
				summary.Snippet = snippet
				matchFound = true
			}
		}
	}
	return summary, matchFound
}

func isNoiseLine(line string) bool {
	for _, marker := range noiseMarkers {
		if strings.Contains(line, marker) {
			return true
		}
	}
	return false
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

func matchSnippet(text, queryLower string) (string, bool) {
	trimmed := strings.TrimSpace(text)
	if queryLower == "" || trimmed == "" {
		return "", false
	}
	lower := strings.ToLower(trimmed)
	if !strings.Contains(lower, queryLower) || isNoiseLine(lower) {
		return "", false
	}
	if len(trimmed) > snippetLimit {
		return trimmed[:snippetLimit], true
	}
	return trimmed, true
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
	return out
}
