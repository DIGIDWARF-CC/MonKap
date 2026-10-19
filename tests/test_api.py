"""The /api routes (monkap/api.py), called the way the server calls them: with a fake
request handler that records what it was asked to send. No sockets, no GPU."""
import io
import json
import os
import tempfile
import unittest

import _path  # noqa: F401
from PIL import Image

from monkap import api, appdata


class FakeHandler:
    def __init__(self):
        self.code, self.data, self.body, self.ctype = None, None, None, None

    def json(self, data, code=200, extra=()):
        self.code, self.data = code, data

    def _send(self, code, body, ctype, extra=()):
        self.code, self.body, self.ctype = code, body, ctype


def make_png(path, size=(300, 200), color=(120, 40, 200)):
    Image.new("RGB", size, color).save(path)


def call_get(route, **qs):
    h = FakeHandler()
    api.get(h, route, {k: [str(v)] for k, v in qs.items()})
    return h


def call_post(path, body):
    h = FakeHandler()
    api.post(h, path, body)
    return h


class ApiCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = self.tmp.name
        self._home = os.environ.get("MONKAP_HOME")
        os.environ["MONKAP_HOME"] = os.path.join(self.d, "home")                   # never the real application folder
        os.makedirs(os.path.join(self.d, "ds"))
        self.ds = os.path.join(self.d, "ds")
        for n in ("img_10.png", "img_2.png", "img_1.jpg"):
            make_png(os.path.join(self.ds, n))
        with open(os.path.join(self.ds, "img_2.txt"), "w", encoding="utf-8") as f:
            f.write("a belt over two pulleys")

    def tearDown(self):
        if self._home is None:
            os.environ.pop("MONKAP_HOME", None)
        else:
            os.environ["MONKAP_HOME"] = self._home
        self.tmp.cleanup()


class ListAndLocateTests(ApiCase):
    def test_list_is_natural_ordered_with_caption_flags(self):
        h = call_get("/api/list", folder=self.ds)
        self.assertEqual(h.code, 200)
        self.assertEqual(h.data["items"], [["img_1.jpg", 0], ["img_2.png", 1], ["img_10.png", 0]])

    def test_list_skips_non_pictures_hidden_files_and_subfolders(self):
        open(os.path.join(self.ds, "notes.md"), "w").close()
        open(os.path.join(self.ds, ".hidden.png"), "wb").close()
        os.makedirs(os.path.join(self.ds, "sub.png"))
        names = [n for n, _ in call_get("/api/list", folder=self.ds).data["items"]]
        self.assertEqual(names, ["img_1.jpg", "img_2.png", "img_10.png"])

    def test_the_caption_flag_follows_the_caption_extension(self):
        open(os.path.join(self.ds, "img_10.caption"), "w").close()
        call_post("/api/settings", {"settings": {"caption_ext": ".caption"}})
        items = call_get("/api/list", folder=self.ds).data["items"]
        self.assertEqual(dict(items), {"img_1.jpg": 0, "img_2.png": 0, "img_10.png": 1})

    def test_a_missing_folder_is_404(self):
        self.assertEqual(call_get("/api/list", folder=os.path.join(self.d, "nope")).code, 404)

    def test_names_that_are_not_plain_pictures_are_refused(self):
        for bad in ("../ds/img_2.png", "sub/img_2.png", "..\\img_2.png", ".hidden.png", "notes.md", "", "img_2.txt"):
            h = call_get("/api/info", folder=self.ds, name=bad)
            self.assertIn(h.code, (400, 404), bad)

    def test_a_vanished_picture_is_404(self):
        self.assertEqual(call_get("/api/info", folder=self.ds, name="gone.png").code, 404)


