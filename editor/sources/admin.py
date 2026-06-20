from django.contrib import admin

from .models import (
    Article,
    Author,
    Book,
    Collection,
    Journal,
    JournalIssue,
    Language,
    ArticlePlacement,
    Issue,
    Periodical,
    Section,
    Source,
    SourceAuthor,
    SourceGroup,
    SourceGroupItem,
    SourceTag,
    Tag,
    Work,
    WorkAuthor,
    WorkGroup,
    WorkGroupItem,
    WorkTag,
)


class WorkAuthorInline(admin.TabularInline):
    model = WorkAuthor
    extra = 0
    autocomplete_fields = ["author"]
    fields = [
        "author",
        "sort_order",
        "role",
        "source_text",
        "name_as_printed",
        "include_in_responsibility",
        "is_primary_heading",
    ]


class WorkTagInline(admin.TabularInline):
    model = WorkTag
    extra = 0
    autocomplete_fields = ["tag"]


@admin.register(Work)
class WorkAdmin(admin.ModelAdmin):
    list_display = ["source_sequence", "source_number", "work_type", "short_title", "source_section", "inferred_year"]
    list_filter = ["work_type", "language", "source_section"]
    search_fields = [
        "work_id",
        "source_django_id",
        "title",
        "subtitle",
        "parallel_title",
        "title_remainder",
        "part_title",
        "responsibility_note",
        "responsibility_statement",
        "raw_author_string",
        "publication_place",
        "publisher",
        "publication_date",
        "publication_details",
        "isbn",
        "issn",
        "doi",
        "url",
    ]
    readonly_fields = ["source_django_id"]
    fieldsets = [
        (
            "Identity",
            {
                "fields": [
                    "work_id",
                    "source_django_id",
                    "source_sequence",
                    "source_number",
                    "source_page_marker",
                    "work_type",
                    "description_status",
                ]
            },
        ),
        (
            "Classification",
            {"fields": ["language", "source_section"]},
        ),
        (
            "Title and responsibility",
            {
                "fields": [
                    "raw_author_string",
                    "title",
                    "parallel_title",
                    "subtitle",
                    "title_remainder",
                    "volume_number",
                    "part_number",
                    "part_title",
                    "responsibility_note",
                    "responsibility_statement",
                    "host_title",
                ]
            },
        ),
        (
            "Edition and publication",
            {
                "fields": [
                    "edition_statement",
                    "additional_edition_statement",
                    "publication_place",
                    "publisher",
                    "publication_date",
                    "inferred_year",
                    "manufacture_place",
                    "manufacturer",
                    "manufacture_date",
                    "copyright_date",
                ]
            },
        ),
        (
            "Physical description",
            {
                "fields": [
                    "physical_description",
                    "extent",
                    "illustrations",
                    "dimensions",
                    "accompanying_material",
                    "circulation",
                    "article_pages",
                    "page_start",
                    "page_end",
                ]
            },
        ),
        (
            "Series, notes, identifiers",
            {
                "fields": [
                    "series_statement",
                    "notes",
                    "bibliography_note",
                    "index_note",
                    "contents_note",
                    "isbn",
                    "issn",
                    "doi",
                    "url",
                    "access_date",
                    "content_type",
                    "media_type",
                    "carrier_type",
                ]
            },
        ),
        (
            "Raw and public notes",
            {"fields": ["publication_details", "public_review"]},
        ),
    ]
    autocomplete_fields = ["language", "source_section"]
    inlines = [WorkAuthorInline, WorkTagInline]

    @admin.display(description="Title")
    def short_title(self, obj):
        return obj.title[:120]


@admin.register(Author)
class AuthorAdmin(admin.ModelAdmin):
    search_fields = ["author_id", "source_django_id", "display_name", "heading_name", "sort_name", "aliases"]
    list_display = ["display_name", "heading_name", "sort_name", "person_dates", "source_django_id"]


@admin.register(Section)
class SectionAdmin(admin.ModelAdmin):
    search_fields = ["section_id", "source_django_id", "source_code", "title"]
    list_display = ["source_code", "title", "parent", "sort_order"]
    autocomplete_fields = ["parent"]


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    search_fields = ["tag_id", "source_django_id", "title"]
    list_display = ["title", "tag_type", "parent", "sort_order"]
    list_filter = ["tag_type"]
    autocomplete_fields = ["parent"]


@admin.register(Journal)
class JournalAdmin(admin.ModelAdmin):
    search_fields = [
        "journal_id",
        "source_django_id",
        "title",
        "parallel_title",
        "responsibility_statement",
        "place",
        "publisher",
        "issn",
    ]
    list_display = ["title", "place", "publisher", "periodicity", "start_year", "end_year", "source_django_id"]


@admin.register(JournalIssue)
class JournalIssueAdmin(admin.ModelAdmin):
    search_fields = [
        "journal_issue_id",
        "source_django_id",
        "journal__title",
        "title",
        "issue_number",
        "volume",
        "gross_number",
        "publication_details",
    ]
    list_display = ["journal", "year", "issue_number", "volume", "gross_number", "source_django_id"]
    autocomplete_fields = ["journal"]


@admin.register(Article)
class ArticleAdmin(admin.ModelAdmin):
    search_fields = ["article_id", "source_django_id", "work__title", "container_work__title", "journal_issue__journal__title", "pages_raw"]
    list_display = ["work", "container_work", "journal_issue", "pages", "pages_raw", "page_start", "page_end"]
    autocomplete_fields = ["work", "container_work", "collection", "journal_issue"]


