# Icebox

Ideas that are not for now, with what was worked out about them.

## Enhance several pictures at once

Enhance works on one picture (the sparkle button, `docs` in the README). Doing it for a whole selection was set aside:
unlike captions, which are written at once and can be restored, an enhanced picture waits for a decision, and a batch makes
that the hard part.

What a batch would need:

- **Results that wait.** Each result is Retry (new seed), Discard, Replace or Store as Kapture. For a batch they pile up as
  "Enhanced" pills on the tiles of the selection gallery; a click opens the same compare window with the four buttons, and
  there are group buttons (Store all as Kapture, Replace all, Discard all). The results go to `user_data/enhance/` on disk,
  not memory (500 pictures of 1 MP are about 1 GB) and are removed with the rest of MonKap's data.
- **Leaving the mode with results waiting** asks: store all, discard, or stay.
- **A queue on the server**, not in the page: a run takes minutes per picture, so a reloaded page must not lose it. The page
  only shows it (percent and the ComfyUI preview on the current tile, "n of N" with the time left, "Failed" on a tile that
  failed; the queue goes on after one failure and stops after three in a row).
- **Stop** interrupts the current ComfyUI run (`/interrupt`) and stops the queue; finished results stay. Closing the window
  during a run has to interrupt ComfyUI too.
- **Replace in a batch changes names** (jpg becomes png), so the picked set and the pills follow the name without extension.
- Warn when a Fizgig training is running (its `runs.json` says so): the video memory is busy.