class InfoAndCaptionTests(ApiCase):
    def test_info_reads_size_and_caption(self):
        d = call_get("/api/info", folder=self.ds, name="img_2.png").data
        self.assertEqual((d["size"], d["caption"], d["inherited"]), ([300, 200], "a belt over two pulleys", False))
        self.assertIsNone(call_get("/api/info", folder=self.ds, name="img_10.png").data["caption"])

    def test_save_roundtrip_and_clear(self):
        h = call_post("/api/caption", {"folder": self.ds, "name": "img_10.png", "text": " new words ",
                                              "loaded": None})
        self.assertEqual((h.code, h.data["kind"], h.data["has_caption"]), (200, "written", True))
        with open(os.path.join(self.ds, "img_10.txt"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "new words")
        h = call_post("/api/caption", {"folder": self.ds, "name": "img_10.png", "text": "  ",
                                              "loaded": "new words"})
        self.assertEqual((h.data["kind"], h.data["has_caption"]), ("cleared", False))
        self.assertFalse(os.path.exists(os.path.join(self.ds, "img_10.txt")))
        self.assertTrue(h.data["backup"].startswith(".monkap"))

    def test_a_caption_changed_behind_the_editor_is_kept_in_backups(self):
        h = call_post("/api/caption", {"folder": self.ds, "name": "img_2.png", "text": "mine",
                                              "loaded": "something older"})
        self.assertTrue(h.data["conflict"])
        self.assertTrue(os.path.isfile(os.path.join(self.ds, h.data["backup"])))

    def test_save_follows_the_caption_extension(self):
        call_post("/api/settings", {"settings": {"caption_ext": "caption"}})
        call_post("/api/caption", {"folder": self.ds, "name": "img_10.png", "text": "x", "loaded": None})
        self.assertTrue(os.path.isfile(os.path.join(self.ds, "img_10.caption")))
        self.assertFalse(os.path.isfile(os.path.join(self.ds, "img_10.txt")))


class PictureTests(ApiCase):
    def big(self, size=(5000, 3000)):
        p = os.path.join(self.ds, "big.png")
        Image.new("RGB", size, (10, 200, 30)).save(p)
        return p

    def test_a_small_picture_is_sent_at_native_size(self):
        h = call_get("/api/view", folder=self.ds, name="img_2.png")
        self.assertEqual((h.code, h.ctype), (200, "image/jpeg"))
        self.assertEqual(Image.open(io.BytesIO(h.body)).size, (300, 200))

    def test_a_big_picture_is_scaled_to_the_edge_and_a_tile_is_native_when_small(self):
        self.big()
        h = call_get("/api/view", folder=self.ds, name="big.png", edge=1000)
        self.assertEqual(Image.open(io.BytesIO(h.body)).size, (1000, 600))
        h = call_get("/api/tile", folder=self.ds, name="big.png", x0=100, y0=50, x1=500, y1=350, w=2000)
        self.assertEqual(Image.open(io.BytesIO(h.body)).size, (400, 300))             # native: no resampling
        h = call_get("/api/tile", folder=self.ds, name="big.png", x0=0, y0=0, x1=4000, y1=2000, w=800)
        self.assertEqual(Image.open(io.BytesIO(h.body)).size, (800, 400))

    def test_a_transparent_picture_stays_transparent(self):
        p = os.path.join(self.ds, "alpha.png")
        Image.new("RGBA", (40, 40), (255, 0, 0, 90)).save(p)
        h = call_get("/api/view", folder=self.ds, name="alpha.png")
        self.assertEqual(h.ctype, "image/png")
        self.assertEqual(Image.open(io.BytesIO(h.body)).mode, "RGBA")

    def test_tile_outside_the_picture_is_clamped_or_refused(self):
        h = call_get("/api/tile", folder=self.ds, name="img_2.png", x0=250, y0=150, x1=999, y1=999, w=500)
        self.assertEqual(Image.open(io.BytesIO(h.body)).size, (50, 50))
        h = call_get("/api/tile", folder=self.ds, name="img_2.png", x0=400, y0=0, x1=500, y1=10, w=100)
        self.assertEqual(h.code, 400)

    def test_thumbnail(self):
        h = call_get("/api/thumb", folder=self.ds, name="img_2.png")
        self.assertEqual((h.code, h.ctype), (200, "image/jpeg"))

    def test_pixels_are_not_turned_by_exif(self):
        p = os.path.join(self.ds, "turned.jpg")
        im = Image.new("RGB", (60, 20), (1, 2, 3))
        exif = Image.Exif()
        exif[0x0112] = 6                                                  # "rotate 90": a viewer would turn it
        im.save(p, exif=exif)
        self.assertEqual(call_get("/api/info", folder=self.ds, name="turned.jpg").data["size"], [60, 20])
        h = call_get("/api/view", folder=self.ds, name="turned.jpg")
        self.assertEqual(Image.open(io.BytesIO(h.body)).size, (60, 20))


class KaptureApiTests(ApiCase):
    def test_kapture_uses_the_saved_settings(self):
        call_post("/api/settings", {"settings": {"caption_mode": "trigger", "template": "{parent}_p{n:02d}"}})
        h = call_post("/api/kapture", {"folder": self.ds, "name": "img_2.png", "box": [10, 20, 110, 120],
                                              "text": "a belt over two pulleys", "trigger": "engine_timing"})
        self.assertEqual(h.code, 200)
        self.assertEqual(h.data["name"], "img_2_p01.png")
        self.assertEqual(h.data["size"], [100, 100])
        self.assertEqual(h.data["caption"], "engine_timing, a belt over two pulleys")
        with Image.open(os.path.join(self.ds, "img_2_p01.png")) as im:
            self.assertEqual(im.size, (100, 100))
        self.assertTrue(os.path.isfile(os.path.join(self.ds, "img_2_p01.txt")))
        h2 = call_post("/api/kapture", {"folder": self.ds, "name": "img_2.png", "box": [0, 0, 50, 50],
                                               "text": "", "trigger": ""})
        self.assertEqual(h2.data["name"], "img_2_p02.png")

    def test_the_new_crop_is_flagged_as_inherited_until_its_caption_changes(self):
        call_post("/api/kapture", {"folder": self.ds, "name": "img_2.png", "box": [0, 0, 80, 80],
                                          "text": "a belt over two pulleys", "trigger": ""})
        crop = "img_2_crop_001.png"
        self.assertTrue(call_get("/api/info", folder=self.ds, name=crop).data["inherited"])
        call_post("/api/caption", {"folder": self.ds, "name": crop, "text": "a belt, closer",
                                          "loaded": "a belt over two pulleys"})
        self.assertFalse(call_get("/api/info", folder=self.ds, name=crop).data["inherited"])

    def test_a_bad_box_is_a_400_and_nothing_is_written(self):
        before = sorted(os.listdir(self.ds))
        for box in ([0, 0, 9999, 10], [5, 5, 5, 5], "nope", [1, 2, 3]):
            h = call_post("/api/kapture", {"folder": self.ds, "name": "img_2.png", "box": box,
                                                  "text": "", "trigger": ""})
            self.assertEqual(h.code, 400, box)
        self.assertEqual(sorted(os.listdir(self.ds)), before)


class DeleteApiTests(ApiCase):
    def test_soft_delete_is_the_default_and_undo_restores(self):
        h = call_post("/api/delete", {"folder": self.ds, "name": "img_2.png"})
        self.assertEqual((h.code, h.data["mode"]), (200, "soft"))
        self.assertFalse(os.path.exists(os.path.join(self.ds, "img_2.png")))
        self.assertTrue(os.path.isfile(os.path.join(self.ds, "removed", "img_2.png")))
        self.assertTrue(os.path.isfile(os.path.join(self.ds, "removed", "img_2.txt")))
        h = call_post("/api/undelete", {"folder": self.ds, "moved": h.data["moved"]})
        self.assertEqual(sorted(h.data["restored"]), ["img_2.png", "img_2.txt"])
        self.assertTrue(os.path.isfile(os.path.join(self.ds, "img_2.png")))

    def test_hard_delete_erases_the_picture_and_its_caption(self):
        call_post("/api/settings", {"settings": {"delete_mode": "hard"}})
        h = call_post("/api/delete", {"folder": self.ds, "name": "img_2.png"})
        self.assertEqual((h.code, h.data["mode"]), (200, "hard"))
        self.assertEqual(sorted(os.listdir(self.ds)), ["img_1.jpg", "img_10.png"])
        self.assertFalse(os.path.exists(os.path.join(self.ds, "removed")))

    def test_deleting_a_name_that_is_not_there_is_404(self):
        self.assertEqual(call_post("/api/delete", {"folder": self.ds, "name": "gone.png"}).code, 404)


class SettingsApiTests(ApiCase):
    def test_defaults_roundtrip_and_garbage(self):
        d = call_get("/api/state").data
        self.assertEqual(d["settings"]["delete_mode"], "soft")
        self.assertEqual(d["examples"], ["image_001_crop_001.png", "image_001_crop_002.png"])
        h = call_post("/api/settings", {"settings": {"ratio": "16:9", "delete_mode": "bogus", "junk": 1,
                                                            "confirm_delete": False}})
        s = h.data["settings"]
        self.assertEqual((s["ratio"], s["delete_mode"], s["confirm_delete"]), ("16:9", "soft", False))
        with open(appdata.user_file("settings.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["ratio"], "16:9")
        self.assertEqual(call_get("/api/state").data["settings"]["ratio"], "16:9")

    def test_a_template_without_a_number_gets_one_and_a_bad_one_is_refused(self):
        h = call_post("/api/settings", {"settings": {"template": "{parent}_part"}})
        self.assertEqual(h.data["settings"]["template"], "{parent}_part_{n:03d}")
        self.assertTrue(h.data["note"])
        self.assertEqual(call_post("/api/settings", {"settings": {"template": "{oops}"}}).code, 400)

    def test_live_template_preview(self):
        d = call_get("/api/template", t="{parent}_x{n}").data
        self.assertEqual((d["ok"], d["examples"][0]), (True, "image_001_x1.png"))
        self.assertFalse(call_get("/api/template", t="{bad}").data["ok"])

    def test_unknown_route_is_404(self):
        self.assertEqual(call_get("/api/nothing").code, 404)
        self.assertEqual(call_post("/api/nothing", {}).code, 404)


if __name__ == "__main__":
    unittest.main()


class FolderAndDataTests(ApiCase):
    def test_opening_a_folder_remembers_it_and_its_trigger(self):
        h = call_post("/api/folder", {"folder": self.ds, "trigger": " ohwx "})
        self.assertEqual((h.code, h.data["trigger"]), (200, "ohwx"))
        self.assertEqual(call_get("/api/list", folder=self.ds).data["trigger"], "ohwx")
        s = call_get("/api/state").data["settings"]
        self.assertEqual((s["last_folder"], s["recent"]), (os.path.abspath(self.ds), [os.path.abspath(self.ds)]))
        call_post("/api/folder", {"folder": self.ds})                       # no trigger given: the old one stays
        self.assertEqual(call_get("/api/list", folder=self.ds).data["trigger"], "ohwx")
        self.assertEqual(call_post("/api/folder", {"folder": os.path.join(self.d, "nope")}).code, 404)

    def test_browse_lists_folders_and_counts_pictures(self):
        os.makedirs(os.path.join(self.ds, "sub"))
        os.makedirs(os.path.join(self.ds, ".monkap"))
        d = call_get("/api/browse", path=self.ds).data
        self.assertEqual([x["name"] for x in d["dirs"]], ["sub"])
        self.assertEqual(d["pictures"], 3)
        self.assertEqual(call_get("/api/browse", path=os.path.join(self.d, "nope")).code, 404)

    def test_browse_lists_the_files_of_the_kinds_asked_for_and_takes_a_file_as_its_folder(self):
        for n in ("a.json", "B.JSON", "notes.txt", ".hidden.json"):
            with open(os.path.join(self.ds, n), "w", encoding="utf-8") as f:
                f.write("{}")
        d = call_get("/api/browse", path=self.ds, files=".json, .nope!").data
        self.assertEqual([x["name"] for x in d["files"]], ["a.json", "B.JSON"])
        self.assertEqual(call_get("/api/browse", path=self.ds).data["files"], [])          # none asked for: none listed
        d = call_get("/api/browse", path=os.path.join(self.ds, "a.json"), files=".json").data
        self.assertEqual(d["path"], os.path.abspath(self.ds))

    def test_forget_removes_only_the_application_folder(self):
        call_post("/api/folder", {"folder": self.ds, "trigger": "x"})
        files = call_get("/api/data").data["files"]
        self.assertEqual(sorted(f[0] for f in files), ["folders.json", "settings.json"])
        self.assertEqual(call_post("/api/data/forget", {}).data["removed"], 2)
        self.assertFalse(os.path.exists(appdata.root()))
        self.assertTrue(os.path.isfile(os.path.join(self.ds, "img_2.png")))            # the dataset is untouched

    def test_the_picture_is_gone_with_undo_in_removed_and_back(self):
        d = call_post("/api/delete", {"folder": self.ds, "name": "img_2.png"}).data
        self.assertEqual(d["mode"], "soft")
        self.assertTrue(os.path.isfile(os.path.join(self.ds, "removed", "img_2.txt")))
        call_post("/api/undelete", {"folder": self.ds, "moved": d["moved"]})
        self.assertTrue(os.path.isfile(os.path.join(self.ds, "img_2.png")))
