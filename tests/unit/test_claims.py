"""Tests for claim extraction.

Extraction only fires on backtick-quoted spans and a closed set of relation
phrases — see the module docstring for why. `TestFalsePositiveGuards` pins
down the regressions found while dogfooding this against real agent-style
text: a bare English word sitting between a backtick-quoted subject and a
relation verb ("`GraphStore` class inherits from `Base`") must not be captured
as the subject instead of the actual symbol.
"""

from verityai.consistency.claims import extract_claims
from verityai.core.models import ClaimKind


class TestSymbolExtraction:
    def test_a_dotted_identifier_is_a_symbol_claim(self):
        claims = extract_claims("The fix is in `ContextPipeline.run`.")

        assert len(claims) == 1
        assert claims[0].kind is ClaimKind.SYMBOL_EXISTS
        assert claims[0].subject == "ContextPipeline.run"

    def test_a_call_marker_is_stripped(self):
        claims = extract_claims("Call `make_service()` to construct one.")

        assert claims[0].subject == "make_service"

    def test_camelcase_without_punctuation_is_still_a_symbol(self):
        claims = extract_claims("See `GraphStore` for details.")

        assert claims[0].kind is ClaimKind.SYMBOL_EXISTS

    def test_underscored_names_are_symbols(self):
        claims = extract_claims("It calls `_enforce_budget` internally.")

        assert claims[0].subject == "_enforce_budget"


class TestFileExtraction:
    def test_a_path_with_a_slash_is_a_file_claim(self):
        claims = extract_claims("Defined in `src/verityai/core/models.py`.")

        assert claims[0].kind is ClaimKind.FILE_EXISTS
        assert claims[0].subject == "src/verityai/core/models.py"

    def test_a_bare_filename_with_extension_is_a_file_claim(self):
        claims = extract_claims("Check `README.md` for the overview.")

        assert claims[0].kind is ClaimKind.FILE_EXISTS


class TestFalsePositiveGuards:
    """Regressions found by running this against real, human-shaped text."""

    def test_a_noun_between_subject_and_verb_does_not_steal_the_subject(self):
        """`GraphStore` class inherits from `Base` must bind to GraphStore,
        not to the bare word "class" sitting between them. An earlier version
        of the relation regex made backticks optional and matched exactly
        this wrong."""
        claims = extract_claims("The `GraphStore` class inherits from `Base`.")

        relations = [c for c in claims if c.kind is ClaimKind.SYMBOL_RELATION]
        assert len(relations) == 1
        assert relations[0].subject == "GraphStore"
        assert relations[0].target == "Base"

    def test_plain_english_words_in_backticks_are_not_symbols(self):
        """A single common word with no punctuation is more often a literal
        value or a shell command than a code reference."""
        claims = extract_claims("Set the flag to `true` and run `build`.")

        assert claims == []

    def test_words_outside_backticks_are_never_extracted(self):
        claims = extract_claims("ContextPipeline.run does the pruning.")

        assert claims == []

    def test_a_relation_without_backticks_is_not_extracted(self):
        """Bare-text relation claims are exactly the over-eager extraction
        this module is designed to avoid -- see the module docstring."""
        claims = extract_claims("ContextPipeline calls the ranker.")

        assert not any(c.kind is ClaimKind.SYMBOL_RELATION for c in claims)


class TestRelationExtraction:
    def test_calls_is_recognised(self):
        claims = extract_claims("`A` calls `B` during setup.")

        relation = next(c for c in claims if c.kind is ClaimKind.SYMBOL_RELATION)
        assert relation.relation == "calls"
        assert relation.subject == "A"
        assert relation.target == "B"

    def test_inherits_from_is_recognised(self):
        claims = extract_claims("`Service` inherits from `Base`.")

        relation = next(c for c in claims if c.kind is ClaimKind.SYMBOL_RELATION)
        assert relation.relation == "inherits"

    def test_extends_is_recognised(self):
        claims = extract_claims("`Service` extends `Base`.")

        relation = next(c for c in claims if c.kind is ClaimKind.SYMBOL_RELATION)
        assert relation.relation == "inherits"

    def test_an_unmapped_verb_is_not_extracted_as_a_relation(self):
        """'depends on' and 'uses' are ambiguous about which edge kind they
        mean, so they are left unrecognised rather than mapped to a guess."""
        claims = extract_claims("`A` depends on `B`. `A` uses `B`.")

        assert not any(c.kind is ClaimKind.SYMBOL_RELATION for c in claims)

    def test_a_relation_match_is_not_also_extracted_as_two_bare_symbols(self):
        """One assertion must not be double-counted as three claims."""
        claims = extract_claims("`A` calls `B`.")

        assert len(claims) == 1


class TestNoExtraction:
    def test_empty_text_extracts_nothing(self):
        assert extract_claims("") == []

    def test_text_with_no_backticks_extracts_nothing(self):
        assert extract_claims("This is a plain sentence with no code refs.") == []

    def test_unmatched_backtick_does_not_crash(self):
        assert extract_claims("this has a stray ` backtick") == []
