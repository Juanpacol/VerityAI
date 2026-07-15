"""Fetchers for external evidence sources (arXiv, HumanEval/MBPP, Semgrep,
GitHub issues, Z3 docs). Each fetcher returns a `FetchResult` and never
raises past its own boundary -- per-item failures are recorded in
`FetchResult.errors`, not allowed to abort an entire run.
"""
