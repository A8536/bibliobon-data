# Bibliobon Field Contract

This document defines the canonical field vocabulary shared by the parser,
editor models, Google Sheets, exported SQLite artifact, and the public site
contract.

Canonical field names use target/export names. Legacy compatibility fields may
remain in editor models, but import/export layers must map them to the canonical
name.

| canonical_name | model | google_sheet_header | artifact_column | site_field | status | notes |
|---|---|---|---|---|---|---|
| source_id | Source | work_id | sources.source_id | sources.source_id | active | Stable canonical bibliographic source ID. |
| source_type | Source | work_type | sources.source_type | sources.source_type | active | Target values are source categories, not parser confidence classes. |
| source_sequence | Source | source_sequence | sources.source_sequence | sources.source_sequence | active | Ordering in the historical bibliography. |
| source_number | Source | source_number | sources.source_number | sources.source_number | active | Historical source number. |
| source_page_marker | Source |  | sources.source_page_marker | sources.source_page_marker | active | No Google Sheets header yet. |
| section_id | Source.section | section_id | sources.section_id | sources.section_id | active | Foreign key to Section. |
| section_code | Section | source_code; section_code | sections.source_code | sections.source_code | active | Human/editor-facing section code; `section_code` is the Works sheet lookup helper. |
| parent_id | Section, Tag | parent_section_id; parent_tag_id | sections.parent_id; tags.parent_id | parent_id | active | Parent relation ID for hierarchical records. |
| parent_source_code | Section | parent_source_code |  |  | compatibility | Google Sheets display helper for parent section code. |
| language_id | Source.language | language_id | sources.language_id | sources.language_id | active | Foreign key to Language. |
| code | Language | language_code; code | languages.code | languages.code | active | Language code; `language_code` is the Works sheet lookup helper. |
| description | Language, Tag, Periodical | description | languages.description; tags.description; periodicals.description | description | active | Context-specific description text. |
| container_index | ArticlePlacement | container_index |  |  | compatibility | Google Sheets helper for linking a work row to an issue/container row. |
| linked_authors | SourceAuthor | linked_authors |  |  | compatibility | Google Sheets display helper; canonical relation rows live in `WorkAuthors`/`source_authors`. |
| raw_author_string | Source | raw_author_string | sources.raw_author_string | sources.raw_author_string | active | Raw author string before normalized author links. |
| title | Source | title | sources.title | sources.title | active | Main title. |
| parallel_title | Source | parallel_title | sources.parallel_title | sources.parallel_title | active | Parallel title. |
| subtitle | Source | subtitle | sources.subtitle | sources.subtitle | active | Subtitle parsed separately from title. |
| title_remainder | Source | title_remainder | sources.title_remainder | sources.title_remainder | active | Remaining title/responsibility title area. |
| volume_number | Source | volume_number | sources.volume_number | sources.volume_number | active | Volume number for source records. |
| part_number | Source | part_number | sources.part_number | sources.part_number | active | Part number. |
| part_title | Source | part_title | sources.part_title | sources.part_title | active | Part title. |
| responsibility_statement | Source | responsibility_statement | sources.responsibility_statement | sources.responsibility_statement | active | Statement of responsibility. |
| edition_statement | Source | edition_statement | sources.edition_statement | sources.edition_statement | active | Edition statement. |
| additional_edition_statement | Source | additional_edition_statement | sources.additional_edition_statement | sources.additional_edition_statement | active | Additional edition statement. |
| publication_place | Source | publication_place | sources.publication_place | sources.publication_place | active | Place of publication. |
| publisher | Source | publisher | sources.publisher | sources.publisher | active | Publisher. |
| publication_date | Source | publication_date | sources.publication_date | sources.publication_date | active | Publication date as printed. |
| inferred_year | Source | year | sources.inferred_year | sources.inferred_year | active | Normalized/inferred year. |
| manufacture_place | Source |  | sources.manufacture_place | sources.manufacture_place | active | No Google Sheets header yet. |
| manufacturer | Source |  | sources.manufacturer | sources.manufacturer | active | No Google Sheets header yet. |
| manufacture_date | Source |  | sources.manufacture_date | sources.manufacture_date | active | No Google Sheets header yet. |
| copyright_date | Source |  | sources.copyright_date | sources.copyright_date | active | No Google Sheets header yet. |
| extent | Source | extent | sources.extent | sources.extent | active | Extent/volume, not article placement pages. |
| physical_description | Source | physical_description |  |  | compatibility | Legacy editor/Sheets helper; target uses structured physical fields and `extent`. |
| article_pages | ArticlePlacement | article_pages |  |  | compatibility | Legacy Work sheet helper; target article pages live in `ArticlePlacements.pages_raw`, `page_start`, `page_end`. |
| illustrations | Source | illustrations | sources.illustrations | sources.illustrations | active | Illustration statement. |
| dimensions | Source | dimensions | sources.dimensions | sources.dimensions | active | Dimensions. |
| accompanying_material | Source | accompanying_material | sources.accompanying_material | sources.accompanying_material | active | Accompanying material. |
| circulation | Source | circulation | sources.circulation | sources.circulation | active | Print run/circulation. |
| series_statement | Source | series_statement | sources.series_statement | sources.series_statement | active | Series statement. |
| notes | Source | notes | sources.notes | sources.notes | active | Editorial/public note field. |
| bibliography_note | Source |  | sources.bibliography_note | sources.bibliography_note | active | No Google Sheets header yet. |
| index_note | Source |  | sources.index_note | sources.index_note | active | No Google Sheets header yet. |
| contents_note | Source |  | sources.contents_note | sources.contents_note | active | No Google Sheets header yet. |
| isbn | Source | isbn | sources.isbn | sources.isbn | active | ISBN. |
| issn | Source | issn | sources.issn | sources.issn | active | ISSN. |
| doi | Source | doi | sources.doi | sources.doi | active | DOI. |
| url | Source | url | sources.url | sources.url | active | URL. |
| access_date | Source |  | sources.access_date | sources.access_date | active | Access date. |
| content_type | Source |  | sources.content_type | sources.content_type | active | GOST carrier/content metadata. |
| media_type | Source |  | sources.media_type | sources.media_type | active | GOST media metadata. |
| carrier_type | Source |  | sources.carrier_type | sources.carrier_type | active | GOST carrier metadata. |
| raw_publication_details | Source, Issue | raw_publication_details | sources.raw_publication_details; issues.raw_publication_details | raw_publication_details | active | Canonical raw bibliographic/publication detail field. Legacy Google Sheets alias `publication_details_raw` is import-only. |
| raw_host_title | Source |  | sources.raw_host_title | sources.raw_host_title | active | Raw host/container title before normalized placement. |
| public_review | Source | public_review | sources.public_review | sources.public_review | active | Annotation/public review text, including trailing parser `{...}` annotation. |
| data_source | Source | data_source | sources.data_source | sources.data_source | active | Free-text provenance for the source record, for example `Баранов 2021`, `ручное добавление`, `Google Sheets`, `сообщение пользователя`, `проверено по РГБ`. |
| first_seen_at | Source | first_seen_at | sources.first_seen_at | sources.first_seen_at | active | Timestamp when the entity first appeared in the editor database. |
| updated_at | Source, SourceAuthor, SourceTag, ArticlePlacement, SourceGroupItem | updated_at | sources.updated_at; source_authors.updated_at; source_tags.updated_at; article_placements.updated_at; source_group_items.updated_at | updated_at | active | Timestamp when the record/relation was last changed. |
| description_status | Source | description_status | sources.description_status | sources.description_status | active | `parsed`, `partial`, `raw_only`, `needs_review`. |
| author_id | Author, SourceAuthor | author_id | authors.author_id; source_authors.author_id | author_id | active | Normalized author ID. |
| display_name | Author | display_name | authors.display_name | authors.display_name | active | Display form. |
| heading_name | Author | heading_name | authors.heading_name | authors.heading_name | active | Bibliographic heading. |
| sort_name | Author | sort_name | authors.sort_name | authors.sort_name | active | Sorting form. |
| aliases | Author | aliases | authors.aliases | authors.aliases | active | Alternate names. |
| person_dates | Author | person_dates | authors.person_dates | authors.person_dates | active | Life dates. |
| authority_note | Author | authority_note | authors.authority_note | authors.authority_note | active | Authority-control note. |
| note | Author, Section, SourceGroup | note | note | note | active | Context-specific note. |
| sort_order | SourceAuthor, SourceTag, Section, SourceGroupItem | sort_order | sort_order | sort_order | active | Ordered relation position. |
| role | SourceAuthor | role | source_authors.role | source_authors.role | active | Author role. |
| source_text | SourceAuthor, SourceTag | source_text | source_text | source_text | active | Raw relation text. |
| created_at | SourceAuthor, SourceTag, ArticlePlacement, SourceGroupItem |  | source_authors.created_at; source_tags.created_at; article_placements.created_at; source_group_items.created_at | created_at | active | Timestamp when the relation first appeared in the editor database. Relation-level provenance uses timestamps only unless a future workflow needs relation-specific `data_source`. |
| work_title | Work, Source | work_title |  |  | compatibility | Google Sheets display helper for relation tabs. |
| author_display_name | Author | author_display_name |  |  | compatibility | Google Sheets display helper for relation tabs. |
| name_as_printed | SourceAuthor | name_as_printed | source_authors.name_as_printed | source_authors.name_as_printed | active | Author name as printed in source. |
| include_in_responsibility | SourceAuthor | include_in_responsibility | source_authors.include_in_responsibility | source_authors.include_in_responsibility | active | Rendering control. |
| is_primary_heading | SourceAuthor | is_primary_heading | source_authors.is_primary_heading | source_authors.is_primary_heading | active | Primary access-point flag. |
| periodical_id | Periodical, Issue | journal_id | periodicals.periodical_id; issues.periodical_id | periodical_id | active | Canonical serial identity ID. |
| periodical_title | Periodical | journal_title |  |  | compatibility | Google Sheets display helper for issue rows. |
| place | Periodical | place | periodicals.place | periodicals.place | active | Periodical place; source publication place remains `publication_place`. |
| periodicity | Periodical | periodicity | periodicals.periodicity | periodicals.periodicity | active | Serial periodicity. |
| numbering_start | Periodical | numbering_start | periodicals.numbering_start | periodicals.numbering_start | active | Start of serial numbering. |
| numbering_end | Periodical | numbering_end | periodicals.numbering_end | periodicals.numbering_end | active | End of serial numbering. |
| start_year | Periodical | start_year | periodicals.start_year | periodicals.start_year | active | Start year for serial identity. |
| end_year | Periodical | end_year | periodicals.end_year | periodicals.end_year | active | End year for serial identity. |
| title_history_note | Periodical | title_history_note | periodicals.title_history_note | periodicals.title_history_note | active | Periodical title history note. |
| issue_id | Issue, ArticlePlacement | journal_issue_id | issues.issue_id; article_placements.issue_id | issue_id | active | Canonical issue/container ID. |
| issue_type | Issue |  | issues.issue_type | issues.issue_type | active | Issue/container kind. |
| year | Issue | year | issues.year | issues.year | active | Issue/container year. Source year is exported as `inferred_year`. |
| issue_number | Issue | issue_number | issues.issue_number | issues.issue_number | active | Issue number. |
| volume | Issue | volume | issues.volume | issues.volume | active | Issue volume. |
| gross_number | Issue | gross_number | issues.gross_number | issues.gross_number | active | Gross/continuous issue number. |
| date_text | Issue | date_text |  |  | active | Issue date text in Google Sheets/editor compatibility layer; artifact currently exports `chronology`/`enumeration` plus `publication_date`. |
| chronology | Issue | chronology | issues.chronology | issues.chronology | active | Chronology text. |
| enumeration | Issue | enumeration | issues.enumeration | issues.enumeration | active | Enumeration text. |
| placement_id | ArticlePlacement | article_id | article_placements.placement_id | article_placements.placement_id | active | Article placement row ID in artifact. |
| container_id | ArticlePlacement | container_id |  |  | compatibility | Google Sheets helper: issue/container target ID before canonical placement export. |
| container_type | ArticlePlacement | container_type |  |  | compatibility | Google Sheets helper: issue/container target type. |
| container_title | ArticlePlacement | container_title |  |  | compatibility | Google Sheets display helper for container target. |
| pages_raw | ArticlePlacement | pages_raw | article_placements.pages_raw | article_placements.pages_raw | active | Raw article page range. |
| page_start | ArticlePlacement, Source | page_start | article_placements.page_start | article_placements.page_start | active | Normalized article start page. |
| page_end | ArticlePlacement, Source | page_end | article_placements.page_end | article_placements.page_end | active | Normalized article end page. |
| location_note | ArticlePlacement | location_note | article_placements.location_note | article_placements.location_note | active | Placement location note. |
| placement_note | ArticlePlacement | placement_note | article_placements.placement_note | article_placements.placement_note | active | Placement note. |
| group_id | SourceGroup, SourceGroupItem | group_id | source_groups.group_id; source_group_items.group_id | group_id | active | Related-source group ID. |
| group_title | SourceGroup | group_title |  |  | compatibility | Google Sheets display helper for group item rows. |
| group_type | SourceGroup | group_type | source_groups.group_type | source_groups.group_type | active | Group type. |
| tag_id | Tag, SourceTag | tag_id | tags.tag_id; source_tags.tag_id | tag_id | active | Normalized tag ID. |
| tag_title | Tag, SourceTag | tag_title |  |  | compatibility | Google Sheets display helper for tag relation rows. |
| tag_type | Tag | tag_type | tags.tag_type | tags.tag_type | active | Tag category. |
| parent_title | Tag | parent_title |  |  | compatibility | Google Sheets display helper for parent tag title. |
| legacy_* | Source, Periodical, Issue, ArticlePlacement, SourceGroup |  | legacy_* | legacy_* | compatibility | Compatibility references only; not canonical public identifiers. |

## Audit

Run the field audit whenever model, sheet, parser, or artifact fields change:

```bash
python3 scripts/audit_field_contract.py
```

The audit writes:

- `reports/field_contract_audit.md`
- `reports/field_contract_audit.tsv`
- `reports/field_contract_inventory.json`
