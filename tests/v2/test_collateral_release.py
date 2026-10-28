"""Operator early release of a finished benchmark's collateral."""
from pool_manager.pool_v2 import benchmarks, dashboard, members
from pool_manager.pool_v2.money import Conflict, FundsError
from funds_helpers import DatabaseCase, WALLET


class CollateralReleaseTests(DatabaseCase):
    def setUp(self):
        super().setUp()
        self.fund()

    def finished(self, key, *, outcome="expired", handed=True):
        row = self.reserve(key)
        benchmarks.mark_submitting(self.db, row["id"])
        row = benchmarks.accept(self.db, row["id"], "bench-" + key,
            {"benchmark_id": "bench-" + key, "settings": {"player_id": WALLET, "challenge_id": "c1"}, "num_nonces": 16},
            actual_fee=0, evidence={"source": "fixture"})
        if handed:
            row = benchmarks.acknowledge(self.db, row["id"], self.member, row["assignment_digest"])
        if outcome:
            row = benchmarks.record_outcome(self.db, row["id"], outcome, height=39, evidence={"source": "fixture"})
        return row

    def balance(self):
        return members.balances(self.db, self.member)

    def test_full_release_returns_the_hold_and_is_audited_and_idempotent(self):
        row = self.finished("one")
        amount, before = int(row["amount"]), self.balance()
        result = benchmarks.release_collateral(self.db, row["id"], amount=amount, actor="operator:test",
                                               reason="the member's mistake", event_key="release-1")
        self.assertEqual((result["released"], result["remaining_held"], result["collateral_outcome"]), (amount, 0, "returned"))
        after = self.balance()
        self.assertEqual(after["available"], before["available"] + amount)
        self.assertEqual(after["collateral"], before["collateral"] - amount)
        self.assertEqual(self.row("SELECT collateral_outcome FROM reservations WHERE id=%s", (row["id"],))["collateral_outcome"], "returned")
        # The same key with the same inputs replays; a different amount under that key is refused.
        again = benchmarks.release_collateral(self.db, row["id"], amount=amount, actor="operator:test",
                                              reason="the member's mistake", event_key="release-1")
        self.assertEqual((again["released"], again["remaining_held"]), (amount, 0))
        self.assertEqual(self.balance(), after)
        with self.assertRaisesRegex(Conflict, "reused"):
            benchmarks.release_collateral(self.db, row["id"], amount=1, actor="operator:test",
                                          reason="the member's mistake", event_key="release-1")
        with self.assertRaisesRegex(Conflict, "already finalized or returned"):
            benchmarks.release_collateral(self.db, row["id"], amount=1, actor="operator:test", reason="again", event_key="release-2")
        operator = dashboard.operator(self.db)
        self.assertEqual([str(hold["id"]) for hold in operator["collateral"]], [])
        self.assertEqual(len(operator["collateral_releases"]), 1)
        self.assertEqual(operator["collateral_releases"][0]["details"]["amount"], str(amount))
        self.assertEqual(operator["collateral_releases"][0]["details"]["actor"], "operator:test")
        self.assertEqual(operator["members"][0]["collateral"], 0)
        member = dashboard.member(self.db, self.member)
        self.assertEqual(member["assignments"][0]["held"], 0)

    def test_partial_release_keeps_the_remainder_held(self):
        row = self.finished("part", outcome="active")
        amount, before = int(row["amount"]), self.balance()
        part = amount // 3
        result = benchmarks.release_collateral(self.db, row["id"], amount=part, actor="operator:test", reason="goodwill", event_key="part-1")
        self.assertEqual((result["released"], result["remaining_held"], result["collateral_outcome"]), (part, amount - part, None))
        self.assertIsNone(self.row("SELECT collateral_outcome FROM reservations WHERE id=%s", (row["id"],))["collateral_outcome"])
        after = self.balance()
        self.assertEqual((after["available"], after["collateral"]), (before["available"] + part, before["collateral"] - part))
        operator = dashboard.operator(self.db)
        self.assertEqual(int(operator["collateral"][0]["held"]), amount - part)
        with self.assertRaisesRegex(Conflict, "exceeds the held collateral"):
            benchmarks.release_collateral(self.db, row["id"], amount=amount, actor="operator:test", reason="too much", event_key="part-2")
        rest = benchmarks.release_collateral(self.db, row["id"], amount=amount - part, actor="operator:test", reason="the rest", event_key="part-3")
        self.assertEqual((rest["remaining_held"], rest["collateral_outcome"]), (0, "returned"))
        self.assertEqual(self.balance()["available"], before["available"] + amount)

    def test_in_flight_unknown_and_invalid_requests_are_refused(self):
        running = self.finished("running", outcome=None)
        with self.assertRaisesRegex(Conflict, "still in flight"):
            benchmarks.release_collateral(self.db, running["id"], amount=1, actor="operator:test", reason="early", event_key="flight")
        with self.assertRaisesRegex(FundsError, "positive whole number"):
            benchmarks.release_collateral(self.db, running["id"], amount=0, actor="operator:test", reason="zero", event_key="zero")
        with self.assertRaisesRegex(FundsError, "requires an operator, reason and event key"):
            benchmarks.release_collateral(self.db, running["id"], amount=1, actor="", reason="x", event_key="k")
        import uuid
        with self.assertRaisesRegex(FundsError, "unknown reservation"):
            benchmarks.release_collateral(self.db, uuid.uuid4(), amount=1, actor="operator:test", reason="x", event_key="k")
        self.assertEqual(int(self.row("SELECT balance FROM accounts WHERE id=%s", (benchmarks.held(running["id"]),))["balance"]), int(running["amount"]))
