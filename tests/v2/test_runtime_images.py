"""The pool pins each active challenge's official runtime image itself."""

import io
import json
import unittest
from unittest.mock import Mock
from urllib.error import HTTPError

import psycopg2

from pool_manager.pool_v2 import benchmarks, runtime_images
from pool_manager.pool_v2.protocol import ProtocolDataError
from funds_helpers import DatabaseCase


def image(name, digest):
    return f"ghcr.io/tig-foundation/tig-monorepo/{name}/runtime@sha256:{digest}"


class Response:
    def __init__(self, body, headers):
        self.body, self.headers = body, headers
    def read(self, limit=None):
        return self.body
    def __enter__(self):
        return self
    def __exit__(self, *_):
        return False


def registry(digests, token="anonymous-pull-token", version="0.0.8"):
    """Fake ghcr: anonymous token per repository, HEAD on a tag answers its digest."""
    def opener(request, timeout):
        url = request.full_url
        if url.startswith("https://ghcr.io/token?scope=repository:tig-foundation/tig-monorepo/"):
            name = url.split("tig-monorepo/")[1].split("/runtime:pull")[0]
            if name not in digests:
                raise HTTPError(url, 403, "denied", {}, None)
            return Response(json.dumps({"token": token}).encode(), {})
        assert request.get_method() == "HEAD" and request.get_header("Authorization") == "Bearer " + token
        name, tag = url.split("tig-monorepo/")[1].split("/runtime/manifests/")
        if digests.get(name) is None or tag != version:
            raise HTTPError(url, 404, "manifest unknown", {}, None)
        return Response(b"", {"Docker-Content-Digest": "sha256:" + digests[name]})
    return opener


class ResolveTests(unittest.TestCase):
    def test_digest_lookup_uses_an_anonymous_pull_token_and_reports_unpublished_runtimes(self):
        opener = registry({"knapsack": "b" * 64, "unpublished": None})
        self.assertEqual(runtime_images.resolve_digest("knapsack", "0.0.8", opener=opener), "b" * 64)
        self.assertIsNone(runtime_images.resolve_digest("knapsack", "0.0.9", opener=opener))  # tag not pushed yet
        self.assertIsNone(runtime_images.resolve_digest("unpublished", "0.0.8", opener=opener))
        self.assertIsNone(runtime_images.resolve_digest("hypergraph", "0.0.8", opener=opener))  # no such repository
        for name, version in (("Knapsack", "0.0.8"), ("knapsack", "latest"), ("../knapsack", "0.0.8"), (None, "0.0.8")):
            with self.subTest(name=name, version=version), self.assertRaises(ValueError):
                runtime_images.resolve_digest(name, version, opener=opener)
        with self.assertRaises(runtime_images.RegistryError):
            runtime_images.resolve_digest("knapsack", "0.0.8", opener=registry({"knapsack": "not-a-digest"}))
        def outage(request, timeout):
            raise HTTPError(request.full_url, 503, "unavailable", {}, None)
        with self.assertRaises(HTTPError):
            runtime_images.resolve_digest("knapsack", "0.0.8", opener=outage)

    def test_image_maps_name_official_pinned_digests_only(self):
        good = {"c003": image("knapsack", "b" * 64)}
        self.assertEqual(runtime_images.check_images(good), good)
        for invalid in (["c003"], {"c003": "ghcr.io/tig-foundation/tig-monorepo/knapsack/runtime:0.0.8"},
                        {"c003": image("Knapsack", "b" * 64)}, {"": image("knapsack", "b" * 64)}, {"c003": None},
                        {"c003": "docker.io/tig-foundation/tig-monorepo/knapsack/runtime@sha256:" + "b" * 64}):
            with self.subTest(invalid=invalid), self.assertRaises(ProtocolDataError):
                runtime_images.check_images(invalid)