class WorkGroupItemInline(admin.TabularInline):
    model = WorkGroupItem
    extra = 0
    autocomplete_fields = ["work"]


@admin.register(WorkGroup)
class WorkGroupAdmin(admin.ModelAdmin):
    search_fields = ["group_id", "source_django_id", "title", "note"]
    list_display = ["title", "group_type", "source_django_id"]
    list_filter = ["group_type"]
    inlines = [WorkGroupItemInline]


@admin.register(Collection)
class CollectionAdmin(admin.ModelAdmin):
    search_fields = ["collection_id", "source_django_id", "title", "publication_details", "source_text"]
    list_display = ["title", "year", "place", "publisher", "parent_work"]
    autocomplete_fields = ["parent_work"]


@admin.register(Language)
class LanguageAdmin(admin.ModelAdmin):
    search_fields = ["language_id", "code", "title"]
    list_display = ["code", "title", "sort_order"]


admin.site.register(Book)


class SourceAuthorInline(admin.TabularInline):
    model = SourceAuthor
    extra = 0
    autocomplete_fields = ["author"]


class SourceTagInline(admin.TabularInline):
    model = SourceTag
    extra = 0
    autocomplete_fields = ["tag"]


@admin.register(Source)
class SourceAdmin(admin.ModelAdmin):
    list_display = ["source_sequence", "source_number", "source_type", "short_title", "section", "inferred_year"]
    list_filter = ["source_type", "language", "section", "description_status"]
    search_fields = [
        "source_id",
        "source_django_id",
        "title",
        "subtitle",
        "raw_author_string",
        "responsibility_statement",
        "publication_place",
        "publisher",
        "publication_date",
        "raw_publication_details",
        "raw_host_title",
        "isbn",
        "issn",
        "doi",
        "url",
    ]
    autocomplete_fields = ["language", "section", "legacy_work"]
    fieldsets = [
        (
            "Identity",
            {
                "fields": [
                    "source_id",
                    "source_django_id",
                    "legacy_work",
                    "source_sequence",
                    "source_number",
                    "source_page_marker",
                    "source_type",
                    "description_status",
                ]
            },
        ),
        ("Classification", {"fields": ["section", "language"]}),
        (
            "Title and responsibility",
            {
                "fields": [
                    "raw_author_string",
                    "title",
                    "parallel_title",
                    "subtitle",
                    "title_remainder",
                    "volume_number",
                    "part_number",
                    "part_title",
                    "responsibility_statement",
                ]
            },
        ),
        (
            "Edition and publication",
            {
                "fields": [
                    "edition_statement",
                    "additional_edition_statement",
                    "publication_place",
                    "publisher",
                    "publication_date",
                    "inferred_year",
                    "manufacture_place",
                    "manufacturer",
                    "manufacture_date",
                    "copyright_date",
                ]
            },
        ),
        (
            "Physical description",
            {
                "fields": [
                    "extent",
                    "illustrations",
                    "dimensions",
                    "accompanying_material",
                    "circulation",
                ]
            },
        ),
        (
            "Series, notes, identifiers",
            {
                "fields": [
                    "series_statement",
                    "notes",
                    "bibliography_note",
                    "index_note",
                    "contents_note",
                    "isbn",
                    "issn",
                    "doi",
                    "url",
                    "access_date",
                    "content_type",
                    "media_type",
                    "carrier_type",
                ]
            },
        ),
        (
            "Raw source text",
            {"fields": ["raw_publication_details", "raw_host_title", "public_review"]},
        ),
    ]
    inlines = [SourceAuthorInline, SourceTagInline]

    @admin.display(description="Title")
    def short_title(self, obj):
        return obj.title[:120]


@admin.register(Periodical)
class PeriodicalAdmin(admin.ModelAdmin):
    search_fields = ["periodical_id", "source_django_id", "title", "place", "publisher", "issn"]
    list_display = ["title", "place", "publisher", "periodicity", "start_year", "end_year", "source_django_id"]
    autocomplete_fields = ["legacy_journal"]


@admin.register(Issue)
class IssueAdmin(admin.ModelAdmin):
    search_fields = [
        "issue_id",
        "source_django_id",
        "periodical__title",
        "source__title",
        "title",
        "issue_number",
        "volume",
        "publication_details",
    ]
    list_display = ["issue_type", "periodical", "source", "title", "year", "issue_number", "volume"]
    list_filter = ["issue_type"]
    autocomplete_fields = ["periodical", "source", "legacy_journal_issue", "legacy_container_work"]


@admin.register(ArticlePlacement)
class ArticlePlacementAdmin(admin.ModelAdmin):
    search_fields = ["placement_id", "source_django_id", "source__title", "issue__title", "issue__periodical__title", "pages_raw"]
    list_display = ["source", "issue", "pages_raw", "page_start", "page_end"]
    autocomplete_fields = ["source", "issue", "legacy_article"]


class SourceGroupItemInline(admin.TabularInline):
    model = SourceGroupItem
    extra = 0
    autocomplete_fields = ["source"]


@admin.register(SourceGroup)
class SourceGroupAdmin(admin.ModelAdmin):
    search_fields = ["group_id", "source_django_id", "title", "note"]
    list_display = ["title", "group_type", "source_django_id"]
    list_filter = ["group_type"]
    autocomplete_fields = ["legacy_work_group"]
    inlines = [SourceGroupItemInline]
