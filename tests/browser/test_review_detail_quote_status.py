"""Browser-surface contract checks for review detail quote/status rendering hooks."""
from __future__ import annotations

from pathlib import Path


def test_review_detail_renders_quote_strip_and_skipped_status():
    root = Path(__file__).resolve().parents[2]
    html = (root / "src/pr_guardian/dashboard/review_detail.html").read_text()

    assert "quote-strip" in html
    assert "renderQuoteStrip(f.quote)" in html
    assert "agent.status === 'skipped'" in html
    assert "agent.agent_name === 'architecture_intent' ? 'Architecture'" in html
    assert "skipped${agent.status_reason ? ' - ' + agent.status_reason : ''}" in html
