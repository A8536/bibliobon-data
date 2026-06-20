from django.db import models


class Language(models.Model):
    language_id = models.CharField(max_length=64, primary_key=True)
    source_django_id = models.PositiveIntegerField(null=True, blank=True, db_index=True)
    code = models.CharField(max_length=16, unique=True)
    title = models.CharField(max_length=128)
    description = models.TextField(blank=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "title"]

    def __str__(self):
        return self.title


class Section(models.Model):
    section_id = models.CharField(max_length=64, primary_key=True)
    source_django_id = models.PositiveIntegerField(null=True, blank=True, db_index=True)
    source_code = models.CharField(max_length=32, unique=True)
    parent = models.ForeignKey("self", on_delete=models.PROTECT, null=True, blank=True, related_name="children")
    title = models.CharField(max_length=512)
    description = models.TextField(blank=True)
    note = models.TextField(blank=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "source_code"]

    def __str__(self):
        return self.title


class Tag(models.Model):
    class TagType(models.TextChoices):
        GENERAL = "general", "General"
        THEMATIC = "thematic", "Thematic"
        GEOGRAPHIC = "geographic", "Geographic"
        ISSUER = "issuer", "Issuer"
        NAME = "name", "Name"

    tag_id = models.CharField(max_length=64, primary_key=True)
    source_django_id = models.PositiveIntegerField(null=True, blank=True, db_index=True)
    title = models.CharField(max_length=512)
    tag_type = models.CharField(max_length=32, choices=TagType.choices, default=TagType.GENERAL)
    parent = models.ForeignKey("self", on_delete=models.PROTECT, null=True, blank=True, related_name="children")
    description = models.TextField(blank=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "title"]
        constraints = [
            models.UniqueConstraint(fields=["parent", "title"], name="sources_tag_unique_parent_title")
        ]

    def __str__(self):
        return self.title


class Author(models.Model):
    author_id = models.CharField(max_length=64, primary_key=True)
    source_django_id = models.PositiveIntegerField(null=True, blank=True, db_index=True)
    display_name = models.CharField(max_length=512, unique=True)
    heading_name = models.CharField(max_length=512, blank=True)
    sort_name = models.CharField(max_length=512, blank=True)
    aliases = models.TextField(blank=True)
    person_dates = models.CharField(max_length=128, blank=True)
    authority_note = models.TextField(blank=True)
    note = models.TextField(blank=True)

    class Meta:
        ordering = ["sort_name", "display_name"]

    def __str__(self):
        return self.display_name


class Journal(models.Model):
    journal_id = models.CharField(max_length=64, primary_key=True)
    source_django_id = models.PositiveIntegerField(null=True, blank=True, db_index=True)
    title = models.CharField(max_length=512, unique=True)
    parallel_title = models.CharField(max_length=512, blank=True)
    title_remainder = models.TextField(blank=True)
    responsibility_statement = models.TextField(blank=True)
    place = models.CharField(max_length=255, blank=True)
    publisher = models.CharField(max_length=255, blank=True)
    issn = models.CharField(max_length=64, blank=True)
    periodicity = models.CharField(max_length=128, blank=True)
    numbering_start = models.CharField(max_length=128, blank=True)
    numbering_end = models.CharField(max_length=128, blank=True)
    start_year = models.PositiveSmallIntegerField(null=True, blank=True)
    end_year = models.PositiveSmallIntegerField(null=True, blank=True)
    title_history_note = models.TextField(blank=True)
    description = models.TextField(blank=True)

    class Meta:
        ordering = ["title"]

    def __str__(self):
        return self.title


