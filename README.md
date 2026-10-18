# MonKap

A workbench for preparing image datasets: frame and crop pictures, write and edit captions by hand or with a model, and review the whole set from the keyboard.

MonKap runs on your computer and opens in your browser. Pictures are never resized behind your back, and nothing is erased without a way back unless you ask for it.

## What it does

- **Kapture.** Drag a frame on a picture (ratio presets, the size shown in pixels), press `C`, and the framed area is saved next to the original as a new PNG, at the original's own pixels. The frame stays, so a four-panel page is four key presses. New pictures get names from a template (`{parent}_crop_{n:03d}`), and a number is never used twice, even after a crop is deleted.
- **Captions.** The keyboard starts in the caption box; it saves by itself, and `PgUp` / `PgDn` walk through the folder. Clearing a caption on purpose removes the file (the old text is backed up first). A folder can have a trigger word, which MonKap can put first in a caption with one button.
- **Caption with a model.** The brain button writes the caption again with a model served by LM Studio, or by any server with an OpenAI-style API (Ollama, llama.cpp, vLLM…). Pick the model and the task in the settings; each task's instruction is editable.
- **Big folders and big pictures.** A virtual filmstrip (a 4000-picture folder opens in a fraction of a second), a whole-picture preview, and full-resolution tiles when you zoom in. Pinch-zoom works on touch.
- **Delete.** Either move to the folder's `removed/` with Undo, or erase for good. Your choice in the settings.

## Run it

Needs Python 3.10 or newer.

```bash
git clone https://github.com/DIGIDWARF-CC/MonKap.git
cd MonKap
pip install .
monkap
```

`monkap` prints an address with a session key and opens it in the browser. `monkap <folder>` opens a folder; `monkap --no-browser` only prints the address; `monkap --help` lists the rest. Without installing: `pip install Pillow`, then `python -m monkap` from the `src` folder.

For captions with a model, start LM Studio's server (or any compatible one), and in MonKap's settings set the address (`http://localhost:1234` is the default), press Refresh, and choose a model that can see pictures. The pictures are sent to that address, so use one you trust.

## What it keeps on your computer

- Its own files, in one folder: the settings and the list of folders you opened (`%LOCALAPPDATA%\MonKap` on Windows, `~/.local/share/monkap` on Linux, `~/Library/Application Support/MonKap` on macOS, or the folder in `MONKAP_HOME`). The settings dialog shows what is there and can remove it; `monkap --forget` does the same.
- In a dataset folder: a hidden `.monkap` folder (the crop ledger and caption backups), the pictures and captions you asked for, and `removed/` if you remove pictures.
- Pictures, previews and tiles are made in memory and are not written out.
- The server listens on 127.0.0.1 only and wants the session key on every request.

Deleting files from a disk doesn't guarantee they can't be recovered.

## Tests

```bash
python -m unittest discover -s tests
node --test "tests/js/*.test.mjs"
```

No GPU, no display and no network are needed. The server and the LM Studio client are tested against fake servers.

## Where it came from

MonKap started as a dataset tool for a LoRA trainer, and its file logic follows what trainers expect: a picture with no caption file is left out of training, a name never comes back, and a crop is cut from the original pixels.

## License

GPL-3.0, see `LICENSE`.
