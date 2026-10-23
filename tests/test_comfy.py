"""Enhancing with a ComfyUI: the template contract, the checks, the WebSocket client and a whole run against a made-up server."""
import copy
import json
import os
import socket
import time
import unittest

import _path  # noqa: F401
from fake_comfy import PREVIEW, RESULT, FakeComfy, frame

from monkap.ai import comfy
from monkap.ai.wsclient import BINARY, TEXT, WebSocket, WebSocketError

HERE = os.path.dirname(os.path.abspath(__file__))
REFERENCE = os.path.join(HERE, "..", "src", "monkap", "ai", "templates", "enhance-qwen21-reference.json")


def reference() -> dict:
    with open(REFERENCE, encoding="utf-8") as f:
        return json.load(f)


# what the made-up server knows: every class of the reference, with the inputs a literal value sits in
CLASSES = {"LoadImage": ["image"], "PrimitiveStringMultiline": ["value"], "PrimitiveInt": ["value"], "PreviewImage": [],
           "UNETLoader": ["unet_name", "weight_dtype"], "CLIPLoader": ["clip_name", "type", "device"], "VAELoader": ["vae_name"],
           "TextEncodeQwenImage21": ["prompt"], "KSampler": ["seed", "steps"], "VAEDecode": []}


class TemplateTests(unittest.TestCase):
    def test_the_reference_template_is_valid_and_its_markers_are_found(self):
        t = comfy.parse_template(reference())
        self.assertEqual(t.nodes, {"image": "1", "prompt": "2", "seed": "3", "size": "13", "result": "4"})
        self.assertEqual((t.fields["image"], t.fields["prompt"], t.fields["seed"], t.fields["result"]),
                         ("image", "value", "value", "images"))
        self.assertEqual(t.version, 1)

    def test_a_workflow_saved_from_the_editor_is_refused_with_the_way_out(self):
        with self.assertRaisesRegex(comfy.TemplateError, "Save \\(API Format\\)"):
            comfy.parse_template({"nodes": [], "links": [], "version": 0.4})

    def test_not_json_and_not_a_workflow(self):
        with self.assertRaises(comfy.TemplateError):
            comfy.parse_template("{broken")
        with self.assertRaises(comfy.TemplateError):
            comfy.parse_template({"1": "not a node"})
        with self.assertRaises(comfy.TemplateError):
            comfy.parse_template([])

    def test_the_picture_and_the_result_are_required_the_prompt_and_the_seed_are_not(self):
        g = reference()
        del g["2"], g["3"]
        g["9"]["inputs"]["prompt"] = "fixed"
        g["10"]["inputs"]["seed"] = 5
        self.assertEqual(sorted(comfy.parse_template(g).nodes), ["image", "result", "size"])      # (the size node is the reference's)
        del g["13"]
        g["9"]["inputs"]["resolution"] = 1024
        self.assertEqual(sorted(comfy.parse_template(g).nodes), ["image", "result"])
        g = reference()
        g["1"]["_meta"]["title"] = "Load Image"
        with self.assertRaisesRegex(comfy.TemplateError, "MonKap Image"):
            comfy.parse_template(g)

    def test_a_duplicated_marker_is_refused(self):
        g = reference()
        g["20"] = copy.deepcopy(g["3"])
        with self.assertRaisesRegex(comfy.TemplateError, "2 nodes are titled \"MonKap Seed\""):
            comfy.parse_template(g)

    def test_a_marked_node_must_have_the_input_that_is_written(self):
        g = reference()
        g["3"]["inputs"] = {"something": 1}
        with self.assertRaisesRegex(comfy.TemplateError, "MonKap Seed"):
            comfy.parse_template(g)

    def test_a_missing_link_is_caught_by_following_the_graph(self):
        g = reference()
        g["9"]["inputs"]["images.image_1"] = "unlinked.png"                  # the picture no longer feeds the result
        with self.assertRaisesRegex(comfy.TemplateError, "MonKap Image"):
            comfy.parse_template(g)

    def test_titles_may_carry_a_version_and_a_newer_one_is_refused(self):
        g = reference()
        g["4"]["_meta"]["title"] = "monkap result V2 (do not alter)"
        with self.assertRaisesRegex(comfy.TemplateError, "version 2"):
            comfy.parse_template(g)
        g["4"]["_meta"]["title"] = "MonKap Result (do not alter)"           # no version means 1
        self.assertEqual(comfy.parse_template(g).version, 1)

    def test_prepare_writes_the_marked_nodes_and_leaves_the_template_alone(self):
        t = comfy.parse_template(reference())
        g = comfy.prepare(t, "abc.png [temp]", "sharper", 77)
        self.assertEqual(g["1"]["inputs"]["image"], "abc.png [temp]")
        self.assertEqual((g["2"]["inputs"]["value"], g["3"]["inputs"]["value"]), ("sharper", 77))
        self.assertEqual(t.graph["1"]["inputs"]["image"], "example.png")
        self.assertEqual(comfy.prepare(t, "x [temp]")["2"]["inputs"]["value"], t.graph["2"]["inputs"]["value"])

    def test_the_size_marker_is_optional_written_when_given_and_must_be_linked(self):
        t = comfy.parse_template(reference())
        self.assertEqual(comfy.prepare(t, "a [temp]", size=2048)["13"]["inputs"]["value"], 2048)
        self.assertEqual(comfy.prepare(t, "a [temp]")["13"]["inputs"]["value"], 1448)           # not given: the template's own
        g = reference()
        del g["13"]
        g["9"]["inputs"]["resolution"] = 1024                                                    # an older template: no size node
        old = comfy.parse_template(g)
        self.assertNotIn("size", old.nodes)
        self.assertEqual(comfy.prepare(old, "a [temp]", size=2048)["9"]["inputs"]["resolution"], 1024)
        g = reference()
        g["9"]["inputs"]["resolution"] = 1024                                                    # the node is there but feeds nothing
        with self.assertRaisesRegex(comfy.TemplateError, "MonKap Size"):
            comfy.parse_template(g)


