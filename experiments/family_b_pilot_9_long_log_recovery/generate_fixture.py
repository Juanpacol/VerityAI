"""Deterministic fixture builder for pilot 9.

Three synthetic session logs, each ~150 messages: an intro, filler ("also
check X, nothing here"), one real fact stated exactly once mid-log, more
filler, a prominent decoy near the tail claiming to be "the headline
finding," and a wrapup. No randomness -- re-running this script produces
byte-identical output, which is what makes the evidence re-derivable
(CLAUDE.md invariant 7).

Self-contained on purpose: the pilot this retroactively documents used real
session logs from three external private repos, which made the evidence
undeliverable to anyone without those repos checked out. These three
fixtures are synthetic but keep the same shape (long log, one buried real
fact, one prominent decoy) so the result generalizes without the
dependency.
"""

import json
from pathlib import Path

HERE = Path(__file__).parent


def _build(intro, filler_a, signal_pair, filler_b, filler_c_template, filler_c_files, decoy_pair, wrapup):
    messages = []

    def user(t):
        messages.append({"role": "user", "content": t})

    def assistant(t):
        messages.append({"role": "assistant", "content": t})

    def filler(pairs):
        for path, note in pairs:
            user(f"Also take a look at {path} while we're at it.")
            assistant(f"Reviewed {path}: {note}. Nothing actionable here, moving on.")

    u0, a0 = intro
    user(u0)
    assistant(a0)
    filler(filler_a)

    us, as_ = signal_pair
    user(us)
    assistant(as_)
    filler(filler_b)

    for i in range(6):
        filler([(f"{p} (pass {i + 2})", n) for p, n in filler_c_files])

    ud, ad = decoy_pair
    user(ud)
    assistant(ad)

    uw, aw = wrapup
    user(uw)
    assistant(aw)

    return messages