class JournalIssue(models.Model):
    journal_issue_id = models.CharField(max_length=64, primary_key=True)
    source_django_id = models.PositiveIntegerField(null=True, blank=True, db_index=True)
    journal = models.ForeignKey(Journal, on_delete=models.PROTECT, related_name="issues")
    title = models.TextField(blank=True)
    parallel_title = models.CharField(max_length=512, blank=True)
    title_remainder = models.TextField(blank=True)
    responsibility_statement = models.TextField(blank=True)
    year = models.PositiveSmallIntegerField(null=True, blank=True)
    publication_date = models.CharField(max_length=128, blank=True)
    issue_number = models.CharField(max_length=64, blank=True)
    volume = models.CharField(max_length=64, blank=True)
    part_number = models.CharField(max_length=64, blank=True)
    gross_number = models.CharField(max_length=64, blank=True)
    date_text = models.CharField(max_length=128, blank=True)
    chronology = models.CharField(max_length=255, blank=True)
    enumeration = models.CharField(max_length=255, blank=True)
    publication_place = models.CharField(max_length=255, blank=True)
    publisher = models.CharField(max_length=255, blank=True)
    publication_details = models.TextField(blank=True)
    issn = models.CharField(max_length=64, blank=True)
    isbn = models.CharField(max_length=64, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["journal__title", "year", "issue_number"]

    def __str__(self):
        bits = [self.journal.title]
        if self.year:
            bits.append(str(self.year))
        if self.issue_number:
            bits.append(f"#{self.issue_number}")
        return ", ".join(bits)


class Work(models.Model):
    class WorkType(models.TextChoices):
        BOOK = "book", "Book"
        CONTAINER = "container", "Container"
        ARTICLE = "article", "Article"
        UNKNOWN = "unknown", "Unknown"

    class DescriptionStatus(models.TextChoices):
        PARSED = "parsed", "Parsed"
        PARTIAL = "partial", "Partial"
        RAW_ONLY = "raw_only", "Raw only"
        NEEDS_REVIEW = "needs_review", "Needs review"

    work_id = models.CharField(max_length=64, primary_key=True)
    source_django_id = models.PositiveIntegerField(null=True, blank=True, db_index=True)
    source_sequence = models.PositiveIntegerField(null=True, blank=True, unique=True)
    source_number = models.PositiveIntegerField(db_index=True)
    source_page_marker = models.CharField(max_length=32, blank=True)
    source_section = models.ForeignKey(Section, on_delete=models.PROTECT, null=True, blank=True, related_name="source_works")
    language = models.ForeignKey(Language, on_delete=models.PROTECT, related_name="works")
    work_type = models.CharField(max_length=16, choices=WorkType.choices, default=WorkType.UNKNOWN)
    is_container = models.BooleanField(default=False)
    raw_author_string = models.TextField(blank=True)
    title = models.TextField()
    parallel_title = models.CharField(max_length=512, blank=True)
    subtitle = models.TextField(blank=True)
    title_remainder = models.TextField(blank=True)
    volume_number = models.CharField(max_length=64, blank=True)
    part_number = models.CharField(max_length=64, blank=True)
    part_title = models.TextField(blank=True)
    responsibility_note = models.TextField(blank=True)
    responsibility_statement = models.TextField(blank=True)
    host_title = models.TextField(blank=True)
    edition_statement = models.CharField(max_length=255, blank=True)
    additional_edition_statement = models.CharField(max_length=255, blank=True)
    publication_place = models.CharField(max_length=255, blank=True)
    publisher = models.CharField(max_length=255, blank=True)
    publication_date = models.CharField(max_length=128, blank=True)
    manufacture_place = models.CharField(max_length=255, blank=True)
    manufacturer = models.CharField(max_length=255, blank=True)
    manufacture_date = models.CharField(max_length=128, blank=True)
    copyright_date = models.CharField(max_length=128, blank=True)
    physical_description = models.TextField(blank=True)
    extent = models.CharField(max_length=255, blank=True)
    illustrations = models.CharField(max_length=255, blank=True)
    dimensions = models.CharField(max_length=128, blank=True)
    accompanying_material = models.CharField(max_length=255, blank=True)
    circulation = models.CharField(max_length=128, blank=True)
    article_pages = models.CharField(max_length=128, blank=True)
    page_start = models.PositiveIntegerField(null=True, blank=True)
    page_end = models.PositiveIntegerField(null=True, blank=True)
    publication_details = models.TextField(blank=True)
    series_statement = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    bibliography_note = models.TextField(blank=True)
    index_note = models.TextField(blank=True)
    contents_note = models.TextField(blank=True)
    isbn = models.CharField(max_length=64, blank=True)
    issn = models.CharField(max_length=64, blank=True)
    doi = models.CharField(max_length=255, blank=True)
    url = models.URLField(blank=True)
    access_date = models.DateField(null=True, blank=True)
    content_type = models.CharField(max_length=128, blank=True)
    media_type = models.CharField(max_length=128, blank=True)
    carrier_type = models.CharField(max_length=128, blank=True)
    description_status = models.CharField(
        max_length=32,
        choices=DescriptionStatus.choices,
        default=DescriptionStatus.RAW_ONLY,
    )
    public_review = models.TextField(blank=True)
    inferred_year = models.PositiveSmallIntegerField(null=True, blank=True)
    authors = models.ManyToManyField(Author, through="WorkAuthor", related_name="works", blank=True)
    tags = models.ManyToManyField(Tag, through="WorkTag", related_name="works", blank=True)

    class Meta:
        ordering = ["source_sequence", "source_number"]

    def __str__(self):
        return f"{self.source_number}. {self.title}"

    @property
    def historical_source_number(self):
        if self.source_sequence is None or self.source_number >= 900000000:
            return None
        return self.source_number


class Book(models.Model):
    book_id = models.CharField(max_length=64, primary_key=True)
    source_django_id = models.PositiveIntegerField(null=True, blank=True, db_index=True)
    work = models.OneToOneField(Work, on_delete=models.CASCADE, related_name="book")
    edition = models.CharField(max_length=255, blank=True)
    page_count = models.CharField(max_length=255, blank=True)
    isbn = models.CharField(max_length=32, blank=True)

    def __str__(self):
        return str(self.work)


class Collection(models.Model):
    collection_id = models.CharField(max_length=64, primary_key=True)
    source_django_id = models.PositiveIntegerField(null=True, blank=True, db_index=True)
    parent_work = models.OneToOneField(Work, on_delete=models.PROTECT, null=True, blank=True, related_name="legacy_collection")
    title = models.CharField(max_length=1024)
    publication_details = models.TextField(blank=True)
    year = models.PositiveSmallIntegerField(null=True, blank=True)
    place = models.CharField(max_length=255, blank=True)
    publisher = models.CharField(max_length=255, blank=True)
    source_text = models.TextField(blank=True)

    class Meta:
        ordering = ["title", "year"]

    def __str__(self):
        return self.title


class Article(models.Model):
    article_id = models.CharField(max_length=64, primary_key=True)
    source_django_id = models.PositiveIntegerField(null=True, blank=True, db_index=True)
    work = models.OneToOneField(Work, on_delete=models.CASCADE, related_name="article")
    container_work = models.ForeignKey(Work, on_delete=models.PROTECT, null=True, blank=True, related_name="contained_articles")
    collection = models.ForeignKey(Collection, on_delete=models.PROTECT, null=True, blank=True, related_name="articles")
    journal_issue = models.ForeignKey(JournalIssue, on_delete=models.PROTECT, null=True, blank=True, related_name="articles")
    pages = models.CharField(max_length=128, blank=True)
    pages_raw = models.CharField(max_length=128, blank=True)
    page_start = models.PositiveIntegerField(null=True, blank=True)
    page_end = models.PositiveIntegerField(null=True, blank=True)
    location_note = models.CharField(max_length=255, blank=True)
    placement_note = models.TextField(blank=True)

    def __str__(self):
        return str(self.work)


class WorkGroup(models.Model):
    class GroupType(models.TextChoices):
        MULTIVOLUME = "multivolume", "Multivolume"
        SERIES = "series", "Series"
        SET = "set", "Set"
        SUPPLEMENT_GROUP = "supplement_group", "Supplement group"
        RELATED_GROUP = "related_group", "Related group"

    group_id = models.CharField(max_length=64, primary_key=True)
    source_django_id = models.PositiveIntegerField(null=True, blank=True, db_index=True)
    title = models.CharField(max_length=1024)
    group_type = models.CharField(max_length=32, choices=GroupType.choices, default=GroupType.MULTIVOLUME)
    note = models.TextField(blank=True)

    class Meta:
        ordering = ["title", "group_id"]

    def __str__(self):
        return self.title


class WorkGroupItem(models.Model):
    group = models.ForeignKey(WorkGroup, on_delete=models.CASCADE, related_name="items")
    work = models.ForeignKey(Work, on_delete=models.CASCADE, related_name="group_items")
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "work__volume_number", "work__inferred_year", "work__title"]
        constraints = [
            models.UniqueConstraint(fields=["group", "work"], name="sources_work_group_item_unique_group_work")
        ]


