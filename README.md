# MonKap

A workbench for preparing image datasets: frame and crop pictures, write and edit captions by hand or with a model, and review the whole set from the keyboard.

Pictures are never resized behind your back, and nothing is erased without a way back unless you ask for it.

## What it does

- **Kapture.** Drag a frame on a picture (ratio presets, the size shown in pixels), press `C`, and the framed area is saved next to the original as a new PNG, at the original's own pixels. The frame stays, so a four-panel page is four key presses. New pictures get names from a template (`{parent}_crop_{n:03d}`), and a number is never used twice, even after a crop is deleted.
- **Captions.** The keyboard starts in the caption box; it saves by itself, and `PgUp` / `PgDn` walk through the folder. Clearing a caption on purpose removes the file (the old text is backed up first). A folder can have a trigger word, which MonKap can put first in a caption with one button.
- **Caption with a model.** The brain button writes the caption again with a model served by LM Studio, or by any server with an OpenAI-style API (Ollama, llama.cpp, vLLM…). Pick the model and the task in the settings; each task's instruction is editable.
- **Big folders and big pictures.** A virtual filmstrip (a 4000-picture folder opens in a fraction of a second), a whole-picture preview, and full-resolution tiles when you zoom in. Pinch-zoom works on touch.
- **Delete.** Either move to the folder's `removed/` with Undo, or erase for good. Your choice in the settings.

## Install (Windows 10 and 11)

Run `MonKap-<version>-setup.exe`. It installs for your user (no administrator rights) and adds a Start menu entry.

MonKap shows its page in the system's **WebView2** runtime. Windows 11 has it. On Windows 10 it usually comes with Microsoft Edge, but an old Windows 10 may only have the older web view: if the window doesn't open, install the Evergreen runtime from <https://developer.microsoft.com/microsoft-edge/webview2/>. Or use the Start menu entry **MonKap (in the browser)**, which opens the same page in your default browser instead of a window.

For captions with a model, start LM Studio's server (or any compatible one), and in MonKap's settings set the address (`http://localhost:1234` is the default), press Refresh, and choose a model that can see pictures. The pictures are sent to that address, so use one you trust.

## Run from source (any system)

Needs Python 3.10 or newer.

```bash
git clone https://github.com/DIGIDWARF-CC/MonKap.git
cd MonKap
pip install .
monkap
```

`monkap` starts the local server and opens the page in your browser. `monkap <folder>` opens a folder; `monkap --no-browser` only prints the address (for a Linux box without a browser, or to open it from another program); `monkap --help` lists the rest. `pip install ".[window]"` adds `monkap-gui`, MonKap's own window, on systems where pywebview works.

## Fizgig

Optional. In the settings, the Fizgig tab takes the folder of a [Fizgig](https://github.com/shootthesound/Fizgig) install, and MonKap reads a few things from it instead of asking you twice: the trigger word of a folder, the Caption Extension, and where Fizgig keeps its latent cache (so erasing a picture for good also clears its latents). MonKap only reads Fizgig's files and never writes to them.

**Training review.** With the Fizgig folder set, and once a Fizgig run has trained on the folder you have open, MonKap reads that run's per-image loss watch and shows it where you work: a coloured label on each picture (Stuck, Suspect, Learning, Learned…), the verdict and what to do about it under the caption, a list you can filter ("Problems") and sort ("Hardest first"), and a page that explains every label and, from the run's own settings, which of Fizgig's options could help (auto-recaption, per-image LR, where learning finished). It shows no charts: Fizgig's Runs page has them.

This depends on the Fizgig version. It was checked against Fizgig 8.5.9, and if Fizgig changes how it stores folders or settings it can stop working (MonKap then falls back to its own settings). Watch for MonKap updates after Fizgig updates.

## What it keeps on your computer

MonKap's own files are the settings and the list of folders you opened. The installed program keeps them in the `user_data` folder next to `MonKap.exe`, so removing the program's folder removes them too (the uninstaller asks first). Run from source, they go to `%LOCALAPPDATA%\MonKap` on Windows, `~/.local/share/monkap` on Linux, `~/Library/Application Support/MonKap` on macOS, or to the folder in `MONKAP_HOME`. The settings dialog shows what is there and can remove it; `monkap --forget` does the same. In a dataset folder MonKap keeps a hidden `.monkap` folder (the crop ledger and caption backups), the pictures and captions you asked for, and `removed/` if you remove pictures. Previews and tiles are made in memory.

The server listens on 127.0.0.1 only and wants a session key on every request. The browser keeps its usual cache for the page, as any page does.

## Build the installer

```bash
python -m venv .venv
.venv\Scripts\pip install . pywebview pyinstaller
.venv\Scripts\python packaging\build.py
```

This makes `dist\MonKap\MonKap.exe` (a folder, so nothing unpacks itself into the temp folder on start) and, with [Inno Setup 6](https://jrsoftware.org/isinfo.php) installed, `dist\MonKap-<version>-setup.exe`.

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
