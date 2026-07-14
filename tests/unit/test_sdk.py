"""Unit tests for sdk.py (`from verityai import Verifier`)."""

from unittest.mock import patch

from verityai import Verifier
from verityai.ontology.models import VerificationStatus


class TestVerifierVerify:
    def test_verify_does_not_need_ollama(self):
        """verify() never calls the LLM -- constructing a Verifier still
        creates an OllamaClient, but .verify() shouldn't touch it."""
        v = Verifier()
        result = v.verify("x = 1\nassert x == 1")
        assert result.status == VerificationStatus.PASS

    def test_verify_catches_real_bug(self):
        v = Verifier()
        result = v.verify("x = 1\nassert x == 999")
        assert result.status == VerificationStatus.FAIL


class TestVerifierGenerate:
    def test_generate_delegates_to_orchestrator(self):
        v = Verifier()
        with patch.object(v._orchestrator, "run") as mock_run:
            v.generate("write a function")
            assert mock_run.called
            request = mock_run.call_args[0][0]
            assert request.prompt == "write a function"
            assert request.max_attempts == 3

    def test_generate_passes_through_custom_params(self):
        v = Verifier()
        with patch.object(v._orchestrator, "run") as mock_run:
            v.generate("write a function", language="java", max_attempts=1)
            request = mock_run.call_args[0][0]
            assert request.language == "java"
            assert request.max_attempts == 1