class WorkAuthor(models.Model):
    work = models.ForeignKey(Work, on_delete=models.CASCADE)
    author = models.ForeignKey(Author, on_delete=models.PROTECT)
    sort_order = models.PositiveIntegerField(default=0)
    role = models.CharField(max_length=128, blank=True)
    source_text = models.CharField(max_length=512, blank=True)
    name_as_printed = models.CharField(max_length=512, blank=True)
    include_in_responsibility = models.BooleanField(default=True)
    is_primary_heading = models.BooleanField(default=False)

    class Meta:
        ordering = ["sort_order", "id"]
        constraints = [
            models.UniqueConstraint(fields=["work", "author", "role"], name="sources_work_author_unique_role")
        ]


class WorkTag(models.Model):
    work = models.ForeignKey(Work, on_delete=models.CASCADE)
    tag = models.ForeignKey(Tag, on_delete=models.PROTECT)
    sort_order = models.PositiveIntegerField(default=0)
    source_text = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["sort_order", "id"]
        constraints = [
            models.UniqueConstraint(fields=["work", "tag"], name="sources_work_tag_unique")
        ]


class Source(models.Model):
    class SourceType(models.TextChoices):
        MONOGRAPH = "monograph", "Monograph"
        ARTICLE = "article", "Article"
        ISSUE = "issue", "Issue"
        UNKNOWN = "unknown", "Unknown"

    class DescriptionStatus(models.TextChoices):
        PARSED = "parsed", "Parsed"
        PARTIAL = "partial", "Partial"
        RAW_ONLY = "raw_only", "Raw only"
        NEEDS_REVIEW = "needs_review", "Needs review"

    source_id = models.CharField(max_length=64, primary_key=True)
    source_django_id = models.PositiveIntegerField(null=True, blank=True, db_index=True)
    legacy_work = models.OneToOneField(Work, on_delete=models.SET_NULL, null=True, blank=True, related_name="target_source")
    source_sequence = models.PositiveIntegerField(null=True, blank=True, db_index=True)
    source_number = models.PositiveIntegerField(db_index=True)
    source_page_marker = models.CharField(max_length=32, blank=True)
    source_type = models.CharField(max_length=16, choices=SourceType.choices, default=SourceType.UNKNOWN)
    section = models.ForeignKey(Section, on_delete=models.PROTECT, null=True, blank=True, related_name="sources")
    language = models.ForeignKey(Language, on_delete=models.PROTECT, related_name="sources")
    raw_author_string = models.TextField(blank=True)
    title = models.TextField()
    parallel_title = models.CharField(max_length=512, blank=True)
    subtitle = models.TextField(blank=True)
    title_remainder = models.TextField(blank=True)
    volume_number = models.CharField(max_length=64, blank=True)
    part_number = models.CharField(max_length=64, blank=True)
    part_title = models.TextField(blank=True)
    responsibility_statement = models.TextField(blank=True)
    edition_statement = models.CharField(max_length=255, blank=True)
    additional_edition_statement = models.CharField(max_length=255, blank=True)
    publication_place = models.CharField(max_length=255, blank=True)
    publisher = models.CharField(max_length=255, blank=True)
    publication_date = models.CharField(max_length=128, blank=True)
    inferred_year = models.PositiveSmallIntegerField(null=True, blank=True)
    manufacture_place = models.CharField(max_length=255, blank=True)
    manufacturer = models.CharField(max_length=255, blank=True)
    manufacture_date = models.CharField(max_length=128, blank=True)
    copyright_date = models.CharField(max_length=128, blank=True)
    extent = models.CharField(max_length=255, blank=True)
    illustrations = models.CharField(max_length=255, blank=True)
    dimensions = models.CharField(max_length=128, blank=True)
    accompanying_material = models.CharField(max_length=255, blank=True)
    circulation = models.CharField(max_length=128, blank=True)
    series_statement = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    bibliography_note = models.TextField(blank=True)
    index_note = models.TextField(blank=True)
    contents_note = models.TextField(blank=True)
    isbn = models.CharField(max_length=64, blank=True)
    issn = models.CharField(max_length=64, blank=True)
    doi = models.CharField(max_length=255, blank=True)
    url = models.URLField(blank=True)
    access_date = models.DateField(null=True, blank=True)
    content_type = models.CharField(max_length=128, blank=True)
    media_type = models.CharField(max_length=128, blank=True)
    carrier_type = models.CharField(max_length=128, blank=True)
    raw_publication_details = models.TextField(blank=True)
    raw_host_title = models.TextField(blank=True)
    public_review = models.TextField(blank=True)
    data_source = models.CharField(max_length=128, blank=True, default="editor")
    first_seen_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(null=True, blank=True)
    description_status = models.CharField(
        max_length=32,
        choices=DescriptionStatus.choices,
        default=DescriptionStatus.RAW_ONLY,
    )

    class Meta:
        ordering = ["source_sequence", "source_number", "source_id"]

    def __str__(self):
        return f"{self.source_number}. {self.title}"


