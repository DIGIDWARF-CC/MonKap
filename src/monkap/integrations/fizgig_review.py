"""What a Fizgig training run found out about each picture: its per-image loss watch, read-only.

Fizgig's trainer logs every step's loss with the picture it trained on (`loss_log/per_image_loss.jsonl` in the run's
output folder) and, at each epoch boundary, gives every picture a verdict (`loss_log/problem_images.json`). The runs are
listed in `user_data/runs.json`, each with the folder it trained on and the settings it used. The keys in the logs are
the pictures' names without the extension.

Like the rest of the Fizgig integration this depends on how Fizgig 8.5 stores these files; when a file or a key is not
where it was, the answer is "nothing to show" and never an error.
"""
from __future__ import annotations

import json
import os

from .fizgig import _json, looks_like_fizgig

N_BUCKETS = 40                              # the watch's own noise-level buckets (training/loss_logger.py)

# verdict: (label, colour, what it means, what to do). Fizgig's words are its own; this is MonKap's reading of them.
VERDICTS = {
    "excluded": ("Excluded", "#7F8C8D", "Fizgig took this picture out of training: two rewritten captions didn't help.",
                 "Read the caption against the picture. Fix the caption, or remove the picture from the folder."),
    "stuck": ("Stuck", "#E74C3C", "Harder than the rest at the same noise level, and it hasn't got easier.",
              "Check the caption first: does it name what is visible, in the order the others use? If the picture is crowded, "
              "frame the part you mean with Kapture. If nothing explains it, consider removing it."),
    "suspect": ("Suspect", "#D35400", "Extremely hard from the very start, which is typical of a caption that contradicts the picture.",
                "Read the caption now, before the run goes further."),
    "watch": ("Watch", "#E67E22", "Looked stuck in the last epoch; not enough epochs yet to be sure.",
              "Nothing yet. If it is still on watch in a longer run, treat it as stuck."),
    "warmup": ("Warming up", "#8E7CC3", "An unusual look (angle, framing) that Fizgig eases in at a low rate.",
               "Nothing: it is released early once it starts improving."),
    "exhausted": ("Mined out", "#16A085", "Improved a lot, then levelled off. The caption is fine.",
                  "Nothing. More epochs mostly add overbaking for this picture."),
    "learning": ("Learning", "#5B9BD5", "Hard, but getting easier.", "Leave it alone."),
    "mid": ("Normal", "#95A5A6", "About as hard as the rest of the set.", "Nothing."),
    "easy": ("Learned", "#70AD47", "Consistently easier than the rest: the model has it.", "Nothing."),
}
ORDER = ("excluded", "stuck", "suspect", "watch", "warmup", "exhausted", "learning", "mid", "easy")
PROBLEMS = ("excluded", "stuck", "suspect", "watch")


def legend() -> list[dict]:
    return [{"key": k, "label": VERDICTS[k][0], "color": VERDICTS[k][1], "means": VERDICTS[k][2], "todo": VERDICTS[k][3]}
            for k in ORDER]


