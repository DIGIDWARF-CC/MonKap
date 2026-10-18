"""What the model is told for each kind of caption. Plain text, one per task; the user can edit any of them in the
settings (kept per task) and write their own ("custom"). Each task suggests how long a caption may be."""
from __future__ import annotations

START = "Begin directly with the subject. Never open with 'This image shows', 'The image depicts' or a similar preamble. "

TASKS = {
    "training": ("Training caption", 120,
                 "Write one factual training caption for this image as a single sentence, covering in order: the subject "
                 "and what it is doing; the camera viewpoint (for example 'viewed from behind', 'side profile', "
                 "'close-up'); the pose or arrangement; colours and materials; the setting; the lighting. Use the same "
                 "order and the same plain phrasing every time. " + START +
                 "Name the subject with the specific term that is visually apparent, not a vague one. Mention anything "
                 "prominent a viewer would notice, especially an unusual angle, framing, or what is hidden or cropped. "
                 "State only what is visible: no speculation, no names, no comments on style or quality."),
    "short": ("Short caption", 60,
              "Describe this image in one short sentence: the main subject and what it is doing or where it is. " + START +
              "State only what is visible."),
    "detailed": ("Detailed description", 220,
                 "Write a detailed factual description of this image in 2 to 4 sentences. Cover the subject and how much "
                 "of it is visible, its pose or arrangement, every visible part with colours and materials, anything "
                 "partly hidden or cropped, the lighting, and the background with its main objects. " + START +
                 "State only what is visible: no speculation, no names, no comments on style."),
    "tags": ("Tag list", 100,
             "List the visible content of this image as short, comma-separated tags: the subject, its parts, colours, "
             "materials, the setting, the lighting and the camera angle. Lower case, no sentences, no repeats."),
}
DEFAULT_TASK = "training"
CUSTOM = "custom"


def listing(edited: dict) -> list[dict]:
    """The tasks for the page: key, label, the suggested length, the text in use, the shipped text, and whether it is edited."""
    out = []
    for key, (label, tokens, text) in TASKS.items():
        mine = str((edited or {}).get(key) or "").strip()
        out.append({"key": key, "label": label, "tokens": tokens, "builtin": text, "instruction": mine or text, "edited": bool(mine)})
    mine = str((edited or {}).get(CUSTOM) or "").strip()
    base = TASKS[DEFAULT_TASK]
    out.append({"key": CUSTOM, "label": "Custom…", "tokens": base[1], "builtin": base[2], "instruction": mine or base[2], "edited": bool(mine)})
    return out


def instruction(key: str, edited: dict) -> str:
    for t in listing(edited):
        if t["key"] == key:
            return t["instruction"]
    return listing(edited)[0]["instruction"]