class SourceAuthor(models.Model):
    source = models.ForeignKey(Source, on_delete=models.CASCADE, related_name="source_authors")
    author = models.ForeignKey(Author, on_delete=models.PROTECT, related_name="source_authors")
    sort_order = models.PositiveIntegerField(default=0)
    role = models.CharField(max_length=128, blank=True)
    source_text = models.CharField(max_length=512, blank=True)
    name_as_printed = models.CharField(max_length=512, blank=True)
    include_in_responsibility = models.BooleanField(default=True)
    is_primary_heading = models.BooleanField(default=False)
    created_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["sort_order", "id"]
        constraints = [
            models.UniqueConstraint(fields=["source", "author", "role"], name="sources_source_author_unique_role")
        ]


class SourceTag(models.Model):
    source = models.ForeignKey(Source, on_delete=models.CASCADE, related_name="source_tags")
    tag = models.ForeignKey(Tag, on_delete=models.PROTECT, related_name="source_tags")
    sort_order = models.PositiveIntegerField(default=0)
    source_text = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["sort_order", "id"]
        constraints = [
            models.UniqueConstraint(fields=["source", "tag"], name="sources_source_tag_unique")
        ]


class Periodical(models.Model):
    periodical_id = models.CharField(max_length=64, primary_key=True)
    source_django_id = models.PositiveIntegerField(null=True, blank=True, db_index=True)
    legacy_journal = models.OneToOneField(Journal, on_delete=models.SET_NULL, null=True, blank=True, related_name="target_periodical")
    title = models.CharField(max_length=512, unique=True)
    parallel_title = models.CharField(max_length=512, blank=True)
    title_remainder = models.TextField(blank=True)
    responsibility_statement = models.TextField(blank=True)
    place = models.CharField(max_length=255, blank=True)
    publisher = models.CharField(max_length=255, blank=True)
    issn = models.CharField(max_length=64, blank=True)
    periodicity = models.CharField(max_length=128, blank=True)
    numbering_start = models.CharField(max_length=128, blank=True)
    numbering_end = models.CharField(max_length=128, blank=True)
    start_year = models.PositiveSmallIntegerField(null=True, blank=True)
    end_year = models.PositiveSmallIntegerField(null=True, blank=True)
    title_history_note = models.TextField(blank=True)
    description = models.TextField(blank=True)

    class Meta:
        ordering = ["title"]

    def __str__(self):
        return self.title