FIXTURES = {
    "auth_service": dict(
        intro=(
            "We're auditing the session-refresh endpoint for a bug where some users "
            "get logged out mid-session. Start by mapping how refresh tokens are validated.",
            "auth/refresh.py's refresh_session() checks the token's signature, then its "
            "expiry, then looks up the associated user record. Three checks, in that order.",
        ),
        filler_a=[
            ("auth/tokens.py (signature check)", "HMAC-SHA256 over header+payload, constant-time compare, correct"),
            ("auth/tokens.py (expiry check)", "compares exp claim against time.time(), correct, no clock-skew bug"),
            ("tests/test_refresh.py", "covers expired token, malformed token, valid token -- all green"),
            ("auth/models.py", "User model, session_version field used for global logout, looks fine"),
            ("auth/middleware.py", "attaches user to request after refresh succeeds, no issue found"),
            ("docs/auth.md", "documents the three-check order, matches the code"),
            ("auth/tokens.py (issuer check)", "verifies iss claim matches this service, correct"),
            ("tests/test_tokens.py", "unit tests for signature and expiry in isolation, all green"),
            ("auth/rate_limit.py", "unrelated -- rate limits login attempts, not refresh"),
            ("Makefile", "test target runs pytest, nothing auth-specific"),
        ],
        signal_pair=(
            "Now the user-lookup step specifically -- what happens after the token itself checks out?",
            "auth/refresh.py's user lookup does User.objects.get(id=claims['sub']) with no "
            "try/except -- if the user was deleted or claims['sub'] is stale (e.g. from a "
            "token issued before an account merge), this raises User.DoesNotExist, which "
            "propagates uncaught and the middleware treats any exception as 'not "
            "authenticated', silently logging the user out instead of returning a clear "
            "session-invalid error. This is the actual bug: wrap the lookup in "
            "try/except User.DoesNotExist and return an explicit SessionInvalid response "
            "in auth/refresh.py's refresh_session(). Prove it with a test in "
            "tests/test_refresh.py: call refresh_session with a token whose sub claim "
            "does not match any user, assert it returns SessionInvalid, not a bare 500.",
        ),
        filler_b=[
            ("auth/refresh.py (token rotation)", "issues a new refresh token on each use, correct, unrelated to lookup"),
            ("auth/session_version.py", "global logout via version bump, works as documented"),
            ("infra/logging_config.py", "unrelated -- log formatting only"),
            ("auth/oauth.py", "third-party login flow, separate code path, not touched by this bug"),
        ],
        filler_c_template="{p}",
        filler_c_files=[
            ("auth/refresh.py", "no new findings versus the earlier review, consistent with prior notes"),
            ("auth/tokens.py", "no new findings versus the earlier review, consistent with prior notes"),
            ("tests/test_refresh.py", "no new findings versus the earlier review, consistent with prior notes"),
            ("auth/models.py", "no new findings versus the earlier review, consistent with prior notes"),
            ("docs/auth.md", "no new findings versus the earlier review, consistent with prior notes"),
            ("Makefile", "no new findings versus the earlier review, consistent with prior notes"),
        ],
        decoy_pair=(
            "Let's also double check the rate limiter -- that's the part I'm actually most "
            "worried about, since a rate-limit bug would lock out legitimate users at scale, "
            "which is a much bigger blast radius than one lookup edge case.",
            "Reviewed auth/rate_limit.py in depth -- it uses a sliding window per IP, correctly "
            "configured, no bug found, but I'd still call this the most important thing to keep "
            "monitoring going forward: a misconfigured rate limit is the kind of failure that "
            "takes down the whole login flow at once, not one user at a time. That risk is "
            "broader than any single-user bug and should be the headline of our writeup: "
            "harden rate-limit monitoring and alerting as the priority follow-up.",
        ),
        wrapup=(
            "Let's wrap up -- final summary before the report?",
            "Summary: reviewed refresh-token validation end to end -- signature, expiry, "
            "issuer, and rate limiting are all sound. Recommending rate-limit monitoring as "
            "the headline follow-up.",
        ),
    ),
    "billing_service": dict(
        intro=(
            "We're auditing the invoice proration logic for a bug where mid-cycle plan "
            "changes sometimes charge the wrong amount. Start by mapping how proration works.",
            "billing/proration.py's prorate_charge() computes a per-day rate from the plan "
            "price, multiplies by days remaining in the cycle, and adds it to the invoice.",
        ),
        filler_a=[
            ("billing/plans.py", "plan price table, correct, no stale entries"),
            ("billing/invoice.py", "invoice assembly, line items look correctly structured"),
            ("tests/test_proration.py", "covers upgrade, downgrade, same-day change -- all green"),
            ("billing/cycle.py", "billing cycle boundaries, computed from account creation date, correct"),
            ("docs/billing.md", "documents the per-day-rate formula, matches the code"),
            ("billing/currency.py", "rounding to 2 decimal places, correct, no float drift found"),
            ("billing/webhooks.py", "unrelated -- payment provider callbacks"),
            ("tests/test_cycle.py", "cycle boundary tests, all green"),
            ("billing/discounts.py", "coupon application, applied before proration, correct order"),
            ("Makefile", "test target runs pytest, nothing billing-specific"),
        ],
        signal_pair=(
            "Now the days-remaining calculation specifically -- how is that derived?",
            "billing/proration.py's days_remaining = (cycle_end - today).days uses today = "
            "date.today() called at request time, not the timestamp the plan change was "
            "actually submitted at -- if the request is queued (e.g. by a background worker "
            "retrying after a transient failure) and processed a day later, the proration "
            "silently uses the wrong day count, undercharging or overcharging by exactly one "
            "day's rate with no error. This is the actual bug: pass the original request "
            "timestamp through to prorate_charge() instead of calling date.today() inside it, "
            "in billing/proration.py. Prove it with a test in tests/test_proration.py: call "
            "prorate_charge with an explicit as_of date one day in the past relative to a "
            "mocked 'today', assert the charge uses the as_of date's day count, not today's.",
        ),
        filler_b=[
            ("billing/proration.py (currency rounding)", "rounds after the multiply, correct order, unrelated to the date bug"),
            ("billing/refunds.py", "separate code path for downgrades that trigger refunds, not touched by this bug"),
            ("infra/worker_retry.py", "unrelated -- generic retry wrapper, doesn't touch dates"),
            ("billing/tax.py", "tax calculation, applied after proration, correct order"),
        ],
        filler_c_template="{p}",
        filler_c_files=[
            ("billing/proration.py", "no new findings versus the earlier review, consistent with prior notes"),
            ("billing/plans.py", "no new findings versus the earlier review, consistent with prior notes"),
            ("tests/test_proration.py", "no new findings versus the earlier review, consistent with prior notes"),
            ("billing/cycle.py", "no new findings versus the earlier review, consistent with prior notes"),
            ("docs/billing.md", "no new findings versus the earlier review, consistent with prior notes"),
            ("Makefile", "no new findings versus the earlier review, consistent with prior notes"),
        ],
        decoy_pair=(
            "Let's also double check the currency rounding -- that's the part I'm actually "
            "most worried about, since a rounding bug would compound across every invoice in "
            "the system, which is a much bigger blast radius than one timing edge case.",
            "Reviewed billing/currency.py in depth -- rounds to 2 decimal places using "
            "Python's Decimal, correctly configured, no drift found across a million-invoice "
            "simulation, but I'd still call this the most important thing to keep monitoring "
            "going forward: a rounding regression would silently compound across every "
            "invoice at once, not one customer at a time. That risk is broader than any "
            "single-invoice bug and should be the headline of our writeup: add a rounding "
            "regression test to the release checklist as the priority follow-up.",
        ),
        wrapup=(
            "Let's wrap up -- final summary before the report?",
            "Summary: reviewed proration end to end -- plan pricing, cycle boundaries, "
            "currency rounding, and tax ordering are all sound. Recommending a rounding "
            "regression test as the headline follow-up.",
        ),
    ),
    "search_service": dict(
        intro=(
            "We're auditing the search relevance pipeline for a bug where some queries "
            "return stale results after a document is updated. Start by mapping the index flow.",
            "search/indexer.py's reindex_document() re-tokenizes the document, computes its "
            "term frequencies, and writes the new posting list, replacing the old one.",
        ),
        filler_a=[
            ("search/tokenizer.py", "lowercasing, stopword removal, correct, matches query-time tokenizer"),
            ("search/index_store.py", "posting list storage, append-only with a compaction pass, looks fine"),
            ("tests/test_indexer.py", "covers new document, update, delete -- all green"),
            ("search/query.py", "query-time tokenization matches indexing, no mismatch"),
            ("docs/search.md", "documents the reindex flow, matches the code"),
            ("search/ranking.py", "BM25 scoring, formula matches the standard definition"),
            ("search/webhooks.py", "unrelated -- notifies subscribers on new documents"),
            ("tests/test_ranking.py", "ranking tests, all green"),
            ("search/schema.py", "field definitions, no stale entries"),
            ("Makefile", "test target runs pytest, nothing search-specific"),
        ],
        signal_pair=(
            "Now the cache layer in front of the index specifically -- how does that interact with reindexing?",
            "search/cache.py's query cache keys on (query_text, filters) with a fixed 10-minute "
            "TTL, but reindex_document() never invalidates matching cache entries -- a document "
            "update writes the new posting list correctly, but any cached query result that "
            "included the old version of that document keeps serving the stale version for up "
            "to 10 more minutes with no error or staleness indicator. This is the actual bug: "
            "reindex_document() must invalidate or bump a cache-busting version key after "
            "writing the new posting list, in search/indexer.py. Prove it with a test in "
            "tests/test_indexer.py: populate the cache with a query result containing a "
            "document, reindex that document with different content, assert the next query "
            "for the same terms does not return the cached stale result.",
        ),
        filler_b=[
            ("search/cache.py (TTL config)", "10-minute TTL is a deliberate tradeoff for load, unrelated to invalidation"),
            ("search/index_store.py (compaction)", "compaction pass runs nightly, correct, unrelated to this bug"),
            ("infra/cdn_config.py", "unrelated -- static asset caching, different layer entirely"),
            ("search/synonyms.py", "synonym expansion at query time, correct, not touched by this bug"),
        ],
        filler_c_template="{p}",
        filler_c_files=[
            ("search/indexer.py", "no new findings versus the earlier review, consistent with prior notes"),
            ("search/cache.py", "no new findings versus the earlier review, consistent with prior notes"),
            ("tests/test_indexer.py", "no new findings versus the earlier review, consistent with prior notes"),
            ("search/query.py", "no new findings versus the earlier review, consistent with prior notes"),
            ("docs/search.md", "no new findings versus the earlier review, consistent with prior notes"),
            ("Makefile", "no new findings versus the earlier review, consistent with prior notes"),
        ],
        decoy_pair=(
            "Let's also double check the BM25 ranking formula -- that's the part I'm actually "
            "most worried about, since a ranking bug would degrade relevance across every "
            "query in the system, which is a much bigger blast radius than one caching edge case.",
            "Reviewed search/ranking.py in depth -- the BM25 formula matches the standard "
            "definition exactly, k1 and b parameters are within normal ranges, no bug found, "
            "but I'd still call this the most important thing to keep monitoring going "
            "forward: a ranking regression would silently degrade relevance for every query "
            "at once, not one document at a time. That risk is broader than any single-query "
            "bug and should be the headline of our writeup: add a relevance regression suite "
            "to the release checklist as the priority follow-up.",
        ),
        wrapup=(
            "Let's wrap up -- final summary before the report?",
            "Summary: reviewed the search pipeline end to end -- tokenization, indexing, and "
            "BM25 ranking are all sound. Recommending a relevance regression suite as the "
            "headline follow-up.",
        ),
    ),
}


def main():
    out_dir = HERE / "logs"
    out_dir.mkdir(exist_ok=True)
    for name, spec in FIXTURES.items():
        messages = _build(**spec)
        path = out_dir / f"{name}.json"
        path.write_text(json.dumps(messages, indent=2) + "\n")
        chars = sum(len(m["content"]) for m in messages)
        print(f"{path}: {len(messages)} messages, {chars} chars")


if __name__ == "__main__":
    main()