def _runs(dir_: str) -> list:
    try:
        with open(os.path.join(dir_, "user_data", "runs.json"), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _same_folder(a, b) -> bool:
    return bool(a) and bool(b) and os.path.normcase(os.path.abspath(str(a))) == os.path.normcase(os.path.abspath(str(b)))


def _started(run) -> float:
    try:
        return float(run.get("started_at") or 0)
    except (TypeError, ValueError):
        return 0.0


def find_run(dir_: str, folder: str):
    """The newest run that trained on `folder` and left a per-image log (None when there is none)."""
    best = None
    for r in _runs(dir_):
        if not isinstance(r, dict) or not _same_folder(r.get("image_folder"), folder):
            continue
        out = str(r.get("output_dir") or "")
        if not out or not os.path.isfile(os.path.join(out, "loss_log", "per_image_loss.jsonl")):
            continue
        if best is None or _started(r) > _started(best):
            best = r
    return best


def residuals(log_path: str) -> dict:
    """{key: [mean residual by epoch]}: a step's loss minus the mean loss of its noise-level bucket over the whole log,
    which is the watch's own measure (a picture is not 'hard' because it drew a high noise level)."""
    recs, bsum, bcnt = [], [0.0] * N_BUCKETS, [0] * N_BUCKETS
    try:
        with open(log_path, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                    t, loss = float(r["t"]), float(r["loss"])
                except (ValueError, KeyError, TypeError):
                    continue                          # a line cut short by a crash
                b = min(int(min(max(t, 0.0), 0.999999) * N_BUCKETS), N_BUCKETS - 1)
                bsum[b] += loss
                bcnt[b] += 1
                recs.append((str(r.get("key", "")), int(r.get("epoch", 0)), b, loss))
    except OSError:
        return {}
    mean = [bsum[i] / bcnt[i] if bcnt[i] else 0.0 for i in range(N_BUCKETS)]
    per: dict = {}
    for key, ep, b, loss in recs:
        if "|" not in key:                            # a batch mean belongs to no single picture
            per.setdefault(key, {}).setdefault(ep, []).append(loss - mean[b])
    return {k: [sum(v) / len(v) for _e, v in sorted(eps.items())] for k, eps in per.items()}


def stamp(dir_: str, folder: str) -> str:
    """A short string that changes whenever what `review` reads changes (the runs list, the newest run's log and report):
    a cheap way to ask 'is there anything new?' while a run is going. '' when there is no run to read."""
    if not looks_like_fizgig(dir_):
        return ""
    run = find_run(dir_, folder)
    if run is None:
        try:
            st = os.stat(os.path.join(dir_, "user_data", "runs.json"))
            return f"none:{st.st_mtime_ns}:{st.st_size}"
        except OSError:
            return ""
    parts = [os.path.join(dir_, "user_data", "runs.json"), os.path.join(str(run["output_dir"]), "loss_log", "per_image_loss.jsonl"),
             os.path.join(str(run["output_dir"]), "loss_log", "problem_images.json")]
    out = []
    for p in parts:
        try:
            st = os.stat(p)
            out.append(f"{st.st_mtime_ns}:{st.st_size}")
        except OSError:
            out.append("-")
    return str(run.get("id")) + "|" + "|".join(out)


def review(dir_: str, folder: str) -> dict:
    """What the newest run on `folder` found out: {'available': False, 'reason'}, or the run, each picture's verdict
    (by stem) with its recent and mean residual, the run's settings that matter, and the report's summary."""
    run = find_run(dir_, folder) if looks_like_fizgig(dir_) else None
    if run is None:
        return {"available": False,
                "reason": "No run of Fizgig's has trained on this folder yet (or it wrote no per-image log)."}
    out = str(run["output_dir"])
    series = residuals(os.path.join(out, "loss_log", "per_image_loss.jsonl"))
    report = _json(out, "loss_log", "problem_images.json")
    verdicts = report.get("images") if isinstance(report.get("images"), dict) else {}
    images = {}
    for key, vals in series.items():
        v = verdicts.get(key) if isinstance(verdicts.get(key), dict) else {}
        recent = vals[-5:]
        images[key] = {"verdict": v.get("verdict") or "", "mean": round(sum(vals) / len(vals), 5),
                       "recent": round(sum(recent) / len(recent), 5), "epochs": len(vals),
                       "slope": v.get("slope"), "multiplier": v.get("multiplier")}
    values = run.get("values") if isinstance(run.get("values"), dict) else {}
    prog = run.get("progress") if isinstance(run.get("progress"), dict) else {}
    return {"available": True,
            "run": {"id": str(run.get("id") or ""), "lora": str(run.get("lora") or ""), "family": str(run.get("family") or ""),
                    "started_at": run.get("started_at"), "result": str(run.get("result") or ""),
                    "epochs": prog.get("total_epochs"), "output_dir": out},
            "report": {"epoch": report.get("epoch"), "plateaued": report.get("plateaued"),
                       "best_epoch": report.get("best_epoch_estimate"), "improving": report.get("improving_count")},
            "settings": {"loss_watch": bool(values.get("KREA2_LOSS_WATCH")), "per_image_lr": bool(values.get("KREA2_PER_IMAGE_LR")),
                         "auto_recaption": bool(values.get("KREA2_AUTO_RECAPTION")), "warmup_look": bool(values.get("KREA2_WARMUP_LOOK"))},
            "images": images}


def advice(rev: dict) -> list[str]:
    """Plain advice from a review: what the verdicts say about the set, and which of Fizgig's own settings could help.
    The settings are named as in Fizgig 8.5; they may differ in other versions."""
    if not rev.get("available"):
        return []
    imgs, rep, st = rev["images"], rev["report"], rev["settings"]
    n = len(imgs)
    count = {k: sum(1 for i in imgs.values() if i["verdict"] == k) for k in ORDER}
    bad = sum(count[k] for k in ("stuck", "suspect", "excluded"))
    out = []
    if n and n < 30:
        out.append(f"Only {n} pictures: verdicts compare the pictures with each other, so in a small set they are noisy. "
                   "Treat them as a place to look first, not as a diagnosis.")
    if n and bad / n >= 0.25:
        out.append(f"{bad} of {n} pictures read as stuck or suspect. When that many do, it is usually the set (many different "
                   "scenes, a lot of fine detail) and not that many bad captions. Start with the hardest few, and read whether "
                   "their captions describe the detail that makes them hard.")
    if bad and not st["auto_recaption"]:
        out.append("In Fizgig, Training: tick \"Auto-recaption stuck images\". It rewrites a stuck picture's caption between "
                   "epochs (it needs the Krea 2 text encoder set in Preferences). Rewriting by hand here works too.")
    if bad and not st["per_image_lr"]:
        out.append("In Fizgig, Training: tick \"Per-image adaptive LR\". It slows the stuck pictures so one bad picture can't "
                   "keep pulling the weights, and gives the learned ones a small boost.")
    if not st["loss_watch"]:
        out.append("The run didn't have \"Detect problem images\" ticked, so there are no verdicts of the watch: tick it in "
                   "Fizgig's Training tab.")
    be, total = rep.get("best_epoch"), rev["run"].get("epochs")
    if rep.get("plateaued") and be and total and be < total:
        out.append(f"Fizgig estimates the learning finished around epoch {be} of {total}. Later checkpoints add overbaking: "
                   f"try the saved checkpoints near epoch {be} first (LoRA Royale), and next time train for fewer epochs.")
    if count["watch"]:
        out.append(f"{count['watch']} on watch: a longer run would show whether they are stuck.")
    return out
