# Normalized Parser Input

This directory stores normalized raw bibliography records before parsing.

Format:

```json
{"raw_record_id":"raw-...","source_record_index":1,"source_line_start":1,"source_line_end":1,"raw_text":"...","normalized_text":"...","source_input_path":"...","source_sha256":"..."}
```

One JSONL line is one raw bibliographic record. These files are staging inputs,
not editor-database writes.

