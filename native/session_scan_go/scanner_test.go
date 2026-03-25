package main

import "testing"

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
	if snippet != "befor" {
		t.Fatalf("expected snippet truncated to limit, got %q", snippet)
	}

	if _, ok := matcher.Match("noise skill.md query"); ok {
		t.Fatalf("expected noise lines to stay filtered")
	}
	if _, ok := matcher.Match("missing keyword here"); ok {
		t.Fatalf("expected lines without keyword to not match")
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
