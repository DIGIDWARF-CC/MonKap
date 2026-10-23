"""MonKap settings: a small dataclass, sanitised on load, kept in the application folder (appdata.py)."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field, fields

from .captions import CAPTION_MODES, MODE_COPY
from .naming import DEFAULT_TEMPLATE, TemplateError, normalize_template

RATIO_NAMES = ("1:1", "3:4", "4:3", "9:16", "16:9", "Free")
DELETE_SOFT = "soft"        # into the dataset's removed/ folder, with Undo
DELETE_HARD = "hard"        # the file and its caption are erased (and the cached latents, when a cache folder is set)
DELETE_MODES = (DELETE_SOFT, DELETE_HARD)


@dataclass
class MonkapSettings:
    ratio: str = "1:1"                    # last frame ratio; also the default
    template: str = DEFAULT_TEMPLATE      # output file name, no extension
    caption_mode: str = MODE_COPY         # what a new crop's caption gets
    store_provenance: bool = True         # .monkap/provenance.jsonl
    show_thirds: bool = True              # rule-of-thirds grid in the frame
    confirm_delete: bool = True           # ask before Shift+Delete removes an image
    delete_mode: str = DELETE_SOFT        # soft = recoverable, hard = erased for good
    caption_ext: str = ".txt"             # the caption files' extension (the trainer's Caption Extension)
    latent_cache_dir: str = ""            # a trainer's latent cache folder: a hard delete clears the picture's latents there
    fizgig_dir: str = ""                  # a Fizgig install to read from (integrations/fizgig.py); empty: not used
    fizgig_trigger: bool = True           # a folder without a trigger word of its own gets the one Fizgig has for it
    fizgig_ext: bool = True               # the caption files' extension is Fizgig's Caption Extension
    fizgig_cache: bool = True             # a hard delete clears the picture's latents in Fizgig's cache
    last_folder: str = ""                 # the folder shown at start
    recent: list = field(default_factory=list)      # the folders opened lately, newest first
    lm_url: str = ""                      # the caption server (empty: http://localhost:1234)
    lm_model: str = ""                    # the model on it
    ai_task: str = "training"             # a task key (ai/tasks.py), or "custom"
    ai_instructions: dict = field(default_factory=dict)   # edited task instructions, by task key
    ai_max_tokens: int = 120
    ai_think: bool = False                # let a reasoning model think first
    comfy_url: str = ""                    # the ComfyUI that enhances pictures (empty: http://127.0.0.1:8188)
    enhance_template: str = ""            # a template of the user's own (a workflow in API format); empty: the reference
    enhance_prompt: str = ""              # words for the enhance, instead of the template's own
    enhance_size: str = "2mp"             # the size of the enhanced picture: 1mp / 2mp / 4mp / original (at most 4 MP)

    @classmethod
    def from_dict(cls, raw) -> "MonkapSettings":
        """Tolerant load: unknown keys are dropped, bad values fall back to the defaults."""
        s = cls()
        if not isinstance(raw, dict):
            return s
        known = {f.name for f in fields(cls)}
        for k, v in raw.items():
            if k in known:
                setattr(s, k, v)
        if s.ratio not in RATIO_NAMES:
            s.ratio = "1:1"
        if s.caption_mode not in CAPTION_MODES:
            s.caption_mode = MODE_COPY
        if s.delete_mode not in DELETE_MODES:
            s.delete_mode = DELETE_SOFT
        try:
            s.template, _ = normalize_template(str(s.template))
        except TemplateError:
            s.template = DEFAULT_TEMPLATE
        s.store_provenance = bool(s.store_provenance)
        s.show_thirds = bool(s.show_thirds)
        s.confirm_delete = bool(s.confirm_delete)
        e = str(s.caption_ext or "").strip()
        e = e if e.startswith(".") or not e else "." + e
        s.caption_ext = e if re.fullmatch(r"\.[A-Za-z0-9_]{1,12}", e) else ".txt"
        s.latent_cache_dir = str(s.latent_cache_dir or "").strip()
        s.fizgig_dir = str(s.fizgig_dir or "").strip()
        s.fizgig_trigger, s.fizgig_ext, s.fizgig_cache = bool(s.fizgig_trigger), bool(s.fizgig_ext), bool(s.fizgig_cache)
        s.last_folder = str(s.last_folder or "")
        s.recent = [str(x) for x in s.recent if isinstance(x, str)][:12] if isinstance(s.recent, list) else []
        s.lm_url, s.lm_model = str(s.lm_url or "").strip(), str(s.lm_model or "").strip()
        s.ai_task = str(s.ai_task or "training")
        s.ai_instructions = ({str(k): str(v) for k, v in s.ai_instructions.items() if isinstance(v, str)}
                             if isinstance(s.ai_instructions, dict) else {})
        try:
            s.ai_max_tokens = min(max(int(s.ai_max_tokens), 16), 4000)
        except (TypeError, ValueError):
            s.ai_max_tokens = 120
        s.ai_think = bool(s.ai_think)
        s.comfy_url, s.enhance_template = str(s.comfy_url or "").strip(), str(s.enhance_template or "").strip()
        s.enhance_prompt = " ".join(str(s.enhance_prompt or "").split())[:600]
        s.enhance_size = s.enhance_size if s.enhance_size in ("1mp", "2mp", "4mp", "original") else "2mp"
        return s

    def to_dict(self) -> dict:
        return asdict(self)
