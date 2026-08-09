"""Unit tests for api/live_page.py.

Structural assertions only -- there is no browser here. What's worth
guarding is that the page stays self-contained, that the consent gate
exists, and that the two trust measures stay visibly separate.
"""

import re

from verityai.api.live_page import render_live_page


def page():
    return render_live_page()


def prose():
    """The page with runs of whitespace collapsed.

    Assertions on sentences must not depend on where the source HTML
    happens to wrap a line.
    """
    return re.sub(r"\s+", " ", render_live_page())


class TestSelfContainment:
    def test_is_a_complete_html_document(self):
        html = page()
        assert html.startswith("<!DOCTYPE html>")
        assert "</html>" in html
        assert "<title>" in html

    def test_has_no_external_resources(self):
        """No CDN, no remote fonts, no external scripts -- same rule as dashboard.py."""
        html = page()
        assert "http://" not in html
        assert "https://" not in html
        assert not re.search(r"<script[^>]+src=", html)
        assert not re.search(r"<link[^>]+href=", html)

    def test_inlines_the_shared_run_view_stylesheet(self):
        """Streamed panels must look identical to the post-hoc /runs/{id}/view."""
        from verityai.api.run_view import live_css

        html = page()
        assert "--series-aqua" in html
        assert live_css()[:200] in html

    def test_only_references_relative_api_paths(self):
        html = page()
        assert "'/live/runs'" in html
        assert "'/study/responses'" in html


class TestConsentGate:
    def test_has_a_consent_checkbox(self):
        assert 'id="consent"' in page()

    def test_prompt_and_run_start_disabled(self):
        """Nothing is runnable until consent is checked."""
        html = page()
        assert '<textarea id="prompt" disabled' in html
        assert '<button id="run" disabled>' in html

    def test_discloses_what_is_recorded_and_what_is_not(self):
        text = prose()
        assert "What is recorded" in text
        assert "What is not recorded" in text
        assert "no payment for taking part" in text

    def test_discloses_the_builder_bias(self):
        """The protocol requires stating this rather than hiding it."""
        text = prose()
        assert "bias risk" in text
        assert "also the person running the study" in text


class TestQuestionnaire:
    def test_asks_the_attitudinal_trust_question(self):
        assert "Do you trust this code?" in page()

    def test_asks_the_behavioural_intent_question_separately(self):
        """Conflating stated trust with reliance is the gap this closes."""
        html = page()
        assert "What would you actually do with it?" in html
        assert "separate question from the one above" in prose()

    def test_offers_all_three_merge_intents(self):
        html = page()
        for value in ("merge_as_is", "merge_after_skim", "full_review"):
            assert f'value="{value}"' in html

    def test_asks_the_keep_only_one_question(self):
        html = page()
        assert "keep only one thing" in prose()
        for value in ("z3", "confidence", "retrieval", "code", "other"):
            assert f'value="{value}"' in html

    def test_asks_whether_anything_reduced_trust(self):
        assert "trust the code <em>less</em>" in prose()

    def test_records_ai_tool_experience_as_context_not_a_filter(self):
        assert "context, not a filter" in prose()

    def test_questionnaire_starts_hidden_until_the_run_finishes(self):
        assert '<section id="questions" class="hidden">' in page()


class TestHonestyDisclosures:
    def test_footer_states_the_free_prompt_tradeoff(self):
        """What this design gains and loses vs the fixed-sample protocol."""
        text = prose()
        assert "no two participants judge the same code" in text
        assert "weaker" in text
        assert "stronger" in text

    def test_page_does_not_hardcode_any_panel_visibility(self):
        """Masking is server-side; a CSS rule here would be defeated by dev tools."""
        html = page()
        assert "condition" not in html.lower().split("<script>")[0]