class Issue(models.Model):
    class IssueType(models.TextChoices):
        PERIODICAL_ISSUE = "periodical_issue", "Periodical issue"
        COLLECTION = "collection", "Collection"
        VOLUME = "volume", "Volume"
        UNKNOWN = "unknown", "Unknown"

    issue_id = models.CharField(max_length=64, primary_key=True)
    source_django_id = models.PositiveIntegerField(null=True, blank=True, db_index=True)
    legacy_journal_issue = models.OneToOneField(JournalIssue, on_delete=models.SET_NULL, null=True, blank=True, related_name="target_issue")
    legacy_container_work = models.OneToOneField(Work, on_delete=models.SET_NULL, null=True, blank=True, related_name="target_container_issue")
    issue_type = models.CharField(max_length=32, choices=IssueType.choices, default=IssueType.UNKNOWN)
    periodical = models.ForeignKey(Periodical, on_delete=models.PROTECT, null=True, blank=True, related_name="issues")
    source = models.ForeignKey(Source, on_delete=models.PROTECT, null=True, blank=True, related_name="described_issues")
    title = models.TextField(blank=True)
    parallel_title = models.CharField(max_length=512, blank=True)
    title_remainder = models.TextField(blank=True)
    responsibility_statement = models.TextField(blank=True)
    year = models.PositiveSmallIntegerField(null=True, blank=True)
    publication_date = models.CharField(max_length=128, blank=True)
    issue_number = models.CharField(max_length=64, blank=True)
    volume = models.CharField(max_length=64, blank=True)
    part_number = models.CharField(max_length=64, blank=True)
    gross_number = models.CharField(max_length=64, blank=True)
    chronology = models.CharField(max_length=255, blank=True)
    enumeration = models.CharField(max_length=255, blank=True)
    publication_place = models.CharField(max_length=255, blank=True)
    publisher = models.CharField(max_length=255, blank=True)
    publication_details = models.TextField(blank=True)
    issn = models.CharField(max_length=64, blank=True)
    isbn = models.CharField(max_length=64, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["periodical__title", "year", "issue_number", "title", "issue_id"]

    def __str__(self):
        bits = []
        if self.periodical:
            bits.append(self.periodical.title)
        elif self.source:
            bits.append(self.source.title)
        elif self.title:
            bits.append(self.title)
        else:
            bits.append(self.issue_id)
        if self.year:
            bits.append(str(self.year))
        if self.issue_number:
            bits.append(f"№ {self.issue_number}")
        if self.volume:
            bits.append(f"т. {self.volume}")
        return ", ".join(bits)


class ArticlePlacement(models.Model):
    placement_id = models.CharField(max_length=64, primary_key=True)
    source_django_id = models.PositiveIntegerField(null=True, blank=True, db_index=True)
    legacy_article = models.OneToOneField(Article, on_delete=models.SET_NULL, null=True, blank=True, related_name="target_placement")
    source = models.OneToOneField(Source, on_delete=models.CASCADE, related_name="article_placement")
    issue = models.ForeignKey(Issue, on_delete=models.PROTECT, related_name="article_placements")
    pages_raw = models.CharField(max_length=128, blank=True)
    page_start = models.PositiveIntegerField(null=True, blank=True)
    page_end = models.PositiveIntegerField(null=True, blank=True)
    location_note = models.CharField(max_length=255, blank=True)
    placement_note = models.TextField(blank=True)
    created_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["issue", "page_start", "source__source_sequence", "source__source_number"]


class SourceGroup(models.Model):
    class GroupType(models.TextChoices):
        MULTIVOLUME = "multivolume", "Multivolume"
        EDITIONS = "editions", "Editions"
        SERIES = "series", "Series"
        SET = "set", "Set"
        SUPPLEMENT_GROUP = "supplement_group", "Supplement group"
        RELATED_GROUP = "related_group", "Related group"

    group_id = models.CharField(max_length=64, primary_key=True)
    source_django_id = models.PositiveIntegerField(null=True, blank=True, db_index=True)
    legacy_work_group = models.OneToOneField(WorkGroup, on_delete=models.SET_NULL, null=True, blank=True, related_name="target_group")
    title = models.CharField(max_length=1024)
    group_type = models.CharField(max_length=32, choices=GroupType.choices, default=GroupType.MULTIVOLUME)
    note = models.TextField(blank=True)

    class Meta:
        ordering = ["title", "group_id"]

    def __str__(self):
        return self.title


class SourceGroupItem(models.Model):
    group = models.ForeignKey(SourceGroup, on_delete=models.CASCADE, related_name="items")
    source = models.ForeignKey(Source, on_delete=models.CASCADE, related_name="group_items")
    sort_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["sort_order", "source__volume_number", "source__inferred_year", "source__title"]
        constraints = [
            models.UniqueConstraint(fields=["group", "source"], name="sources_source_group_item_unique_group_source")
        ]


class ImportBatch(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PARSED = "parsed", "Parsed"
        REVIEW_REQUIRED = "review_required", "Review required"
        READY_TO_APPLY = "ready_to_apply", "Ready to apply"
        APPLIED = "applied", "Applied"
        CANCELLED = "cancelled", "Cancelled"

    class SourceType(models.TextChoices):
        PLAIN_TEXT = "plain_text", "Plain text"
        FILE = "file", "File"
        CSV = "csv", "CSV"
        JSON = "json", "JSON"
        MANUAL = "manual", "Manual"

    title = models.CharField(max_length=512)
    source_name = models.CharField(max_length=512, blank=True)
    source_type = models.CharField(max_length=32, choices=SourceType.choices, default=SourceType.PLAIN_TEXT)
    raw_input = models.TextField(blank=True)
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.DRAFT, db_index=True)
    created_by = models.ForeignKey("auth.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="bibliography_imports")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    parsed_at = models.DateTimeField(null=True, blank=True)
    applied_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return self.title


class ImportItem(models.Model):
    class DetectedType(models.TextChoices):
        BOOK = "book", "Book"
        JOURNAL_ARTICLE = "journal_article", "Journal article"
        NEWSPAPER_ARTICLE = "newspaper_article", "Newspaper article"
        COLLECTION_ARTICLE = "collection_article", "Collection article"
        JOURNAL = "journal", "Journal"
        JOURNAL_ISSUE = "journal_issue", "Journal issue"
        COLLECTION = "collection", "Collection"
        VOLUME = "volume", "Volume"
        AUTHOR = "author", "Author"
        UNKNOWN = "unknown", "Unknown"

    class Status(models.TextChoices):
        PARSED = "parsed", "Parsed"
        NEEDS_REVIEW = "needs_review", "Needs review"
        FOUND_EXISTING_NO_CHANGES = "found_existing_no_changes", "Found existing, no changes"
        FOUND_EXISTING_WITH_DIFFERENCES = "found_existing_with_differences", "Found existing with differences"
        STRUCTURAL_CONFLICT = "structural_conflict", "Structural conflict"
        READY = "ready", "Ready"
        APPLIED = "applied", "Applied"
        REJECTED = "rejected", "Rejected"
        POSTPONED = "postponed", "Postponed"
        ERROR = "error", "Error"

    import_batch = models.ForeignKey(ImportBatch, on_delete=models.CASCADE, related_name="items")
    raw_text = models.TextField()
    detected_type = models.CharField(max_length=32, choices=DetectedType.choices, default=DetectedType.UNKNOWN, db_index=True)
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.PARSED, db_index=True)
    confidence = models.FloatField(default=0)
    parsed_data_json = models.JSONField(default=dict, blank=True)
    normalized_data_json = models.JSONField(default=dict, blank=True)
    errors_json = models.JSONField(default=list, blank=True)
    matched_existing_type = models.CharField(max_length=64, blank=True)
    matched_existing_id = models.CharField(max_length=128, blank=True)
    comparison_json = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.import_batch_id}: {self.detected_type}"


