"""The Enhance lab's server side (monkap/lab.py through api.py) against a made-up ComfyUI: tasks, the worker, the one-at-a-time
rule, what survives a restart, and the decisions about waiting results."""
import json
import os
import tempfile
import time
import unittest

import _path  # noqa: F401
from fake_comfy import RESULT, FakeComfy
from test_api import call_get, call_post, make_png

from monkap import enhance, lab
from test_comfy import CLASSES


class Case(unittest.TestCase):
    steps = 3

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.d = self.tmp.name
        self._home = os.environ.get("MONKAP_HOME")
        os.environ["MONKAP_HOME"] = os.path.join(self.d, "home")
        self.ds = os.path.join(self.d, "ds")
        os.makedirs(self.ds)
        for n in ("a", "b", "c"):
            make_png(os.path.join(self.ds, n + ".png"), (60, 40))
            with open(os.path.join(self.ds, n + ".txt"), "w", encoding="utf-8") as f:
                f.write("caption " + n)
        self.fc = FakeComfy(classes=CLASSES, steps=self.steps)
        call_post("/api/settings", {"settings": {"comfy_url": self.fc.url}})
        enhance._run = None
        self.reset_lab()

    def reset_lab(self):
        lab._queue.clear()
        lab._stops.clear()
        lab._last_error.clear()
        lab._run = None

    def tearDown(self):
        for _ in range(300):
            if not lab.running():
                break
            lab.shutdown()
            time.sleep(0.05)
        self.fc.stop()
        if self._home is None:
            os.environ.pop("MONKAP_HOME", None)
        else:
            os.environ["MONKAP_HOME"] = self._home
        self.tmp.cleanup()

    def post(self, route, **b):
        return call_post("/api/lab/" + route, {"folder": self.ds, **b})

    def state(self):
        return call_get("/api/lab/state", folder=self.ds).data

    def task(self, names=("a", "b"), **extra):
        h = self.post("task", names=[n + ".png" for n in names], **extra)
        self.assertEqual(h.code, 200, h.data)
        return h.data["id"]

    def wait_idle(self, timeout=20):
        end = time.time() + timeout
        while time.time() < end:
            if not lab.running():
                return self.state()
            time.sleep(0.05)
        self.fail(f"the lab never became idle: {self.state()}")


class TaskTests(Case):
    def test_a_task_runs_its_pictures_and_leaves_results_waiting(self):
        tid = self.task(instruction="  only the face ")
        self.assertEqual(self.state()["assigned"], {"a.png": tid, "b.png": tid})
        self.assertEqual(self.post("start", task=tid).code, 200)
        st = self.wait_idle()
        t = st["tasks"][0]
        self.assertEqual((t["state"], t["done"], t["total"], t["pictures"]), ("done", 2, 2, []))
        self.assertEqual(sorted(p["name"] for p in st["pending"]), ["a.png", "b.png"])
        self.assertEqual(st["assigned"], {})
        self.assertTrue(all(not p["stale"] for p in st["pending"]))
        got = call_get("/api/lab/result", folder=self.ds, name="a.png")
        self.assertEqual(got.body, RESULT)
        self.assertTrue(self.fc.prompts[0]["2"]["inputs"]["value"].endswith("only the face"))
        self.assertEqual(len(self.fc.prompts), 2)

    def test_a_picture_in_a_task_or_waiting_is_skipped_at_creation(self):
        self.task(("a",))
        h = self.post("task", names=["a.png", "b.png"])
        self.assertEqual((h.data["added"], h.data["skipped"]), (1, ["a.png"]))
        h = self.post("task", names=["a.png", "b.png"])
        self.assertEqual(h.code, 400)
        self.assertIn("already in a task", h.data["error"])

    def test_a_running_task_cannot_be_edited_and_the_main_view_waits(self):
        self.fc.stop()
        self.fc = FakeComfy(classes=CLASSES, steps=40, step_delay=0.05)
        call_post("/api/settings", {"settings": {"comfy_url": self.fc.url}})
        tid = self.task()
        self.post("start", task=tid)
        self.assertTrue(lab.running())
        self.assertEqual(self.post("task/update", task=tid, instruction="x").code, 400)
        self.assertEqual(self.post("task/delete", task=tid).code, 400)
        h = call_post("/api/enhance/start", {"folder": self.ds, "name": "c.png"})
        self.assertEqual(h.code, 400)
        live = call_get("/api/live", folder=self.ds).data
        self.assertEqual(live["busy"], {"single": False, "lab": True})
        self.assertEqual(self.state()["current"]["name"], "a.png")
        self.assertEqual(self.post("stop", task=tid).code, 200)
        st = self.wait_idle()
        self.assertEqual(st["tasks"][0]["state"], "stopped")
        self.assertEqual(st["tasks"][0]["pictures"], ["a.png", "b.png"])      # nothing finished: nothing left the task
        self.assertEqual(self.post("task/update", task=tid, instruction="x").code, 200)

    def test_the_lab_refuses_to_start_while_the_main_view_runs(self):
        self.fc.stop()
        self.fc = FakeComfy(classes=CLASSES, steps=40, step_delay=0.05)
        call_post("/api/settings", {"settings": {"comfy_url": self.fc.url}})
        self.assertEqual(call_post("/api/enhance/start", {"folder": self.ds, "name": "c.png"}).code, 200)
        tid = self.task()
        self.assertEqual(self.post("start", task=tid).code, 400)
        call_post("/api/enhance/cancel", {})

    def test_the_queue_runs_tasks_one_after_another_and_numbers_them(self):
        self.fc.stop()
        self.fc = FakeComfy(classes=CLASSES, steps=10, step_delay=0.05)
        call_post("/api/settings", {"settings": {"comfy_url": self.fc.url}})
        t1, t2 = self.task(("a",)), self.task(("b",))
        self.post("start", task=t1)
        self.post("start", task=t2)
        tasks = {t["id"]: t for t in self.state()["tasks"]}
        self.assertEqual((tasks[t1]["position"], tasks[t2]["position"]), (1, 2))
        self.assertEqual(self.post("stop", task=t2).code, 200)               # waiting its turn: simply dropped
        self.assertEqual({t["id"]: t["state"] for t in self.state()["tasks"]}[t2], "stopped")
        st = self.wait_idle()
        self.assertEqual([p["name"] for p in st["pending"]], ["a.png"])

    def test_three_failures_in_a_row_stop_the_task_and_the_failed_stay_marked(self):
        self.fc.stop()
        self.fc = FakeComfy(classes=CLASSES, steps=2, fail=True)
        call_post("/api/settings", {"settings": {"comfy_url": self.fc.url}})
        for n in ("d", "e"):
            make_png(os.path.join(self.ds, n + ".png"), (60, 40))
        tid = self.task(("a", "b", "c", "d", "e"))
        self.post("start", task=tid)
        st = self.wait_idle()
        t = st["tasks"][0]
        self.assertEqual(t["state"], "stopped")
        self.assertEqual(len(t["failed"]), 3)
        self.assertEqual(len(t["pictures"]), 5)
        self.assertIn("3 failures in a row", st["error"])
        self.assertEqual(st["pending"], [])

    def test_a_task_comes_back_stopped_after_a_restart(self):
        tid = self.task()
        path = os.path.join(lab._dir(self.ds), "state.json")
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        data["tasks"][0]["state"] = "running"                                 # as if MonKap was closed in the middle of it
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        t = self.state()["tasks"][0]
        self.assertEqual((t["id"], t["state"]), (tid, "stopped"))
        self.assertEqual(self.post("start", task=tid).code, 200)
        self.assertEqual(self.wait_idle()["tasks"][0]["state"], "done")