class ProblemsTests(unittest.TestCase):
    def setUp(self):
        self.fc = FakeComfy(classes=CLASSES, choices={("UNETLoader", "unet_name"): ["qwen_image_2.1_int8_convrot.safetensors"],
                                                      ("CLIPLoader", "clip_name"): ["qwen3vl_8b_int8_convrot.safetensors"],
                                                      ("VAELoader", "vae_name"): ["qwen_image_2.1_vae_bf16.safetensors"]})
        self.client = comfy.Client(self.fc.url)

    def tearDown(self):
        self.fc.stop()

    def test_a_server_that_has_everything_has_no_problems(self):
        self.assertEqual(comfy.problems(comfy.parse_template(reference()), self.client.node_info), [])

    def test_a_missing_node_and_a_missing_model_are_named(self):
        del self.fc.classes["TextEncodeQwenImage21"]
        self.fc.choices[("UNETLoader", "unet_name")] = ["other.safetensors"]
        text = "\n".join(comfy.problems(comfy.parse_template(reference()), self.client.node_info))
        self.assertIn("no node \"TextEncodeQwenImage21\"", text)
        self.assertIn("qwen_image_2.1_int8_convrot.safetensors", text)
        self.assertIn("Load Diffusion Model", text)

    def test_the_new_form_of_a_combo_is_understood(self):
        self.assertEqual(comfy._options(["COMBO", {"options": ["a", "b"]}]), ["a", "b"])
        self.assertEqual(comfy._options([["a"]]), ["a"])
        self.assertIsNone(comfy._options(["STRING", {}]))


class WebSocketTests(unittest.TestCase):
    def setUp(self):
        self.fc = FakeComfy()
        self.ws = WebSocket(self.fc.url.replace("http", "ws") + "/ws?clientId=t", timeout=3)
        for _ in range(50):
            if self.fc.clients:
                break
            time.sleep(0.02)

    def tearDown(self):
        self.ws.close()
        self.fc.stop()

    def send(self, raw):
        self.fc.clients[0].sendall(raw)

    def test_text_and_binary_messages_and_the_wait_that_finds_nothing(self):
        self.assertIsNone(self.ws.recv(timeout=0.1))
        self.send(frame(1, "héllo".encode()))
        self.send(frame(2, b"\x00\x01"))
        self.assertEqual(self.ws.recv(timeout=2), (TEXT, "héllo"))
        self.assertEqual(self.ws.recv(timeout=2), (BINARY, b"\x00\x01"))

    def test_a_long_message_and_one_that_arrives_in_pieces(self):
        big = b"x" * 70000
        self.send(frame(2, big))
        self.assertEqual(self.ws.recv(timeout=2), (BINARY, big))
        whole = frame(1, b"in pieces")
        self.send(whole[:3])
        self.assertIsNone(self.ws.recv(timeout=0.1))                         # half a frame is kept, not lost
        self.send(whole[3:])
        self.assertEqual(self.ws.recv(timeout=2), (TEXT, "in pieces"))

    def test_a_fragmented_message_is_joined_and_a_ping_does_not_get_in_the_way(self):
        self.send(frame(1, b"one ", fin=False) + frame(9, b"ping") + frame(0, b"two"))
        self.assertEqual(self.ws.recv(timeout=2), (TEXT, "one two"))

    def test_close_from_the_server_raises(self):
        self.send(frame(8, b"\x03\xe8"))
        with self.assertRaises(WebSocketError):
            self.ws.recv(timeout=2)
        with self.assertRaises(WebSocketError):
            self.ws.recv(timeout=0.1)

    def test_no_websocket_on_the_server_is_an_error_not_a_crash(self):
        fc = FakeComfy(ws=False)
        try:
            with self.assertRaises(WebSocketError):
                WebSocket(fc.url.replace("http", "ws") + "/ws", timeout=2)
        finally:
            fc.stop()
        with self.assertRaises(WebSocketError):
            WebSocket("http://nope/ws")


