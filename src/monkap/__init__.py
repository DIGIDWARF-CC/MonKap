"""MonKap: a workbench for preparing image datasets. Go through the pictures, caption them (by hand or with a model),
and frame a part of a picture to save as a new picture (Kapture).

  core     - what touches the dataset's files: names, the crop ledger, crops, caption files, Kapture, delete
  ai       - caption backends (LM Studio and other OpenAI-compatible servers) and the task instructions
  appdata  - the one folder MonKap keeps its own files in
  dataset  - listing a folder, thumbnails, previews and tiles, remove and undo
  server   - the local web server and its routes (the page is in static/)
"""
__version__ = "0.4.0"
