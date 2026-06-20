# Bibliobon Target Data Model

This document defines the target canonical model for `bibliobon-data`.

It is independent from the current public Django site model. Legacy names such as
`Work`, `JournalIssue`, and `Collection` are compatibility inputs, not the target
conceptual model.

## Core Rule

Model bibliographic reality, not the accidental state of the current import.

The public site may simplify presentation, but canonical data should preserve the
normalized relationships needed for future editing, validation, and imports.

## Entities

### Source

A `Source` is a citeable bibliographic unit.

Examples:

- a book;
- an article;
- a journal issue, if the issue itself has a bibliographic record;
- a collection or conference volume;
- a volume of a multivolume work;
- a reprint or another edition of the same work.

Expected fields:

- `source_id` - stable canonical ID;
- `source_type` - `book`, `article`, `issue`, `collection`, `volume`, `unknown`;
- `source_django_work_id` - legacy compatibility field during migration;
- `source_sequence`;
- `source_number`;
- `title`;
- `parallel_title`;
- `subtitle`;
- `title_remainder`;
- `part_number`;
- `part_title`;
- `responsibility_statement`;
- `edition_statement`;
- `additional_edition_statement`;
- `publication_place`;
- `publisher`;
- `publication_date`;
- `year`;
- `manufacture_place`;
- `manufacturer`;
- `manufacture_date`;
- `copyright_date`;
- `extent`;
- `illustrations`;
- `dimensions`;
- `accompanying_material`;
- `series_statement`;
- `notes`;
- `bibliography_note`;
- `index_note`;
- `contents_note`;
- `isbn`;
- `issn`;
- `doi`;
- `url`;
- `access_date`;
- `content_type`;
- `media_type`;
- `carrier_type`;
- `raw_publication_details`;
- `data_source` - free-text provenance, for example `Баранов 2021`, `ручное добавление`, `Google Sheets`, `сообщение пользователя`, `проверено по РГБ`;
- `first_seen_at` - when the entity first appeared in the editor database;
- `updated_at` - when the record was last changed;
- `review_note`;
- `description_status` - `parsed`, `partial`, `raw_only`, `needs_review`;
- `language`;
- `section`;

### Author / SourceAuthor

Authors are normalized display entities.

`SourceAuthor` is an ordered relation between `Source` and `Author`.

Author fields should include:

- `display_name`;
- `heading_name` - heading form used for bibliographic access points;
- `sort_name`;
- `aliases`;
- `person_dates`;
- `authority_note`.

`SourceAuthor` fields should include:

- `role`;
- `sort_order`;
- `name_as_printed`;
- `include_in_responsibility`;
- `is_primary_heading`;
- `created_at`;
- `updated_at`.

### Section

Hierarchical source-book/category structure.

### Tag / SourceTag

Index terms and controlled tags attached to `Source`.

`SourceTag` should include `created_at` and `updated_at`, because assigning a
tag can be a meaningful bibliographic edit.

### Periodical

A `Periodical` is the continuing identity of a serial publication.

Examples:

- journal;
- newspaper;
- bulletin;
- annual if it behaves as a continuing publication;
- proceedings series if it has continuing identity.

A `Periodical` is not itself a bibliographic `Source`.

Expected fields:

- `periodical_id`;
- `title`;
- `parallel_title`;
- `title_remainder`;
- `responsibility_statement`;
- `periodicity`;
- `start_year`;
- `end_year`;
- `place`;
- `publisher`;
- `issn`;
- `numbering_start`;
- `numbering_end`;
- `title_history_note`;
- editorial notes.

### Issue

An `Issue` is a concrete container.

Examples:

- one journal issue;
- one annual issue;
- one collection;
- one conference volume;
- one concrete volume of a continuing publication.

An `Issue` may or may not have its own `Source`.

Expected fields:

- `issue_id`;
- `periodical_id` nullable;
- `source_id` nullable;
- `title`;
- `parallel_title`;
- `title_remainder`;
- `responsibility_statement`;
- `year`;
- `publication_date`;
- `issue_number`;
- `volume`;
- `part_number`;
- `gross_number`;
- `date_text`;
- `chronology`;
- `enumeration`;
- `publication_place`;
- `publisher`;
- `raw_publication_details`;
- `issn`;
- `isbn`;
- notes.