class JobTests(unittest.TestCase):
    def make(self, **kw):
        fc = FakeComfy(classes=CLASSES, **kw)
        self.addCleanup(fc.stop)
        return fc, comfy.Client(fc.url, timeout=5)

    def run_job(self, client, **kw):
        job = comfy.Job(client, comfy.parse_template(reference()), b"\xff\xd8photo-bytes", ".jpg", **kw).start()
        job.join(15)
        return job

    def test_a_whole_run_reports_progress_gives_the_picture_and_leaves_nothing_behind(self):
        fc, client = self.make(steps=4)
        job = comfy.Job(client, comfy.parse_template(reference()), b"\xff\xd8photo-bytes", ".jpg", prompt="sharper", seed=99).start()
        seen = set()
        while job.status in ("queued", "running"):
            seen.add(job.snapshot()["percent"])
            time.sleep(0.01)
        job.join(5)
        snap = job.snapshot()
        self.assertEqual((job.status, snap["percent"], snap["step"], snap["steps"]), ("done", 100, 4, 4), job.error)
        self.assertTrue(snap["result"] and snap["live"])
        self.assertEqual(job.result, RESULT)
        self.assertEqual((job.preview, snap["preview"] > 0), (PREVIEW, True))
        self.assertTrue(any(p not in (None, 0, 100) for p in seen), seen)          # in between, not only start and end
        # what was sent: a temporary upload with a random name, the marked nodes filled in, the run forgotten afterwards
        name, data, temp = fc.uploads[0]
        self.assertTrue(name.startswith("monkap_") and name.endswith(".jpg") and temp and data == b"\xff\xd8photo-bytes")
        g = fc.prompts[0]
        self.assertEqual(g["1"]["inputs"]["image"], name + " [temp]")
        self.assertEqual((g["2"]["inputs"]["value"], g["3"]["inputs"]["value"]), ("sharper", 99))
        self.assertEqual(len(fc.deleted), 1)

    def test_the_stage_follows_the_nodes_by_their_titles(self):
        fc, client = self.make(steps=3, step_delay=0.15)
        job = comfy.Job(client, comfy.parse_template(reference()), b"x", ".png").start()
        stages = set()
        while job.status in ("queued", "running"):
            stages.add(job.snapshot()["stage"])
            time.sleep(0.01)
        self.assertIn("KSampler", stages)
        self.assertEqual(job.snapshot()["stage"], "Done")

    def test_an_error_in_comfy_reaches_the_person_with_the_node_and_the_reason(self):
        _fc, client = self.make(fail=True)
        job = self.run_job(client)
        self.assertEqual(job.status, "error")
        self.assertIn("out of memory", job.error)
        self.assertIn("KSampler", job.error)
        self.assertIsNone(job.result)

    def test_cancel_interrupts_comfy_and_ends_the_job(self):
        fc, client = self.make(hang=True)
        job = comfy.Job(client, comfy.parse_template(reference()), b"x", ".png").start()
        for _ in range(100):
            if fc.prompts:
                break
            time.sleep(0.02)
        job.cancel()
        job.join(10)
        self.assertEqual(job.status, "cancelled")
        self.assertTrue(fc.interrupted.is_set())
        self.assertEqual(len(fc.queue_deleted), 1)

    def test_without_a_progress_channel_the_run_still_finishes(self):
        _fc, client = self.make(ws=False)
        job = self.run_job(client)
        snap = job.snapshot()
        self.assertEqual((job.status, job.result), ("done", RESULT), job.error)
        self.assertFalse(snap["live"])
        self.assertIsNone(snap["percent"])

    def test_a_job_comfy_refuses_says_why(self):
        _fc, client = self.make(reject={"error": {"type": "prompt_outputs_failed_validation", "message": "Prompt outputs failed validation"},
                                        "node_errors": {"5": {"class_type": "UNETLoader", "errors": [
                                            {"message": "Value not in list", "details": "unet_name: 'x' not in []"}]}}})
        job = self.run_job(client)
        self.assertEqual(job.status, "error")
        self.assertIn("Prompt outputs failed validation", job.error)
        self.assertIn("UNETLoader 5: Value not in list", job.error)

    def test_a_server_that_is_not_there_is_a_plain_message(self):
        with socket.socket() as sock:                                  # a port that was free a moment ago: nothing listens
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        job = comfy.Job(comfy.Client(f"http://127.0.0.1:{port}", timeout=2), comfy.parse_template(reference()), b"x", ".png").start()
        job.join(10)
        self.assertEqual(job.status, "error")
        self.assertIn("Can't reach ComfyUI", job.error)

    def test_free_memory_asks_comfy_to_unload(self):
        fc, client = self.make()
        client.free()
        self.assertEqual(fc.freed, 1)

    def test_addresses_are_tidied(self):
        self.assertEqual(comfy.normalize_url(""), comfy.DEFAULT_URL)
        self.assertEqual(comfy.normalize_url("localhost:8188/"), "http://localhost:8188")
        self.assertEqual(comfy.normalize_url("http://10.0.0.5:8188"), "http://10.0.0.5:8188")


if __name__ == "__main__":
    unittest.main()
