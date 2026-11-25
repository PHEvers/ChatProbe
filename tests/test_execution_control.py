import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from queue import Queue
from tempfile import TemporaryDirectory
from threading import Event, Thread
from time import monotonic
from unittest.mock import patch

from src.chatprobe.browser import smoke
from src.chatprobe.browser.timing import utc_now
from src.chatprobe.providers import Action, ProviderError, ProviderProfile, retry_after_seconds
from src.chatprobe.runner import batch
from src.chatprobe.runner.consumption import (
    ConsumptionLedger, ExecutionOptions, LocalQuotaError, Quota, estimate_tokens, generate_with_controls,
    quota_scopes,
)
from src.chatprobe.storage.queues import QueueStore, scheduled_timestamp
from src.chatprobe.storage.sqlite_store import RunStore


class Clock:
    def __init__(self):
        self.now = 100000

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class LedgerTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "test.sqlite3"
        self.clock = Clock()
        self.ledger = ConsumptionLedger(RunStore(self.path), self.clock)

    def test_four_limits_and_daily_persistence(self):
        for index, (quota, delay) in enumerate((
            (Quota(rpm=1), 60), (Quota(rpd=1), 86400),
            (Quota(tpm=10), 60), (Quota(tpd=10), 86400),
        )):
            with self.subTest(quota=quota):
                scopes = [(f"scope-{index}", quota)]
                identifier, _ = self.ledger.reserve(scopes, 10)
                reopened = ConsumptionLedger(RunStore(self.path), self.clock)
                self.assertTrue(identifier)
                self.assertEqual(reopened.reserve(scopes, 10), (None, delay))
                self.clock.advance(delay)
                self.assertIsNotNone(reopened.reserve(scopes, 10)[0])

    def test_thread_reservations_share_scope_and_are_atomic(self):
        scopes = [("service", Quota(rpm=2)), ("queue", Quota(rpm=5))]
        with ThreadPoolExecutor(8) as pool:
            attempts = list(pool.map(lambda _: self.ledger.reserve(scopes, 10), range(16)))
        self.assertEqual(sum(identifier is not None for identifier, _ in attempts), 2)
        self.assertEqual(self.ledger.snapshot("queue")[0], (2, 0, 2))

    def test_model_and_queue_limits_intersect_with_shared_provider(self):
        base = ProviderProfile("one", "Um", "openai", "https://example.invalid/v1", "m",
                               quota="2 10 0 0", model_quota="1 0 0 0")
        other = replace(base, id="two", model="different", quota="3 0 0 0", model_quota="")
        policy = ExecutionOptions(queue_quota="0 1 0 0")
        first = quota_scopes(base, base.metadata(), "queue1", policy, [other])
        second = quota_scopes(other, other.metadata(), "queue2", policy, [base])
        self.assertEqual(second[0][1], Quota(rpm=2, rpd=10))
        self.assertEqual(first[0][0], second[0][0])
        self.assertNotEqual(first[1][0], second[1][0])
        self.assertTrue(self.ledger.reserve(first, 1)[0])
        self.assertTrue(self.ledger.reserve(second, 1)[0])
        self.assertEqual(self.ledger.reserve(second, 1), (None, 86400))

    def test_estimates_reconcile_only_with_measurements(self):
        scope = [("scope", Quota(tpm=10))]
        first, _ = self.ledger.reserve(scope, 9)
        self.ledger.finish(first, {"total_tokens": 2}, "ok")
        second, _ = self.ledger.reserve(scope, 8)
        self.ledger.finish(second, {}, "ok")
        self.assertEqual(self.ledger.snapshot("scope")[0], (2, 2, 1))
        self.assertEqual(self.ledger.reserve(scope, 1), (None, 60))
        with self.assertRaises(LocalQuotaError):
            self.ledger.reserve(scope, 11)
        self.assertGreater(estimate_tokens("😀", [], {}), estimate_tokens("a", [], {}))

    def test_retries_and_retry_after_are_persisted(self):
        records = []
        waits = []
        scope = [("scope", Quota())]

        def wait_for(delay, label):
            waits.append(delay)
            self.clock.advance(delay)

        with patch("src.chatprobe.runner.consumption.estimate_tokens", return_value=10):
            response = iter([ProviderError("rate_limit", 5), ProviderError("temporary"), Action("send", "ok")])

            def generate(*args, **kwargs):
                value = next(response)
                if isinstance(value, Exception):
                    raise value
                return value

            decision = generate_with_controls(
                generate, "teste", [], {}, ledger=self.ledger, scopes=scope, test_id=None,
                policy=ExecutionOptions(retries=2), checkpoint=lambda: None, wait_for=wait_for,
                records=records, clock=self.clock, jitter=lambda *_: 1,
            )
        self.assertEqual(decision.text, "ok")
        self.assertEqual(waits, [5, 2])
        self.assertEqual([record["outcome"] for record in records], ["rate_limit", "temporary", "ok"])
        with self.ledger.store.transaction() as db:
            count, delay = db.execute("SELECT COUNT(*),MAX(retry_after) FROM api_requests").fetchone()
        self.assertEqual((count, delay), (3, 5))

    def test_permanent_errors_and_daily_retry_wait_budget(self):
        for error in (ProviderError("auth"), ProviderError("model"), ProviderError("rate_limit", 3600)):
            with self.subTest(code=error.code), patch("src.chatprobe.runner.consumption.estimate_tokens", return_value=1):
                with self.assertRaises(ProviderError), patch.object(self.ledger, "reserve", wraps=self.ledger.reserve) as reserve:
                    def bad(*args):
                        raise error
                    generate_with_controls(bad, "teste", [], {}, ledger=self.ledger, scopes=[(error.code, Quota())],
                                           test_id=None, policy=ExecutionOptions(retries=3, max_wait=2),
                                           checkpoint=lambda: None, wait_for=lambda *_: self.fail("unexpected wait"),
                                           records=[], clock=self.clock)
                self.assertEqual(reserve.call_count, 1)
        self.assertEqual(retry_after_seconds("120"), 120)
        self.assertEqual(retry_after_seconds("Thu, 01 Jan 1970 00:01:00 GMT", now=30), 30)
        self.assertIsNone(retry_after_seconds("invalid"))


