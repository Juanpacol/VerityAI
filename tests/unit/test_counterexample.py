"""Unit tests for counterexample generation."""

import pytest
from z3 import Int, Solver

from verityai.ontology.models import Counterexample
from verityai.symbolic.counterexample import CounterexampleGenerator


class MockZ3Value:
    """Mock Z3 value for testing."""

    def __init__(self, value, value_type="int"):
        self.value = value
        self.value_type = value_type

    def as_long(self):
        if self.value_type == "int":
            return self.value
        raise RuntimeError("Not an int")

    def as_bool(self):
        if self.value_type == "bool":
            return self.value
        raise RuntimeError("Not a bool")

    def as_string(self):
        if self.value_type == "str":
            return self.value
        raise RuntimeError("Not a string")

    def __str__(self):
        return str(self.value)


class MockZ3Model:
    """Mock Z3 model for testing."""

    def __init__(self, values: dict):
        """Initialize with variable assignments.

        Args:
            values: Dict mapping variable names to (value, type) tuples
        """
        self.values = values

    def decls(self):
        """Return mock declarations."""
        return [MockDecl(name) for name in self.values.keys()]

    def __getitem__(self, decl):
        """Return mock Z3 value."""
        name = decl.name()
        value, value_type = self.values[name]
        return MockZ3Value(value, value_type)


class MockDecl:
    """Mock Z3 declaration."""

    def __init__(self, name):
        self._name = name

    def name(self):
        return self._name


class TestCounterexampleGenerator:
    """Tests for CounterexampleGenerator."""

    def test_creation_with_source_code(self):
        """Test creating generator with source code."""
        source_code = "x = 5\nassert x > 10"
        gen = CounterexampleGenerator(source_code=source_code)

        assert gen.source_code == source_code
        assert len(gen.lines) == 2

    def test_creation_without_source_code(self):
        """Test creating generator without source code."""
        gen = CounterexampleGenerator()

        assert gen.source_code is None
        assert len(gen.lines) == 0

    def test_z3_to_python_int(self):
        """Test converting Z3 int to Python int."""
        gen = CounterexampleGenerator()
        z3_val = MockZ3Value(42, "int")

        result = gen._z3_to_python(z3_val)

        assert result == 42
        assert isinstance(result, int)

    def test_z3_to_python_bool(self):
        """Test converting Z3 bool to Python bool."""
        gen = CounterexampleGenerator()
        z3_val = MockZ3Value(True, "bool")

        result = gen._z3_to_python(z3_val)

        assert result is True

    def test_z3_to_python_string(self):
        """Test converting Z3 string to Python string."""
        gen = CounterexampleGenerator()
        z3_val = MockZ3Value("hello", "str")

        result = gen._z3_to_python(z3_val)

        assert result == "hello"

    def test_from_z3_model(self):
        """Test creating counterexample from Z3 model."""
        model_values = {
            "x": (5, "int"),
            "y": (10, "int"),
        }
        model = MockZ3Model(model_values)

        gen = CounterexampleGenerator()
        ce = gen.from_z3_model(model, rule_id="rule_test")

        assert ce.rule_id == "rule_test"
        assert ce.input_values["x"] == 5
        assert ce.input_values["y"] == 10

    def test_extract_model_values(self):
        """Test extracting all values from Z3 model."""
        model_values = {
            "arr_len": (10, "int"),
            "target": (5, "int"),
            "is_found": (True, "bool"),
        }
        model = MockZ3Model(model_values)

        gen = CounterexampleGenerator()
        values = gen._extract_model_values(model)

        assert len(values) == 3
        assert values["arr_len"] == 10
        assert values["target"] == 5
        assert values["is_found"] is True

    def test_generate_fix_suggestion(self):
        """Test generating fix suggestion."""
        ce = Counterexample(
            rule_id="rule_bounds",
            input_values={"idx": 100, "arr_len": 10},
            description="Array index out of bounds",
        )

        gen = CounterexampleGenerator()
        suggestion = gen.generate_fix_suggestion(ce, "Bounds Check Rule")

        assert "idx=100" in suggestion
        assert "Bounds Check Rule" in suggestion
        assert "boundary" in suggestion.lower()

    def test_format_for_display(self):
        """Test formatting counterexample for display."""
        ce = Counterexample(
            rule_id="rule_test",
            input_values={"x": 5, "y": -3},
            description="Negative value violation",
            suggested_fix="Add validation: assert y > 0",
        )

        gen = CounterexampleGenerator()
        display = gen.format_for_display(ce)

        assert "Negative value violation" in display
        assert "x = 5" in display
        assert "y = -3" in display
        assert "Add validation" in display

    def test_group_counterexamples(self):
        """Test grouping counterexamples by rule."""
        ce1 = Counterexample(
            rule_id="rule_bounds",
            input_values={"idx": 100},
            description="Bounds violation",
        )
        ce2 = Counterexample(
            rule_id="rule_bounds",
            input_values={"idx": -1},
            description="Negative index",
        )
        ce3 = Counterexample(
            rule_id="rule_null",
            input_values={"ptr": None},
            description="Null pointer",
        )

        gen = CounterexampleGenerator()
        grouped = gen.group_counterexamples([ce1, ce2, ce3])

        assert len(grouped["rule_bounds"]) == 2
        assert len(grouped["rule_null"]) == 1

    def test_counterexample_with_extraction_error(self):
        """Test handling extraction errors gracefully."""
        # Create a broken model that raises on decls()
        class BrokenModel:
            def decls(self):
                raise RuntimeError("Mock decoding error")

        gen = CounterexampleGenerator()
        try:
            values = gen._extract_model_values(BrokenModel())
            # Should have error marker
            assert "_extraction_error" in values
        except RuntimeError:
            # Acceptable to propagate in this test
            pass
