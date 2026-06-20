# Architecture

## Direction

`bibliobon-data` is the canonical data builder.

`bibliobon-catalog` is the Django publishing site.

The site should not be the long-term place where bibliography data is normalized and corrected. It can continue to provide current import/export tools during transition, but the target is to move that logic here.

## Migration Strategy

1. Bootstrap this project from the current site database.
2. Preserve source Django IDs for matching.
3. Assign stable data-project IDs.
4. Add diagnostics and cleanup reports.
5. Generate `data/bibliobon.sqlite` under a contract.
6. Add a site importer that validates and imports that contract.
7. Stop treating the site database as the canonical editing source.

## Non-Goals

- Redesigning public templates.
- Deploying production changes.
- Reimporting from the legacy Excel workbook as the active workflow.
- Removing Google Sheets before a replacement editorial workflow is proven.
