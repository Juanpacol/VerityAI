"""Unit tests for symbolic/security_facts.py -- T6's prototype fact
extractors for SQL injection and check-then-act race patterns.

Hand-written fixtures (vulnerable AND safe) since neither
correctness_benchmarks.json nor security_benchmarks.json contains a real
SQLi or race-condition sample -- confirmed by inspection before writing
this module; those benchmarks only cover divide-by-zero, bounds-checks,
and a lock-flag proxy, all reducible to Z3-checkable assertions.
"""

from verityai.symbolic.security_facts import (
    extract_race_condition_facts,
    extract_sql_injection_facts,
)


class TestSqlInjectionDetection:
    def test_string_concatenation_flagged(self):
        code = """
def get_user(cursor, username):
    query = "SELECT * FROM users WHERE name = '" + username + "'"
    cursor.execute(query)
"""
        facts = extract_sql_injection_facts(code)
        assert "sql_query_built_dynamically" in facts
        assert "sql_query_built_via_concatenation" in facts

    def test_fstring_flagged(self):
        code = """
def get_user(cursor, username):
    cursor.execute(f"SELECT * FROM users WHERE name = '{username}'")
"""
        facts = extract_sql_injection_facts(code)
        assert "sql_query_built_via_fstring" in facts

    def test_percent_format_flagged(self):
        code = """
def get_user(cursor, username):
    cursor.execute("SELECT * FROM users WHERE name = '%s'" % username)
"""
        facts = extract_sql_injection_facts(code)
        assert "sql_query_built_via_percent_format" in facts

    def test_dot_format_flagged(self):
        code = """
def get_user(cursor, username):
    cursor.execute("SELECT * FROM users WHERE name = '{}'".format(username))
"""
        facts = extract_sql_injection_facts(code)
        assert "sql_query_built_via_format_call" in facts

    def test_executemany_also_checked(self):
        code = """
def bulk_insert(cursor, names):
    cursor.executemany("INSERT INTO t VALUES ('" + names[0] + "')", [])
"""
        facts = extract_sql_injection_facts(code)
        assert "sql_query_built_dynamically" in facts


class TestSqlInjectionSafePatternsNotFlagged:
    def test_parameterized_query_recognized_as_safe(self):
        code = """
def get_user(cursor, username):
    cursor.execute("SELECT * FROM users WHERE name = ?", (username,))
"""
        facts = extract_sql_injection_facts(code)
        assert "uses_parameterized_query" in facts
        assert "sql_query_built_dynamically" not in facts

    def test_percent_s_placeholder_with_params_arg_is_safe(self):
        code = """
def get_user(cursor, username):
    cursor.execute("SELECT * FROM users WHERE name = %s", (username,))
"""
        facts = extract_sql_injection_facts(code)
        assert "uses_parameterized_query" in facts

    def test_plain_literal_query_no_execute_relevant_facts(self):
        code = """
def get_all(cursor):
    cursor.execute("SELECT * FROM users")
"""
        facts = extract_sql_injection_facts(code)
        assert facts == set()

    def test_unrelated_execute_like_call_not_flagged(self):
        # A method named `execute` that has nothing to do with SQL --
        # the detector matches by method name only (documented limitation),
        # so this IS expected to still produce a fact; this test documents
        # that limitation rather than asserting a false negative is good.
        code = """
def run_task(executor, cmd):
    executor.execute("echo " + cmd)
"""
        facts = extract_sql_injection_facts(code)
        assert "sql_query_built_dynamically" in facts  # documented false positive risk

    def test_syntax_error_returns_empty_not_raises(self):
        assert extract_sql_injection_facts("def f(:\n  pass") == set()

    def test_no_execute_call_at_all(self):
        code = "def f(x: int) -> int:\n    return x\n"
        assert extract_sql_injection_facts(code) == set()


class TestRaceConditionDetection:
    def test_unguarded_check_then_act_flagged(self):
        code = """
def add_if_missing(cache, key, value):
    if key in cache:
        return cache[key]
    cache[key] = value
    return value
"""
        facts = extract_race_condition_facts(code)
        assert "check_then_act_on_shared_resource" in facts

    def test_dict_get_check_variant_flagged(self):
        code = """
def add_if_missing(cache, key, value):
    if cache.get(key):
        return cache[key]
    cache[key] = value
    return value
"""
        facts = extract_race_condition_facts(code)
        assert "check_then_act_on_shared_resource" in facts


class TestRaceConditionSafePatternsNotFlagged:
    def test_lock_guarded_check_and_act_recognized_as_safe(self):
        code = """
def add_if_missing(cache, key, value, lock):
    with lock:
        if key in cache:
            return cache[key]
        cache[key] = value
    return value
"""
        facts = extract_race_condition_facts(code)
        assert "check_and_act_combined_atomically" in facts
        assert "check_then_act_on_shared_resource" not in facts

    def test_self_lock_attribute_recognized(self):
        code = """
class Cache:
    def add_if_missing(self, key, value):
        with self._lock:
            if key in self._data:
                return self._data[key]
            self._data[key] = value
        return value
"""
        facts = extract_race_condition_facts(code)
        assert "check_then_act_on_shared_resource" not in facts

    def test_check_without_subsequent_act_not_flagged(self):
        code = """
def has_key(cache, key):
    if key in cache:
        return True
    return False
"""
        facts = extract_race_condition_facts(code)
        assert facts == set()

    def test_act_without_prior_check_not_flagged(self):
        code = """
def set_value(cache, key, value):
    cache[key] = value
"""
        facts = extract_race_condition_facts(code)
        assert facts == set()

    def test_different_containers_not_conflated(self):
        code = """
def f(cache_a, cache_b, key, value):
    if key in cache_a:
        return None
    cache_b[key] = value
    return value
"""
        facts = extract_race_condition_facts(code)
        assert facts == set()

    def test_syntax_error_returns_empty_not_raises(self):
        assert extract_race_condition_facts("def f(:\n  pass") == set()


class TestAgainstExistingSecurityBenchmarks:
    """False-positive check against the project's own real security
    benchmark tasks (security_001-006) -- none of them involve SQL or
    dict-based check-then-act, so both extractors should stay silent.
    """

    def test_lock_guard_proxy_benchmark_not_falsely_flagged(self):
        # security_003_lock_guard's actual shape: an int flag assert, not a
        # real dict-based check-then-act -- the proxy the benchmark itself
        # documents (see docs/PHASE_3_METHODOLOGY.md's benchmark exclusions).
        code = """
def use_locked_resource(lock_flag: int) -> int:
    assert lock_flag == 1
    return 42
"""
        assert extract_race_condition_facts(code) == set()
        assert extract_sql_injection_facts(code) == set()

    def test_bounds_check_benchmark_not_falsely_flagged(self):
        code = """
def check_index_bounds(idx: int, n: int) -> bool:
    assert idx >= 0
    assert idx < n
    return True
"""
        assert extract_race_condition_facts(code) == set()
        assert extract_sql_injection_facts(code) == set()
