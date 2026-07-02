# Colab Notebooks

`book_colab.py` is the source-of-truth file for the mixed bibliography
verification Colab workflow. It uses percent-cell markers:

```python
# %% [markdown]
# # Title

# %%
print("code cell")
```

Regenerate the runnable notebook after editing the `.py` source:

```bash
python3 scripts/percent_py_to_ipynb.py \
  notebooks/book_colab.py \
  notebooks/book_colab.ipynb
```

Open the generated `.ipynb` in Google Colab. Keep both files in sync when
committing changes: review/edit the `.py`, regenerate the `.ipynb`, then push.

## Checkpoints

`book_colab.py` writes checkpoints to Google Drive by default:

```text
MyDrive/bibliobon_colab_checkpoints/<input-file-stem>/
```

The notebook saves the current JSONL/TSV/grounding/manifest/zip outputs after
each processed record. If the Colab session disconnects, reopen the notebook,
upload the same input file, and run again. Rows already saved with status `ok`
are skipped and processing resumes from the remaining rows.

Current output files:

- `verified_bibliography.jsonl`;
- `verified_bibliography.tsv`;
- `grounding_sources.tsv`;
- `run_manifest.json`;
- `bibliography_verification_results.zip`.

## API Key From Drive

The notebook can read `GEMINI_API_KEY` from Google Drive. Create this file:

```text
MyDrive/bibliobon_colab_secrets/gemini_api_key.env
```

Supported contents:

```text
GEMINI_API_KEY=your_key_here
```

or just the raw key on the first non-empty line. If the file is missing, the
notebook falls back to a hidden `getpass` prompt.
