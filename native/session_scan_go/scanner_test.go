package main

import (
	"encoding/json"
	"os"
	"strings"
	"testing"
)

// ── NoiseFilter ───────────────────────────────────────────────────────────────

func TestNoiseFilter(t *testing.T) {
	filter := NewNoiseFilter([]string{"marker", "agent"})

	t.Run("detects_marker", func(t *testing.T) {
		if !filter.IsNoise("this line mentions marker text") {
			t.Fatal("expected noise marker to be detected")
		}
	})

	t.Run("detects_prefix", func(t *testing.T) {
		if !filter.IsNoise("## heading style noise") {
			t.Fatal("expected noise prefix to be detected")
		}
	})

	t.Run("passes_clean_line", func(t *testing.T) {
		if filter.IsNoise("clean, helpful line") {
			t.Fatal("did not expect clean line to be marked as noise")
		}
	})
}

func TestNoiseFilterMetaChatter(t *testing.T) {
	filter := NewNoiseFilter(DefaultNoiseMarkers)
	line := "我继续沿结果质量这条线打，不回到命名层。先复看当前工作树和主链 search NotebookLM 的命中。"
	if !filter.IsNoise(strings.ToLower(line)) {
		t.Fatal("expected active-session meta chatter to be filtered")
	}
}

// ── SnippetMatcher ────────────────────────────────────────────────────────────

func TestSnippetMatcher(t *testing.T) {
	filter := NewNoiseFilter([]string{"noise"})

	t.Run("non_empty_query", func(t *testing.T) {
		m := NewSnippetMatcher("query", filter, 5)
		if m.QueryEmpty() {
			t.Fatal("query should not be empty")
		}
	})

	t.Run("snippet_honours_limit", func(t *testing.T) {
		m := NewSnippetMatcher("query", filter, 5)
		snippet, ok := m.Match("before query text")
		if !ok {
			t.Fatal("expected match for text containing query")
		}
		if len(snippet) != 5 {
			t.Fatalf("expected snippet length 5, got %d (%q)", len(snippet), snippet)
		}
	})

	t.Run("noise_line_filtered", func(t *testing.T) {
		m := NewSnippetMatcher("query", filter, 40)
		if _, ok := m.Match("prefix query noise skill.md near match"); ok {
			t.Fatal("expected noise lines to stay filtered")
		}
	})

	t.Run("no_keyword_no_match", func(t *testing.T) {
		m := NewSnippetMatcher("query", filter, 5)
		if _, ok := m.Match("missing keyword here"); ok {
			t.Fatal("expected lines without keyword to not match")
		}
	})

	t.Run("distant_noise_marker_does_not_kill_real_match", func(t *testing.T) {
		m := NewSnippetMatcher("notebooklm", NewNoiseFilter(DefaultNoiseMarkers), 60)
		text := "skill.md very far away before the useful section and no longer near the final match ................................ NotebookLM useful content near query"
		snippet, ok := m.Match(text)
		if !ok {
			t.Fatal("expected local query window to survive distant noise markers")
		}
		if !strings.Contains(strings.ToLower(snippet), "notebooklm") {
			t.Fatalf("expected snippet to contain query, got %q", snippet)
		}
	})

	t.Run("empty_query_is_empty", func(t *testing.T) {
		if !NewSnippetMatcher("", filter, 1).QueryEmpty() {
			t.Fatal("empty query should be considered empty")
		}
	})
}

// ── summarize ─────────────────────────────────────────────────────────────────

func TestSummarize(t *testing.T) {
	results := []SessionSummary{
		{Source: "claude", Lines: 2, SizeBytes: 10},
		{Source: "codex", Lines: 3, SizeBytes: 5},
		{Source: "codex", Lines: 1, SizeBytes: 4},
	}
	aggs := summarize(results)
	if len(aggs) != 2 {
		t.Fatalf("expected 2 aggregates, got %d", len(aggs))
	}
	want := map[string]Aggregate{
		"claude": {Source: "claude", Count: 1, TotalLines: 2, TotalSize: 10},
		"codex":  {Source: "codex", Count: 2, TotalLines: 4, TotalSize: 9},
	}
	for _, agg := range aggs {
		w, ok := want[agg.Source]
		if !ok {
			t.Fatalf("unexpected source %q", agg.Source)
		}
		if agg.Count != w.Count || agg.TotalLines != w.TotalLines || agg.TotalSize != w.TotalSize {
			t.Fatalf("aggregate mismatch for %q: got %+v, want %+v", agg.Source, agg, w)
		}
	}
}

// ── shouldSkipRecordType ──────────────────────────────────────────────────────

func TestShouldSkipRecordType(t *testing.T) {
	t.Run("skips_function_call_output", func(t *testing.T) {
		rec := map[string]any{
			"type":    "response_item",
			"payload": map[string]any{"type": "function_call_output"},
		}
		if !shouldSkipRecordType(rec) {
			t.Fatal("expected function_call_output record to be skipped")
		}
	})

	t.Run("keeps_normal_message", func(t *testing.T) {
		rec := map[string]any{
			"type":    "response_item",
			"payload": map[string]any{"type": "message"},
		}
		if shouldSkipRecordType(rec) {
			t.Fatal("did not expect normal message record to be skipped")
		}
	})

	t.Run("skips_token_count", func(t *testing.T) {
		rec := map[string]any{
			"type":    "event_msg",
			"payload": map[string]any{"type": "token_count"},
		}
		if !shouldSkipRecordType(rec) {
			t.Fatal("expected token_count event to be skipped")
		}
	})
}

