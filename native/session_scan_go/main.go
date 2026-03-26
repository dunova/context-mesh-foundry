// Package main implements a high-performance scanner for Codex/Claude session
// files.  It walks .json and .jsonl session directories in parallel, extracts
// structured metadata (timestamps, session IDs, text snippets), suppresses
// noise, and emits either a human-readable summary or a machine-readable JSON
// report consumed by the Python layer of ContextGO.
//
// Usage:
//
//	session_scan_go --query "agent" --limit 20 --json
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

// WorkItem pairs a file path with its logical source label.
type WorkItem struct {
	Source string
	Path   string
}

// SessionSummary holds the metadata and best match extracted from one session
// file.  Fields tagged with omitempty are omitted from JSON when empty.
type SessionSummary struct {
	Source         string `json:"source"`
	Path           string `json:"path"`
	SessionID      string `json:"session_id"`
	Lines          int    `json:"lines"`
	SizeBytes      int64  `json:"size_bytes"`
	FirstTimestamp string `json:"first_timestamp,omitempty"`
	LastTimestamp  string `json:"last_timestamp,omitempty"`
	Snippet        string `json:"snippet,omitempty"`
	MatchField     string `json:"match_field,omitempty"`
	MatchScore     int    `json:"-"`
}

// ScanOutput is the top-level JSON envelope emitted when --json is set.
type ScanOutput struct {
	FilesScanned int              `json:"files_scanned"`
	Query        string           `json:"query,omitempty"`
	Matches      []SessionSummary `json:"matches"`
	Truncated    bool             `json:"truncated,omitempty"`
}

// Aggregates returns per-source statistics derived from the matched results.
func (o ScanOutput) Aggregates() []Aggregate {
	return summarize(o.Matches)
}

func main() {
	home, err := os.UserHomeDir()
	if err != nil {
		fmt.Fprintf(os.Stderr, "warning: cannot determine home directory: %v\n", err)
		home = "."
	}
	codexRoot := flag.String("codex-root", filepath.Join(home, ".codex", "sessions"), "Root directory for Codex session files")
	claudeRoot := flag.String("claude-root", filepath.Join(home, ".claude", "projects"), "Root directory for Claude session files")
	threads := flag.Int("threads", 4, "Number of parallel worker goroutines")
	query := flag.String("query", "", "Return only results whose text contains this substring")
	limit := flag.Int("limit", 20, "Maximum number of results to return")
	jsonOutput := flag.Bool("json", false, "Emit machine-readable JSON instead of a human summary")
	flag.Parse()

	start := time.Now()

	roots := []WorkItem{
		{Source: "codex_session", Path: *codexRoot},
		{Source: "codex_session", Path: filepath.Join(home, ".codex", "archived_sessions")},
		{Source: "claude_session", Path: *claudeRoot},
	}
	work := collectFiles(roots)

	scanner := NewSessionScanner(NewNoiseFilter(DefaultNoiseMarkers), defaultSnippetLimit)
	results, truncated := scan(work, *threads, *query, *limit, scanner)

	sort.Slice(results, func(i, j int) bool {
		a, b := results[i], results[j]
		if a.MatchScore != b.MatchScore {
			return a.MatchScore > b.MatchScore
		}
		if a.LastTimestamp != b.LastTimestamp {
			return a.LastTimestamp > b.LastTimestamp
		}
		if a.FirstTimestamp != b.FirstTimestamp {
			return a.FirstTimestamp > b.FirstTimestamp
		}
		if a.Source != b.Source {
			return a.Source < b.Source
		}
		return a.Path < b.Path
	})

	if *limit > 0 && len(results) > *limit {
		results = results[:*limit]
		truncated = true
	}

	payload := ScanOutput{
		FilesScanned: len(work),
		Query:        *query,
		Matches:      results,
		Truncated:    truncated,
	}

	if *jsonOutput {
		raw, err := json.MarshalIndent(payload, "", "  ")
		if err != nil {
			fmt.Fprintf(os.Stderr, "json marshal error: %v\n", err)
			os.Exit(1)
		}
		fmt.Println(string(raw))
		return
	}

	fmt.Printf("Scan complete: %d files, %d matches, elapsed %s.\n",
		len(work), len(results), time.Since(start).Round(time.Millisecond))

	aggs := payload.Aggregates()
	w := tabwriter.NewWriter(os.Stdout, 0, 0, 2, ' ', 0)
	fmt.Fprintln(w, "source\tmatches\ttotal_lines\ttotal_bytes")
	for _, agg := range aggs {
		fmt.Fprintf(w, "%s\t%d\t%d\t%d\n", agg.Source, agg.Count, agg.TotalLines, agg.TotalSize)
	}
	if err := w.Flush(); err != nil {
		fmt.Fprintf(os.Stderr, "tabwriter flush: %v\n", err)
	}

	if truncated && *limit > 0 {
		fmt.Printf("Results truncated at limit %d; additional matches may exist.\n", *limit)
	}
}

// collectFiles walks each root directory and returns WorkItems for all .json
// and .jsonl files, skipping skill directories.
func collectFiles(roots []WorkItem) []WorkItem {
	items := make([]WorkItem, 0, 64)
	for _, root := range roots {
		if _, err := os.Stat(root.Path); err != nil {
			continue
		}
		walkErr := filepath.Walk(root.Path, func(path string, info os.FileInfo, err error) error {
			if err != nil {
				fmt.Fprintf(os.Stderr, "warning: skipping %s: %v\n", path, err)
				return nil
			}
			if info == nil || info.IsDir() {
				return nil
			}
			if shouldSkipPath(path) {
				return nil
			}
			switch filepath.Ext(path) {
			case ".jsonl", ".json":
				items = append(items, WorkItem{Source: root.Source, Path: path})
			}
			return nil
		})
		if walkErr != nil {
			fmt.Fprintf(os.Stderr, "walk error for %s: %v\n", root.Path, walkErr)
		}
	}
	return items
}

// shouldSkipPath reports whether path belongs to a skill directory that should
// be excluded from session scanning.
func shouldSkipPath(path string) bool {
	lower := strings.ToLower(path)
	return strings.Contains(lower, "/skills/") || strings.Contains(lower, "skills-repo")
}

// scan fans out WorkItems to threads workers, collects SessionSummary results,
// and returns them together with a truncated flag (always false here; the
// caller applies the limit after sorting).
func scan(items []WorkItem, threads int, query string, limit int, scanner *SessionScanner) ([]SessionSummary, bool) {
	if threads < 1 {
		threads = 1
	}

	workCh := make(chan WorkItem, threads*2)
	resultCh := make(chan SessionSummary, threads*2)

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

	results := make([]SessionSummary, 0, min(len(items), limit*2))
	for result := range resultCh {
		results = append(results, result)
	}
	return results, false
}

