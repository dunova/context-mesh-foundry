package main

import (
	"encoding/json"
	"os"
	"strings"
	"testing"
)

func TestNoiseFilter(t *testing.T) {
	filter := NewNoiseFilter([]string{"marker", "agent"})
	if !filter.IsNoise("this line mentions marker text") {
		t.Fatalf("expected noise marker to be detected")
	}
	if !filter.IsNoise("## heading style noise") {
		t.Fatalf("expected noise prefix to be detected")
	}
	if filter.IsNoise("clean, helpful line") {
		t.Fatalf("did not expect clean line to be marked as noise")
	}
}

func TestSnippetMatcher(t *testing.T) {
	filter := NewNoiseFilter([]string{"noise"})
	matcher := NewSnippetMatcher("query", filter, 5)
	if matcher.QueryEmpty() {
		t.Fatalf("query should not be empty")
	}
	snippet, ok := matcher.Match("before query text")
	if !ok {
		t.Fatalf("expected match for text containing query")
	}
	if len(snippet) != 5 {
		t.Fatalf("expected snippet to honor limit, got %q", snippet)
	}

	if _, ok := NewSnippetMatcher("query", filter, 40).Match("prefix query noise skill.md near match"); ok {
		t.Fatalf("expected noise lines to stay filtered")
	}
	if _, ok := matcher.Match("missing keyword here"); ok {
		t.Fatalf("expected lines without keyword to not match")
	}
	snippet, ok = NewSnippetMatcher("notebooklm", NewNoiseFilter(DefaultNoiseMarkers), 60).Match(
		"skill.md very far away before the useful section and no longer near the final match ................................ NotebookLM useful content near query",
	)
	if !ok {
		t.Fatalf("expected local query window to survive distant noise markers")
	}
	if !strings.Contains(strings.ToLower(snippet), "notebooklm") {
		t.Fatalf("expected snippet to keep query, got %q", snippet)
	}
	if !NewSnippetMatcher("", filter, 1).QueryEmpty() {
		t.Fatalf("empty query should be considered empty")
	}
}

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
	expected := map[string]Aggregate{
		"claude": {Source: "claude", Count: 1, TotalLines: 2, TotalSize: 10},
		"codex":  {Source: "codex", Count: 2, TotalLines: 4, TotalSize: 9},
	}
	for _, agg := range aggs {
		want, ok := expected[agg.Source]
		if !ok {
			t.Fatalf("unexpected source %s", agg.Source)
		}
		if agg.Count != want.Count || agg.TotalLines != want.TotalLines || agg.TotalSize != want.TotalSize {
			t.Fatalf("aggregate mismatch for %s: got %+v, want %+v", agg.Source, agg, want)
		}
	}
}

func TestShouldSkipRecordType(t *testing.T) {
	if !shouldSkipRecordType(map[string]any{
		"type": "response_item",
		"payload": map[string]any{"type": "function_call_output"},
	}) {
		t.Fatalf("expected function_call_output record to be skipped")
	}
	if shouldSkipRecordType(map[string]any{
		"type": "response_item",
		"payload": map[string]any{"type": "message"},
	}) {
		t.Fatalf("did not expect normal message record to be skipped")
	}
}

func TestNoiseFilterSkipsMetaChatter(t *testing.T) {
	filter := NewNoiseFilter(DefaultNoiseMarkers)
	line := "我继续沿结果质量这条线打，不回到命名层。先复看当前工作树和主链 search NotebookLM 的命中。"
	if !filter.IsNoise(strings.ToLower(line)) {
		t.Fatalf("expected active-session meta chatter to be filtered")
	}
}

func TestShouldSkipPath(t *testing.T) {
	if !shouldSkipPath("/Users/dunova/.codex/skills/notebooklm/SKILL.md") {
		t.Fatalf("expected skills path to be skipped")
	}
	if !shouldSkipPath("/Users/dunova/.claude/projects/-Users-dunova-skills-repo/a.jsonl") {
		t.Fatalf("expected skills-repo path to be skipped")
	}
	if shouldSkipPath("/Users/dunova/.codex/sessions/2026/03/test.jsonl") {
		t.Fatalf("did not expect normal session path to be skipped")
	}
}

func TestProcessFileSurvivesLargeArchivedLines(t *testing.T) {
	tmp, err := os.CreateTemp(t.TempDir(), "*.jsonl")
	if err != nil {
		t.Fatalf("create temp file: %v", err)
	}
	defer tmp.Close()

	huge := strings.Repeat("x", 80*1024)
	first, _ := json.Marshal(map[string]any{
		"type": "response_item",
		"payload": map[string]any{
			"type":   "function_call_output",
			"output": huge,
		},
	})
	second, _ := json.Marshal(map[string]any{
		"type": "event_msg",
		"payload": map[string]any{
			"type":    "agent_message",
			"message": "这里有一个 NotebookLM 历史结论。",
		},
	})
	if _, err := tmp.Write(append(first, '\n')); err != nil {
		t.Fatalf("write first line: %v", err)
	}
	if _, err := tmp.Write(append(second, '\n')); err != nil {
		t.Fatalf("write second line: %v", err)
	}

	scanner := NewSessionScanner(NewNoiseFilter(DefaultNoiseMarkers), defaultSnippetLimit)
	summary, ok := scanner.ProcessFile(WorkItem{Source: "codex_session", Path: tmp.Name()}, "NotebookLM")
	if !ok {
		t.Fatalf("expected match after large line")
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
	meta, _ := json.Marshal(map[string]any{
		"type": "session_meta",
		"payload": map[string]any{
			"id":  "current-session",
			"cwd": cwd,
		},
	})
	msg, _ := json.Marshal(map[string]any{
		"type": "event_msg",
		"payload": map[string]any{
			"type":    "agent_message",
			"message": "NotebookLM 当前主链优化记录。",
		},
	})
	if _, err := tmp.Write(append(meta, '\n')); err != nil {
		t.Fatalf("write meta: %v", err)
	}
	if _, err := tmp.Write(append(msg, '\n')); err != nil {
		t.Fatalf("write msg: %v", err)
	}

	scanner := NewSessionScanner(NewNoiseFilter(DefaultNoiseMarkers), defaultSnippetLimit)
	if _, ok := scanner.ProcessFile(WorkItem{Source: "codex_session", Path: tmp.Name()}, "NotebookLM"); ok {
		t.Fatalf("expected current workdir session to be skipped")
	}
}