class ImportEntity(models.Model):
    class EntityType(models.TextChoices):
        AUTHOR = "author", "Author"
        BOOK = "book", "Book"
        ARTICLE = "article", "Article"
        JOURNAL = "journal", "Journal"
        JOURNAL_ISSUE = "journal_issue", "Journal issue"
        COLLECTION = "collection", "Collection"
        COLLECTION_VOLUME = "collection_volume", "Collection volume"
        PUBLISHER = "publisher", "Publisher"
        THEME = "theme", "Theme"
        SECTION = "section", "Section"

    class Status(models.TextChoices):
        UNRESOLVED = "unresolved", "Unresolved"
        WILL_CREATE = "will_create", "Will create"
        LINKED_EXISTING = "linked_existing", "Linked existing"
        WILL_UPDATE_EXISTING = "will_update_existing", "Will update existing"
        IGNORED = "ignored", "Ignored"
        APPLIED = "applied", "Applied"
        ERROR = "error", "Error"

    import_batch = models.ForeignKey(ImportBatch, on_delete=models.CASCADE, related_name="entities")
    entity_type = models.CharField(max_length=32, choices=EntityType.choices, db_index=True)
    label = models.CharField(max_length=1024)
    normalized_key = models.CharField(max_length=1024, db_index=True)
    data_json = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.UNRESOLVED, db_index=True)
    confidence = models.FloatField(default=0)
    matched_existing_type = models.CharField(max_length=64, blank=True)
    matched_existing_id = models.CharField(max_length=128, blank=True)
    created_entity_type = models.CharField(max_length=64, blank=True)
    created_entity_id = models.CharField(max_length=128, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["entity_type", "label", "id"]
        constraints = [
            models.UniqueConstraint(fields=["import_batch", "entity_type", "normalized_key"], name="sources_import_entity_unique_key")
        ]

    def __str__(self):
        return self.label


class ImportEntityRelation(models.Model):
    import_batch = models.ForeignKey(ImportBatch, on_delete=models.CASCADE, related_name="entity_relations")
    parent_entity = models.ForeignKey(ImportEntity, on_delete=models.CASCADE, related_name="child_relations")
    child_entity = models.ForeignKey(ImportEntity, on_delete=models.CASCADE, related_name="parent_relations")
    relation_type = models.CharField(max_length=64, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(fields=["import_batch", "parent_entity", "child_entity", "relation_type"], name="sources_import_relation_unique")
        ]


class ImportGroup(models.Model):
    class GroupType(models.TextChoices):
        JOURNAL_ISSUE_GROUP = "journal_issue_group", "Journal issue group"
        COLLECTION_VOLUME_GROUP = "collection_volume_group", "Collection volume group"
        AUTHOR_GROUP = "author_group", "Author group"
        STANDALONE_BOOKS = "standalone_books", "Standalone books"
        UNRESOLVED = "unresolved", "Unresolved"

    class Status(models.TextChoices):
        NEEDS_REVIEW = "needs_review", "Needs review"
        PARTIALLY_READY = "partially_ready", "Partially ready"
        READY = "ready", "Ready"
        APPLIED = "applied", "Applied"
        POSTPONED = "postponed", "Postponed"
        ERROR = "error", "Error"

    import_batch = models.ForeignKey(ImportBatch, on_delete=models.CASCADE, related_name="groups")
    group_type = models.CharField(max_length=64, choices=GroupType.choices, db_index=True)
    label = models.CharField(max_length=1024)
    root_entity = models.ForeignKey(ImportEntity, on_delete=models.SET_NULL, null=True, blank=True, related_name="root_import_groups")
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.NEEDS_REVIEW, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["group_type", "label", "id"]

    def __str__(self):
        return self.label


class ImportDecision(models.Model):
    class DecisionType(models.TextChoices):
        CREATE = "create", "Create"
        LINK_EXISTING = "link_existing", "Link existing"
        UPDATE_EXISTING = "update_existing", "Update existing"
        SKIP = "skip", "Skip"
        REJECT = "reject", "Reject"
        POSTPONE = "postpone", "Postpone"
        SPLIT_GROUP = "split_group", "Split group"
        MOVE_TO_GROUP = "move_to_group", "Move to group"

    import_batch = models.ForeignKey(ImportBatch, on_delete=models.CASCADE, related_name="decisions")
    entity = models.ForeignKey(ImportEntity, on_delete=models.CASCADE, null=True, blank=True, related_name="decisions")
    item = models.ForeignKey(ImportItem, on_delete=models.CASCADE, null=True, blank=True, related_name="decisions")
    group = models.ForeignKey(ImportGroup, on_delete=models.CASCADE, null=True, blank=True, related_name="decisions")
    decision_type = models.CharField(max_length=32, choices=DecisionType.choices, db_index=True)
    target_type = models.CharField(max_length=64, blank=True)
    target_id = models.CharField(max_length=128, blank=True)
    payload_json = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey("auth.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="bibliography_import_decisions")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at", "-id"]


class ImportMatch(models.Model):
    import_batch = models.ForeignKey(ImportBatch, on_delete=models.CASCADE, related_name="matches")
    entity = models.ForeignKey(ImportEntity, on_delete=models.CASCADE, related_name="matches")
    existing_type = models.CharField(max_length=64)
    existing_id = models.CharField(max_length=128)
    score = models.FloatField(default=0, db_index=True)
    match_reason_json = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-score", "existing_type", "existing_id"]


class ImportApplyLog(models.Model):
    import_batch = models.ForeignKey(ImportBatch, on_delete=models.CASCADE, related_name="apply_logs")
    applied_by = models.ForeignKey("auth.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="bibliography_import_apply_logs")
    applied_at = models.DateTimeField(auto_now_add=True)
    summary_json = models.JSONField(default=dict, blank=True)
    created_entities_json = models.JSONField(default=list, blank=True)
    updated_entities_json = models.JSONField(default=list, blank=True)
    created_relations_json = models.JSONField(default=list, blank=True)
    rejected_items_json = models.JSONField(default=list, blank=True)
    decisions_json = models.JSONField(default=list, blank=True)
    raw_input = models.TextField(blank=True)

    class Meta:
        ordering = ["-applied_at", "-id"]
