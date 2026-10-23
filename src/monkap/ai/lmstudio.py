"""Captions from a model served by LM Studio (or any OpenAI-compatible server): the picture goes over HTTP to the
address set in MonKap's settings, `http://localhost:1234` unless changed. Nothing here uses this machine's GPU.

Two endpoints are used: LM Studio's own /api/v1/models for the list (it says which models can see pictures) and the
OpenAI-style /v1/chat/completions for the captions, which is the one every OpenAI-compatible server has.
"""
import base64
import io
import json
import os
import re
import urllib.error
import urllib.request

DEFAULT_URL = "http://localhost:1234"
SIDE = 1024                            # the longest side a picture is sent at: plenty for a caption, quick to read
THINK_EXTRA_TOKENS = 1500              # a model that thinks first needs room for it before the caption

_PROBE_TIMEOUT = 4
_CAPTION_TIMEOUT = 600                 # the first picture waits while LM Studio loads the model


def base_url(url) -> str:
    """The server's address, tidied: http(s) only, no trailing slash, no /v1 on the end."""
    u = str(url or "").strip() or DEFAULT_URL
    if not re.match(r"^https?://", u, re.I):
        u = "http://" + u
    u = u.rstrip("/")
    for tail in ("/v1", "/api/v1"):
        if u.lower().endswith(tail):
            u = u[: -len(tail)]
    if not re.match(r"^https?://[^/\s]+$", u, re.I):
        raise ValueError("The LM Studio address should look like http://localhost:1234")
    return u


def _get_json(url):
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=_PROBE_TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def list_models(url) -> list:
    """The models that can caption: language models that see pictures, as [{"id", "loaded"}]. Raises OSError (the server
    isn't there) or ValueError (it answered with something else)."""
    base = base_url(url)
    try:
        data = _get_json(base + "/api/v1/models")
    except urllib.error.HTTPError:
        data = None                                # not LM Studio's own API: the OpenAI list has no vision flag
    except urllib.error.URLError as e:
        raise OSError(f"Nothing answers at {base} ({e.reason}).") from e
    except (TimeoutError, OSError) as e:
        raise OSError(f"Nothing answers at {base} ({e}).") from e
    except ValueError as e:
        raise ValueError(f"{base} didn't answer like LM Studio.") from e
    out = []
    if data is not None:
        for m in data.get("models") or data.get("data") or []:
            caps = m.get("capabilities") or {}
            if m.get("type") in (None, "llm", "vlm") and caps.get("vision"):
                out.append({"id": str(m.get("key") or m.get("id")), "loaded": bool(m.get("loaded_instances"))})
        return out
    try:
        for m in _get_json(base + "/v1/models").get("data") or []:
            out.append({"id": str(m["id"]), "loaded": False})     # no way to tell here: the user knows
    except (OSError, ValueError, KeyError) as e:
        raise OSError(f"Couldn't read the model list from {base} ({e}).") from e
    return out


def _post_json(url, payload, timeout=_PROBE_TIMEOUT * 5):
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode("utf-8", "replace")
    return json.loads(raw) if raw.strip() else {}


def unload(url, model) -> int:
    """Unload every loaded instance of `model` (LM Studio's own API). Returns how many were unloaded: a model that is not
    loaded is not an error, it is 0. Raises OSError when the server isn't there and ValueError when it isn't LM Studio."""
    base = base_url(url)
    try:
        data = _get_json(base + "/api/v1/models")
    except urllib.error.HTTPError as e:
        raise ValueError(f"{base} can't unload models: it is not LM Studio's own API.") from e
    except urllib.error.URLError as e:
        raise OSError(f"Nothing answers at {base} ({e.reason}).") from e
    except (TimeoutError, OSError) as e:
        raise OSError(f"Nothing answers at {base} ({e}).") from e
    except ValueError as e:
        raise ValueError(f"{base} didn't answer like LM Studio.") from e
    ids = [str(i.get("id")) for m in (data.get("models") or data.get("data") or [])
           if str(m.get("key") or m.get("id")) == model for i in (m.get("loaded_instances") or []) if i.get("id")]
    done = 0
    for iid in ids:
        try:
            _post_json(base + "/api/v1/models/unload", {"instance_id": iid})
            done += 1
        except urllib.error.HTTPError:
            continue                                 # gone meanwhile (unloaded in LM Studio itself): fine
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise OSError(f"Nothing answers at {base} ({getattr(e, 'reason', e)}).") from e
    return done


def _data_uri(path) -> str:
    from PIL import Image
    im = Image.open(path)                     # the pixels as stored, the way training reads them
    im.load()
    if im.mode not in ("RGB", "L"):
        im = im.convert("RGB")
    if max(im.size) > SIDE:
        im.thumbnail((SIDE, SIDE), Image.LANCZOS)
    buf = io.BytesIO()
    im.convert("RGB").save(buf, "JPEG", quality=90)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def clean(text) -> str:
    """The caption as one line: no thinking block, no wrapping quotes, no line breaks."""
    t = re.sub(r"<think>.*?</think>", "", str(text or ""), flags=re.S | re.I).strip()
    t = " ".join(t.split())
    if len(t) > 1 and t[0] == t[-1] and t[0] in "\"'":
        t = t[1:-1].strip()
    return t


def caption_image(url, model, path, instruction, max_tokens, think=False) -> str:
    """One picture's caption. Raises RuntimeError with what the server said when it refuses."""
    body = {"model": str(model), "temperature": 0.3, "stream": False,
            "max_tokens": int(max_tokens or 120) + (THINK_EXTRA_TOKENS if think else 0),
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": instruction},
                {"type": "image_url", "image_url": {"url": _data_uri(path)}}]}]}
    if not think:
        body["reasoning_effort"] = "none"        # otherwise a reasoning model spends the tokens thinking
    req = urllib.request.Request(base_url(url) + "/v1/chat/completions", json.dumps(body).encode("utf-8"),
                                 {"Content-Type": "application/json", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=_CAPTION_TIMEOUT) as r:
            reply = json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        try:
            said = json.loads(e.read().decode("utf-8", "replace")).get("error")
            said = said.get("message") if isinstance(said, dict) else said
        except Exception:
            said = None
        raise RuntimeError(said or f"the server said {e.code}") from e
    except (OSError, ValueError) as e:
        raise RuntimeError(f"no answer from {base_url(url)} ({e})") from e
    try:
        choice = reply["choices"][0]
        text = clean(choice["message"].get("content"))
    except (KeyError, IndexError, TypeError, AttributeError) as e:
        raise RuntimeError("the server's answer had no caption in it") from e
    if not text and choice.get("finish_reason") == "length":
        raise RuntimeError("the model ran out of tokens before it answered (raise Max tokens, or untick Let the model think)")
    return text
