# Pull Request

## Summary

<!-- Describe the change in one or two sentences. What does this PR do and why? -->

## Motivation

<!-- Link to the issue this PR addresses, or explain the problem it solves. -->

Closes #

## Type of Change

<!-- Mark the applicable item with an x. -->

- [ ] Bug fix (non-breaking change that resolves a defect)
- [ ] New feature (non-breaking change that adds functionality)
- [ ] Breaking change (fix or feature that changes existing behavior)
- [ ] Refactor (no functional change)
- [ ] Performance improvement
- [ ] Documentation update
- [ ] CI/CD / tooling change

## Changes Made

<!-- Bullet list of the specific changes in this PR. -->

-

## Testing

<!-- Describe how you verified this change. Include commands run and output observed. -->

- [ ] Existing tests pass locally (`python -m pytest scripts/test_context_cli.py ...`)
- [ ] New tests added to cover the change
- [ ] Go tests pass (`go test ./...` in `native/session_scan_go/`)
- [ ] Rust check passes (`cargo check --manifest-path native/session_scan/Cargo.toml`)
- [ ] Quality gate passes (`python scripts/e2e_quality_gate.py`)
- [ ] Secret scan passes (no credentials or hardcoded paths introduced)

## Checklist

- [ ] My changes follow the existing code style and conventions.
- [ ] I have updated documentation where applicable.
- [ ] This PR is focused on a single concern (not mixing unrelated changes).
- [ ] I have reviewed the diff for accidental debug output, commented-out code, or secrets.
