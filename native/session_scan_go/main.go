package main

import (
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
	scanner := NewSessionScanner(NewNoiseFilter(DefaultNoiseMarkers), defaultSnippetLimit)
	results, truncated := scan(work, *threads, *query, *limit, scanner)
	sort.Slice(results, func(i, j int) bool {
		if results[i].Source != results[j].Source {
			return results[i].Source < results[j].Source
		}
		return results[i].Path < results[j].Path
	})
	payload := ScanOutput{
		FilesScanned: len(work),
		Query:        *query,
		Matches:      results,
		Truncated:    truncated,
	}
	if *jsonOutput {
		raw, _ := json.MarshalIndent(payload, "", "  ")
		fmt.Println(string(raw))
		return
	}
	fmt.Printf("扫描完毕：%d 文件，匹配 %d 条，耗时 %s。\n", len(work), len(results), time.Since(start).Round(time.Millisecond))
	aggs := payload.Aggregates()
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

func scan(items []WorkItem, threads int, query string, limit int, scanner *SessionScanner) ([]SessionSummary, bool) {
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
				if summary, ok := scanner.ProcessFile(item, query); ok {
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
