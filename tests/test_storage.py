import tempfile
import unittest
from pathlib import Path

from storage import CampaignStore


class CampaignStoreTests(unittest.TestCase):
    def test_campaign_lifecycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = CampaignStore(Path(tmp) / "campaigns.sqlite3")
            targets = [("username", "one"), ("id", -1001), ("username", "one")]
            campaign_id = store.create_campaign(42, "hello", targets, dry_run=True)
            store.mark_started(campaign_id)
            store.record_target(campaign_id, ("username", "one"), "dry_run")
            store.finish(campaign_id, "completed", sent=2)

            row = store.get(campaign_id)
            self.assertEqual(row["status"], "completed")
            self.assertEqual(row["total"], 2)
            self.assertEqual(row["sent"], 2)
            self.assertEqual(len(store.recent(42)), 1)
            store.close()

    def test_cancel_marks_pending_targets(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = CampaignStore(Path(tmp) / "campaigns.sqlite3")
            campaign_id = store.create_campaign(
                1, "hello", [("username", "one"), ("username", "two")]
            )
            store.finish(campaign_id, "cancelled", sent=1)
            row = store.get(campaign_id)
            self.assertEqual(row["status"], "cancelled")
            store.close()

    def test_work_tasks_are_scoped_to_owner(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = CampaignStore(Path(tmp) / "campaigns.sqlite3")
            task_id = store.create_task(10, "prepare report")
            self.assertEqual(len(store.list_tasks(10)), 1)
            self.assertEqual(len(store.list_tasks(11)), 0)
            self.assertTrue(store.complete_task(10, task_id))
            self.assertFalse(store.complete_task(11, task_id))
            self.assertEqual(store.list_tasks(10)[0]["status"], "done")
            store.close()


if __name__ == "__main__":
    unittest.main()
