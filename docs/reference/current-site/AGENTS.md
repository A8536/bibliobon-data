# Agent Instructions

## Project Memory

- Keep `TODO.md` as the current task backlog.
- Keep `DEVELOPMENT.MD` as the session and decision log.
- When a change adds a meaningful feature, migration, workflow, data-processing command, or operational step, update one or both files in the same work session.
- Do not log every tiny edit. Log durable context that helps continue the project on another computer or in another session.
- If work changes Google Sheets import/export, update `GOOGLE_SHEETS.md`.

## Local Checks

- Run `manage.py check` after Django model, view, URL, template, or settings changes.
- Run focused tests when import/export, parsing, or filtering logic changes.