class RunnerControlTests(unittest.TestCase):
    def test_pause_resume_and_stop_interrupt_wait(self):
        control = batch.BatchControl()
        waiting = Event()
        ended = Event()

        def worker():
            try:
                control.wait(30, lambda _: waiting.set())
            except batch.BatchStopped:
                ended.set()

        worker_thread = Thread(target=worker, daemon=True)
        worker_thread.start()
        self.assertTrue(waiting.wait(2))
        control.pause()
        waiting.clear()
        self.assertFalse(waiting.wait(0.05))
        control.resume()
        self.assertTrue(waiting.wait(2))
        control.stop()
        worker_thread.join(2)
        self.assertTrue(ended.is_set())

    def test_send_interval_applies_only_to_consecutive_real_messages(self):
        scope = Event()
        now = Clock()
        options = ExecutionOptions(interaction_seconds=4)
        waits = []

        def simulate_wait(self, seconds, notify):
            waits.append(seconds)
            now.advance(seconds)

        def chat(argv, *, log, scenario, next_message, turns, before_message, on_send, **kwargs):
            before_message()
            on_send()
            turns.append({"message": "Enviado", "replies": []})
            now.advance(1)
            before_message()
            on_send()

        with patch.object(batch, "monotonic", now), patch.object(smoke, "main", side_effect=chat), patch.object(
            batch.BatchControl, "wait", simulate_wait,
        ):
            result = batch.run_test(1, "https://example.invalid", ("teste", []), "", None, scope, Queue(),
                                    execution=options, control=batch.BatchControl())
        self.assertEqual(waits, [3])
        self.assertEqual(result["wait_seconds"]["Intervalo entre interações"], 3)

    def test_parallel_initial_wave_and_interval_before_replacement(self):
        started = []
        first_wave = Event()
        events = Queue()

        def fake_run(number, *args, **kwargs):
            started.append((number, monotonic()))
            if len(started) == 2:
                first_wave.set()
            self.assertTrue(first_wave.wait(1))
            return {"test": number, "status": "completed"}

        with patch.object(batch, "run_test", side_effect=fake_run):
            batch.run_batch("https://example.invalid", 3, 2, events,
                            execution=ExecutionOptions(test_seconds=0.08))
        self.assertEqual([number for number, _ in sorted(started)], [1, 2, 3])
        self.assertGreaterEqual(started[2][1] - max(stamp for _, stamp in started[:2]), 0.07)


class QueueTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = RunStore(Path(directory.name) / "test.sqlite3")
        self.queues = QueueStore(self.store)

    def test_schedule_cancellation_and_exclusive_claim(self):
        scheduled = scheduled_timestamp((datetime.now(UTC) + timedelta(minutes=5)).isoformat())
        identifier = self.queues.create({"mode": "JSON"}, ["original"], scheduled)
        self.assertEqual(self.queues.available(), [(identifier, "scheduled", scheduled)])
        self.assertTrue(self.queues.cancel(identifier))
        self.assertFalse(self.queues.claim(identifier, "app"))
        second = self.queues.create({}, ["b"])
        self.assertTrue(self.queues.claim(second, "app"))
        self.assertFalse(self.queues.claim(second, "another"))
        with self.assertRaises(ValueError):
            scheduled_timestamp("2000-01-01 10:00")

    def test_recovers_active_partial_without_replaying_it_or_completed_items(self):
        identifier = self.queues.create({"model": "old"}, ["first", "second", "third"])
        self.queues.claim(identifier, "lost-app")
        self.queues.set_item(identifier, 1, "completed", {"test": 1})
        self.queues.set_item(identifier, 2, "running")
        test = self.store.start_test(identifier, 2, "json", utc_now())
        self.store.save_turn(test, 1, {"message": "enviado", "replies": [], "sent_at": utc_now(),
                                       "reply_received_at": []})
        with self.store.transaction() as db:
            db.execute("UPDATE execution_queues SET updated_at='2000' WHERE id=?", (identifier,))
        self.queues.recover_stale()
        snapshot, items = self.queues.read(identifier)
        self.assertEqual(snapshot["model"], "old")
        self.assertEqual([status for _, _, status, _ in items], ["completed", "stopped", "pending"])
        self.assertEqual(items[1][3]["turns"][0]["message"], "enviado")
        self.assertTrue(self.queues.claim(identifier, "new-app"))