Rules:

- journal article containers use `Issue.periodical_id`;
- collection article containers use `Issue` without `periodical_id`;
- if the container is also cited as a whole, `Issue.source_id` points to that `Source`;
- if the container only exists to place articles, `Issue.source_id` may be empty.

### ArticlePlacement

An `ArticlePlacement` links an article `Source` to its container `Issue`.

Expected fields:

- `article_source_id`;
- `issue_id`;
- `pages_raw`;
- `page_start`;
- `page_end`;
- `location_note`;
- `placement_note`;
- `created_at`;
- `updated_at`;
- placement/order fields if needed;
- notes.

Rules:

- the article itself is always a `Source`;
- pages belong here when they describe pages within the container;
- raw pages are preserved separately from normalized numeric page bounds;
- one article should normally have one placement, but the model can later allow
  multiple placements if reprints are represented explicitly.

## Citation Rendering

The model should store structured bibliographic data. ГОСТ strings and shorter
site variants should be generated by a separate renderer layer.

Planned renderer profiles are tracked in `TODO.md` and are intentionally not part
of the current implementation.

### SourceGroup / SourceGroupItem

`SourceGroup` groups related `Source` records that are not simply issues of one
periodical.

Examples:

- multivolume work;
- editions of the same book;
- reprints;
- translations;
- related sets;
- loosely connected publication groups.

Expected fields:

- `group_id`;
- `title`;
- `group_type` - `multivolume`, `editions`, `series`, `set`, `related`;
- notes.

`SourceGroupItem` links a `Source` to a `SourceGroup` with ordering and optional
volume/edition metadata.

`SourceGroupItem` should include `created_at` and `updated_at`, because the
existence/order of a relation can be a meaningful bibliographic edit. Do not add
relation-level `data_source` unless a workflow really needs to record the
provenance of the link itself rather than the linked source.

## Periodical vs SourceGroup

`Periodical` groups `Issue` records under a continuing serial identity.

`SourceGroup` groups `Source` records under a bibliographic/editorial relation.

Examples:

```text
Periodical: Советский коллекционер
Issue: 1974, №12
ArticlePlacement: article Source -> Issue
```

```text
SourceGroup: Бумажные денежные знаки России
SourceGroupItem: Том 1 Source
SourceGroupItem: Том 2 Source
```

```text
SourceGroup: Теория бумажно-денежного обращения
group_type: editions
SourceGroupItem: 1883 edition Source
SourceGroupItem: 1890 edition Source
SourceGroupItem: 2001 reprint Source
```

## One-Article Containers

Do not decide structure by article count.

If a record is truly an article inside a container, create and keep:

```text
Source(article)
ArticlePlacement
Issue
Periodical nullable
```

This is true even when only one article is currently known.

If the record is actually a standalone bibliographic source and a journal or
collection was created only by import/parsing error, keep only `Source` and
remove the accidental container structure.

The public site may hide container navigation when an issue has only one article.
The editor should preserve the structure.

## Editor Transformations

The editor must support these controlled transformations:

- standalone `Source` -> article in `Periodical` issue;
- standalone `Source` -> article in collection/container `Issue`;
- one-article `Issue` -> standalone `Source`, with preview;
- standalone collection/source -> `Issue.source_id`;
- merge duplicate `Issue` records;
- merge duplicate `Periodical` records;
- group editions or volumes through `SourceGroup`.

All destructive or merging transformations should have preview/dry-run behavior.

## Legacy Mapping

Initial compatibility mapping:

```text
catalog_work -> Source
catalog_author -> Author
catalog_workauthor -> SourceAuthor
catalog_section -> Section
catalog_tag -> Tag
catalog_worktag -> SourceTag
catalog_journal -> Periodical
catalog_journalissue -> Issue
catalog_article -> ArticlePlacement
catalog_collection -> Issue or Issue.source_id migration input
catalog_workgroup -> SourceGroup
catalog_workgroupitem -> SourceGroupItem
```

Legacy IDs should be preserved as compatibility fields, not as canonical keys.
