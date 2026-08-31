import json
import unittest

from solution_runtime import Solution


class ManifestDashboardTest(unittest.TestCase):
    def test_omitted_when_unset(self):
        manifest = Solution(id="lastlogin", title="Last Login").manifest()
        self.assertNotIn("dashboard", manifest)

    def test_included_when_set(self):
        graph = {"events": [], "metrics": [], "dashboards": []}
        manifest = Solution(id="lastlogin", title="Last Login", dashboard=graph).manifest()
        self.assertEqual(manifest["dashboard"], graph)

    def test_round_trips_through_json_unchanged(self):
        graph = {
            "events": [{"name": "login", "fields": {"user": "string"}}],
            "metrics": [{"name": "logins", "event": "login", "agg": "count"}],
            "dashboards": [{"title": "Activity", "widgets": [{"metric": "logins"}]}],
        }
        manifest = Solution(id="lastlogin", title="Last Login", dashboard=graph).manifest()
        self.assertEqual(json.loads(json.dumps(manifest))["dashboard"], graph)


if __name__ == "__main__":
    unittest.main()
