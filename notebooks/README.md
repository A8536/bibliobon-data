# Colab Notebooks

`book_colab.py` is the source-of-truth file for the ordinary book verification
Colab workflow. It uses percent-cell markers:

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