class ResolveTests(Case):
    def made(self):
        tid = self.task(("a", "b"))
        self.post("start", task=tid)
        self.wait_idle()

    def resolve(self, name, what, **extra):
        return self.post("resolve", name=name + ".png", action=what, **extra)

    def test_discard_drops_the_result_and_its_file(self):
        self.made()
        before = os.listdir(lab._dir(self.ds))
        self.assertEqual(self.resolve("a", "discard").code, 200)
        self.assertEqual([p["name"] for p in self.state()["pending"]], ["b.png"])
        self.assertEqual(len(os.listdir(lab._dir(self.ds))), len(before) - 1)
        self.assertEqual(self.resolve("a", "discard").code, 400)

    def test_save_makes_a_new_picture_and_marks_it_enhanced(self):
        self.made()
        h = self.resolve("a", "save")
        self.assertEqual(h.code, 200, h.data)
        self.assertTrue(os.path.isfile(os.path.join(self.ds, h.data["name"])))
        self.assertNotEqual(h.data["name"], "a.png")
        self.assertEqual([p["name"] for p in self.state()["pending"]], ["b.png"])

    def test_replace_swaps_the_picture_but_asks_when_the_original_changed(self):
        self.made()
        time.sleep(0.05)
        make_png(os.path.join(self.ds, "a.png"), (80, 50))                    # changed since the result was made
        st = self.state()
        self.assertTrue(next(p for p in st["pending"] if p["name"] == "a.png")["stale"])
        h = self.resolve("a", "replace")
        self.assertEqual(h.data, {"stale": True})
        self.assertEqual(len(self.state()["pending"]), 2)                     # nothing was decided
        h = self.resolve("a", "replace", force=True)
        self.assertEqual(h.code, 200, h.data)
        self.assertTrue(h.data["backup"])
        h = self.resolve("b", "replace")
        self.assertEqual(h.code, 200, h.data)
        self.assertEqual(self.state()["pending"], [])

    def test_delete_removes_the_original_as_unfixable(self):
        self.made()
        h = self.resolve("a", "delete")
        self.assertEqual(h.code, 200, h.data)
        self.assertFalse(os.path.exists(os.path.join(self.ds, "a.png")))
        self.assertEqual([p["name"] for p in self.state()["pending"]], ["b.png"])

    def test_regenerate_waits_for_the_lab_to_be_idle_then_makes_a_new_result(self):
        self.made()
        seeds = [p["seed"] for p in self.state()["pending"] if p["name"] == "a.png"]
        h = self.post("regenerate", name="a.png")
        self.assertEqual(h.code, 200, h.data)
        st = self.wait_idle()
        pend = [p for p in st["pending"] if p["name"] == "a.png"]
        self.assertEqual(len(pend), 1)
        self.assertNotEqual(pend[0]["seed"], seeds[0])
        self.assertEqual([t for t in st["tasks"] if t["state"] != "done"], [])  # the helper task does not show
        self.assertEqual(len(self.fc.prompts), 3)

    def test_regenerate_is_refused_while_a_task_runs(self):
        self.made()
        self.fc.stop()
        self.fc = FakeComfy(classes=CLASSES, steps=40, step_delay=0.05)
        call_post("/api/settings", {"settings": {"comfy_url": self.fc.url}})
        tid = self.task(("c",))
        self.post("start", task=tid)
        h = self.post("regenerate", name="a.png")
        self.assertEqual(h.code, 400)
        self.assertIn("after all tasks are done or stopped", h.data["error"])
        self.post("stop", task=tid)


if __name__ == "__main__":
    unittest.main()