class RecordTests(DatabaseCase):
    def test_records_change_history_and_serves_the_newest_image_per_version(self):
        self.assertEqual(runtime_images.current(self.db, "0.0.8"), {})
        self.assertTrue(runtime_images.record(self.db, "c003", "knapsack", "0.0.8", image("knapsack", "b" * 64)))
        # The same answer again adds nothing; a re-published tag is a new record.
        self.assertFalse(runtime_images.record(self.db, "c003", "knapsack", "0.0.8", image("knapsack", "b" * 64)))
        self.assertTrue(runtime_images.record(self.db, "c003", "knapsack", "0.0.8", image("knapsack", "c" * 64)))
        self.assertTrue(runtime_images.record(self.db, "c005", "hypergraph", "0.0.8", image("hypergraph", "d" * 64)))
        self.assertEqual(runtime_images.current(self.db, "0.0.8"),
                         {"c003": image("knapsack", "c" * 64), "c005": image("hypergraph", "d" * 64)})
        self.assertEqual(runtime_images.current(self.db, "0.0.9"), {})  # another version pins nothing until resolved
        self.assertTrue(runtime_images.record(self.db, "c003", "knapsack", "0.0.9", image("knapsack", "e" * 64)))
        self.assertEqual(runtime_images.current(self.db, "0.0.8")["c003"], image("knapsack", "c" * 64))
        self.assertEqual(runtime_images.latest(self.db, "c003"), image("knapsack", "e" * 64))
        self.assertIsNone(runtime_images.latest(self.db, "c001"))
        rows = runtime_images.rows(self.db, "0.0.8")
        self.assertEqual([(row["challenge_id"], row["challenge_name"], row["image"]) for row in rows],
                         [("c003", "knapsack", image("knapsack", "c" * 64)), ("c005", "hypergraph", image("hypergraph", "d" * 64))])
        self.assertTrue(all(row["resolved_at"] for row in rows))
        self.assertEqual(self.row("SELECT count(*) AS n FROM runtime_images")["n"], 4)
        for args in (("c003", "knapsack", "0.0.8", image("hypergraph", "b" * 64)),  # image of another challenge
                     ("c003", "Knapsack", "0.0.8", image("Knapsack", "b" * 64)),
                     ("c003", "knapsack", "latest", image("knapsack", "b" * 64)),
                     ("x3", "knapsack", "0.0.8", image("knapsack", "b" * 64)),
                     ("c003", "knapsack", "0.0.8", "ghcr.io/tig-foundation/tig-monorepo/knapsack/runtime:0.0.8")):
            with self.subTest(args=args), self.assertRaises((ProtocolDataError, ValueError)):
                runtime_images.record(self.db, *args)
        for statement in ("UPDATE runtime_images SET image=%s", "DELETE FROM runtime_images", "TRUNCATE runtime_images"):
            with self.subTest(statement=statement), self.assertRaises(psycopg2.Error):
                with self.db.transaction() as cursor:
                    cursor.execute(statement, (image("knapsack", "f" * 64),) if "%s" in statement else None)

    def test_resolver_pins_active_challenges_by_their_tig_names_at_most_once_per_interval(self):
        snapshot = Mock(challenges={"c003": {"config": {"name": "knapsack"}}, "c005": {"config": {"name": "hypergraph"}},
                                    "c009": {"config": {"name": "Not A Path"}}, "c010": {"config": {"name": "unpublished"}},
                                    "c011": {"config": {"name": "failing"}}})
        digests = {"knapsack": "b" * 64, "hypergraph": "c" * 64, "unpublished": None}
        calls = []
        def resolve(name, version):
            calls.append((name, version))
            if name == "failing":
                raise OSError("registry unreachable")
            return digests[name]
        clock = [1000.0]
        resolver = runtime_images.Resolver(self.db, "0.0.8", interval=600, resolve=resolve, clock=lambda: clock[0])
        self.assertTrue(resolver.due())
        self.assertEqual(resolver.refresh(snapshot), {"runtime_version": "0.0.8", "pinned": ["c003", "c005"],
            "changed": ["c003", "c005"], "unpublished": ["c010"], "failed": ["c009", "c011"]})
        self.assertEqual(calls, [("knapsack", "0.0.8"), ("hypergraph", "0.0.8"), ("unpublished", "0.0.8"), ("failing", "0.0.8")])
        self.assertEqual(resolver.current(), {"c003": image("knapsack", "b" * 64), "c005": image("hypergraph", "c" * 64)})
        self.assertFalse(resolver.due())
        clock[0] += 599
        self.assertFalse(resolver.due())
        clock[0] += 1
        self.assertTrue(resolver.due())
        digests["knapsack"] = "d" * 64  # TIG re-published the tag: the newest digest is pinned, the history kept
        outcome = resolver.refresh(snapshot)
        self.assertEqual((outcome["pinned"], outcome["changed"]), (["c003", "c005"], ["c003"]))
        self.assertEqual(resolver.current()["c003"], image("knapsack", "d" * 64))
        self.assertEqual(self.row("SELECT count(*) AS n FROM runtime_images WHERE challenge_id='c003'")["n"], 2)
        for version, interval in (("latest", 600), (None, 600), ("0.0.8", 0), ("0.0.8", 86401), ("0.0.8", "600")):
            with self.subTest(version=version, interval=interval), self.assertRaises(ValueError):
                runtime_images.Resolver(self.db, version, interval=interval)

    def test_migration_grants_the_table_to_the_deployment_roles_when_they_exist(self):
        # The deployment grant file is release-paired and unchanged; the migration itself grants the new
        # table once the roles exist. A disposable test database has no such roles until this test adds them.
        with self.db.transaction() as cursor:
            for role in ("innopool_runtime", "innopool_observer"):
                cursor.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,))
                if not cursor.fetchone():
                    cursor.execute("CREATE ROLE " + role + " NOLOGIN")
            cursor.execute("DROP SCHEMA pool_v2 CASCADE")
        self.db.migrate()
        benchmarks.initialize_accounts(self.db)
        with self.db.transaction() as cursor:
            cursor.execute("""SELECT has_table_privilege('innopool_runtime','pool_v2.runtime_images','SELECT') AS runtime_select,
                has_table_privilege('innopool_runtime','pool_v2.runtime_images','INSERT') AS runtime_insert,
                has_table_privilege('innopool_runtime','pool_v2.runtime_images','DELETE') AS runtime_delete,
                has_table_privilege('innopool_observer','pool_v2.runtime_images','SELECT') AS observer_select,
                has_table_privilege('innopool_observer','pool_v2.runtime_images','INSERT') AS observer_insert""")
            self.assertEqual(dict(cursor.fetchone()), {"runtime_select": True, "runtime_insert": True, "runtime_delete": False,
                                                       "observer_select": True, "observer_insert": False})


if __name__ == "__main__":
    unittest.main()