// ── shouldSkipPath ────────────────────────────────────────────────────────────

func TestShouldSkipPath(t *testing.T) {
	cases := []struct {
		path string
		skip bool
	}{
		{"/Users/dunova/.codex/skills/notebooklm/SKILL.md", true},
		{"/Users/dunova/.claude/projects/-Users-dunova-skills-repo/a.jsonl", true},
		{"/Users/dunova/.codex/sessions/2026/03/test.jsonl", false},
	}
	for _, tc := range cases {
		t.Run(tc.path, func(t *testing.T) {
			got := shouldSkipPath(tc.path)
			if got != tc.skip {
				t.Fatalf("shouldSkipPath(%q) = %v, want %v", tc.path, got, tc.skip)
			}
		})
	}
}

// ── ProcessFile integration ───────────────────────────────────────────────────

func TestProcessFileSurvivesLargeArchivedLines(t *testing.T) {
	tmp, err := os.CreateTemp(t.TempDir(), "*.jsonl")
	if err != nil {
		t.Fatalf("create temp file: %v", err)
	}
	defer tmp.Close()

	huge := strings.Repeat("x", 80*1024)
	first, err := json.Marshal(map[string]any{
		"type":    "response_item",
		"payload": map[string]any{"type": "function_call_output", "output": huge},
	})
	if err != nil {
		t.Fatalf("marshal first line: %v", err)
	}
	second, err := json.Marshal(map[string]any{
		"type":    "event_msg",
		"payload": map[string]any{"type": "agent_message", "message": "这里有一个 NotebookLM 历史结论。"},
	})
	if err != nil {
		t.Fatalf("marshal second line: %v", err)
	}

	for _, data := range [][]byte{
		append(first, '\n'),
		append(second, '\n'),
	} {
		if _, err := tmp.Write(data); err != nil {
			t.Fatalf("write temp file: %v", err)
		}
	}

	sc := NewSessionScanner(NewNoiseFilter(DefaultNoiseMarkers), defaultSnippetLimit)
	summary, ok := sc.ProcessFile(WorkItem{Source: "codex_session", Path: tmp.Name()}, "NotebookLM")
	if !ok {
		t.Fatal("expected match after large line")
	}
	if !strings.Contains(strings.ToLower(summary.Snippet), "notebooklm") {
		t.Fatalf("expected NotebookLM snippet, got %q", summary.Snippet)
	}
}

func TestProcessFileSkipsCurrentWorkdirSession(t *testing.T) {
	tmp, err := os.CreateTemp(t.TempDir(), "*.jsonl")
	if err != nil {
		t.Fatalf("create temp file: %v", err)
	}
	defer tmp.Close()

	cwd, err := os.Getwd()
	if err != nil {
		t.Fatalf("getwd: %v", err)
	}
	meta, err := json.Marshal(map[string]any{
		"type":    "session_meta",
		"payload": map[string]any{"id": "current-session", "cwd": cwd},
	})
	if err != nil {
		t.Fatalf("marshal meta: %v", err)
	}
	msg, err := json.Marshal(map[string]any{
		"type":    "event_msg",
		"payload": map[string]any{"type": "agent_message", "message": "NotebookLM 当前主链优化记录。"},
	})
	if err != nil {
		t.Fatalf("marshal msg: %v", err)
	}

	for _, data := range [][]byte{
		append(meta, '\n'),
		append(msg, '\n'),
	} {
		if _, err := tmp.Write(data); err != nil {
			t.Fatalf("write temp file: %v", err)
		}
	}

	sc := NewSessionScanner(NewNoiseFilter(DefaultNoiseMarkers), defaultSnippetLimit)
	if _, ok := sc.ProcessFile(WorkItem{Source: "codex_session", Path: tmp.Name()}, "NotebookLM"); ok {
		t.Fatal("expected current workdir session to be skipped")
	}
}

// ── collectFiles ──────────────────────────────────────────────────────────────

func TestCollectFilesSkipsNonExistentRoots(t *testing.T) {
	items := collectFiles([]WorkItem{
		{Source: "codex_session", Path: "/nonexistent/path/sessions"},
	})
	if len(items) != 0 {
		t.Fatalf("expected 0 items for non-existent root, got %d", len(items))
	}
}

func TestCollectFilesFindsJsonlFiles(t *testing.T) {
	dir := t.TempDir()

	// Create a .jsonl file that should be found.
	f, err := os.CreateTemp(dir, "session*.jsonl")
	if err != nil {
		t.Fatalf("create temp file: %v", err)
	}
	f.Close()

	// Create a .txt file that should not be found.
	txt, err := os.CreateTemp(dir, "ignore*.txt")
	if err != nil {
		t.Fatalf("create txt file: %v", err)
	}
	txt.Close()

	items := collectFiles([]WorkItem{{Source: "test", Path: dir}})
	if len(items) != 1 {
		t.Fatalf("expected 1 item, got %d", len(items))
	}
	if items[0].Source != "test" {
		t.Fatalf("unexpected source %q", items[0].Source)
	}
}
