from django.contrib.auth import get_user_model
from django.test import TestCase
from unittest.mock import patch

from .import_workflow import (
    apply_entity_decision,
    apply_import_batch,
    apply_item_decision,
    build_import_plan,
    compare_work_to_parsed_data,
    contributor_role_label_ru,
    describe_existing_entity,
    extract_responsibility_contributors,
    normalize_issue_number,
    parse_host_details,
    parse_import_batch,
    parse_record,
    readiness_problems,
    normalize_contributor_role,
    split_author_list,
    split_article_to_new_group,
    validate_import_batch,
    move_article_to_group,
)
from .journal_normalization import apply_journal_normalization_plan, build_journal_normalization_plan
from .issue_collection_conversion import apply_issue_to_collection, build_issue_to_collection_plan
from .views import build_work_relations_context, parse_parent_fragment, split_journal_issue_title_for_work, split_multi_issue_bibliographic_line
from .models import (
    Article,
    ArticlePlacement,
    Author,
    Book,
    Issue,
    ImportApplyLog,
    ImportBatch,
    ImportDecision,
    ImportEntity,
    ImportEntityRelation,
    ImportGroup,
    ImportItem,
    Journal,
    JournalIssue,
    Language,
    Periodical,
    Section,
    Source,
    Work,
    WorkAuthor,
)


class ImportWorkflowTests(TestCase):
    def setUp(self):
        self.language = Language.objects.create(language_id="language-ru", code="ru", title="Russian")
        self.section = Section.objects.create(section_id="section-test", source_code="test", title="Test", sort_order=1)

    def make_import_article_group(self, batch, group_type, root_label, article_labels):
        if group_type == ImportGroup.GroupType.JOURNAL_ISSUE_GROUP:
            root_type = ImportEntity.EntityType.JOURNAL_ISSUE
            relation_type = "issue_has_article"
        else:
            root_type = ImportEntity.EntityType.COLLECTION
            relation_type = "article_in_collection"
        root = ImportEntity.objects.create(
            import_batch=batch,
            entity_type=root_type,
            label=root_label,
            normalized_key=f"{root_type}:{root_label}:{batch.pk}",
            status=ImportEntity.Status.WILL_CREATE,
        )
        group = ImportGroup.objects.create(
            import_batch=batch,
            group_type=group_type,
            label=root_label,
            root_entity=root,
            status=ImportGroup.Status.READY,
        )
        author = ImportEntity.objects.create(
            import_batch=batch,
            entity_type=ImportEntity.EntityType.AUTHOR,
            label=f"Автор {root_label}",
            normalized_key=f"author:{root_label}:{batch.pk}",
            status=ImportEntity.Status.WILL_CREATE,
        )
        articles = []
        for index, label in enumerate(article_labels, start=1):
            item = ImportItem.objects.create(
                import_batch=batch,
                raw_text=f"{label} // {root_label}",
                detected_type=ImportItem.DetectedType.JOURNAL_ARTICLE
                if group_type == ImportGroup.GroupType.JOURNAL_ISSUE_GROUP
                else ImportItem.DetectedType.COLLECTION_ARTICLE,
                status=ImportItem.Status.PARSED,
            )
            article = ImportEntity.objects.create(
                import_batch=batch,
                entity_type=ImportEntity.EntityType.ARTICLE,
                label=label,
                normalized_key=f"article:{root_label}:{index}:{batch.pk}",
                data_json={"item_id": item.id},
                status=ImportEntity.Status.WILL_CREATE,
            )
            ImportEntityRelation.objects.create(
                import_batch=batch,
                parent_entity=root,
                child_entity=article,
                relation_type=relation_type,
            )
            ImportEntityRelation.objects.create(
                import_batch=batch,
                parent_entity=author,
                child_entity=article,
                relation_type="author_of",
            )
            articles.append(article)
        return group, root, articles, author

    def make_weak_book_match_import(self, batch_status=ImportBatch.Status.REVIEW_REQUIRED):
        existing_author = Author.objects.create(
            author_id="author-weak-match",
            display_name="Мигулин П.П.",
            sort_name="Мигулин П.П.",
        )
        existing_work = Work.objects.create(
            work_id="work-weak-match",
            source_number=91001,
            source_sequence=91001,
            source_section=self.section,
            language=self.language,
            work_type=Work.WorkType.BOOK,
            title="Наша банковская политика (1729–1903): Опыт исследования",
            publication_place="Харьков",
            publication_date="1904",
            inferred_year=1904,
        )
        WorkAuthor.objects.create(work=existing_work, author=existing_author, sort_order=1)
        batch = ImportBatch.objects.create(
            title="Weak match import",
            raw_input="Мигулин П.П. Наша банковская политика (1729–1903): Опыт исследования. — Харьков, 1904.",
            status=batch_status,
        )
        item = ImportItem.objects.create(
            import_batch=batch,
            raw_text="Мигулин П.П. Наша банковская политика (1729–1903): Опыт исследования. — Харьков, 1904.",
            detected_type=ImportItem.DetectedType.BOOK,
            status=ImportItem.Status.PARSED,
            confidence=0.82,
        )
        author_entity = ImportEntity.objects.create(
            import_batch=batch,
            entity_type=ImportEntity.EntityType.AUTHOR,
            label="Мигулин П.П.",
            normalized_key="author:migulin",
            data_json={"name": "Мигулин П.П."},
            status=ImportEntity.Status.LINKED_EXISTING,
            matched_existing_type="author",
            matched_existing_id=existing_author.author_id,
        )
        book_entity = ImportEntity.objects.create(
            import_batch=batch,
            entity_type=ImportEntity.EntityType.BOOK,
            label="Наша банковская политика (1729–1903): Опыт исследования",
            normalized_key="book:migulin-policy",
            data_json={"item_id": item.id, "title": "Наша банковская политика (1729–1903): Опыт исследования"},
            status=ImportEntity.Status.UNRESOLVED,
        )
        ImportEntityRelation.objects.create(
            import_batch=batch,
            parent_entity=author_entity,
            child_entity=book_entity,
            relation_type="author_of",
        )
        batch.matches.create(
            entity=book_entity,
            existing_type="work",
            existing_id=existing_work.work_id,
            score=0.84,
            match_reason_json={"title_similarity": 0.84},
        )
        return batch, item, book_entity, author_entity, existing_work

    def make_weak_article_group_import(self, batch_status=ImportBatch.Status.REVIEW_REQUIRED):
        existing_author = Author.objects.create(
            author_id="author-weak-article",
            display_name="Мекк А.",
            sort_name="Мекк А.",
        )
        existing_work = Work.objects.create(
            work_id="work-weak-article",
            source_number=91002,
            source_sequence=91002,
            source_section=self.section,
            language=self.language,
            work_type=Work.WorkType.ARTICLE,
            title="К вопросу о бумажно-денежном обращении",
            inferred_year=1887,
        )
        WorkAuthor.objects.create(work=existing_work, author=existing_author, sort_order=1)
        journal = Journal.objects.create(journal_id="journal-weak-article", title="Экономический журнал")
        issue = JournalIssue.objects.create(
            journal_issue_id="journal-issue-weak-article",
            journal=journal,
            year=1887,
            issue_number="8–9",
        )
        batch = ImportBatch.objects.create(
            title="Weak article group",
            raw_input="Мекк А. К вопросу о бумажно-денежном обращении // Экономический журнал. 1887. № 8–9.",
            status=batch_status,
        )
        item = ImportItem.objects.create(
            import_batch=batch,
            raw_text="Мекк А. К вопросу о бумажно-денежном обращении // Экономический журнал. 1887. № 8–9.",
            detected_type=ImportItem.DetectedType.JOURNAL_ARTICLE,
            status=ImportItem.Status.PARSED,
            confidence=0.81,
        )
        journal_entity = ImportEntity.objects.create(
            import_batch=batch,
            entity_type=ImportEntity.EntityType.JOURNAL,
            label="Экономический журнал",
            normalized_key="journal:economic",
            data_json={"title": "Экономический журнал"},
            status=ImportEntity.Status.LINKED_EXISTING,
            matched_existing_type="journal",
            matched_existing_id=journal.journal_id,
        )
        issue_entity = ImportEntity.objects.create(
            import_batch=batch,
            entity_type=ImportEntity.EntityType.JOURNAL_ISSUE,
            label="Экономический журнал — 1887 — № 8–9",
            normalized_key="journal:economic:1887:8-9",
            data_json={"journal_title": "Экономический журнал", "year": "1887", "issue_number": "8–9"},
            status=ImportEntity.Status.LINKED_EXISTING,
            matched_existing_type="journal_issue",
            matched_existing_id=issue.journal_issue_id,
        )
        article = ImportEntity.objects.create(
            import_batch=batch,
            entity_type=ImportEntity.EntityType.ARTICLE,
            label="К вопросу о бумажно-денежном обращении",
            normalized_key="article:mekk:money",
            data_json={"item_id": item.id, "title": "К вопросу о бумажно-денежном обращении", "raw_text": item.raw_text},
            status=ImportEntity.Status.UNRESOLVED,
        )
        ImportEntityRelation.objects.create(
            import_batch=batch,
            parent_entity=journal_entity,
            child_entity=issue_entity,
            relation_type="journal_has_issue",
        )
        ImportEntityRelation.objects.create(
            import_batch=batch,
            parent_entity=issue_entity,
            child_entity=article,
            relation_type="issue_has_article",
        )
        group = ImportGroup.objects.create(
            import_batch=batch,
            group_type=ImportGroup.GroupType.JOURNAL_ISSUE_GROUP,
            label="Экономический журнал — 1887 — № 8–9",
            root_entity=issue_entity,
            status=ImportGroup.Status.NEEDS_REVIEW,
        )
        batch.matches.create(
            entity=article,
            existing_type="work",
            existing_id=existing_work.work_id,
            score=0.85,
            match_reason_json={"title_similarity": 0.85},
        )
        return batch, group, item, article, journal_entity, issue_entity, existing_work

    def make_journal_article_inspect_fixture(self, with_placement=True, volume_number="№ 8–9; № 5–6"):
        author = Author.objects.create(author_id="author-inspect", display_name="Мец Н.", sort_name="Мец Н.")
        journal = Journal.objects.create(journal_id="journal-inspect", title="Экономический журнал")
        legacy_issue = JournalIssue.objects.create(
            journal_issue_id="journal-issue-inspect",
            journal=journal,
            year=1887,
            issue_number="8–9",
        )
        periodical = Periodical.objects.create(
            periodical_id="periodical-inspect",
            legacy_journal=journal,
            title="Экономический журнал",
        )
        target_issue = Issue.objects.create(
            issue_id="issue-inspect",
            legacy_journal_issue=legacy_issue,
            issue_type=Issue.IssueType.PERIODICAL_ISSUE,
            periodical=periodical,
            year=1887,
            issue_number="8–9",
        )
        work = Work.objects.create(
            work_id="work-inspect",
            source_number=142,
            source_sequence=142,
            source_section=self.section,
            language=self.language,
            work_type=Work.WorkType.ARTICLE,
            raw_author_string="Мец Н.",
            title="По поводу полемики о бумажных деньгах",
            volume_number=volume_number,
        )
        WorkAuthor.objects.create(work=work, author=author, sort_order=1)
        article = Article.objects.create(
            article_id="article-inspect",
            work=work,
            journal_issue=legacy_issue if with_placement else None,
        )
        source = Source.objects.create(
            source_id=work.work_id,
            legacy_work=work,
            source_number=work.source_number,
            source_sequence=work.source_sequence,
            source_type=Source.SourceType.ARTICLE,
            section=self.section,
            language=self.language,
            raw_author_string="Мец Н.",
            title=work.title,
            volume_number=volume_number,
        )
        placement = None
        if with_placement:
            placement = ArticlePlacement.objects.create(
                placement_id="article-placement-inspect",
                legacy_article=article,
                source=source,
                issue=target_issue,
            )
        return work, author, journal, legacy_issue, periodical, target_issue, article, source, placement

    def make_issue_to_collection_fixture(self):
        author = Author.objects.create(author_id="author-issue-collection", display_name="Иванов И.И.", sort_name="Иванов И.И.")
        journal = Journal.objects.create(journal_id="journal-issue-collection", title="Краеведение и музей")
        legacy_issue = JournalIssue.objects.create(
            journal_issue_id="journal-issue-to-collection",
            journal=journal,
            year=1992,
            publication_date="1992",
        )
        periodical = Periodical.objects.create(
            periodical_id="periodical-issue-collection",
            legacy_journal=journal,
            title="Краеведение и музей.— Петрозаводск, 1992",
        )
        target_issue = Issue.objects.create(
            issue_id="issue-to-collection",
            legacy_journal_issue=legacy_issue,
            issue_type=Issue.IssueType.PERIODICAL_ISSUE,
            periodical=periodical,
            year=1992,
            publication_date="1992",
        )
        work = Work.objects.create(
            work_id="work-issue-collection",
            source_number=48459,
            source_sequence=48459,
            source_section=self.section,
            language=self.language,
            work_type=Work.WorkType.ARTICLE,
            raw_author_string="Иванов И.И.",
            title="Из истории бон первых лет советской власти (фонды КГКМ)",
            inferred_year=1992,
        )
        WorkAuthor.objects.create(work=work, author=author, sort_order=1)
        article = Article.objects.create(
            article_id="article-issue-collection",
            work=work,
            journal_issue=legacy_issue,
            pages="129–135",
            pages_raw="129–135",
        )
        source = Source.objects.create(
            source_id=work.work_id,
            legacy_work=work,
            source_number=work.source_number,
            source_sequence=work.source_sequence,
            source_type=Source.SourceType.ARTICLE,
            section=self.section,
            language=self.language,
            raw_author_string=work.raw_author_string,
            title=work.title,
            inferred_year=1992,
        )
        placement = ArticlePlacement.objects.create(
            placement_id="article-placement-issue-collection",
            legacy_article=article,
            source=source,
            issue=target_issue,
            pages_raw="129–135",
        )
        return work, article, legacy_issue, target_issue, placement, periodical

    def make_journal_normalization_fixture(self, duplicate_target=False, ambiguous=False):
        source_journal = Journal.objects.create(journal_id="journal-normalize-wrong", title="Петербург– ский коллекционер")
        target_journal = Journal.objects.create(journal_id="journal-normalize-target", title="Петербургский коллекционер")
        source_periodical = Periodical.objects.create(periodical_id="periodical-normalize-wrong", legacy_journal=source_journal, title=source_journal.title)
        target_periodical = Periodical.objects.create(periodical_id="periodical-normalize-target", legacy_journal=target_journal, title=target_journal.title)
        source_issue = JournalIssue.objects.create(
            journal_issue_id="journal-issue-normalize-source",
            journal=source_journal,
            year=1912,
            issue_number="4",
            publication_details="СПб., 1912",
        )
        source_target_issue = Issue.objects.create(
            issue_id="issue-normalize-source",
            legacy_journal_issue=source_issue,
            issue_type=Issue.IssueType.PERIODICAL_ISSUE,
            periodical=source_periodical,
            year=1912,
            issue_number="4",
        )
        target_issue = None
        target_target_issue = None
        if duplicate_target or ambiguous:
            target_issue = JournalIssue.objects.create(
                journal_issue_id="journal-issue-normalize-target",
                journal=target_journal,
                year=1912,
                issue_number="4",
                publication_details="Пг., 1912",
            )
            target_target_issue = Issue.objects.create(
                issue_id="issue-normalize-target",
                legacy_journal_issue=target_issue,
                issue_type=Issue.IssueType.PERIODICAL_ISSUE,
                periodical=target_periodical,
                year=1912,
                issue_number="4",
            )
        if ambiguous:
            JournalIssue.objects.create(
                journal_issue_id="journal-issue-normalize-target-2",
                journal=target_journal,
                year=1912,
                issue_number="4",
            )
        author = Author.objects.create(author_id="author-normalize", display_name="Иванов И.", sort_name="Иванов И.")
        work = Work.objects.create(
            work_id="work-normalize-article",
            source_number=94001,
            source_sequence=94001,
            source_section=self.section,
            language=self.language,
            work_type=Work.WorkType.ARTICLE,
            title="Статья из ошибочного журнала",
            inferred_year=1912,
        )
        WorkAuthor.objects.create(work=work, author=author, sort_order=1)
        article = Article.objects.create(article_id="article-normalize", work=work, journal_issue=source_issue)
        source = Source.objects.create(
            source_id="source-normalize",
            legacy_work=work,
            source_number=94001,
            source_sequence=94001,
            source_type=Source.SourceType.ARTICLE,
            section=self.section,
            language=self.language,
            title=work.title,
        )
        placement = ArticlePlacement.objects.create(
            placement_id="placement-normalize",
            legacy_article=article,
            source=source,
            issue=source_target_issue,
        )
        return {
            "source_journal": source_journal,
            "target_journal": target_journal,
            "source_issue": source_issue,
            "source_target_issue": source_target_issue,
            "target_issue": target_issue,
            "target_target_issue": target_target_issue,
            "article": article,
            "placement": placement,
        }

    def test_journal_normalization_preview_marks_safe_issue_move(self):
        fixture = self.make_journal_normalization_fixture()

        plan = build_journal_normalization_plan(fixture["source_journal"].pk, fixture["target_journal"].pk)

        self.assertEqual(plan.totals["source_issues"], 1)
        self.assertEqual(plan.rows[0]["action"], "move")
        self.assertEqual(plan.rows[0]["article_count"], 1)
        self.assertEqual(plan.rows[0]["placement_count"], 1)
        self.assertTrue(plan.can_apply)

    def test_journal_normalization_apply_moves_non_conflicting_issue(self):
        fixture = self.make_journal_normalization_fixture()

        with patch("sources.journal_normalization.backup_sqlite_database", return_value=None), patch(
            "sources.journal_normalization.write_journal_normalization_report",
            return_value="reports/test-journal-normalization.json",
        ):
            result = apply_journal_normalization_plan(fixture["source_journal"].pk, fixture["target_journal"].pk)

        fixture["source_issue"].refresh_from_db()
        fixture["source_target_issue"].refresh_from_db()
        self.assertEqual(fixture["source_issue"].journal_id, fixture["target_journal"].journal_id)
        self.assertEqual(fixture["source_target_issue"].periodical.legacy_journal_id, fixture["target_journal"].journal_id)
        self.assertEqual(len(result["moved_issues"]), 1)
        self.assertEqual(len(result["merged_issues"]), 0)
        self.assertTrue(result["report_path"].endswith(".json"))
        self.assertTrue(result["backup_path"] == "" or result["backup_path"].endswith(".sqlite"))
        self.assertTrue(Journal.objects.filter(pk=fixture["source_journal"].pk).exists())

    def test_journal_normalization_apply_merges_duplicate_issue_links_and_placements(self):
        fixture = self.make_journal_normalization_fixture(duplicate_target=True)

        plan = build_journal_normalization_plan(fixture["source_journal"].pk, fixture["target_journal"].pk)
        self.assertEqual(plan.rows[0]["action"], "merge")
        self.assertTrue(plan.rows[0]["differences"])

        with patch("sources.journal_normalization.backup_sqlite_database", return_value=None), patch(
            "sources.journal_normalization.write_journal_normalization_report",
            return_value="reports/test-journal-normalization.json",
        ):
            result = apply_journal_normalization_plan(fixture["source_journal"].pk, fixture["target_journal"].pk)

        fixture["article"].refresh_from_db()
        fixture["placement"].refresh_from_db()
        self.assertEqual(fixture["article"].journal_issue_id, fixture["target_issue"].journal_issue_id)
        self.assertEqual(fixture["placement"].issue_id, fixture["target_target_issue"].issue_id)
        self.assertEqual(result["moved_articles"], 1)
        self.assertEqual(result["moved_placements"], 1)
        self.assertEqual(len(result["merged_issues"]), 1)
        self.assertTrue(result["merged_issues"][0]["field_differences"])
        self.assertTrue(Journal.objects.filter(pk=fixture["source_journal"].pk).exists())

    def test_journal_normalization_ambiguous_duplicate_issue_requires_review_and_is_skipped(self):
        fixture = self.make_journal_normalization_fixture(ambiguous=True)

        plan = build_journal_normalization_plan(fixture["source_journal"].pk, fixture["target_journal"].pk)
        self.assertEqual(plan.rows[0]["action"], "review")
        self.assertFalse(plan.can_apply)

        with patch("sources.journal_normalization.backup_sqlite_database", return_value=None), patch(
            "sources.journal_normalization.write_journal_normalization_report",
            return_value="reports/test-journal-normalization.json",
        ):
            result = apply_journal_normalization_plan(fixture["source_journal"].pk, fixture["target_journal"].pk)

        fixture["article"].refresh_from_db()
        fixture["placement"].refresh_from_db()
        self.assertEqual(fixture["article"].journal_issue_id, fixture["source_issue"].journal_issue_id)
        self.assertEqual(fixture["placement"].issue_id, fixture["source_target_issue"].issue_id)
        self.assertEqual(len(result["skipped_issues"]), 1)
        self.assertEqual(result["moved_articles"], 0)

    def test_journal_normalization_page_renders_preview_for_staff(self):
        user = get_user_model().objects.create_user("journal_normalizer", password="x", is_staff=True)
        fixture = self.make_journal_normalization_fixture(duplicate_target=True)

        self.client.force_login(user)
        response = self.client.get(
            "/journals/normalize/",
            {
                "source_journal": fixture["source_journal"].pk,
                "target_journal": fixture["target_journal"].pk,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Нормализация журналов")
        self.assertContains(response, "Ошибочный журнал")
        self.assertContains(response, "Правильный журнал")
        self.assertContains(response, "Выпуск будет слит с существующим")
        self.assertContains(response, "Статья из ошибочного журнала")

    def make_book_container_relation_fixture(self):
        author = Author.objects.create(author_id="author-container-rel", display_name="Голицын Ю.П.", sort_name="Голицын Ю.П.")
        child = Work.objects.create(
            work_id="work-048476",
            source_number=4785,
            source_sequence=4785,
            source_section=self.section,
            language=self.language,
            work_type=Work.WorkType.BOOK,
            raw_author_string="Голицын Ю.П.",
            title="Акционерные и паевые общества",
            publication_place="М.",
            publication_details="М., — 31\n___История России XIX-XX веков в облигациях, обязательствах и заемных письмах. Ч.3. Т.1. Ценные бумаги предприятий — 2017",
            inferred_year=2017,
        )
        WorkAuthor.objects.create(work=child, author=author, sort_order=1)
        container = Work.objects.create(
            work_id="work-044508",
            source_number=816,
            source_sequence=816,
            source_section=self.section,
            language=self.language,
            work_type=Work.WorkType.BOOK,
            title="История России XIX-XX веков в акциях, паях и облигациях",
            part_number="Ч.3. Т.1",
            publication_place="М.",
            publisher="Банк Центрокредит",
            extent="248 с.",
            inferred_year=2017,
        )
        Work.objects.create(
            work_id="work-044506",
            source_number=814,
            source_sequence=814,
            source_section=self.section,
            language=self.language,
            work_type=Work.WorkType.BOOK,
            title="История России XIX-XX веков в акциях, паях и облигациях",
            part_number="Ч.1",
            inferred_year=2012,
        )
        Work.objects.create(
            work_id="work-044507",
            source_number=815,
            source_sequence=815,
            source_section=self.section,
            language=self.language,
            work_type=Work.WorkType.BOOK,
            title="История России XIX-XX веков в акциях, паях и облигациях",
            part_number="Ч.2",
            inferred_year=2014,
        )
        Work.objects.create(
            work_id="work-044509",
            source_number=817,
            source_sequence=817,
            source_section=self.section,
            language=self.language,
            work_type=Work.WorkType.BOOK,
            title="История России XIX-XX веков в акциях, паях и облигациях",
            part_number="Ч.3. Т.2",
            inferred_year=2018,
        )
        return child, container, author

    def make_missing_parent_relation_fixture(self):
        author = Author.objects.create(author_id="author-missing-parent", display_name="Авчухов А.Ю.", sort_name="Авчухов А.Ю.")
        child = Work.objects.create(
            work_id="work-048411",
            source_number=4720,
            source_sequence=4720,
            source_section=self.section,
            language=self.language,
            work_type=Work.WorkType.BOOK,
            raw_author_string="Авчухов А.Ю.",
            title="Боны Волгоградской области",
            publication_details="___Энциклопедия Волгоградской области.— Волгоград, 2008 — 2008",
            inferred_year=2008,
        )
        WorkAuthor.objects.create(work=child, author=author, sort_order=1)
        return child, author

    def make_article_in_book_inspect_fixture(self):
        child, author = self.make_missing_parent_relation_fixture()
        parent = Work.objects.create(
            work_id="work-parent-book",
            source_number=9001,
            source_sequence=9001,
            source_section=self.section,
            language=self.language,
            work_type=Work.WorkType.BOOK,
            title="Энциклопедия Волгоградской области",
            publication_place="Волгоград",
            publication_date="2008",
            inferred_year=2008,
        )
        Article.objects.create(article_id="article-in-book", work=child, container_work=parent)
        child.work_type = Work.WorkType.ARTICLE
        child.save(update_fields=["work_type"])
        return child, parent, author

    def make_wrong_journal_issue_fixture(self):
        author = Author.objects.create(author_id="author-wrong-journal", display_name="Рогов Г.И.", sort_name="Рогов Г.И.")
        wrong_journal = Journal.objects.create(journal_id="journal-wrong-issue", title="Разыскания.— Вып. 7")
        wrong_issue = JournalIssue.objects.create(
            journal_issue_id="journal-issue-wrong",
            journal=wrong_journal,
            year=2007,
            issue_number="",
        )
        wrong_periodical = Periodical.objects.create(
            periodical_id="periodical-wrong-issue",
            legacy_journal=wrong_journal,
            title="Разыскания.— Вып. 7",
        )
        wrong_target_issue = Issue.objects.create(
            issue_id="issue-wrong",
            legacy_journal_issue=wrong_issue,
            issue_type=Issue.IssueType.PERIODICAL_ISSUE,
            periodical=wrong_periodical,
            year=2007,
            issue_number="",
        )
        work = Work.objects.create(
            work_id="work-048666",
            source_number=4775,
            source_sequence=4775,
            source_section=self.section,
            language=self.language,
            work_type=Work.WorkType.ARTICLE,
            raw_author_string="Рогов Г.И.",
            title="Денежные знаки Мариинского и Томского уездов",
            publication_place="Кемерово",
        )
        WorkAuthor.objects.create(work=work, author=author, sort_order=1)
        article = Article.objects.create(article_id="article-wrong-journal", work=work, journal_issue=wrong_issue)
        source = Source.objects.create(
            source_id=work.work_id,
            legacy_work=work,
            source_number=work.source_number,
            source_sequence=work.source_sequence,
            source_type=Source.SourceType.ARTICLE,
            section=self.section,
            language=self.language,
            raw_author_string="Рогов Г.И.",
            title=work.title,
        )
        placement = ArticlePlacement.objects.create(
            placement_id="placement-wrong-journal",
            legacy_article=article,
            source=source,
            issue=wrong_target_issue,
        )
        context_work = Work.objects.create(
            work_id="work-049299",
            source_number=4900,
            source_sequence=4900,
            source_section=self.section,
            language=self.language,
            work_type=Work.WorkType.CONTAINER,
            is_container=True,
            title="Разыскания. Историко-краеведческий альманах",
            publication_place="Кемерово",
            publication_date="2004",
            inferred_year=2004,
        )
        return work, article, wrong_journal, wrong_issue, wrong_periodical, wrong_target_issue, placement, context_work, author

    def test_book_import_creates_item_author_and_book_entity(self):
        batch = ImportBatch.objects.create(
            title="Book test",
            raw_input="Иванов И.И. Бумажные деньги Сибири. Новосибирск, 1998. 120 с.",
        )

        parse_import_batch(batch)

        self.assertEqual(batch.items.count(), 1)
        self.assertEqual(batch.items.first().detected_type, ImportItem.DetectedType.BOOK)
        self.assertEqual(batch.entities.filter(entity_type=ImportEntity.EntityType.AUTHOR).count(), 1)
        self.assertEqual(batch.entities.filter(entity_type=ImportEntity.EntityType.BOOK).count(), 1)

    def test_import_cleanup_deletes_only_non_applied_batches(self):
        user = get_user_model().objects.create_user("cleanup_imports", password="x", is_staff=True)
        Work.objects.create(
            work_id="work-cleanup-safe",
            source_number=70001,
            source_sequence=70001,
            source_section=self.section,
            language=self.language,
            title="Cleanup safety work",
        )
        draft = ImportBatch.objects.create(title="Draft import", status=ImportBatch.Status.DRAFT)
        review = ImportBatch.objects.create(title="Review import", status=ImportBatch.Status.REVIEW_REQUIRED)
        applied = ImportBatch.objects.create(title="Applied import", status=ImportBatch.Status.APPLIED)

        self.client.force_login(user)
        response = self.client.post("/imports/cleanup/", follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(ImportBatch.objects.filter(pk=draft.pk).exists())
        self.assertFalse(ImportBatch.objects.filter(pk=review.pk).exists())
        self.assertTrue(ImportBatch.objects.filter(pk=applied.pk).exists())
        self.assertTrue(Work.objects.filter(work_id="work-cleanup-safe").exists())
        self.assertContains(response, "Библиографические записи не изменялись")

    def test_import_cleanup_applied_deletes_only_applied_history(self):
        user = get_user_model().objects.create_user("cleanup_applied_imports", password="x", is_staff=True)
        Work.objects.create(
            work_id="work-cleanup-applied-safe",
            source_number=70002,
            source_sequence=70002,
            source_section=self.section,
            language=self.language,
            title="Applied cleanup safety work",
        )
        active = ImportBatch.objects.create(title="Active import", status=ImportBatch.Status.REVIEW_REQUIRED)
        applied = ImportBatch.objects.create(title="Applied import", status=ImportBatch.Status.APPLIED)
        ImportApplyLog.objects.create(import_batch=applied, applied_by=user)

        self.client.force_login(user)
        response = self.client.post("/imports/cleanup/applied/", follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(ImportBatch.objects.filter(pk=active.pk).exists())
        self.assertFalse(ImportBatch.objects.filter(pk=applied.pk).exists())
        self.assertFalse(ImportApplyLog.objects.filter(import_batch_id=applied.pk).exists())
        self.assertTrue(Work.objects.filter(work_id="work-cleanup-applied-safe").exists())
        self.assertContains(response, "Удалена история применённых импортов: 1")
        self.assertContains(response, "Библиографические записи не изменялись")

    def test_import_list_separates_active_and_applied_batches(self):
        user = get_user_model().objects.create_user("import_list_archive", password="x", is_staff=True)
        ImportBatch.objects.create(title="Current import", status=ImportBatch.Status.REVIEW_REQUIRED)
        ImportBatch.objects.create(title="Archived applied import", status=ImportBatch.Status.APPLIED)

        self.client.force_login(user)
        response = self.client.get("/imports/")
        text = response.content.decode("utf-8")

        self.assertContains(response, "Текущие импорты")
        self.assertContains(response, "История применённых импортов")
        self.assertLess(text.index("Текущие импорты"), text.index("История применённых импортов"))
        self.assertLess(text.index("Current import"), text.index("История применённых импортов"))
        self.assertGreater(text.index("Archived applied import"), text.index("История применённых импортов"))
        self.assertContains(response, "Очистить историю применённых импортов")

    def test_journal_articles_are_deduplicated_into_one_issue_group(self):
        batch = ImportBatch.objects.create(
            title="Journal articles",
            raw_input=(
                "Иванов И.И. Металлические боны Урала // Новый бонист. 2024. № 3. С. 12-14.\n"
                "Петров П.П. Каталогизация бон // Новый бонист. 2024. № 3. С. 15-20.\n"
                "Сидоров С.С. Новые находки // Новый бонист. 2024. № 3. С. 21-25."
            ),
        )

        parse_import_batch(batch)

        self.assertEqual(batch.items.count(), 3)
        self.assertEqual(batch.entities.filter(entity_type=ImportEntity.EntityType.JOURNAL).count(), 1)
        self.assertEqual(batch.entities.filter(entity_type=ImportEntity.EntityType.JOURNAL_ISSUE).count(), 1)
        self.assertEqual(batch.entities.filter(entity_type=ImportEntity.EntityType.ARTICLE).count(), 3)
        self.assertEqual(batch.groups.filter(group_type=ImportGroup.GroupType.JOURNAL_ISSUE_GROUP).count(), 1)
        issue = batch.entities.get(entity_type=ImportEntity.EntityType.JOURNAL_ISSUE)
        self.assertEqual(
            ImportEntityRelation.objects.filter(
                import_batch=batch,
                parent_entity=issue,
                relation_type="issue_has_article",
            ).count(),
            3,
        )

    def make_existing_economic_journal_article(self):
        author = Author.objects.create(author_id="author-economic-existing", display_name="Мец Н.", sort_name="Мец Н.")
        work = Work.objects.create(
            work_id="work-economic-existing",
            source_number=92001,
            source_sequence=92001,
            source_section=self.section,
            language=self.language,
            work_type=Work.WorkType.ARTICLE,
            title="По поводу полемики о бумажных деньгах",
            inferred_year=1887,
        )
        WorkAuthor.objects.create(work=work, author=author, sort_order=1)
        journal = Journal.objects.create(journal_id="journal-economic-existing", title="Экономический журнал")
        issue = JournalIssue.objects.create(
            journal_issue_id="journal-issue-economic-1887",
            journal=journal,
            year=1887,
            issue_number="8–9",
        )
        Article.objects.create(article_id="article-economic-existing", work=work, journal_issue=issue)
        return author, work, journal, issue

    def test_journal_article_existing_work_still_builds_container_graph(self):
        _, existing_work, _, _ = self.make_existing_economic_journal_article()
        batch = ImportBatch.objects.create(
            title="Economic journal container first",
            raw_input=(
                "Мец Н. По поводу полемики о бумажных деньгах // Экономический журнал. — 1887. — № 8–9.\n"
                "Мец Н. По поводу полемики о бумажных деньгах // Экономический журнал. — 1888. — № 5–6."
            ),
        )

        parse_import_batch(batch)

        self.assertEqual(batch.items.count(), 2)
        self.assertEqual(batch.entities.filter(entity_type=ImportEntity.EntityType.AUTHOR).count(), 1)
        self.assertEqual(batch.entities.filter(entity_type=ImportEntity.EntityType.JOURNAL).count(), 1)
        self.assertEqual(batch.entities.filter(entity_type=ImportEntity.EntityType.JOURNAL_ISSUE).count(), 2)
        self.assertEqual(batch.entities.filter(entity_type=ImportEntity.EntityType.ARTICLE).count(), 2)
        for item in batch.items.all():
            article = batch.entities.get(entity_type=ImportEntity.EntityType.ARTICLE, data_json__item_id=item.id)
            self.assertTrue(
                ImportEntityRelation.objects.filter(import_batch=batch, child_entity=article, relation_type="author_of").exists()
            )
            issue_relation = ImportEntityRelation.objects.get(import_batch=batch, child_entity=article, relation_type="issue_has_article")
            self.assertTrue(
                ImportEntityRelation.objects.filter(import_batch=batch, child_entity=issue_relation.parent_entity, relation_type="journal_has_issue").exists()
            )
        self.assertTrue(batch.matches.filter(existing_type="work", existing_id=existing_work.work_id).exists())

    def test_journal_article_different_issue_is_context_candidate_not_found_item(self):
        _, existing_work, journal, _ = self.make_existing_economic_journal_article()
        batch = ImportBatch.objects.create(
            title="Different issue candidate",
            raw_input="Мец Н. По поводу полемики о бумажных деньгах // Экономический журнал. — 1888. — № 5–6.",
        )

        parse_import_batch(batch)

        item = batch.items.get()
        journal_entity = batch.entities.get(entity_type=ImportEntity.EntityType.JOURNAL)
        issue_entity = batch.entities.get(entity_type=ImportEntity.EntityType.JOURNAL_ISSUE)
        article_entity = batch.entities.get(entity_type=ImportEntity.EntityType.ARTICLE)
        match = article_entity.matches.get(existing_type="work", existing_id=existing_work.work_id)

        self.assertEqual(item.status, ImportItem.Status.PARSED)
        self.assertEqual(item.matched_existing_id, "")
        self.assertEqual(journal_entity.status, ImportEntity.Status.LINKED_EXISTING)
        self.assertEqual(journal_entity.matched_existing_id, journal.journal_id)
        self.assertEqual(issue_entity.status, ImportEntity.Status.WILL_CREATE)
        self.assertEqual(issue_entity.data_json["year"], "1888")
        self.assertEqual(issue_entity.data_json["issue_number"], "5-6")
        self.assertEqual(article_entity.status, ImportEntity.Status.UNRESOLVED)
        self.assertEqual(match.match_reason_json["import_issue_label"], "Экономический журнал, 1888, № 5-6")
        self.assertEqual(match.match_reason_json["existing_issue_label"], "Экономический журнал, 1887, № 8–9")
        self.assertFalse(match.match_reason_json["same_issue"])

    def test_journal_article_issue_context_renders_on_item_and_group_pages(self):
        user = get_user_model().objects.create_user("issue_context_reviewer", password="x", is_staff=True)
        self.make_existing_economic_journal_article()
        batch = ImportBatch.objects.create(
            title="Issue context UI",
            raw_input="Мец Н. По поводу полемики о бумажных деньгах // Экономический журнал. — 1888. — № 5–6.",
        )
        parse_import_batch(batch)
        item = batch.items.get()
        group = batch.groups.get(group_type=ImportGroup.GroupType.JOURNAL_ISSUE_GROUP)

        self.client.force_login(user)
        item_response = self.client.get(f"/imports/{batch.pk}/items/{item.pk}/")
        group_response = self.client.get(f"/imports/{batch.pk}/groups/{group.pk}/")

        for response in [item_response, group_response]:
            self.assertContains(response, "Найдена похожая статья, но в другом выпуске")
            self.assertContains(response, "Текущая строка описывает публикацию в выпуске")
            self.assertContains(response, "«Экономический журнал», № 5-6, 1888 год")
            self.assertContains(response, "«Экономический журнал», № 8–9, 1887 год")
            self.assertContains(response, "Создать отдельную статью в выпуске «Экономический журнал», № 5-6, 1888 год.")

    def test_linking_import_issue_refreshes_dependent_article_match_context(self):
        user = get_user_model().objects.create_user("issue_context_decision", password="x", is_staff=True)
        _author, existing_work, _journal, existing_issue = self.make_existing_economic_journal_article()
        batch = ImportBatch.objects.create(
            title="Refresh article issue context",
            raw_input="Мец Н. По поводу полемики о бумажных деньгах // Экономический журнал. — 1888. — № 5–6.",
        )
        parse_import_batch(batch)
        issue_entity = batch.entities.get(entity_type=ImportEntity.EntityType.JOURNAL_ISSUE)
        article_entity = batch.entities.get(entity_type=ImportEntity.EntityType.ARTICLE)
        match = article_entity.matches.get(existing_type="work", existing_id=existing_work.work_id)
        self.assertFalse(match.match_reason_json["same_issue"])

        apply_entity_decision(
            issue_entity,
            ImportDecision.DecisionType.LINK_EXISTING,
            target_type="journal_issue",
            target_id=existing_issue.journal_issue_id,
            user=user,
        )

        match.refresh_from_db()
        article_entity.refresh_from_db()
        self.assertTrue(match.match_reason_json["same_issue"])
        self.assertEqual(match.match_reason_json["import_issue_label"], "Экономический журнал, 1887, № 8–9")
        self.assertEqual(match.match_reason_json["existing_issue_label"], "Экономический журнал, 1887, № 8–9")
        self.assertEqual(article_entity.status, ImportEntity.Status.UNRESOLVED)

    def test_linking_journal_auto_links_exact_issue_candidate_and_refreshes_articles(self):
        user = get_user_model().objects.create_user("journal_issue_auto_link", password="x", is_staff=True)
        author = Author.objects.create(author_id="author-vestnik-auto", display_name="Норман Б.", sort_name="Норман Б.")
        journal = Journal.objects.create(journal_id="journal-vestnik-auto", title="Вестник Азии")
        existing_issue = JournalIssue.objects.create(
            journal_issue_id="journal-issue-vestnik-auto",
            journal=journal,
            year=1911,
            issue_number="11",
        )
        existing_work = Work.objects.create(
            work_id="work-vestnik-auto",
            source_number=93001,
            source_sequence=93001,
            source_section=self.section,
            language=self.language,
            work_type=Work.WorkType.ARTICLE,
            title="Китайское денежное обращение",
            inferred_year=1911,
        )
        WorkAuthor.objects.create(work=existing_work, author=author, sort_order=1)
        Article.objects.create(article_id="article-vestnik-auto", work=existing_work, journal_issue=existing_issue)
        batch = ImportBatch.objects.create(
            title="Auto-link issue after journal decision",
            raw_input="Норман Б. Денежное обращение Китая // Вестник Азии. - Харбин, — 1911. — № 11.",
        )
        parse_import_batch(batch)
        journal_entity = batch.entities.get(entity_type=ImportEntity.EntityType.JOURNAL)
        issue_entity = batch.entities.get(entity_type=ImportEntity.EntityType.JOURNAL_ISSUE)
        article_entity = batch.entities.get(entity_type=ImportEntity.EntityType.ARTICLE)
        self.assertEqual(journal_entity.status, ImportEntity.Status.UNRESOLVED)
        self.assertEqual(issue_entity.status, ImportEntity.Status.UNRESOLVED)

        apply_entity_decision(
            journal_entity,
            ImportDecision.DecisionType.LINK_EXISTING,
            target_type="journal",
            target_id=journal.journal_id,
            user=user,
        )

        issue_entity.refresh_from_db()
        article_entity.refresh_from_db()
        match = article_entity.matches.get(existing_type="work", existing_id=existing_work.work_id)
        group = batch.groups.get(group_type=ImportGroup.GroupType.JOURNAL_ISSUE_GROUP)

        self.assertEqual(issue_entity.status, ImportEntity.Status.LINKED_EXISTING)
        self.assertEqual(issue_entity.matched_existing_type, "journal_issue")
        self.assertEqual(issue_entity.matched_existing_id, existing_issue.journal_issue_id)
        self.assertTrue(match.match_reason_json["same_issue"])
        self.assertEqual(match.match_reason_json["import_issue_label"], "Вестник Азии, 1911, № 11")
        self.assertEqual(match.match_reason_json["existing_issue_label"], "Вестник Азии, 1911, № 11")
        self.assertEqual(article_entity.status, ImportEntity.Status.UNRESOLVED)

        self.client.force_login(user)
        review_response = self.client.get(f"/imports/{batch.pk}/review/")
        group_response = self.client.get(f"/imports/{batch.pk}/groups/{group.pk}/")

        self.assertContains(review_response, "Готов")
        self.assertContains(review_response, "Проверить статьи")
        self.assertNotContains(review_response, "Проверить журнал и выпуск")
        self.assertContains(group_response, "уже связано с существующей записью: Вестник Азии — 1911 — № 11")
        self.assertNotContains(group_response, "Найдена похожая статья, но в другом выпуске")
        self.assertContains(group_response, "Статья уже есть в этом выпуске")

    def test_exact_issue_auto_link_skips_ambiguous_duplicate_issues(self):
        user = get_user_model().objects.create_user("journal_issue_ambiguous", password="x", is_staff=True)
        journal = Journal.objects.create(journal_id="journal-ambiguous-auto", title="Вестник Азии")
        JournalIssue.objects.create(
            journal_issue_id="journal-issue-ambiguous-one",
            journal=journal,
            year=1911,
            issue_number="11",
        )
        JournalIssue.objects.create(
            journal_issue_id="journal-issue-ambiguous-two",
            journal=journal,
            year=1911,
            issue_number="11",
        )
        batch = ImportBatch.objects.create(
            title="Ambiguous issue auto-link",
            raw_input="Норман Б. Денежное обращение Китая // Вестник Азии. — 1911. — № 11.",
        )
        parse_import_batch(batch)
        journal_entity = batch.entities.get(entity_type=ImportEntity.EntityType.JOURNAL)
        issue_entity = batch.entities.get(entity_type=ImportEntity.EntityType.JOURNAL_ISSUE)

        if journal_entity.status != ImportEntity.Status.LINKED_EXISTING:
            apply_entity_decision(
                journal_entity,
                ImportDecision.DecisionType.LINK_EXISTING,
                target_type="journal",
                target_id=journal.journal_id,
                user=user,
            )
            issue_entity.refresh_from_db()

        self.assertEqual(issue_entity.status, ImportEntity.Status.UNRESOLVED)
        self.assertEqual(issue_entity.matched_existing_id, "")
        self.assertEqual(issue_entity.matches.filter(existing_type="journal_issue").count(), 2)

    def test_linking_import_issue_removes_different_issue_warning_from_ui(self):
        user = get_user_model().objects.create_user("issue_context_ui_refresh", password="x", is_staff=True)
        _author, existing_work, _journal, existing_issue = self.make_existing_economic_journal_article()
        batch = ImportBatch.objects.create(
            title="Refreshed issue context UI",
            raw_input="Мец Н. По поводу полемики о бумажных деньгах // Экономический журнал. — 1888. — № 5–6.",
        )
        parse_import_batch(batch)
        issue_entity = batch.entities.get(entity_type=ImportEntity.EntityType.JOURNAL_ISSUE)
        article_entity = batch.entities.get(entity_type=ImportEntity.EntityType.ARTICLE)
        apply_entity_decision(
            issue_entity,
            ImportDecision.DecisionType.LINK_EXISTING,
            target_type="journal_issue",
            target_id=existing_issue.journal_issue_id,
            user=user,
        )
        group = batch.groups.get(group_type=ImportGroup.GroupType.JOURNAL_ISSUE_GROUP)
        item = batch.items.get()

        self.client.force_login(user)
        item_response = self.client.get(f"/imports/{batch.pk}/items/{item.pk}/")
        group_response = self.client.get(f"/imports/{batch.pk}/groups/{group.pk}/")

        for response in [item_response, group_response]:
            self.assertNotContains(response, "Найдена похожая статья, но в другом выпуске")
            self.assertNotContains(response, "Текущая строка описывает публикацию в выпуске")
            self.assertContains(response, "«Экономический журнал», № 8–9, 1887 год")

        self.assertEqual(
            batch.decisions.filter(entity=article_entity, decision_type=ImportDecision.DecisionType.LINK_EXISTING).count(),
            0,
        )

    def test_refresh_keeps_explicit_article_link_decision(self):
        user = get_user_model().objects.create_user("issue_context_keep_article", password="x", is_staff=True)
        _author, existing_work, _journal, existing_issue = self.make_existing_economic_journal_article()
        batch = ImportBatch.objects.create(
            title="Keep explicit article decision",
            raw_input="Мец Н. По поводу полемики о бумажных деньгах // Экономический журнал. — 1888. — № 5–6.",
        )
        parse_import_batch(batch)
        issue_entity = batch.entities.get(entity_type=ImportEntity.EntityType.JOURNAL_ISSUE)
        article_entity = batch.entities.get(entity_type=ImportEntity.EntityType.ARTICLE)
        apply_entity_decision(
            article_entity,
            ImportDecision.DecisionType.LINK_EXISTING,
            target_type="work",
            target_id=existing_work.work_id,
            user=user,
        )

        apply_entity_decision(
            issue_entity,
            ImportDecision.DecisionType.LINK_EXISTING,
            target_type="journal_issue",
            target_id=existing_issue.journal_issue_id,
            user=user,
        )

        article_entity.refresh_from_db()
        match = article_entity.matches.get(existing_type="work", existing_id=existing_work.work_id)
        self.assertEqual(article_entity.status, ImportEntity.Status.LINKED_EXISTING)
        self.assertEqual(article_entity.matched_existing_id, existing_work.work_id)
        self.assertTrue(match.match_reason_json["same_issue"])

    def test_same_issue_article_match_renders_as_already_found(self):
        user = get_user_model().objects.create_user("same_issue_ui", password="x", is_staff=True)
        batch, group, item, article, _journal_entity, _issue_entity, existing_work = self.make_weak_article_group_import()
        issue = JournalIssue.objects.get(journal_issue_id="journal-issue-weak-article")
        Article.objects.create(article_id="article-weak-article", work=existing_work, journal_issue=issue)

        self.client.force_login(user)
        response = self.client.get(f"/imports/{batch.pk}/items/{item.pk}/")

        self.assertContains(response, "Статья уже есть в этом выпуске")
        self.assertContains(response, "Название совпадает")
        self.assertContains(response, "Автор совпадает")
        self.assertContains(response, "Журнал и выпуск совпадают")
        self.assertContains(response, "Техническая оценка совпадения: 85%")
        self.assertContains(response, ">Пропустить без изменений</button>")
        self.assertNotContains(response, "Да, связать с найденной записью")
        self.assertNotContains(response, "<p class=\"muted\">Совпадение: 85%</p>")

        group_response = self.client.get(f"/imports/{batch.pk}/groups/{group.pk}/")
        self.assertContains(group_response, "Статья уже есть в этом выпуске")
        self.assertContains(group_response, ">Пропустить без изменений</button>")

    def test_observed_multi_author_strings_are_split(self):
        self.assertEqual(split_author_list("Бугров А., Калмыков С."), ["Бугров А.", "Калмыков С."])
        self.assertEqual(split_author_list("Величко А., Дуров В., Герич Л."), ["Величко А.", "Дуров В.", "Герич Л."])
        self.assertEqual(split_author_list("Бугров А.В.; Калмыков С."), ["Бугров А.В.", "Калмыков С."])
        self.assertEqual(split_author_list("Величко А.; Герич Л.; Дуров В."), ["Величко А.", "Герич Л.", "Дуров В."])

    def test_same_issue_exact_article_match_auto_resolves_and_plan_is_ready(self):
        user = get_user_model().objects.create_user("same_issue_auto", password="x", is_staff=True)
        author_one = Author.objects.create(author_id="author-auto-bugrov", display_name="Бугров А.", sort_name="Бугров А.")
        author_two = Author.objects.create(author_id="author-auto-kalmykov", display_name="Калмыков С.", sort_name="Калмыков С.")
        journal = Journal.objects.create(journal_id="journal-auto-num", title="Нумизматика")
        issue = JournalIssue.objects.create(journal_issue_id="journal-issue-auto-num", journal=journal, year=2017, issue_number="41")
        work = Work.objects.create(
            work_id="work-auto-num",
            source_number=92001,
            source_sequence=92001,
            source_section=self.section,
            language=self.language,
            work_type=Work.WorkType.ARTICLE,
            title="Золотые бумажные деньги. Очерки по истории золотомонетного стандарта",
            inferred_year=2017,
        )
        WorkAuthor.objects.create(work=work, author=author_one, sort_order=1)
        WorkAuthor.objects.create(work=work, author=author_two, sort_order=2)
        Article.objects.create(article_id="article-auto-num", work=work, journal_issue=issue)
        Source.objects.create(
            source_id="source-auto-num",
            legacy_work=work,
            source_number=92001,
            language=self.language,
            title=work.title,
            source_type=Source.SourceType.ARTICLE,
            data_source="",
        )
        batch = ImportBatch.objects.create(
            title="Auto same issue import",
            source_name="Баранов-2021",
            raw_input="Бугров А., Калмыков С. Золотые бумажные деньги. Очерки по истории золотомонетного стандарта // Нумизматика. — 2017. — № 41. — С. 109.",
        )

        parse_import_batch(batch)
        item = batch.items.get()
        article_entity = batch.entities.get(entity_type=ImportEntity.EntityType.ARTICLE)
        plan = build_import_plan(batch)

        self.assertEqual(item.status, ImportItem.Status.FOUND_EXISTING_NO_CHANGES)
        self.assertEqual(article_entity.status, ImportEntity.Status.LINKED_EXISTING)
        self.assertTrue(plan["can_apply"])
        self.assertFalse(plan["problems"])
        self.assertEqual(batch.entities.filter(entity_type=ImportEntity.EntityType.AUTHOR, label="Бугров А., Калмыков С.").count(), 0)
        self.assertEqual(plan["preview"]["create_rows"], [])
        self.assertEqual(len(plan["preview"]["already_existing_rows"]), 1)

        self.client.force_login(user)
        response = self.client.get(f"/imports/{batch.pk}/review/")
        self.assertContains(response, "Уже есть в базе")
        self.assertNotContains(response, "Найдена похожая запись, подтвердите совпадение")
        response = self.client.get(f"/imports/{batch.pk}/plan/")
        self.assertContains(response, "Уже есть в базе")
        self.assertContains(response, "Будут дополнены пустые технические поля")
        self.assertNotContains(response, "<td><strong>Бугров А., Калмыков С.</strong></td>")

    def test_auto_resolved_same_issue_apply_fills_safe_source_fields_and_creates_no_authors(self):
        author = Author.objects.create(author_id="author-auto-apply", display_name="Галанов В.И.", sort_name="Галанов В.И.")
        journal = Journal.objects.create(journal_id="journal-auto-apply", title="Нумизматика")
        issue = JournalIssue.objects.create(journal_issue_id="journal-issue-auto-apply", journal=journal, year=2003, issue_number="2")
        work = Work.objects.create(
            work_id="work-auto-apply",
            source_number=92002,
            source_sequence=92002,
            source_section=self.section,
            language=self.language,
            work_type=Work.WorkType.ARTICLE,
            title="Кожаная бона из г. Дерпт",
            inferred_year=2003,
        )
        WorkAuthor.objects.create(work=work, author=author, sort_order=1)
        article = Article.objects.create(article_id="article-auto-apply", work=work, journal_issue=issue)
        source = Source.objects.create(
            source_id="source-auto-apply",
            legacy_work=work,
            source_number=92002,
            language=self.language,
            title=work.title,
            source_type=Source.SourceType.ARTICLE,
            data_source="editor",
        )
        batch = ImportBatch.objects.create(
            title="Auto same issue apply",
            source_name="Баранов-2021",
            raw_input="Галанов В.И. Кожаная бона из г. Дерпт // Нумизматика. — 2003. — № 2. — С. 24–28.",
        )
        parse_import_batch(batch)
        author_count = Author.objects.count()

        result = apply_import_batch(batch)

        self.assertTrue(result["applied"])
        self.assertEqual(Author.objects.count(), author_count)
        work.refresh_from_db()
        article.refresh_from_db()
        source.refresh_from_db()
        self.assertEqual(work.article_pages, "24-28")
        self.assertEqual(article.pages, "24-28")
        self.assertEqual(article.pages_raw, "24-28")
        self.assertIn("Баранов-2021", source.data_source)
        self.assertIn("Нумизматика", source.raw_publication_details)
        log = ImportApplyLog.objects.get(import_batch=batch)
        self.assertTrue(any(row.get("id") == work.work_id for row in log.updated_entities_json))

    def test_review_page_shows_container_section_before_items_and_ready_container_action(self):
        user = get_user_model().objects.create_user("review_order_ready_container", password="x", is_staff=True)
        batch, _group, _item, _article, _journal_entity, _issue_entity, existing_work = self.make_weak_article_group_import()
        issue = JournalIssue.objects.get(journal_issue_id="journal-issue-weak-article")
        Article.objects.create(article_id="article-weak-article", work=existing_work, journal_issue=issue)

        self.client.force_login(user)
        response = self.client.get(f"/imports/{batch.pk}/review/")
        text = response.content.decode("utf-8")

        self.assertContains(response, "Журналы и сборники для проверки")
        self.assertContains(response, "Все обработанные строки")
        self.assertLess(text.index("Журналы и сборники для проверки"), text.index("Все обработанные строки"))
        self.assertContains(response, "<th>Контейнер</th>", html=True)
        self.assertContains(response, "<th>Статьи</th>", html=True)
        self.assertContains(response, "Готов")
        self.assertContains(response, "1 требует проверки")
        self.assertContains(response, "Проверить статьи")
        self.assertNotContains(response, "Открыть статьи")
        self.assertNotContains(response, "Контейнеры для проверки")

    def test_different_issue_item_detail_uses_unambiguous_actions(self):
        user = get_user_model().objects.create_user("different_issue_item", password="x", is_staff=True)
        self.make_existing_economic_journal_article()
        batch = ImportBatch.objects.create(
            title="Different issue item UI",
            raw_input="Мец Н. По поводу полемики о бумажных деньгах // Экономический журнал. — 1888. — № 5–6.",
        )
        parse_import_batch(batch)
        item = batch.items.get()

        self.client.force_login(user)
        response = self.client.get(f"/imports/{batch.pk}/items/{item.pk}/")

        self.assertContains(response, "Найдена похожая статья, но в другом выпуске")
        self.assertContains(response, "В базе уже есть статья с тем же названием")
        self.assertContains(response, "Текущая строка описывает публикацию в выпуске")
        self.assertContains(response, "Создать отдельную статью в выпуске «Экономический журнал», № 5-6, 1888 год.")
        self.assertContains(response, "Найдена статья в журнале «Экономический журнал», № 8–9, 1887 год.")
        self.assertContains(response, "Найденная статья:")
        self.assertContains(response, ">Создать</button>")
        self.assertContains(response, ">Связать</button>")
        self.assertNotContains(response, "Нет, создать новую запись")
        self.assertNotContains(response, "Да, связать с найденной записью")
        text = response.content.decode("utf-8")
        self.assertLess(
            text.index("Создать отдельную статью в выпуске «Экономический журнал», № 5-6, 1888 год."),
            text.index("Найдена статья в журнале «Экономический журнал», № 8–9, 1887 год."),
        )

    def test_different_issue_group_detail_uses_unambiguous_actions(self):
        user = get_user_model().objects.create_user("different_issue_group", password="x", is_staff=True)
        self.make_existing_economic_journal_article()
        batch = ImportBatch.objects.create(
            title="Different issue group UI",
            raw_input="Мец Н. По поводу полемики о бумажных деньгах // Экономический журнал. — 1888. — № 5–6.",
        )
        parse_import_batch(batch)
        group = batch.groups.get(group_type=ImportGroup.GroupType.JOURNAL_ISSUE_GROUP)

        self.client.force_login(user)
        response = self.client.get(f"/imports/{batch.pk}/groups/{group.pk}/")

        self.assertContains(response, "Найдена похожая статья, но в другом выпуске")
        self.assertContains(response, "Создать отдельную статью в выпуске «Экономический журнал», № 5-6, 1888 год.")
        self.assertContains(response, "Найдена статья в журнале «Экономический журнал», № 8–9, 1887 год.")
        self.assertContains(response, ">Создать</button>")
        self.assertContains(response, ">Связать</button>")
        self.assertNotContains(response, "Нет, создать новую статью")
        self.assertNotContains(response, "Да, связать с найденной статьёй")
        text = response.content.decode("utf-8")
        self.assertLess(
            text.index("Создать отдельную статью в выпуске «Экономический журнал», № 5-6, 1888 год."),
            text.index("Найдена статья в журнале «Экономический журнал», № 8–9, 1887 год."),
        )

    def test_group_detail_shows_container_review_before_articles(self):
        user = get_user_model().objects.create_user("container_first_group", password="x", is_staff=True)
        self.make_existing_economic_journal_article()
        batch = ImportBatch.objects.create(
            title="Container first group",
            raw_input="Мец Н. По поводу полемики о бумажных деньгах // Экономический журнал. — 1888. — № 5–6.",
        )
        parse_import_batch(batch)
        group = batch.groups.get(group_type=ImportGroup.GroupType.JOURNAL_ISSUE_GROUP)

        self.client.force_login(user)
        response = self.client.get(f"/imports/{batch.pk}/groups/{group.pk}/")
        text = response.content.decode("utf-8")

        self.assertContains(response, "Что проверяем в этой группе")
        self.assertContains(response, "Статьи внутри этого выпуска")
        self.assertContains(response, "Создать новый выпуск «Экономический журнал», № 5-6, 1888 год.")
        self.assertLess(text.index("Что проверяем в этой группе"), text.index("Статьи внутри этого выпуска"))

    def test_review_page_distinguishes_item_and_container_actions(self):
        user = get_user_model().objects.create_user("review_levels", password="x", is_staff=True)
        self.make_existing_economic_journal_article()
        batch = ImportBatch.objects.create(
            title="Review level labels",
            raw_input="Мец Н. По поводу полемики о бумажных деньгах // Экономический журнал. — 1888. — № 5–6.",
        )
        parse_import_batch(batch)

        self.client.force_login(user)
        response = self.client.get(f"/imports/{batch.pk}/review/")

        self.assertContains(response, "Все обработанные строки")
        self.assertContains(response, "Здесь проверяются отдельные строки импорта")
        self.assertContains(response, "Проверить статью")
        self.assertContains(response, "Журналы и сборники для проверки")
        self.assertContains(response, "Сначала проверьте контейнеры")
        self.assertContains(response, "Проверить статьи")
        self.assertNotContains(response, "Открыть статьи")
        self.assertNotContains(response, "Группы для новых записей")

    def test_different_issue_primary_create_action_keeps_entity_decision_semantics(self):
        user = get_user_model().objects.create_user("different_issue_create", password="x", is_staff=True)
        self.make_existing_economic_journal_article()
        batch = ImportBatch.objects.create(
            title="Different issue create action",
            raw_input="Мец Н. По поводу полемики о бумажных деньгах // Экономический журнал. — 1888. — № 5–6.",
        )
        parse_import_batch(batch)
        article = batch.entities.get(entity_type=ImportEntity.EntityType.ARTICLE)

        self.client.force_login(user)
        self.client.post(
            f"/imports/{batch.pk}/entities/{article.pk}/decision/",
            {"decision_type": "create", "next": f"/imports/{batch.pk}/items/{batch.items.get().pk}/"},
        )
        article.refresh_from_db()

        self.assertEqual(article.status, ImportEntity.Status.WILL_CREATE)
        self.assertEqual(batch.decisions.filter(entity=article, decision_type=ImportDecision.DecisionType.CREATE).count(), 1)

    def test_different_issue_alternative_link_action_keeps_entity_decision_semantics(self):
        user = get_user_model().objects.create_user("different_issue_link", password="x", is_staff=True)
        _author, existing_work, _journal, _issue = self.make_existing_economic_journal_article()
        batch = ImportBatch.objects.create(
            title="Different issue link action",
            raw_input="Мец Н. По поводу полемики о бумажных деньгах // Экономический журнал. — 1888. — № 5–6.",
        )
        parse_import_batch(batch)
        article = batch.entities.get(entity_type=ImportEntity.EntityType.ARTICLE)

        self.client.force_login(user)
        self.client.post(
            f"/imports/{batch.pk}/entities/{article.pk}/decision/",
            {
                "decision_type": "link_existing",
                "target_type": "work",
                "target_id": existing_work.work_id,
                "next": f"/imports/{batch.pk}/items/{batch.items.get().pk}/",
            },
        )
        article.refresh_from_db()

        self.assertEqual(article.status, ImportEntity.Status.LINKED_EXISTING)
        self.assertEqual(article.matched_existing_id, existing_work.work_id)
        self.assertEqual(batch.decisions.filter(entity=article, decision_type=ImportDecision.DecisionType.LINK_EXISTING).count(), 1)

    def test_possible_duplicate_book_match_is_created(self):
        author = Author.objects.create(author_id="author-000001", display_name="Иванов И.И.", sort_name="Иванов И.И.")
        work = Work.objects.create(
            work_id="work-000001",
            source_number=1,
            source_sequence=1,
            source_section=self.section,
            language=self.language,
            work_type=Work.WorkType.BOOK,
            title="Бумажные деньги Сибири",
            publication_date="1998",
            inferred_year=1998,
        )
        WorkAuthor.objects.create(work=work, author=author, sort_order=1)
        batch = ImportBatch.objects.create(
            title="Duplicate test",
            raw_input="Иванов И.И. Бумажные деньги Сибири. Новосибирск, 1998. 120 с.",
        )

        parse_import_batch(batch)

        item = batch.items.get()
        self.assertEqual(item.status, ImportItem.Status.FOUND_EXISTING_WITH_DIFFERENCES)
        self.assertEqual(item.matched_existing_id, work.work_id)
        self.assertFalse(batch.entities.filter(entity_type=ImportEntity.EntityType.BOOK).exists())

    def test_book_title_with_colon_matches_existing_main_title(self):
        author = Author.objects.create(author_id="author-colon-title", display_name="Гурьев А.", sort_name="Гурьев А.")
        work = Work.objects.create(
            work_id="work-colon-title",
            source_number=91010,
            source_sequence=91010,
            source_section=self.section,
            language=self.language,
            work_type=Work.WorkType.BOOK,
            title="Денежное обращение в России в XIX столетии",
            publication_place="СПб.",
            publisher="Тип. В.Ф. Киршбаума",
            publication_date="1903",
            inferred_year=1903,
        )
        WorkAuthor.objects.create(work=work, author=author, sort_order=1)
        batch = ImportBatch.objects.create(
            title="Colon title match",
            raw_input="Гурьев А. Денежное обращение в России в XIX столетии: Исторический очерк. — СПб.: Тип. В.Ф. Киршбаума, 1903.",
        )

        parse_import_batch(batch)

        item = batch.items.get()
        self.assertEqual(item.status, ImportItem.Status.FOUND_EXISTING_NO_CHANGES)
        self.assertEqual(item.matched_existing_id, work.work_id)
        self.assertEqual(item.parsed_data_json["title"], "Денежное обращение в России в XIX столетии")
        self.assertEqual(item.parsed_data_json["title_remainder"], "Исторический очерк")
        self.assertFalse(batch.entities.filter(entity_type=ImportEntity.EntityType.BOOK).exists())

    def test_long_colon_subtitle_book_matches_existing_main_title(self):
        author = Author.objects.create(author_id="author-degio", display_name="Дегио В.", sort_name="Дегио В.")
        work = Work.objects.create(
            work_id="work-degio",
            source_number=91011,
            source_sequence=91011,
            source_section=self.section,
            language=self.language,
            work_type=Work.WorkType.BOOK,
            title="Русские ценные бумаги",
            publication_place="СПб., М.",
            publisher="Т-во М.О. Вольф",
            publication_date="1885",
            inferred_year=1885,
            extent="477 с.",
        )
        WorkAuthor.objects.create(work=work, author=author, sort_order=1)
        batch = ImportBatch.objects.create(
            title="Long subtitle match",
            raw_input="Дегио В. Русские ценные бумаги: Сб. сведений о всех главнейших фондах, закладных листах, акциях и облигациях, котирующихся на русских биржах. — СПб., М.: т-во М.О. Вольф, 1885. — 477 с.",
        )

        parse_import_batch(batch)

        item = batch.items.get()
        self.assertEqual(item.status, ImportItem.Status.FOUND_EXISTING_NO_CHANGES)
        self.assertEqual(item.matched_existing_id, work.work_id)
        self.assertEqual(item.parsed_data_json["title"], "Русские ценные бумаги")
        self.assertTrue(item.parsed_data_json["title_remainder"].startswith("Сб. сведений"))
        self.assertFalse(batch.entities.filter(entity_type=ImportEntity.EntityType.BOOK).exists())

    def test_extent_normalization_treats_page_suffix_as_same(self):
        author = Author.objects.create(author_id="author-extent", display_name="Гурьев А.", sort_name="Гурьев А.")
        work = Work.objects.create(
            work_id="work-extent",
            source_number=91012,
            source_sequence=91012,
            source_section=self.section,
            language=self.language,
            work_type=Work.WorkType.BOOK,
            title="Материалы для библиографии русской экономической литературы по денежному вопросу",
            publication_place="СПб.",
            publisher="Типография В. Киршбаума",
            publication_date="1896",
            inferred_year=1896,
            extent="20",
        )
        WorkAuthor.objects.create(work=work, author=author, sort_order=1)
        batch = ImportBatch.objects.create(
            title="Extent normalization",
            raw_input="Гурьев А. Материалы для библиографии русской экономической литературы по денежному вопросу. — СПб.: Типография В. Киршбаума, 1896. — 20 с. — 165х250 мм.",
        )

        parse_import_batch(batch)

        item = batch.items.get()
        self.assertEqual(item.status, ImportItem.Status.FOUND_EXISTING_NO_CHANGES)
        self.assertEqual(item.matched_existing_id, work.work_id)
        extent_row = [row for row in item.comparison_json["fields"] if row["label"] == "Страницы"][0]
        self.assertEqual(extent_row["status"], "same")
        size_row = [row for row in item.comparison_json["fields"] if row["label"] == "Размер"][0]
        self.assertEqual(size_row["source"], "165х250 мм")
        self.assertEqual(size_row["status"], "source_extra")

    def test_dimensions_comparison_reads_existing_work_dimensions(self):
        author = Author.objects.create(author_id="author-dimensions", display_name="Алямкин А.В.", sort_name="Алямкин А.В.")
        work = Work.objects.create(
            work_id="work-dimensions",
            source_number=91030,
            source_sequence=91030,
            source_section=self.section,
            language=self.language,
            work_type=Work.WorkType.BOOK,
            title="Государственные законные платёжные средства без ограничений",
            publication_date="2019",
            inferred_year=2019,
            dimensions="250х310 мм",
        )
        WorkAuthor.objects.create(work=work, author=author, sort_order=1)

        comparison = compare_work_to_parsed_data(
            work,
            {"authors": ["Алямкин А.В."], "title": work.title, "year": "2019", "dimensions": "250х310 мм"},
        )
        size_row = [row for row in comparison["fields"] if row["label"] == "Размер"][0]

        self.assertEqual(size_row["existing"], "250х310 мм")
        self.assertEqual(size_row["source"], "250х310 мм")
        self.assertEqual(size_row["status"], "same")

    def test_book_parser_extracts_dash_edition_then_imprint(self):
        parsed = parse_record(
            "Боровиков С.В. Государственные Кредитные Билеты Российской Империи 1898–1912. "
            "Управляющие и кассиры. Альбом-каталог. — 2-е изд. доп. и уточн. — "
            "СПб.: Издательство ДЕАН, 2017. — 240 с.: ил. — 450 экз. — 220х300 мм."
        )

        self.assertEqual(parsed.data["title"], "Государственные Кредитные Билеты Российской Империи 1898-1912")
        self.assertEqual(parsed.data["title_remainder"], "Управляющие и кассиры. Альбом-каталог")
        self.assertEqual(parsed.data["edition_statement"], "2-е изд. доп. и уточн.")
        self.assertEqual(parsed.data["publication_place"], "СПб")
        self.assertEqual(parsed.data["publisher"], "Издательство ДЕАН")
        self.assertEqual(parsed.data["year"], "2017")
        self.assertEqual(parsed.data["dimensions"], "220х300 мм")

    def test_book_parser_extracts_edition_before_responsibility_and_keeps_date_range_title(self):
        parsed = parse_record(
            "Бумажные денежные знаки России. Государственные выпуски 1769–2014 г. Каталог. "
            "6-е изд. / Под общ. ред. В.Б. Загорского. — СПб.: Стандарт-Коллекция, 2015. "
            "— 60 с. — 2000 экз. — 165х235 мм."
        )

        self.assertEqual(parsed.data["title"], "Бумажные денежные знаки России. Государственные выпуски 1769-2014 г")
        self.assertEqual(parsed.data["title_remainder"], "Каталог")
        self.assertEqual(parsed.data["edition_statement"], "6-е изд.")
        self.assertEqual(parsed.data["responsibility_statement"], "Под общ. ред. В.Б. Загорского")
        self.assertEqual(parsed.data["publication_place"], "СПб")
        self.assertEqual(parsed.data["publisher"], "Стандарт-Коллекция")
        self.assertEqual(parsed.data["year"], "2015")

    def test_responsibility_contributor_roles_are_extracted_without_losing_text(self):
        parsed = parse_record(
            "Бумажные денежные знаки России. Каталог. 6-е изд. / Под общ. ред. В.Б. Загорского. "
            "— СПб.: Стандарт-Коллекция, 2015."
        )

        self.assertEqual(parsed.data["responsibility_statement"], "Под общ. ред. В.Б. Загорского")
        self.assertEqual(
            parsed.data["responsibility_contributors"],
            [{"name": "В.Б. Загорского", "role": "responsible_editor", "role_label": "ответственный редактор"}],
        )
        self.assertEqual(normalize_contributor_role("сост."), "compiler")
        self.assertEqual(contributor_role_label_ru("translator"), "переводчик")

    def test_brace_note_is_parsed_and_compared_to_existing_note(self):
        author = Author.objects.create(author_id="author-brace-note", display_name="Гольдман А.", sort_name="Гольдман А.")
        work = Work.objects.create(
            work_id="work-brace-note",
            source_number=91013,
            source_sequence=91013,
            source_section=self.section,
            language=self.language,
            work_type=Work.WorkType.BOOK,
            title="Русские бумажные деньги",
            publication_place="СПб.",
            publication_date="1866",
            inferred_year=1866,
            notes="О попытке реформы в 1862–1863 гг.",
        )
        WorkAuthor.objects.create(work=work, author=author, sort_order=1)
        batch = ImportBatch.objects.create(
            title="Brace note",
            raw_input="Гольдман А. Русские бумажные деньги. — СПб., 1866.; 2-е изд. 1867. {О попытке реформы в 1862–1863 гг.}",
        )

        parse_import_batch(batch)

        item = batch.items.get()
        self.assertEqual(item.status, ImportItem.Status.FOUND_EXISTING_NO_CHANGES)
        self.assertEqual(item.parsed_data_json["notes"], "О попытке реформы в 1862-1863 гг.")
        note_row = [row for row in item.comparison_json["fields"] if row["label"] == "Примечания"][0]
        self.assertEqual(note_row["status"], "same")

    def test_sentence_title_remainder_book_matches_existing_full_title(self):
        author = Author.objects.create(author_id="author-gering", display_name="Геринг С.", sort_name="Геринг С.")
        work = Work.objects.create(
            work_id="work-gering",
            source_number=91015,
            source_sequence=91015,
            source_section=self.section,
            language=self.language,
            work_type=Work.WorkType.BOOK,
            title="Рубль. История, причины колебания и средство упрочнения бумажного рубля",
            publication_date="1893",
            inferred_year=1893,
        )
        WorkAuthor.objects.create(work=work, author=author, sort_order=1)
        batch = ImportBatch.objects.create(
            title="Sentence title remainder",
            raw_input="Геринг С. Рубль. История, причины колебания и средство упрочнения бумажного рубля. 1893.",
        )

        parse_import_batch(batch)

        item = batch.items.get()
        self.assertEqual(item.status, ImportItem.Status.FOUND_EXISTING_NO_CHANGES)
        self.assertEqual(item.matched_existing_id, work.work_id)
        self.assertEqual(item.parsed_data_json["title"], "Рубль")
        self.assertEqual(item.parsed_data_json["title_remainder"], "История, причины колебания и средство упрочнения бумажного рубля")
        self.assertFalse(item.parsed_data_json["publication_place"])
        self.assertFalse(item.parsed_data_json["publisher"])
        self.assertFalse(batch.entities.filter(entity_type=ImportEntity.EntityType.BOOK).exists())

    def test_combined_title_subtitle_and_responsibility_match_existing_work(self):
        work = Work.objects.create(
            work_id="work-state-bank",
            source_number=91016,
            source_sequence=91016,
            source_section=self.section,
            language=self.language,
            work_type=Work.WorkType.BOOK,
            title="Государственный банк",
            subtitle="Краткий очерк деятельности за 1860–1910 годы",
            responsibility_statement="Под ред. Директора Государственного банка Е.Н. Сланского",
            publication_place="СПб.",
            publication_date="1910",
            inferred_year=1910,
            extent="4, 143 с.",
        )
        batch = ImportBatch.objects.create(
            title="Combined title",
            raw_input="Государственный банк. Краткий очерк деятельности за 1860–1910 годы / Под ред. Директора Государственного банка Е.Н. Сланского. — СПб., 1910. — 4, 143 с.: ил.",
        )

        parse_import_batch(batch)

        item = batch.items.get()
        self.assertEqual(item.status, ImportItem.Status.FOUND_EXISTING_NO_CHANGES)
        self.assertEqual(item.matched_existing_id, work.work_id)
        self.assertEqual(item.parsed_data_json["title"], "Государственный банк")
        self.assertEqual(item.parsed_data_json["title_remainder"], "Краткий очерк деятельности за 1860-1910 годы")
        self.assertEqual(item.parsed_data_json["responsibility_statement"], "Под ред. Директора Государственного банка Е.Н. Сланского")
        self.assertFalse(batch.entities.filter(entity_type=ImportEntity.EntityType.BOOK).exists())

    def test_missing_in_source_field_does_not_make_found_record_blocking(self):
        author = Author.objects.create(author_id="author-missing-source", display_name="Гольдман А.", sort_name="Гольдман А.")
        work = Work.objects.create(
            work_id="work-missing-source",
            source_number=91014,
            source_sequence=91014,
            source_section=self.section,
            language=self.language,
            work_type=Work.WorkType.BOOK,
            title="Русские бумажные деньги",
            publication_place="СПб.",
            publication_date="1866",
            inferred_year=1866,
            notes="О попытке реформы в 1862–1863 гг.",
        )
        WorkAuthor.objects.create(work=work, author=author, sort_order=1)
        batch = ImportBatch.objects.create(
            title="Missing source field",
            raw_input="Гольдман А. Русские бумажные деньги. — СПб., 1866.",
        )

        parse_import_batch(batch)

        item = batch.items.get()
        self.assertEqual(item.status, ImportItem.Status.FOUND_EXISTING_NO_CHANGES)
        self.assertIn("В источнике меньше сведений", item.comparison_json["summary"])

    def test_issue_number_normalization(self):
        self.assertEqual(normalize_issue_number("№ 5–6"), "5-6")
        self.assertEqual(normalize_issue_number("N 5-6"), "5-6")

    def test_existing_book_stays_visible_without_new_entity(self):
        author = Author.objects.create(author_id="author-000002", display_name="Ламанский Е.И.", sort_name="Ламанский Е.И.")
        work = Work.objects.create(
            work_id="work-000002",
            source_number=2,
            source_sequence=2,
            source_section=self.section,
            language=self.language,
            work_type=Work.WorkType.BOOK,
            title="Статистический обзор операций государственных кредитных установлений с 1817 г. по настоящее время",
            publication_place="СПб.",
            publication_date="1854",
            inferred_year=1854,
            extent="447 с",
        )
        WorkAuthor.objects.create(work=work, author=author, sort_order=1)
        batch = ImportBatch.objects.create(
            title="Existing book",
            raw_input="Ламанский Е.И. Статистический обзор операций государственных кредитных установлений с 1817 г. по настоящее время. — СПб., 1854. — 447 с.",
        )

        parse_import_batch(batch)

        item = batch.items.get()
        self.assertEqual(item.status, ImportItem.Status.FOUND_EXISTING_NO_CHANGES)
        self.assertEqual(item.matched_existing_id, work.work_id)
        self.assertFalse(batch.entities.filter(entity_type=ImportEntity.EntityType.BOOK).exists())

    def test_slash_record_finds_core_work_before_creating_parent(self):
        author = Author.objects.create(author_id="author-000003", display_name="Ламанский Е.И.", sort_name="Ламанский Е.И.")
        work = Work.objects.create(
            work_id="work-000003",
            source_number=3,
            source_sequence=3,
            source_section=self.section,
            language=self.language,
            work_type=Work.WorkType.BOOK,
            title="Исторический очерк денежного обращения России с 1650 по 1817 г",
            publication_date="",
            inferred_year=None,
        )
        WorkAuthor.objects.create(work=work, author=author, sort_order=1)
        batch = ImportBatch.objects.create(
            title="Structural conflict",
            raw_input="Ламанский Е.И. Исторический очерк денежного обращения России с 1650 по 1817 г. // Сб. стат. сведений о России. — СПб., 1854.",
        )

        parse_import_batch(batch)

        item = batch.items.get()
        self.assertEqual(item.status, ImportItem.Status.STRUCTURAL_CONFLICT)
        self.assertEqual(item.matched_existing_id, work.work_id)
        self.assertEqual(item.parsed_data_json["parent_title"], "Сб. стат. сведений о России")
        self.assertEqual(item.parsed_data_json["parent_place"], "СПб.")
        self.assertEqual(item.parsed_data_json["parent_year"], "1854")
        self.assertFalse(batch.entities.filter(entity_type=ImportEntity.EntityType.COLLECTION).exists())
        self.assertFalse(batch.entities.filter(entity_type=ImportEntity.EntityType.ARTICLE).exists())

    def test_parent_title_does_not_include_place_and_year(self):
        host = parse_host_details("Сб. стат. сведений о России. — СПб., 1854.")

        self.assertEqual(host["container_title"], "Сб. стат. сведений о России")
        self.assertEqual(host["publication_place"], "СПб.")
        self.assertEqual(host["year"], "1854")

    def test_slash_record_without_existing_work_creates_article_and_collection(self):
        batch = ImportBatch.objects.create(
            title="New article in collection",
            raw_input="Автор А.А. Новая неизвестная статья // Новый неизвестный сборник. — М., 1900.",
        )

        parse_import_batch(batch)

        self.assertEqual(batch.items.count(), 1)
        self.assertEqual(batch.entities.filter(entity_type=ImportEntity.EntityType.COLLECTION).count(), 1)
        self.assertEqual(batch.entities.filter(entity_type=ImportEntity.EntityType.ARTICLE).count(), 1)
        collection = batch.entities.get(entity_type=ImportEntity.EntityType.COLLECTION)
        self.assertEqual(collection.label, "Новый неизвестный сборник")
        self.assertTrue(
            ImportEntityRelation.objects.filter(
                import_batch=batch,
                parent_entity=collection,
                relation_type="article_in_collection",
            ).exists()
        )

    def test_existing_work_description_is_bibliographic(self):
        author = Author.objects.create(author_id="author-000004", display_name="Ламанский Е.И.", sort_name="Ламанский Е.И.")
        work = Work.objects.create(
            work_id="work-000004",
            source_number=4,
            source_sequence=4,
            source_section=self.section,
            language=self.language,
            work_type=Work.WorkType.BOOK,
            title="Китайские ассигнации. Экономический указатель",
            publication_date="1857",
            inferred_year=1857,
        )
        WorkAuthor.objects.create(work=work, author=author, sort_order=1)

        self.assertEqual(
            describe_existing_entity("work", work.work_id),
            "Ламанский Е.И. — Китайские ассигнации. Экономический указатель — 1857",
        )

    def test_existing_journal_issue_description_is_bibliographic(self):
        journal = Journal.objects.create(journal_id="journal-000001", title="Московский бонист")
        issue = JournalIssue.objects.create(
            journal_issue_id="journal-issue-000001",
            journal=journal,
            year=1984,
            issue_number="5-6",
        )

        self.assertEqual(
            describe_existing_entity("journal_issue", issue.journal_issue_id),
            "Московский бонист — 1984 — № 5-6",
        )

    def test_existing_entity_description_fallback_does_not_crash(self):
        self.assertEqual(describe_existing_entity("work", "work-missing"), "work work-missing")
        self.assertEqual(describe_existing_entity("author", "author-missing"), "author author-missing")
        self.assertEqual(describe_existing_entity("unknown", "id-1"), "unknown id-1")

    def test_existing_author_description_is_display_name(self):
        author = Author.objects.create(author_id="author-000006", display_name="Ламанский Е.И.", sort_name="Ламанский Е.И.")

        self.assertEqual(describe_existing_entity("author", author.author_id), "Ламанский Е.И.")

    def test_import_review_renders_existing_label_instead_of_raw_work_id(self):
        user = get_user_model().objects.create_user("reviewer", password="x", is_staff=True)
        author = Author.objects.create(author_id="author-000005", display_name="Ламанский Е.И.", sort_name="Ламанский Е.И.")
        work = Work.objects.create(
            work_id="work-000005",
            source_number=5,
            source_sequence=5,
            source_section=self.section,
            language=self.language,
            work_type=Work.WorkType.BOOK,
            title="Китайские ассигнации. Экономический указатель",
            publication_place="СПб.",
            publisher="Тип. К. Метцига",
            publication_date="1857",
            inferred_year=1857,
        )
        WorkAuthor.objects.create(work=work, author=author, sort_order=1)
        batch = ImportBatch.objects.create(
            title="Review labels",
            raw_input="Ламанский Е.И. Китайские ассигнации. Экономический указатель. — СПб.: Тип. К. Метцига, 1857.",
        )
        parse_import_batch(batch)

        self.client.force_login(user)
        response = self.client.get(f"/imports/{batch.pk}/review/")

        self.assertContains(response, "Ламанский Е.И. — Китайские ассигнации. Экономический указатель — 1857")
        self.assertNotContains(response, f"work {work.work_id}")

    def test_import_item_renders_author_match_label_instead_of_raw_author_id(self):
        user = get_user_model().objects.create_user("author_reviewer", password="x", is_staff=True)
        author = Author.objects.create(author_id="author-000007", display_name="Ламанский Е.И.", sort_name="Ламанский Е.И.")
        batch = ImportBatch.objects.create(
            title="Author match labels",
            raw_input="Ламанский Е.И. Совершенно новая работа. — СПб., 1901.",
        )
        parse_import_batch(batch)
        item = batch.items.get()

        self.client.force_login(user)
        response = self.client.get(f"/imports/{batch.pk}/items/{item.pk}/")

        self.assertContains(response, "Ламанский Е.И.")
        self.assertNotContains(response, f"author {author.author_id}")

    def test_item_skip_decision_removes_readiness_problem_for_found_existing_no_changes(self):
        author = Author.objects.create(author_id="author-000008", display_name="Ламанский Е.И.", sort_name="Ламанский Е.И.")
        work = Work.objects.create(
            work_id="work-000008",
            source_number=8,
            source_sequence=8,
            source_section=self.section,
            language=self.language,
            work_type=Work.WorkType.BOOK,
            title="Китайские ассигнации. Экономический указатель",
            publication_place="СПб.",
            publisher="Тип. К. Метцига",
            publication_date="1857",
            inferred_year=1857,
        )
        WorkAuthor.objects.create(work=work, author=author, sort_order=1)
        batch = ImportBatch.objects.create(
            title="Skip existing",
            raw_input="Ламанский Е.И. Китайские ассигнации. Экономический указатель. — СПб.: Тип. К. Метцига, 1857.",
        )
        parse_import_batch(batch)
        item = batch.items.get()

        apply_item_decision(item, ImportDecision.DecisionType.SKIP)
        item.refresh_from_db()
        plan = build_import_plan(batch)

        self.assertEqual(item.status, ImportItem.Status.READY)
        self.assertEqual(batch.decisions.get(item=item).decision_type, ImportDecision.DecisionType.SKIP)
        self.assertEqual(plan["item_decisions"][ImportDecision.DecisionType.SKIP], 1)
        self.assertFalse(readiness_problems(batch))

    def test_unresolved_found_existing_with_differences_blocks_plan(self):
        author = Author.objects.create(author_id="author-000009", display_name="Иванов И.И.", sort_name="Иванов И.И.")
        work = Work.objects.create(
            work_id="work-000009",
            source_number=9,
            source_sequence=9,
            source_section=self.section,
            language=self.language,
            work_type=Work.WorkType.BOOK,
            title="Бумажные деньги Сибири",
            publication_date="1998",
            inferred_year=1998,
        )
        WorkAuthor.objects.create(work=work, author=author, sort_order=1)
        batch = ImportBatch.objects.create(
            title="Existing with differences",
            raw_input="Иванов И.И. Бумажные деньги Сибири. Новосибирск, 1998. 120 с.",
        )

        parse_import_batch(batch)
        item = batch.items.get()
        problems = readiness_problems(batch)

        self.assertEqual(item.status, ImportItem.Status.FOUND_EXISTING_WITH_DIFFERENCES)
        self.assertTrue(any("возможные дополнения" in problem for problem in problems))

    def test_update_existing_item_decision_fills_empty_work_fields_and_logs_backup(self):
        author = Author.objects.create(author_id="author-000010", display_name="Иванов И.И.", sort_name="Иванов И.И.")
        work = Work.objects.create(
            work_id="work-000010",
            source_number=10,
            source_sequence=10,
            source_section=self.section,
            language=self.language,
            work_type=Work.WorkType.BOOK,
            title="Бумажные деньги Сибири",
            publication_date="1998",
            inferred_year=1998,
        )
        WorkAuthor.objects.create(work=work, author=author, sort_order=1)
        batch = ImportBatch.objects.create(
            title="Update existing",
            raw_input="Иванов И.И. Бумажные деньги Сибири. Новосибирск, 1998. 120 с.",
        )
        parse_import_batch(batch)
        item = batch.items.get()

        apply_item_decision(item, ImportDecision.DecisionType.UPDATE_EXISTING)
        item.refresh_from_db()
        plan = build_import_plan(batch)
        with patch("sources.import_workflow.backup_sqlite_database", return_value="/tmp/editor.before-import-apply.sqlite") as backup_mock:
            result = apply_import_batch(batch)

        self.assertEqual(item.status, ImportItem.Status.READY)
        self.assertEqual(batch.decisions.get(item=item).decision_type, ImportDecision.DecisionType.UPDATE_EXISTING)
        self.assertEqual(plan["item_decisions"][ImportDecision.DecisionType.UPDATE_EXISTING], 1)
        self.assertTrue(plan["can_apply"])
        self.assertTrue(result["applied"])
        self.assertTrue(result["backup_path"])
        backup_mock.assert_called_once_with("before-import-apply")
        work.refresh_from_db()
        item.refresh_from_db()
        log = batch.apply_logs.get()
        self.assertEqual(work.publication_place, "Новосибирск")
        self.assertEqual(work.extent, "120 с")
        self.assertEqual(item.status, ImportItem.Status.APPLIED)
        self.assertTrue(any(field["field"] == "publication_place" for field in log.updated_entities_json[0]["updated_fields"]))

    def test_structural_conflict_update_decision_is_saved_without_unresolved_conflict_problem(self):
        author = Author.objects.create(author_id="author-000011", display_name="Ламанский Е.И.", sort_name="Ламанский Е.И.")
        work = Work.objects.create(
            work_id="work-000011",
            source_number=11,
            source_sequence=11,
            source_section=self.section,
            language=self.language,
            work_type=Work.WorkType.BOOK,
            title="Исторический очерк денежного обращения России с 1650 по 1817 г",
        )
        WorkAuthor.objects.create(work=work, author=author, sort_order=1)
        batch = ImportBatch.objects.create(
            title="Structural conflict decision",
            raw_input="Ламанский Е.И. Исторический очерк денежного обращения России с 1650 по 1817 г. // Сб. стат. сведений о России. — СПб., 1854.",
        )
        parse_import_batch(batch)
        item = batch.items.get()

        apply_item_decision(item, ImportDecision.DecisionType.UPDATE_EXISTING)
        item.refresh_from_db()
        problems = readiness_problems(batch)

        self.assertEqual(item.status, ImportItem.Status.READY)
        self.assertEqual(batch.decisions.get(item=item).decision_type, ImportDecision.DecisionType.UPDATE_EXISTING)
        self.assertFalse(any("структуру описания" in problem for problem in problems))

    def test_update_existing_does_not_overwrite_nonempty_work_fields(self):
        work = Work.objects.create(
            work_id="work-000012",
            source_number=12,
            source_sequence=12,
            source_section=self.section,
            language=self.language,
            work_type=Work.WorkType.BOOK,
            title="Неперезаписываемая запись",
            publication_place="СПб.",
        )
        batch = ImportBatch.objects.create(title="No overwrite", raw_input="No overwrite")
        item = ImportItem.objects.create(
            import_batch=batch,
            raw_text="No overwrite",
            detected_type=ImportItem.DetectedType.BOOK,
            status=ImportItem.Status.READY,
            matched_existing_type="work",
            matched_existing_id=work.work_id,
            comparison_json={"fields": [{"label": "Место издания", "existing": "", "source": "М.", "status": "new_in_source"}]},
        )

        apply_item_decision(item, ImportDecision.DecisionType.UPDATE_EXISTING)
        result = apply_import_batch(batch)
        work.refresh_from_db()
        log = batch.apply_logs.get()

        self.assertTrue(result["applied"])
        self.assertEqual(work.publication_place, "СПб.")
        self.assertEqual(log.updated_entities_json[0]["status"], "no_op")

    def test_update_existing_does_not_apply_different_fields(self):
        work = Work.objects.create(
            work_id="work-000013",
            source_number=13,
            source_sequence=13,
            source_section=self.section,
            language=self.language,
            work_type=Work.WorkType.BOOK,
            title="Different fields",
        )
        batch = ImportBatch.objects.create(title="Different ignored", raw_input="Different ignored")
        item = ImportItem.objects.create(
            import_batch=batch,
            raw_text="Different ignored",
            detected_type=ImportItem.DetectedType.BOOK,
            status=ImportItem.Status.READY,
            matched_existing_type="work",
            matched_existing_id=work.work_id,
            comparison_json={"fields": [{"label": "Место издания", "existing": "СПб.", "source": "М.", "status": "different"}]},
        )

        apply_item_decision(item, ImportDecision.DecisionType.UPDATE_EXISTING)
        apply_import_batch(batch)
        work.refresh_from_db()

        self.assertEqual(work.publication_place, "")

    def test_update_existing_applies_only_selected_new_in_source_fields(self):
        work = Work.objects.create(
            work_id="work-000017",
            source_number=17,
            source_sequence=17,
            source_section=self.section,
            language=self.language,
            work_type=Work.WorkType.BOOK,
            title="Selected fields",
        )
        batch = ImportBatch.objects.create(title="Selected fields", raw_input="Selected fields")
        item = ImportItem.objects.create(
            import_batch=batch,
            raw_text="Selected fields",
            detected_type=ImportItem.DetectedType.BOOK,
            status=ImportItem.Status.READY,
            matched_existing_type="work",
            matched_existing_id=work.work_id,
            comparison_json={
                "fields": [
                    {"label": "Место издания", "existing": "", "source": "СПб.", "status": "new_in_source"},
                    {"label": "Издательство / типография", "existing": "", "source": "Тип. К. Метцига", "status": "new_in_source"},
                ]
            },
        )

        apply_item_decision(item, ImportDecision.DecisionType.UPDATE_EXISTING, payload={"selected_fields": ["Место издания"]})
        apply_import_batch(batch)
        work.refresh_from_db()
        log = batch.apply_logs.get()

        self.assertEqual(work.publication_place, "СПб.")
        self.assertEqual(work.publisher, "")
        self.assertEqual(len(log.updated_entities_json[0]["updated_fields"]), 1)

    def test_update_existing_does_not_apply_different_field_even_if_selected(self):
        work = Work.objects.create(
            work_id="work-000018",
            source_number=18,
            source_sequence=18,
            source_section=self.section,
            language=self.language,
            work_type=Work.WorkType.BOOK,
            title="Selected different",
        )
        batch = ImportBatch.objects.create(title="Selected different", raw_input="Selected different")
        item = ImportItem.objects.create(
            import_batch=batch,
            raw_text="Selected different",
            detected_type=ImportItem.DetectedType.BOOK,
            status=ImportItem.Status.READY,
            matched_existing_type="work",
            matched_existing_id=work.work_id,
            comparison_json={"fields": [{"label": "Место издания", "existing": "СПб.", "source": "М.", "status": "different"}]},
        )

        apply_item_decision(item, ImportDecision.DecisionType.UPDATE_EXISTING, payload={"selected_fields": ["Место издания"]})
        apply_import_batch(batch)
        work.refresh_from_db()
        log = batch.apply_logs.get()

        self.assertEqual(work.publication_place, "")
        self.assertEqual(log.updated_entities_json[0]["skipped_fields"][0]["reason"], "replacement_not_selected")

    def test_update_existing_applies_selected_replacement_field(self):
        work = Work.objects.create(
            work_id="work-replace-extent",
            source_number=93010,
            source_sequence=93010,
            source_section=self.section,
            language=self.language,
            work_type=Work.WorkType.BOOK,
            title="Replacement extent",
            extent="6, Х, 928 ХС прим.",
        )
        batch = ImportBatch.objects.create(title="Replacement extent", raw_input="Replacement extent")
        item = ImportItem.objects.create(
            import_batch=batch,
            raw_text="Replacement extent",
            detected_type=ImportItem.DetectedType.BOOK,
            status=ImportItem.Status.READY,
            matched_existing_type="work",
            matched_existing_id=work.work_id,
            comparison_json={
                "fields": [
                    {
                        "label": "Страницы",
                        "existing": "6, Х, 928 ХС прим.",
                        "source": "6, Х, 928 с. ХС с. прим",
                        "status": "different",
                    },
                    {"label": "Место издания", "existing": "СПб.", "source": "М.", "status": "different"},
                ]
            },
        )

        apply_item_decision(item, ImportDecision.DecisionType.UPDATE_EXISTING, payload={"selected_fields": [], "replacement_fields": ["Страницы"]})
        apply_import_batch(batch)
        work.refresh_from_db()
        log = batch.apply_logs.get()

        self.assertEqual(work.extent, "6, Х, 928 с. ХС с. прим")
        self.assertEqual(work.publication_place, "")
        self.assertEqual(log.updated_entities_json[0]["updated_fields"][0]["operation"], "replace")
        self.assertEqual(log.updated_entities_json[0]["updated_fields"][0]["field"], "extent")

    def test_update_existing_updates_empty_target_source_fields_only(self):
        work = Work.objects.create(
            work_id="work-000014",
            source_number=14,
            source_sequence=14,
            source_section=self.section,
            language=self.language,
            work_type=Work.WorkType.BOOK,
            title="Source sync",
        )
        source = Source.objects.create(
            source_id="source-000014",
            legacy_work=work,
            source_number=14,
            language=self.language,
            title="Source sync",
            publisher="Старый издатель",
        )
        batch = ImportBatch.objects.create(title="Source update", raw_input="Source update")
        item = ImportItem.objects.create(
            import_batch=batch,
            raw_text="Source update",
            detected_type=ImportItem.DetectedType.BOOK,
            status=ImportItem.Status.READY,
            matched_existing_type="work",
            matched_existing_id=work.work_id,
            comparison_json={
                "fields": [
                    {"label": "Место издания", "existing": "", "source": "М.", "status": "new_in_source"},
                    {"label": "Издательство / типография", "existing": "", "source": "Новый издатель", "status": "new_in_source"},
                ]
            },
        )

        apply_item_decision(item, ImportDecision.DecisionType.UPDATE_EXISTING)
        apply_import_batch(batch)
        source.refresh_from_db()

        self.assertEqual(source.publication_place, "М.")
        self.assertEqual(source.publisher, "Старый издатель")

    def test_update_existing_fills_empty_target_source_data_source(self):
        work = Work.objects.create(
            work_id="work-000114",
            source_number=114,
            source_sequence=114,
            source_section=self.section,
            language=self.language,
            work_type=Work.WorkType.BOOK,
            title="Source provenance sync",
        )
        source = Source.objects.create(
            source_id="source-000114",
            legacy_work=work,
            source_number=114,
            language=self.language,
            title="Source provenance sync",
            data_source="editor",
        )
        batch = ImportBatch.objects.create(title="Source provenance", source_name="Баранов-2021", source_type=ImportBatch.SourceType.PLAIN_TEXT)
        item = ImportItem.objects.create(
            import_batch=batch,
            raw_text="Source provenance",
            detected_type=ImportItem.DetectedType.BOOK,
            status=ImportItem.Status.READY,
            matched_existing_type="work",
            matched_existing_id=work.work_id,
            comparison_json={"fields": [{"label": "Место издания", "existing": "", "source": "М.", "status": "new_in_source"}]},
        )

        apply_item_decision(item, ImportDecision.DecisionType.UPDATE_EXISTING)
        apply_import_batch(batch)
        source.refresh_from_db()
        log = batch.apply_logs.get()

        self.assertEqual(source.data_source, "Баранов-2021 (plain_text)")
        self.assertIn("source_name", log.summary_json)

    def test_linked_existing_article_apply_fills_safe_empty_source_fields(self):
        author = Author.objects.create(author_id="author-linked-safe", display_name="Бугров А.В.", sort_name="Бугров А.В.")
        journal = Journal.objects.create(journal_id="journal-linked-safe", title="Нумизматика")
        issue = JournalIssue.objects.create(journal_issue_id="journal-issue-linked-safe", journal=journal, year=2009, issue_number="21")
        work = Work.objects.create(
            work_id="work-linked-safe",
            source_number=115,
            source_sequence=115,
            source_section=self.section,
            language=self.language,
            work_type=Work.WorkType.ARTICLE,
            title="Евгений Иванович Ламанский",
        )
        WorkAuthor.objects.create(work=work, author=author, sort_order=1)
        Article.objects.create(article_id="article-linked-safe", work=work, journal_issue=issue)
        source = Source.objects.create(
            source_id="source-linked-safe",
            legacy_work=work,
            source_number=115,
            language=self.language,
            source_type=Source.SourceType.ARTICLE,
            title=work.title,
            data_source="editor",
        )
        batch = ImportBatch.objects.create(title="Linked safe", source_name="Баранов-2021", source_type=ImportBatch.SourceType.PLAIN_TEXT)
        item = ImportItem.objects.create(
            import_batch=batch,
            raw_text="Бугров А.В. Евгений Иванович Ламанский // Нумизматика. — 2009. — № 21. — С. 38–41.",
            detected_type=ImportItem.DetectedType.JOURNAL_ARTICLE,
            status=ImportItem.Status.PARSED,
        )
        issue_entity = ImportEntity.objects.create(
            import_batch=batch,
            entity_type=ImportEntity.EntityType.JOURNAL_ISSUE,
            label="Нумизматика — 2009 — № 21",
            normalized_key="issue-linked-safe",
            data_json={"journal_title": "Нумизматика", "year": "2009", "issue_number": "21"},
            status=ImportEntity.Status.LINKED_EXISTING,
            matched_existing_type="journal_issue",
            matched_existing_id=issue.journal_issue_id,
        )
        article_entity = ImportEntity.objects.create(
            import_batch=batch,
            entity_type=ImportEntity.EntityType.ARTICLE,
            label=work.title,
            normalized_key="article-linked-safe",
            data_json={
                "item_id": item.id,
                "title": work.title,
                "authors": ["Бугров А.В."],
                "journal_title": "Нумизматика",
                "year": "2009",
                "issue_number": "21",
                "pages": "38-41",
                "raw_parent_description": "Нумизматика. - 2009. - № 21. - С. 38-41.",
            },
            status=ImportEntity.Status.LINKED_EXISTING,
            matched_existing_type="work",
            matched_existing_id=work.work_id,
        )
        ImportEntityRelation.objects.create(import_batch=batch, parent_entity=issue_entity, child_entity=article_entity, relation_type="issue_has_article")
        ImportGroup.objects.create(import_batch=batch, group_type=ImportGroup.GroupType.JOURNAL_ISSUE_GROUP, label=issue_entity.label, root_entity=issue_entity, status=ImportGroup.Status.READY)

        result = apply_import_batch(batch)
        work.refresh_from_db()
        source.refresh_from_db()
        article = work.article
        log = batch.apply_logs.get()

        self.assertTrue(result["applied"])
        self.assertEqual(work.article_pages, "38-41")
        self.assertEqual(article.pages, "38-41")
        self.assertEqual(article.pages_raw, "38-41")
        self.assertEqual(source.raw_publication_details, "Нумизматика. - 2009. - № 21. - С. 38-41.")
        self.assertEqual(source.data_source, "Баранов-2021 (plain_text)")
        self.assertTrue(any(row.get("entity_id") == article_entity.id for row in log.updated_entities_json))

    def test_apply_created_record_logs_import_source_name(self):
        batch = ImportBatch.objects.create(title="Create provenance", source_name="Баранов-2021", source_type=ImportBatch.SourceType.PLAIN_TEXT)
        book = ImportEntity.objects.create(
            import_batch=batch,
            entity_type=ImportEntity.EntityType.BOOK,
            label="Новая книга",
            normalized_key="book-create-provenance",
            data_json={"title": "Новая книга", "year": "2026"},
            status=ImportEntity.Status.WILL_CREATE,
        )

        result = apply_import_batch(batch)
        log = batch.apply_logs.get()

        self.assertTrue(result["applied"])
        self.assertEqual(log.summary_json["source_name"], "Баранов-2021")
        self.assertEqual(log.created_entities_json[0]["source_name"], "Баранов-2021")

    def test_structural_conflict_update_fills_host_without_creating_container(self):
        author = Author.objects.create(author_id="author-000015", display_name="Ламанский Е.И.", sort_name="Ламанский Е.И.")
        work = Work.objects.create(
            work_id="work-000015",
            source_number=15,
            source_sequence=15,
            source_section=self.section,
            language=self.language,
            work_type=Work.WorkType.BOOK,
            title="Исторический очерк денежного обращения России с 1650 по 1817 г",
        )
        source = Source.objects.create(
            source_id="source-000015",
            legacy_work=work,
            source_number=15,
            language=self.language,
            title=work.title,
        )
        WorkAuthor.objects.create(work=work, author=author, sort_order=1)
        batch = ImportBatch.objects.create(
            title="Structural safe apply",
            raw_input="Ламанский Е.И. Исторический очерк денежного обращения России с 1650 по 1817 г. // Сб. стат. сведений о России. — СПб., 1854.",
        )
        parse_import_batch(batch)
        item = batch.items.get()

        apply_item_decision(item, ImportDecision.DecisionType.UPDATE_EXISTING)
        result = apply_import_batch(batch)
        work.refresh_from_db()
        source.refresh_from_db()

        self.assertTrue(result["applied"])
        self.assertEqual(work.host_title, "Сб. стат. сведений о России")
        self.assertEqual(source.raw_host_title, "Сб. стат. сведений о России")
        self.assertFalse(batch.entities.filter(entity_type=ImportEntity.EntityType.COLLECTION).exists())
        self.assertFalse(batch.entities.filter(entity_type=ImportEntity.EntityType.ARTICLE).exists())

    def test_skip_reject_postpone_do_not_change_existing_work_on_apply(self):
        work = Work.objects.create(
            work_id="work-000016",
            source_number=16,
            source_sequence=16,
            source_section=self.section,
            language=self.language,
            work_type=Work.WorkType.BOOK,
            title="Skip keeps empty fields",
        )
        batch = ImportBatch.objects.create(title="Skip apply", raw_input="Skip apply")
        item = ImportItem.objects.create(
            import_batch=batch,
            raw_text="Skip apply",
            detected_type=ImportItem.DetectedType.BOOK,
            status=ImportItem.Status.READY,
            matched_existing_type="work",
            matched_existing_id=work.work_id,
            comparison_json={"fields": [{"label": "Место издания", "existing": "", "source": "М.", "status": "new_in_source"}]},
        )

        apply_item_decision(item, ImportDecision.DecisionType.SKIP)
        apply_import_batch(batch)
        work.refresh_from_db()

        self.assertEqual(work.publication_place, "")

    def test_item_detail_renders_editable_comparison_without_action_column(self):
        user = get_user_model().objects.create_user("field_reviewer", password="x", is_staff=True)
        batch = ImportBatch.objects.create(title="Field UI", raw_input="Field UI")
        item = ImportItem.objects.create(
            import_batch=batch,
            raw_text="Field UI",
            detected_type=ImportItem.DetectedType.BOOK,
            status=ImportItem.Status.FOUND_EXISTING_WITH_DIFFERENCES,
            matched_existing_type="work",
            matched_existing_id="work-missing",
            comparison_json={
                "fields": [
                    {"label": "Место издания", "existing": "", "source": "СПб.", "status": "new_in_source"},
                    {"label": "Издательство / типография", "existing": "Старое", "source": "Новое", "status": "different"},
                ]
            },
        )

        self.client.force_login(user)
        response = self.client.get(f"/imports/{batch.pk}/items/{item.pk}/")

        self.assertNotContains(response, "<th>Действие</th>", html=True)
        self.assertContains(response, 'name="comparison_publication_place"')
        self.assertContains(response, 'value="СПб."')
        self.assertContains(response, 'name="comparison_publisher"')
        self.assertContains(response, 'value="Новое"')
        self.assertNotContains(response, 'name="replacement_fields"')
        self.assertNotContains(response, "применить замену")
        self.assertContains(response, 'name="selected_fields"', count=1)

    def test_item_detail_parse_map_is_compact_badge_preview(self):
        user = get_user_model().objects.create_user("parse_badges", password="x", is_staff=True)
        batch = ImportBatch.objects.create(title="Parse badges", raw_input="")
        item = ImportItem.objects.create(
            import_batch=batch,
            raw_text="Гурьев А. (Н.) Реформа денежного обращения. — СПб., 1896.",
            detected_type=ImportItem.DetectedType.BOOK,
            status=ImportItem.Status.PARSED,
            parsed_data_json={
                "authors": ["Гурьев А."],
                "title": "(Н.) Реформа денежного обращения",
                "publication_place": "СПб",
                "year": "1896",
            },
        )

        self.client.force_login(user)
        response = self.client.get(f"/imports/{batch.pk}/items/{item.pk}/")

        self.assertContains(response, "Как разобрана строка")
        self.assertContains(response, 'class="status-badge muted parsed-fragment"', count=4)
        self.assertContains(response, 'title="Авторы"')
        self.assertContains(response, ">Гурьев А.</span>")
        self.assertNotContains(response, "Авторы: Гурьев А.")
        self.assertContains(response, "Исправить поля разбора")
        self.assertNotContains(response, "<th>Значение разбора</th>", html=True)

    def test_item_detail_hides_update_button_when_no_safe_fields_exist(self):
        user = get_user_model().objects.create_user("no_safe_fields", password="x", is_staff=True)
        batch = ImportBatch.objects.create(title="No safe fields", raw_input="")
        item = ImportItem.objects.create(
            import_batch=batch,
            raw_text="Мигулин П. Русский государственный кредит. — Харьков, 1899. — 606 с.",
            detected_type=ImportItem.DetectedType.BOOK,
            status=ImportItem.Status.FOUND_EXISTING_WITH_DIFFERENCES,
            matched_existing_type="work",
            matched_existing_id="work-missing",
            comparison_json={
                "fields": [
                    {"label": "Название", "existing": "Русский государственный кредит", "source": "Русский государственный кредит", "status": "same"},
                    {"label": "Родительское издание", "existing": "старое описание", "source": "", "status": "missing_in_source"},
                ]
            },
        )

        self.client.force_login(user)
        response = self.client.get(f"/imports/{batch.pk}/items/{item.pk}/")

        self.assertContains(response, "нет новых пустых полей")
        self.assertNotContains(response, "Применить выбранные дополнения и замены")
        self.assertContains(response, "Пропустить без изменений")

    def test_item_detail_hides_empty_irrelevant_comparison_rows_for_book(self):
        user = get_user_model().objects.create_user("empty_rows", password="x", is_staff=True)
        batch = ImportBatch.objects.create(title="Empty rows", raw_input="")
        item = ImportItem.objects.create(
            import_batch=batch,
            raw_text="Книга без контейнера.",
            detected_type=ImportItem.DetectedType.BOOK,
            status=ImportItem.Status.FOUND_EXISTING_NO_CHANGES,
            comparison_json={
                "fields": [
                    {"label": "Название", "existing": "Книга", "source": "Книга", "status": "same"},
                    {"label": "Родительское издание", "existing": "", "source": "", "status": "same"},
                    {"label": "Номер выпуска", "existing": "", "source": "", "status": "same"},
                    {"label": "Страницы статьи", "existing": "", "source": "", "status": "same"},
                ]
            },
        )

        self.client.force_login(user)
        response = self.client.get(f"/imports/{batch.pk}/items/{item.pk}/")

        self.assertContains(response, "Название")
        self.assertNotContains(response, "Родительское издание")
        self.assertNotContains(response, "Номер выпуска")
        self.assertNotContains(response, "Страницы статьи")

    def test_author_comparison_incomplete_initials_gets_specific_status(self):
        author = Author.objects.create(author_id="author-initials", display_name="Бугров А.В.", sort_name="Бугров А.В.")
        work = Work.objects.create(
            work_id="work-initials",
            source_number=93001,
            source_section=self.section,
            language=self.language,
            work_type=Work.WorkType.BOOK,
            title="Книга",
        )
        WorkAuthor.objects.create(work=work, author=author, sort_order=1)

        comparison = compare_work_to_parsed_data(work, {"authors": ["Бугров А."], "title": "Книга"})
        author_row = next(row for row in comparison["fields"] if row["label"] == "Автор")

        self.assertEqual(author_row["status"], "author_incomplete_initials")

    def test_source_extra_status_label_is_explicit(self):
        user = get_user_model().objects.create_user("source_extra_status", password="x", is_staff=True)
        batch = ImportBatch.objects.create(title="Source extra", raw_input="")
        item = ImportItem.objects.create(
            import_batch=batch,
            raw_text="Книга. 21 см.",
            detected_type=ImportItem.DetectedType.BOOK,
            status=ImportItem.Status.FOUND_EXISTING_WITH_DIFFERENCES,
            comparison_json={"fields": [{"label": "Размер", "existing": "", "source": "21 см", "status": "source_extra"}]},
        )

        self.client.force_login(user)
        response = self.client.get(f"/imports/{batch.pk}/items/{item.pk}/")

        self.assertContains(response, "есть только в источнике; сейчас не записывается автоматически")
        self.assertNotContains(response, "не требует автоматического действия")

    def test_item_detail_renders_entity_decision_actions_for_new_book(self):
        user = get_user_model().objects.create_user("item_entity_actions", password="x", is_staff=True)
        batch = ImportBatch.objects.create(title="Item entity actions", raw_input="")
        item = ImportItem.objects.create(
            import_batch=batch,
            raw_text="Мигулин П.П. Наша банковская политика. — Харьков, 1904.",
            detected_type=ImportItem.DetectedType.BOOK,
            status=ImportItem.Status.PARSED,
        )
        entity = ImportEntity.objects.create(
            import_batch=batch,
            entity_type=ImportEntity.EntityType.BOOK,
            label="Наша банковская политика",
            normalized_key="item-entity-actions-book",
            status=ImportEntity.Status.UNRESOLVED,
            data_json={"item_id": item.id},
        )
        batch.matches.create(entity=entity, existing_type="work", existing_id="work-missing", score=0.84)

        self.client.force_login(user)
        response = self.client.get(f"/imports/{batch.pk}/items/{item.pk}/")

        self.assertContains(response, "Осталось решить: книга похожа на существующую запись")
        self.assertContains(response, "Нет, создать новую запись")
        self.assertContains(response, "Да, связать с найденной записью")
        self.assertContains(response, "work work-missing")
        self.assertContains(response, 'name="decision_type" value="link_existing"', html=False)

    def test_applied_item_detail_does_not_render_decision_buttons(self):
        user = get_user_model().objects.create_user("applied_item", password="x", is_staff=True)
        batch = ImportBatch.objects.create(title="Applied item", raw_input="", status=ImportBatch.Status.APPLIED)
        ImportApplyLog.objects.create(import_batch=batch, applied_by=user)
        item = ImportItem.objects.create(import_batch=batch, raw_text="Applied item", detected_type=ImportItem.DetectedType.BOOK, status=ImportItem.Status.PARSED)
        ImportEntity.objects.create(
            import_batch=batch,
            entity_type=ImportEntity.EntityType.BOOK,
            label="Applied book",
            normalized_key="applied-item-book",
            status=ImportEntity.Status.WILL_CREATE,
            data_json={"item_id": item.id},
        )

        self.client.force_login(user)
        response = self.client.get(f"/imports/{batch.pk}/items/{item.pk}/")

        self.assertContains(response, "Импорт уже применён")
        self.assertContains(response, "Решения нельзя менять")
        self.assertNotContains(response, "Создать новую")
        self.assertNotContains(response, 'name="decision_type"', html=False)

    def test_applied_author_page_does_not_render_decision_forms(self):
        user = get_user_model().objects.create_user("applied_author", password="x", is_staff=True)
        batch = ImportBatch.objects.create(title="Applied author", raw_input="", status=ImportBatch.Status.APPLIED)
        ImportApplyLog.objects.create(import_batch=batch, applied_by=user)
        ImportEntity.objects.create(
            import_batch=batch,
            entity_type=ImportEntity.EntityType.AUTHOR,
            label="Иванов И.И.",
            normalized_key="applied-author",
            status=ImportEntity.Status.LINKED_EXISTING,
            matched_existing_type="author",
            matched_existing_id="author-missing",
        )

        self.client.force_login(user)
        response = self.client.get(f"/imports/{batch.pk}/authors/")

        self.assertContains(response, "Авторские решения показаны только для справки")
        self.assertNotContains(response, "<form", html=False)
        self.assertNotContains(response, "Создать нового автора")
        self.assertNotContains(response, "Отложить")

    def test_applied_group_detail_does_not_render_split_or_move_controls(self):
        user = get_user_model().objects.create_user("applied_group", password="x", is_staff=True)
        batch = ImportBatch.objects.create(title="Applied group", raw_input="", status=ImportBatch.Status.APPLIED)
        ImportApplyLog.objects.create(import_batch=batch, applied_by=user)
        group, _root, _articles, _author = self.make_import_article_group(
            batch,
            ImportGroup.GroupType.JOURNAL_ISSUE_GROUP,
            "Новый бонист — 2024 — № 3",
            ["Статья"],
        )

        self.client.force_login(user)
        response = self.client.get(f"/imports/{batch.pk}/groups/{group.pk}/")

        self.assertContains(response, "архивное состояние")
        self.assertNotContains(response, "Вынести в новую группу")
        self.assertNotContains(response, "Перенести в другую группу")
        self.assertNotContains(response, 'name="decision_type"', html=False)

    def test_post_item_decision_on_applied_batch_does_not_create_decision(self):
        user = get_user_model().objects.create_user("applied_item_post", password="x", is_staff=True)
        batch = ImportBatch.objects.create(title="Applied item post", raw_input="", status=ImportBatch.Status.APPLIED)
        item = ImportItem.objects.create(import_batch=batch, raw_text="Applied", detected_type=ImportItem.DetectedType.BOOK, status=ImportItem.Status.PARSED)

        self.client.force_login(user)
        response = self.client.post(f"/imports/{batch.pk}/items/{item.pk}/decision/", {"decision_type": "skip"})

        self.assertEqual(response.status_code, 302)
        self.assertFalse(batch.decisions.exists())

    def test_post_entity_decision_on_applied_batch_does_not_change_entity(self):
        user = get_user_model().objects.create_user("applied_entity_post", password="x", is_staff=True)
        batch = ImportBatch.objects.create(title="Applied entity post", raw_input="", status=ImportBatch.Status.APPLIED)
        entity = ImportEntity.objects.create(
            import_batch=batch,
            entity_type=ImportEntity.EntityType.BOOK,
            label="Applied book",
            normalized_key="applied-entity-post",
            status=ImportEntity.Status.UNRESOLVED,
        )

        self.client.force_login(user)
        response = self.client.post(f"/imports/{batch.pk}/entities/{entity.pk}/decision/", {"decision_type": "create"})
        entity.refresh_from_db()

        self.assertEqual(response.status_code, 302)
        self.assertEqual(entity.status, ImportEntity.Status.UNRESOLVED)
        self.assertFalse(batch.decisions.exists())

    def test_post_group_decisions_on_applied_batch_do_not_change_graph(self):
        user = get_user_model().objects.create_user("applied_group_post", password="x", is_staff=True)
        batch = ImportBatch.objects.create(title="Applied group post", raw_input="", status=ImportBatch.Status.APPLIED)
        group, root, articles, _author = self.make_import_article_group(
            batch,
            ImportGroup.GroupType.JOURNAL_ISSUE_GROUP,
            "Новый бонист — 2024 — № 3",
            ["Статья"],
        )

        self.client.force_login(user)
        decision_response = self.client.post(
            f"/imports/{batch.pk}/groups/{group.pk}/decision/",
            {"decision_type": "create"},
        )
        split_response = self.client.post(
            f"/imports/{batch.pk}/groups/{group.pk}/articles/{articles[0].pk}/action/",
            {"action": "split"},
        )

        self.assertEqual(decision_response.status_code, 302)
        self.assertEqual(split_response.status_code, 302)
        self.assertFalse(batch.decisions.exists())
        self.assertEqual(batch.groups.count(), 1)
        self.assertTrue(
            ImportEntityRelation.objects.filter(
                import_batch=batch,
                parent_entity=root,
                child_entity=articles[0],
                relation_type="issue_has_article",
            ).exists()
        )

    def test_applied_plan_does_not_render_apply_button_or_misleading_text(self):
        user = get_user_model().objects.create_user("applied_plan", password="x", is_staff=True)
        batch = ImportBatch.objects.create(title="Applied plan", raw_input="", status=ImportBatch.Status.APPLIED)
        ImportApplyLog.objects.create(import_batch=batch, applied_by=user)

        self.client.force_login(user)
        response = self.client.get(f"/imports/{batch.pk}/plan/")

        self.assertContains(response, "Импорт применён")
        self.assertContains(response, "Показать результат применения")
        self.assertNotContains(response, "Применить импорт к базе")
        self.assertNotContains(response, "Кнопка применения появится")

    def test_applied_review_page_has_result_link_and_no_active_requires_decision_badge(self):
        user = get_user_model().objects.create_user("applied_review", password="x", is_staff=True)
        batch = ImportBatch.objects.create(title="Applied review", raw_input="", status=ImportBatch.Status.APPLIED)
        ImportApplyLog.objects.create(import_batch=batch, applied_by=user)
        ImportItem.objects.create(import_batch=batch, raw_text="Applied parsed", detected_type=ImportItem.DetectedType.BOOK, status=ImportItem.Status.PARSED)

        self.client.force_login(user)
        response = self.client.get(f"/imports/{batch.pk}/review/")

        self.assertContains(response, "Результат применения")
        self.assertContains(response, "Обработано при применении")
        self.assertNotContains(response, "Новая или требует решения")

    def test_update_existing_post_saves_selected_fields_payload(self):
        user = get_user_model().objects.create_user("field_submitter", password="x", is_staff=True)
        batch = ImportBatch.objects.create(title="Field POST", raw_input="Field POST")
        item = ImportItem.objects.create(
            import_batch=batch,
            raw_text="Field POST",
            detected_type=ImportItem.DetectedType.BOOK,
            status=ImportItem.Status.FOUND_EXISTING_WITH_DIFFERENCES,
            matched_existing_type="work",
            matched_existing_id="work-missing",
            comparison_json={"fields": [{"label": "Место издания", "existing": "", "source": "СПб.", "status": "new_in_source"}]},
        )

        self.client.force_login(user)
        response = self.client.post(
            f"/imports/{batch.pk}/items/{item.pk}/decision/",
            {"decision_type": "update_existing", "selected_fields": ["Место издания"]},
        )
        item.refresh_from_db()
        decision = batch.decisions.get(item=item)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(item.status, ImportItem.Status.READY)
        self.assertEqual(decision.payload_json, {"selected_fields": ["Место издания"]})

    def test_update_existing_post_saves_replacement_fields_payload(self):
        user = get_user_model().objects.create_user("replacement_submitter", password="x", is_staff=True)
        batch = ImportBatch.objects.create(title="Replacement POST", raw_input="Replacement POST")
        item = ImportItem.objects.create(
            import_batch=batch,
            raw_text="Replacement POST",
            detected_type=ImportItem.DetectedType.BOOK,
            status=ImportItem.Status.FOUND_EXISTING_WITH_DIFFERENCES,
            matched_existing_type="work",
            matched_existing_id="work-missing",
            comparison_json={"fields": [{"label": "Страницы", "existing": "10 с.", "source": "12 с.", "status": "different"}]},
        )

        self.client.force_login(user)
        response = self.client.post(
            f"/imports/{batch.pk}/items/{item.pk}/decision/",
            {"decision_type": "update_existing", "replacement_fields": ["Страницы"]},
        )
        item.refresh_from_db()
        decision = batch.decisions.get(item=item)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(item.status, ImportItem.Status.READY)
        self.assertEqual(decision.payload_json, {"selected_fields": [], "replacement_fields": ["Страницы"]})

    def test_item_detail_shows_selected_fields_after_update_decision(self):
        user = get_user_model().objects.create_user("selected_viewer", password="x", is_staff=True)
        batch = ImportBatch.objects.create(title="Selected visible", raw_input="Selected visible")
        item = ImportItem.objects.create(
            import_batch=batch,
            raw_text="Selected visible",
            detected_type=ImportItem.DetectedType.BOOK,
            status=ImportItem.Status.READY,
            matched_existing_type="work",
            matched_existing_id="work-missing",
            comparison_json={
                "fields": [
                    {"label": "Место издания", "existing": "", "source": "СПб.", "status": "new_in_source"},
                    {"label": "Издательство / типография", "existing": "", "source": "Тип.", "status": "new_in_source"},
                ]
            },
        )
        apply_item_decision(item, ImportDecision.DecisionType.UPDATE_EXISTING, payload={"selected_fields": ["Место издания"]})

        self.client.force_login(user)
        response = self.client.get(f"/imports/{batch.pk}/items/{item.pk}/")

        self.assertContains(response, "Сохранено решение")
        self.assertContains(response, "Выбрано к дополнению")
        self.assertContains(response, "Место издания")
        self.assertContains(response, "не выбрано")
        self.assertNotContains(response, "checked")

    def test_plan_renders_selected_replacement_before_and_after_values(self):
        user = get_user_model().objects.create_user("replacement_plan", password="x", is_staff=True)
        batch = ImportBatch.objects.create(title="Replacement plan", raw_input="Replacement plan")
        item = ImportItem.objects.create(
            import_batch=batch,
            raw_text="Replacement plan",
            detected_type=ImportItem.DetectedType.BOOK,
            status=ImportItem.Status.READY,
            matched_existing_type="work",
            matched_existing_id="work-missing",
            comparison_json={
                "fields": [
                    {
                        "label": "Страницы",
                        "existing": "6, Х, 928 ХС прим.",
                        "source": "6, Х, 928 с. ХС с. прим",
                        "status": "different",
                    }
                ]
            },
        )
        apply_item_decision(item, ImportDecision.DecisionType.UPDATE_EXISTING, payload={"selected_fields": [], "replacement_fields": ["Страницы"]})

        self.client.force_login(user)
        response = self.client.get(f"/imports/{batch.pk}/plan/")

        self.assertContains(response, "Будет заменено")
        self.assertContains(response, "Было: 6, Х, 928 ХС прим.")
        self.assertContains(response, "Станет: 6, Х, 928 с. ХС с. прим")

    def test_repeated_item_decision_updates_effective_plan_to_latest_decision(self):
        batch = ImportBatch.objects.create(title="Repeated decision", raw_input="Repeated decision")
        item = ImportItem.objects.create(
            import_batch=batch,
            raw_text="Repeated decision",
            detected_type=ImportItem.DetectedType.BOOK,
            status=ImportItem.Status.READY,
            matched_existing_type="work",
            matched_existing_id="work-missing",
            comparison_json={"fields": [{"label": "Место издания", "existing": "", "source": "СПб.", "status": "new_in_source"}]},
        )

        apply_item_decision(item, ImportDecision.DecisionType.UPDATE_EXISTING, payload={"selected_fields": ["Место издания"]})
        apply_item_decision(item, ImportDecision.DecisionType.SKIP)
        plan = build_import_plan(batch)

        self.assertEqual(batch.decisions.filter(item=item).count(), 2)
        self.assertEqual(plan["item_decisions"][ImportDecision.DecisionType.UPDATE_EXISTING], 0)
        self.assertEqual(plan["item_decisions"][ImportDecision.DecisionType.SKIP], 1)
        self.assertEqual(plan["selected_update_fields"], 0)

    def test_ready_plan_shows_apply_ready_message(self):
        user = get_user_model().objects.create_user("plan_ready", password="x", is_staff=True)
        batch = ImportBatch.objects.create(title="Ready plan", raw_input="Ready plan")
        item = ImportItem.objects.create(
            import_batch=batch,
            raw_text="Ready plan",
            detected_type=ImportItem.DetectedType.BOOK,
            status=ImportItem.Status.READY,
            matched_existing_type="work",
            matched_existing_id="work-missing",
        )
        apply_item_decision(item, ImportDecision.DecisionType.SKIP)

        self.client.force_login(user)
        response = self.client.get(f"/imports/{batch.pk}/plan/")

        self.assertContains(response, "Обязательные решения приняты")
        self.assertNotContains(response, "Импорт нельзя применить")

    def test_reject_and_postpone_item_decisions_update_status(self):
        reject_batch = ImportBatch.objects.create(title="Reject item", raw_input="Неизвестный Автор. Слабая запись.")
        reject_item = ImportItem.objects.create(
            import_batch=reject_batch,
            raw_text="Неизвестный Автор. Слабая запись.",
            detected_type=ImportItem.DetectedType.BOOK,
            status=ImportItem.Status.FOUND_EXISTING_WITH_DIFFERENCES,
        )
        postpone_batch = ImportBatch.objects.create(title="Postpone item", raw_input="Другая слабая запись.")
        postpone_item = ImportItem.objects.create(
            import_batch=postpone_batch,
            raw_text="Другая слабая запись.",
            detected_type=ImportItem.DetectedType.BOOK,
            status=ImportItem.Status.STRUCTURAL_CONFLICT,
        )

        apply_item_decision(reject_item, ImportDecision.DecisionType.REJECT)
        apply_item_decision(postpone_item, ImportDecision.DecisionType.POSTPONE)
        reject_item.refresh_from_db()
        postpone_item.refresh_from_db()

        self.assertEqual(reject_item.status, ImportItem.Status.REJECTED)
        self.assertEqual(postpone_item.status, ImportItem.Status.POSTPONED)

    def test_author_review_page_lists_author_entities_and_linked_item_count(self):
        user = get_user_model().objects.create_user("author_page", password="x", is_staff=True)
        batch = ImportBatch.objects.create(
            title="Author page",
            raw_input=(
                "Иванов И.И. Первая новая работа. — СПб., 1901.\n"
                "Иванов И.И. Вторая новая работа. — СПб., 1902."
            ),
        )
        parse_import_batch(batch)
        entity = batch.entities.get(entity_type=ImportEntity.EntityType.AUTHOR)
        entity.status = ImportEntity.Status.UNRESOLVED
        entity.matched_existing_type = ""
        entity.matched_existing_id = ""
        entity.save(update_fields=["status", "matched_existing_type", "matched_existing_id", "updated_at"])

        self.client.force_login(user)
        response = self.client.get(f"/imports/{batch.pk}/authors/")

        self.assertContains(response, "Иванов И.И.")
        self.assertContains(response, "используется в 2 импортируемых записях")
        self.assertContains(response, "Первая новая работа")
        self.assertContains(response, "Вторая новая работа")

    def test_author_review_page_shows_human_readable_author_match(self):
        user = get_user_model().objects.create_user("author_match_page", password="x", is_staff=True)
        author = Author.objects.create(author_id="author-000019", display_name="Иванов И.И.", sort_name="Иванов И.И.")
        batch = ImportBatch.objects.create(title="Author match page", raw_input="")
        entity = ImportEntity.objects.create(
            import_batch=batch,
            entity_type=ImportEntity.EntityType.AUTHOR,
            label="Иванов И.И.",
            normalized_key="ivanov-ii",
            status=ImportEntity.Status.UNRESOLVED,
        )
        batch.matches.create(
            entity=entity,
            existing_type="author",
            existing_id=author.author_id,
            score=1.0,
        )

        self.client.force_login(user)
        response = self.client.get(f"/imports/{batch.pk}/authors/")

        self.assertContains(response, "Автор в базе")
        self.assertContains(response, "Иванов И.И.")
        self.assertContains(response, ">Связать</button>")
        self.assertNotContains(response, "Связать: Иванов И.И.")
        self.assertNotContains(response, f"author {author.author_id}")

    def test_author_review_marks_selected_existing_author(self):
        user = get_user_model().objects.create_user("author_selected_page", password="x", is_staff=True)
        existing = Author.objects.create(author_id="author-selected-001", display_name="Иванов И.И.", sort_name="Иванов И.И.")
        batch = ImportBatch.objects.create(title="Selected author import", raw_input="")
        entity = ImportEntity.objects.create(
            import_batch=batch,
            entity_type=ImportEntity.EntityType.AUTHOR,
            label="Иванов И.И.",
            normalized_key="author-selected",
            status=ImportEntity.Status.LINKED_EXISTING,
            matched_existing_type="author",
            matched_existing_id=existing.author_id,
        )
        batch.matches.create(
            entity=entity,
            existing_type="author",
            existing_id=existing.author_id,
            score=1.0,
            match_reason_json={"work_count": 3},
        )

        self.client.force_login(user)
        response = self.client.get(f"/imports/{batch.pk}/authors/")

        self.assertContains(response, "Выбрано")
        self.assertContains(response, "Иванов И.И.")
        self.assertContains(response, "3")

    def test_author_review_post_link_existing_updates_author_entity(self):
        user = get_user_model().objects.create_user("author_link", password="x", is_staff=True)
        author = Author.objects.create(author_id="author-000020", display_name="Иванов И.И.", sort_name="Иванов И.И.")
        batch = ImportBatch.objects.create(
            title="Author link",
            raw_input="Иванов И.И. Новая работа. — СПб., 1901.",
        )
        parse_import_batch(batch)
        entity = batch.entities.get(entity_type=ImportEntity.EntityType.AUTHOR)

        self.client.force_login(user)
        response = self.client.post(
            f"/imports/{batch.pk}/entities/{entity.pk}/decision/",
            {
                "decision_type": "link_existing",
                "target_type": "author",
                "target_id": author.author_id,
                "next": f"/imports/{batch.pk}/authors/",
            },
        )
        entity.refresh_from_db()

        self.assertEqual(response.status_code, 302)
        self.assertEqual(entity.status, ImportEntity.Status.LINKED_EXISTING)
        self.assertEqual(entity.matched_existing_id, author.author_id)

    def test_author_review_post_create_updates_author_entity(self):
        user = get_user_model().objects.create_user("author_create", password="x", is_staff=True)
        batch = ImportBatch.objects.create(
            title="Author create",
            raw_input="Сидоров С.С. Новая работа. — СПб., 1901.",
        )
        parse_import_batch(batch)
        entity = batch.entities.get(entity_type=ImportEntity.EntityType.AUTHOR)

        self.client.force_login(user)
        response = self.client.post(
            f"/imports/{batch.pk}/entities/{entity.pk}/decision/",
            {
                "decision_type": "create",
                "next": f"/imports/{batch.pk}/authors/",
            },
        )
        entity.refresh_from_db()

        self.assertEqual(response.status_code, 302)
        self.assertEqual(entity.status, ImportEntity.Status.WILL_CREATE)

    def test_unresolved_author_blocks_plan_and_resolved_author_removes_problem(self):
        batch = ImportBatch.objects.create(title="Author readiness", raw_input="")
        entity = ImportEntity.objects.create(
            import_batch=batch,
            entity_type=ImportEntity.EntityType.AUTHOR,
            label="Иванов И.И.",
            normalized_key="author-readiness",
            status=ImportEntity.Status.UNRESOLVED,
        )

        self.assertTrue(any("Иванов И.И." in problem for problem in readiness_problems(batch)))
        apply_entity_decision(entity, ImportDecision.DecisionType.CREATE)

        self.assertFalse(any("Иванов И.И." in problem for problem in readiness_problems(batch)))

    def test_one_author_decision_is_reused_for_multiple_imported_records_on_apply(self):
        author = Author.objects.create(author_id="author-000021", display_name="Иванов Иван", sort_name="Иванов Иван")
        batch = ImportBatch.objects.create(
            title="Author cascade apply",
            raw_input=(
                "Иванов И.И. Первая каскадная работа. — СПб., 1901.\n"
                "Иванов И.И. Вторая каскадная работа. — СПб., 1902."
            ),
        )
        parse_import_batch(batch)
        author_entity = batch.entities.get(entity_type=ImportEntity.EntityType.AUTHOR)
        apply_entity_decision(author_entity, ImportDecision.DecisionType.LINK_EXISTING, target_type="author", target_id=author.author_id)

        result = apply_import_batch(batch)

        self.assertTrue(result["applied"])
        self.assertEqual(WorkAuthor.objects.filter(author=author).count(), 2)
        self.assertEqual(batch.entities.filter(entity_type=ImportEntity.EntityType.BOOK, status=ImportEntity.Status.APPLIED).count(), 2)

    def test_split_journal_article_creates_new_issue_group_and_moves_selected_article(self):
        batch = ImportBatch.objects.create(title="Split journal", raw_input="")
        group, root, articles, author = self.make_import_article_group(
            batch,
            ImportGroup.GroupType.JOURNAL_ISSUE_GROUP,
            "Новый бонист — 2024 — № 3",
            ["Статья 1", "Статья 2"],
        )

        new_group = split_article_to_new_group(group, articles[0])

        self.assertEqual(batch.groups.filter(group_type=ImportGroup.GroupType.JOURNAL_ISSUE_GROUP).count(), 2)
        self.assertEqual(
            ImportEntityRelation.objects.filter(import_batch=batch, parent_entity=root, relation_type="issue_has_article").count(),
            1,
        )
        self.assertTrue(
            ImportEntityRelation.objects.filter(
                import_batch=batch,
                parent_entity=new_group.root_entity,
                child_entity=articles[0],
                relation_type="issue_has_article",
            ).exists()
        )
        self.assertTrue(
            ImportEntityRelation.objects.filter(
                import_batch=batch,
                parent_entity=author,
                child_entity=articles[0],
                relation_type="author_of",
            ).exists()
        )
        decision = batch.decisions.get(decision_type=ImportDecision.DecisionType.SPLIT_GROUP)
        self.assertEqual(decision.payload_json["from_group_id"], group.id)
        self.assertEqual(decision.payload_json["to_group_id"], new_group.id)
        self.assertEqual(decision.payload_json["article_entity_id"], articles[0].id)

    def test_move_journal_article_to_existing_issue_group_updates_issue_relation(self):
        batch = ImportBatch.objects.create(title="Move journal", raw_input="")
        source_group, source_root, articles, author = self.make_import_article_group(
            batch,
            ImportGroup.GroupType.JOURNAL_ISSUE_GROUP,
            "Новый бонист — 2024 — № 3",
            ["Статья 1"],
        )
        target_group, target_root, _target_articles, _target_author = self.make_import_article_group(
            batch,
            ImportGroup.GroupType.JOURNAL_ISSUE_GROUP,
            "Новый бонист — 2024 — № 4",
            ["Статья 2"],
        )

        move_article_to_group(source_group, articles[0], target_group)

        self.assertFalse(
            ImportEntityRelation.objects.filter(
                import_batch=batch,
                parent_entity=source_root,
                child_entity=articles[0],
                relation_type="issue_has_article",
            ).exists()
        )
        self.assertTrue(
            ImportEntityRelation.objects.filter(
                import_batch=batch,
                parent_entity=target_root,
                child_entity=articles[0],
                relation_type="issue_has_article",
            ).exists()
        )
        self.assertTrue(
            ImportEntityRelation.objects.filter(
                import_batch=batch,
                parent_entity=author,
                child_entity=articles[0],
                relation_type="author_of",
            ).exists()
        )
        decision = batch.decisions.get(decision_type=ImportDecision.DecisionType.MOVE_TO_GROUP)
        self.assertEqual(decision.payload_json["from_group_id"], source_group.id)
        self.assertEqual(decision.payload_json["to_group_id"], target_group.id)

    def test_split_collection_article_creates_new_collection_group_and_moves_selected_article(self):
        batch = ImportBatch.objects.create(title="Split collection", raw_input="")
        group, root, articles, _author = self.make_import_article_group(
            batch,
            ImportGroup.GroupType.COLLECTION_VOLUME_GROUP,
            "Деньги в истории",
            ["Статья сборника 1", "Статья сборника 2"],
        )

        new_group = split_article_to_new_group(group, articles[1])

        self.assertEqual(batch.groups.filter(group_type=ImportGroup.GroupType.COLLECTION_VOLUME_GROUP).count(), 2)
        self.assertEqual(
            ImportEntityRelation.objects.filter(import_batch=batch, parent_entity=root, relation_type="article_in_collection").count(),
            1,
        )
        self.assertTrue(
            ImportEntityRelation.objects.filter(
                import_batch=batch,
                parent_entity=new_group.root_entity,
                child_entity=articles[1],
                relation_type="article_in_collection",
            ).exists()
        )

    def test_move_collection_article_to_existing_collection_group_updates_collection_relation(self):
        batch = ImportBatch.objects.create(title="Move collection", raw_input="")
        source_group, source_root, articles, _author = self.make_import_article_group(
            batch,
            ImportGroup.GroupType.COLLECTION_VOLUME_GROUP,
            "Деньги в истории",
            ["Статья сборника 1"],
        )
        target_group, target_root, _target_articles, _target_author = self.make_import_article_group(
            batch,
            ImportGroup.GroupType.COLLECTION_VOLUME_GROUP,
            "Архивариус",
            ["Статья сборника 2"],
        )

        move_article_to_group(source_group, articles[0], target_group)

        self.assertFalse(
            ImportEntityRelation.objects.filter(
                import_batch=batch,
                parent_entity=source_root,
                child_entity=articles[0],
                relation_type="article_in_collection",
            ).exists()
        )
        self.assertTrue(
            ImportEntityRelation.objects.filter(
                import_batch=batch,
                parent_entity=target_root,
                child_entity=articles[0],
                relation_type="article_in_collection",
            ).exists()
        )

    def test_group_detail_page_renders_split_and_move_controls(self):
        user = get_user_model().objects.create_user("group_editor", password="x", is_staff=True)
        batch = ImportBatch.objects.create(title="Group controls", raw_input="")
        group, _root, _articles, _author = self.make_import_article_group(
            batch,
            ImportGroup.GroupType.JOURNAL_ISSUE_GROUP,
            "Новый бонист — 2024 — № 3",
            ["Статья 1"],
        )
        self.make_import_article_group(
            batch,
            ImportGroup.GroupType.JOURNAL_ISSUE_GROUP,
            "Новый бонист — 2024 — № 4",
            ["Статья 2"],
        )

        self.client.force_login(user)
        response = self.client.get(f"/imports/{batch.pk}/groups/{group.pk}/")

        self.assertContains(response, "Если статья попала не в тот выпуск или сборник")
        self.assertContains(response, "Вынести в новую группу")
        self.assertContains(response, "Перенести в другую группу")
        self.assertContains(response, "Новый бонист — 2024 — № 4")

    def test_group_detail_with_unresolved_article_weak_match_renders_editor_summary(self):
        user = get_user_model().objects.create_user("group_weak_summary", password="x", is_staff=True)
        batch, group, item, article, journal_entity, issue_entity, existing_work = self.make_weak_article_group_import()

        self.client.force_login(user)
        response = self.client.get(f"/imports/{batch.pk}/groups/{group.pk}/")

        self.assertContains(response, "Осталось решить: статья похожа на существующую запись")
        self.assertContains(response, "В этой группе 1 статья")
        self.assertContains(response, "Мекк А. — К вопросу о бумажно-денежном обращении")
        self.assertContains(response, "Совпадение: 85%")
        self.assertContains(response, "Да, связать с найденной статьёй")
        self.assertContains(response, "Нет, создать новую статью")
        self.assertContains(response, "Отложить решение")

    def test_group_detail_separates_container_summary_from_article_decision(self):
        user = get_user_model().objects.create_user("group_container_summary", password="x", is_staff=True)
        batch, group, item, article, journal_entity, issue_entity, existing_work = self.make_weak_article_group_import()

        self.client.force_login(user)
        response = self.client.get(f"/imports/{batch.pk}/groups/{group.pk}/")
        text = response.content.decode("utf-8")

        self.assertIn("Что проверяем в этой группе", text)
        self.assertIn("Экономический журнал", text)
        self.assertIn("уже связано с существующей записью", text)
        self.assertLess(text.index("Что проверяем в этой группе"), text.index("Статьи внутри этого выпуска"))
        self.assertLess(text.index("Статьи внутри этого выпуска"), text.index("Если статья попала не в тот выпуск или сборник"))

    def test_group_detail_puts_split_move_controls_in_secondary_section(self):
        user = get_user_model().objects.create_user("group_secondary_tools", password="x", is_staff=True)
        batch, group, item, article, journal_entity, issue_entity, existing_work = self.make_weak_article_group_import()

        self.client.force_login(user)
        response = self.client.get(f"/imports/{batch.pk}/groups/{group.pk}/")
        text = response.content.decode("utf-8")

        self.assertIn("Если статья попала не в тот выпуск или сборник", text)
        self.assertIn("Вынести в новую группу", text)
        self.assertIn("Перенести в другую группу", text)
        self.assertLess(text.index("Да, связать с найденной статьёй"), text.index("Если статья попала не в тот выпуск или сборник"))
        self.assertIn("Технические сущности импорта", text)
        self.assertLess(text.index("Если статья попала не в тот выпуск или сборник"), text.index("Технические сущности импорта"))

    def test_post_group_article_primary_link_action_links_article_entity(self):
        user = get_user_model().objects.create_user("group_article_link", password="x", is_staff=True)
        batch, group, item, article, journal_entity, issue_entity, existing_work = self.make_weak_article_group_import()

        self.client.force_login(user)
        response = self.client.post(
            f"/imports/{batch.pk}/entities/{article.pk}/decision/",
            {
                "decision_type": "link_existing",
                "target_type": "work",
                "target_id": existing_work.work_id,
                "next": f"/imports/{batch.pk}/groups/{group.pk}/",
            },
        )
        article.refresh_from_db()

        self.assertEqual(response.status_code, 302)
        self.assertEqual(article.status, ImportEntity.Status.LINKED_EXISTING)
        self.assertEqual(article.matched_existing_id, existing_work.work_id)

    def test_applied_group_detail_does_not_show_article_decision_buttons(self):
        user = get_user_model().objects.create_user("group_weak_applied", password="x", is_staff=True)
        batch, group, item, article, journal_entity, issue_entity, existing_work = self.make_weak_article_group_import(batch_status=ImportBatch.Status.APPLIED)
        ImportApplyLog.objects.create(import_batch=batch, applied_by=user)

        self.client.force_login(user)
        response = self.client.get(f"/imports/{batch.pk}/groups/{group.pk}/")

        self.assertContains(response, "Импорт применён")
        self.assertNotContains(response, "Да, связать с найденной статьёй")
        self.assertNotContains(response, "Нет, создать новую статью")

    def test_invalid_move_across_incompatible_group_types_is_rejected(self):
        user = get_user_model().objects.create_user("invalid_move", password="x", is_staff=True)
        batch = ImportBatch.objects.create(title="Invalid move", raw_input="")
        journal_group, journal_root, articles, _author = self.make_import_article_group(
            batch,
            ImportGroup.GroupType.JOURNAL_ISSUE_GROUP,
            "Новый бонист — 2024 — № 3",
            ["Статья 1"],
        )
        collection_group, _collection_root, _collection_articles, _collection_author = self.make_import_article_group(
            batch,
            ImportGroup.GroupType.COLLECTION_VOLUME_GROUP,
            "Деньги в истории",
            ["Статья сборника"],
        )

        self.client.force_login(user)
        response = self.client.post(
            f"/imports/{batch.pk}/groups/{journal_group.pk}/articles/{articles[0].pk}/action/",
            {"action": "move", "target_group_id": collection_group.pk},
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            ImportEntityRelation.objects.filter(
                import_batch=batch,
                parent_entity=journal_root,
                child_entity=articles[0],
                relation_type="issue_has_article",
            ).exists()
        )
        self.assertFalse(batch.decisions.filter(decision_type=ImportDecision.DecisionType.MOVE_TO_GROUP).exists())

    def test_plan_preview_includes_concrete_create_rows_for_new_book_and_article(self):
        batch = ImportBatch.objects.create(title="Plan create rows", raw_input="")
        author = ImportEntity.objects.create(
            import_batch=batch,
            entity_type=ImportEntity.EntityType.AUTHOR,
            label="Иванов И.И.",
            normalized_key="plan-author",
            status=ImportEntity.Status.WILL_CREATE,
        )
        book = ImportEntity.objects.create(
            import_batch=batch,
            entity_type=ImportEntity.EntityType.BOOK,
            label="Совершенно новая книга о бонах",
            normalized_key="plan-book",
            status=ImportEntity.Status.WILL_CREATE,
        )
        group, _root, articles, _group_author = self.make_import_article_group(
            batch,
            ImportGroup.GroupType.JOURNAL_ISSUE_GROUP,
            "Новый бонист — 2024 — № 3",
            ["Новая статья"],
        )
        ImportEntityRelation.objects.create(import_batch=batch, parent_entity=author, child_entity=book, relation_type="author_of")

        plan = build_import_plan(batch)
        labels = [row["label"] for row in plan["preview"]["create_rows"]]

        self.assertIn("Совершенно новая книга о бонах", labels)
        self.assertIn("Новая статья", labels)
        article_row = next(row for row in plan["preview"]["create_rows"] if row["label"] == articles[0].label)
        self.assertTrue(any(group.label in detail for detail in article_row["details"]))

    def test_plan_preview_includes_selected_update_existing_fields_and_values(self):
        work = Work.objects.create(
            work_id="work-plan-001",
            source_number=501,
            source_sequence=501,
            source_section=self.section,
            language=self.language,
            work_type=Work.WorkType.BOOK,
            title="Китайские ассигнации",
        )
        batch = ImportBatch.objects.create(title="Plan update rows", raw_input="")
        item = ImportItem.objects.create(
            import_batch=batch,
            raw_text="Ламанский Е.И. Китайские ассигнации. — СПб., 1857.",
            detected_type=ImportItem.DetectedType.BOOK,
            status=ImportItem.Status.READY,
            matched_existing_type="work",
            matched_existing_id=work.work_id,
            comparison_json={
                "fields": [
                    {"label": "Место издания", "existing": "", "source": "СПб.", "status": "new_in_source"},
                    {"label": "Год", "existing": "", "source": "1857", "status": "new_in_source"},
                ]
            },
        )
        apply_item_decision(item, ImportDecision.DecisionType.UPDATE_EXISTING, payload={"selected_fields": ["Место издания"]})

        plan = build_import_plan(batch)
        row = plan["preview"]["update_rows"][0]

        self.assertEqual(row["existing_label"], "Китайские ассигнации")
        self.assertEqual(row["fields"], [{"label": "Место издания", "existing": "", "source": "СПб.", "status": "new_in_source"}])

    def test_plan_preview_shows_linked_existing_author_by_human_readable_name(self):
        existing = Author.objects.create(author_id="author-plan-001", display_name="Ламанский Е.И.", sort_name="Ламанский Е.И.")
        batch = ImportBatch.objects.create(title="Plan author rows", raw_input="")
        entity = ImportEntity.objects.create(
            import_batch=batch,
            entity_type=ImportEntity.EntityType.AUTHOR,
            label="Ламанский Е.И.",
            normalized_key="plan-linked-author",
            status=ImportEntity.Status.LINKED_EXISTING,
            matched_existing_type="author",
            matched_existing_id=existing.author_id,
        )

        plan = build_import_plan(batch)
        row = plan["preview"]["author_rows"][0]

        self.assertEqual(row["label"], entity.label)
        self.assertEqual(row["target_label"], "Ламанский Е.И.")
        self.assertNotEqual(row["target_label"], f"author {existing.author_id}")

    def test_plan_preview_lists_skipped_rejected_and_postponed_items(self):
        batch = ImportBatch.objects.create(title="Plan item decisions", raw_input="")
        skipped = ImportItem.objects.create(import_batch=batch, raw_text="Уже есть", detected_type=ImportItem.DetectedType.BOOK, status=ImportItem.Status.READY)
        rejected = ImportItem.objects.create(import_batch=batch, raw_text="Отклонить", detected_type=ImportItem.DetectedType.BOOK, status=ImportItem.Status.READY)
        postponed = ImportItem.objects.create(import_batch=batch, raw_text="Отложить", detected_type=ImportItem.DetectedType.BOOK, status=ImportItem.Status.READY)
        apply_item_decision(skipped, ImportDecision.DecisionType.SKIP)
        apply_item_decision(rejected, ImportDecision.DecisionType.REJECT)
        apply_item_decision(postponed, ImportDecision.DecisionType.POSTPONE)

        plan = build_import_plan(batch)

        self.assertEqual(plan["preview"]["skipped_rows"][0]["raw_text"], "Уже есть")
        self.assertEqual(plan["preview"]["rejected_rows"][0]["raw_text"], "Отклонить")
        self.assertEqual(plan["preview"]["postponed_rows"][0]["raw_text"], "Отложить")

    def test_unresolved_entities_still_block_apply_in_detailed_plan(self):
        batch = ImportBatch.objects.create(title="Plan unresolved", raw_input="")
        ImportEntity.objects.create(
            import_batch=batch,
            entity_type=ImportEntity.EntityType.AUTHOR,
            label="Нерешённый автор",
            normalized_key="plan-unresolved-author",
            status=ImportEntity.Status.UNRESOLVED,
        )

        plan = build_import_plan(batch)

        self.assertFalse(plan["can_apply"])
        self.assertTrue(any("Нерешённый автор" in problem for problem in plan["problems"]))

    def test_plan_template_renders_preview_sections_and_human_readable_author_label(self):
        user = get_user_model().objects.create_user("plan_preview", password="x", is_staff=True)
        existing = Author.objects.create(author_id="author-plan-002", display_name="Ламанский Е.И.", sort_name="Ламанский Е.И.")
        batch = ImportBatch.objects.create(title="Plan render", raw_input="")
        ImportEntity.objects.create(
            import_batch=batch,
            entity_type=ImportEntity.EntityType.AUTHOR,
            label="Ламанский Е.И.",
            normalized_key="plan-render-author",
            status=ImportEntity.Status.LINKED_EXISTING,
            matched_existing_type="author",
            matched_existing_id=existing.author_id,
        )
        ImportEntity.objects.create(
            import_batch=batch,
            entity_type=ImportEntity.EntityType.BOOK,
            label="Новая книга",
            normalized_key="plan-render-book",
            status=ImportEntity.Status.WILL_CREATE,
        )

        self.client.force_login(user)
        response = self.client.get(f"/imports/{batch.pk}/plan/")

        self.assertContains(response, "Будет создано подробно")
        self.assertContains(response, "Будет дополнено в существующих записях")
        self.assertContains(response, "Авторы")
        self.assertContains(response, "Журналы, выпуски и сборники")
        self.assertContains(response, "Ламанский Е.И.")
        self.assertContains(response, "Новая книга")
        self.assertNotContains(response, f"author {existing.author_id}")

    def test_import_list_renders_russian_batch_status(self):
        user = get_user_model().objects.create_user("status_list", password="x", is_staff=True)
        ImportBatch.objects.create(title="Status batch", raw_input="", status=ImportBatch.Status.REVIEW_REQUIRED)

        self.client.force_login(user)
        response = self.client.get("/imports/")

        self.assertContains(response, "Требует решений")
        self.assertNotContains(response, "Review required")

    def test_item_detail_renders_russian_detected_type_and_confidence_label(self):
        user = get_user_model().objects.create_user("status_item", password="x", is_staff=True)
        batch = ImportBatch.objects.create(title="Item status", raw_input="")
        item = ImportItem.objects.create(
            import_batch=batch,
            raw_text="Статья // Сборник.",
            detected_type=ImportItem.DetectedType.COLLECTION_ARTICLE,
            status=ImportItem.Status.PARSED,
            confidence=0.82,
        )

        self.client.force_login(user)
        response = self.client.get(f"/imports/{batch.pk}/items/{item.pk}/")

        self.assertContains(response, "Статья в сборнике")
        self.assertContains(response, "уверенность разбора 0,82")
        self.assertNotContains(response, "Collection article")
        self.assertNotContains(response, "confidence")

    def test_author_review_renders_russian_entity_status_and_change_decision_heading(self):
        user = get_user_model().objects.create_user("status_author", password="x", is_staff=True)
        existing = Author.objects.create(author_id="author-status-001", display_name="Ламанский Е.И.", sort_name="Ламанский Е.И.")
        batch = ImportBatch.objects.create(title="Author status", raw_input="")
        ImportEntity.objects.create(
            import_batch=batch,
            entity_type=ImportEntity.EntityType.AUTHOR,
            label="Ламанский Е.И.",
            normalized_key="status-author",
            status=ImportEntity.Status.LINKED_EXISTING,
            matched_existing_type="author",
            matched_existing_id=existing.author_id,
        )

        self.client.force_login(user)
        response = self.client.get(f"/imports/{batch.pk}/authors/")

        self.assertContains(response, "Связано с существующей записью")
        self.assertContains(response, "Выбрано: Ламанский Е.И.")
        self.assertContains(response, "Изменить решение")
        self.assertNotContains(response, "Linked existing")

    def test_ready_plan_shows_russian_apply_status_not_review_required(self):
        user = get_user_model().objects.create_user("status_plan", password="x", is_staff=True)
        batch = ImportBatch.objects.create(title="Ready Russian plan", raw_input="", status=ImportBatch.Status.REVIEW_REQUIRED)
        item = ImportItem.objects.create(
            import_batch=batch,
            raw_text="Уже есть",
            detected_type=ImportItem.DetectedType.BOOK,
            status=ImportItem.Status.READY,
        )
        apply_item_decision(item, ImportDecision.DecisionType.SKIP)

        self.client.force_login(user)
        response = self.client.get(f"/imports/{batch.pk}/plan/")

        self.assertContains(response, "Ready Russian plan · Готов к применению")
        self.assertContains(response, "Применить импорт к базе")
        self.assertNotContains(response, "Review required")

    def test_selected_and_unselected_update_fields_have_unambiguous_labels(self):
        user = get_user_model().objects.create_user("status_fields", password="x", is_staff=True)
        batch = ImportBatch.objects.create(title="Field labels", raw_input="")
        item = ImportItem.objects.create(
            import_batch=batch,
            raw_text="Field labels",
            detected_type=ImportItem.DetectedType.BOOK,
            status=ImportItem.Status.READY,
            matched_existing_type="work",
            matched_existing_id="work-missing",
            comparison_json={
                "fields": [
                    {"label": "Место издания", "existing": "", "source": "СПб.", "status": "new_in_source"},
                    {"label": "Год", "existing": "", "source": "1857", "status": "new_in_source"},
                ]
            },
        )
        apply_item_decision(item, ImportDecision.DecisionType.UPDATE_EXISTING, payload={"selected_fields": ["Место издания"]})

        self.client.force_login(user)
        response = self.client.get(f"/imports/{batch.pk}/items/{item.pk}/")
        text = response.content.decode("utf-8")

        self.assertIn("Выбрано к дополнению", text)
        self.assertIn("Место издания", text)
        self.assertIn("Изменить решение", text)
        self.assertIn("будет добавлено", text)
        self.assertIn("не выбрано", text)
        self.assertNotIn("добавить", text)

    def test_group_detail_renders_russian_group_type_and_status(self):
        user = get_user_model().objects.create_user("status_group", password="x", is_staff=True)
        batch = ImportBatch.objects.create(title="Group status", raw_input="")
        group, _root, _articles, _author = self.make_import_article_group(
            batch,
            ImportGroup.GroupType.JOURNAL_ISSUE_GROUP,
            "Новый бонист — 2024 — № 3",
            ["Статья 1"],
        )

        self.client.force_login(user)
        response = self.client.get(f"/imports/{batch.pk}/groups/{group.pk}/")

        self.assertContains(response, "Журнал и выпуск · Готово")
        self.assertContains(response, "Статья в журнале")
        self.assertNotContains(response, "Journal issue group")
        self.assertNotContains(response, "Ready")

    def test_validation_unresolved_author_is_blocking(self):
        batch = ImportBatch.objects.create(title="Validation author", raw_input="")
        ImportEntity.objects.create(
            import_batch=batch,
            entity_type=ImportEntity.EntityType.AUTHOR,
            label="Иванов И.И.",
            normalized_key="validation-author",
            status=ImportEntity.Status.UNRESOLVED,
        )

        validation = validate_import_batch(batch)

        self.assertTrue(any(item["code"] == "unresolved_author" for item in validation["blocking"]))
        self.assertTrue(any(item["target_url"].endswith(f"/imports/{batch.pk}/authors/") for item in validation["blocking"]))

    def test_validation_unresolved_structural_conflict_has_item_link(self):
        batch = ImportBatch.objects.create(title="Validation structural", raw_input="")
        item = ImportItem.objects.create(
            import_batch=batch,
            raw_text="Статья // Сборник.",
            detected_type=ImportItem.DetectedType.COLLECTION_ARTICLE,
            status=ImportItem.Status.STRUCTURAL_CONFLICT,
        )

        validation = validate_import_batch(batch)
        issue = next(item for item in validation["blocking"] if item["code"] == "unresolved_structural_conflict")

        self.assertIn("структуру описания", issue["message"])
        self.assertEqual(issue["target_url"], f"/imports/{batch.pk}/items/{item.pk}/")

    def test_validation_article_without_container_is_blocking(self):
        batch = ImportBatch.objects.create(title="Validation article parent", raw_input="")
        article = ImportEntity.objects.create(
            import_batch=batch,
            entity_type=ImportEntity.EntityType.ARTICLE,
            label="Статья без контейнера",
            normalized_key="validation-orphan-article",
            status=ImportEntity.Status.WILL_CREATE,
        )

        validation = validate_import_batch(batch)

        self.assertTrue(any(item["code"] == "article_without_container" and article.label in item["message"] for item in validation["blocking"]))

    def test_validation_update_existing_no_applicable_selected_fields_warns(self):
        batch = ImportBatch.objects.create(title="Validation no-op update", raw_input="")
        item = ImportItem.objects.create(
            import_batch=batch,
            raw_text="No-op update",
            detected_type=ImportItem.DetectedType.BOOK,
            status=ImportItem.Status.READY,
            matched_existing_type="work",
            matched_existing_id="work-missing",
            comparison_json={"fields": [{"label": "Место издания", "existing": "СПб.", "source": "М.", "status": "different"}]},
        )
        apply_item_decision(item, ImportDecision.DecisionType.UPDATE_EXISTING, payload={"selected_fields": ["Место издания"]})

        validation = validate_import_batch(batch)

        self.assertTrue(any(item["code"] == "update_existing_no_applicable_fields" for item in validation["warnings"]))

    def test_validation_empty_group_after_split_warns(self):
        batch = ImportBatch.objects.create(title="Validation empty group", raw_input="")
        root = ImportEntity.objects.create(
            import_batch=batch,
            entity_type=ImportEntity.EntityType.JOURNAL_ISSUE,
            label="Новый бонист — 2024 — № 3",
            normalized_key="validation-empty-issue",
            status=ImportEntity.Status.WILL_CREATE,
        )
        group = ImportGroup.objects.create(
            import_batch=batch,
            group_type=ImportGroup.GroupType.JOURNAL_ISSUE_GROUP,
            label=root.label,
            root_entity=root,
            status=ImportGroup.Status.NEEDS_REVIEW,
        )

        validation = validate_import_batch(batch)

        self.assertTrue(any(item["code"] == "empty_group" and item["target_url"] == f"/imports/{batch.pk}/groups/{group.pk}/" for item in validation["warnings"]))

    def test_validation_high_score_match_plus_create_warns(self):
        batch = ImportBatch.objects.create(title="Validation high match", raw_input="")
        entity = ImportEntity.objects.create(
            import_batch=batch,
            entity_type=ImportEntity.EntityType.BOOK,
            label="Похожая книга",
            normalized_key="validation-high-match",
            status=ImportEntity.Status.WILL_CREATE,
        )
        batch.matches.create(entity=entity, existing_type="work", existing_id="work-missing", score=0.96)

        validation = validate_import_batch(batch)

        self.assertTrue(any(item["code"] == "high_score_match_created_new" for item in validation["warnings"]))

    def test_validation_clean_ready_import_has_ok_checks_and_no_blocking(self):
        batch = ImportBatch.objects.create(title="Validation clean", raw_input="")
        item = ImportItem.objects.create(
            import_batch=batch,
            raw_text="Уже есть",
            detected_type=ImportItem.DetectedType.BOOK,
            status=ImportItem.Status.READY,
        )
        apply_item_decision(item, ImportDecision.DecisionType.SKIP)

        validation = validate_import_batch(batch)
        plan = build_import_plan(batch)

        self.assertFalse(validation["blocking"])
        self.assertTrue(any(item["code"] == "items_have_state" for item in validation["ok"]))
        self.assertTrue(any(item["code"] == "backup_will_be_created" for item in validation["ok"]))
        self.assertTrue(plan["can_apply"])

    def test_standalone_group_does_not_block_after_book_linked_existing(self):
        batch = ImportBatch.objects.create(title="Standalone linked existing", raw_input="")
        book = ImportEntity.objects.create(
            import_batch=batch,
            entity_type=ImportEntity.EntityType.BOOK,
            label="Наша банковская политика",
            normalized_key="standalone-linked-existing-book",
            status=ImportEntity.Status.UNRESOLVED,
        )
        ImportGroup.objects.create(
            import_batch=batch,
            group_type=ImportGroup.GroupType.STANDALONE_BOOKS,
            label="Отдельные книги",
            status=ImportGroup.Status.NEEDS_REVIEW,
        )

        apply_entity_decision(book, ImportDecision.DecisionType.LINK_EXISTING, target_type="work", target_id="work-000001")
        validation = validate_import_batch(batch)
        plan = build_import_plan(batch)

        self.assertFalse(any(item["code"] == "group_needs_review" for item in validation["blocking"]))
        self.assertFalse(plan["preview"]["group_problem_rows"])
        self.assertTrue(plan["can_apply"])

    def test_plan_template_renders_validation_section(self):
        user = get_user_model().objects.create_user("validation_plan", password="x", is_staff=True)
        batch = ImportBatch.objects.create(title="Validation render", raw_input="")
        ImportEntity.objects.create(
            import_batch=batch,
            entity_type=ImportEntity.EntityType.AUTHOR,
            label="Иванов И.И.",
            normalized_key="validation-render-author",
            status=ImportEntity.Status.UNRESOLVED,
        )

        self.client.force_login(user)
        response = self.client.get(f"/imports/{batch.pk}/plan/")

        self.assertContains(response, "Проверка готовности")
        self.assertContains(response, "Блокирует применение")
        self.assertContains(response, "Есть автор без решения")
        self.assertContains(response, "Требует внимания")
        self.assertContains(response, "Готово")

    def test_successful_apply_redirects_to_result_page(self):
        user = get_user_model().objects.create_user("apply_redirect", password="x", is_staff=True)
        batch = ImportBatch.objects.create(title="Apply redirect", raw_input="")
        item = ImportItem.objects.create(
            import_batch=batch,
            raw_text="Уже есть",
            detected_type=ImportItem.DetectedType.BOOK,
            status=ImportItem.Status.READY,
        )
        apply_item_decision(item, ImportDecision.DecisionType.SKIP, user=user)

        self.client.force_login(user)
        with patch("sources.import_workflow.backup_sqlite_database", return_value="/tmp/editor.before-import-apply.sqlite"):
            response = self.client.post(f"/imports/{batch.pk}/apply/")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], f"/imports/{batch.pk}/result/")

    def test_result_page_renders_backup_path_and_summary_counts(self):
        user = get_user_model().objects.create_user("result_summary", password="x", is_staff=True)
        batch = ImportBatch.objects.create(title="Result summary", raw_input="source")
        ImportApplyLog.objects.create(
            import_batch=batch,
            applied_by=user,
            summary_json={"created": 2, "updated": 1, "update_noop": 1, "relations": 3, "backup_path": "/tmp/editor.backup.sqlite"},
            raw_input=batch.raw_input,
        )

        self.client.force_login(user)
        response = self.client.get(f"/imports/{batch.pk}/result/")

        self.assertContains(response, "Применён")
        self.assertContains(response, "/tmp/editor.backup.sqlite")
        self.assertContains(response, "Создано")
        self.assertContains(response, "2")
        self.assertContains(response, "Дополнено")
        self.assertContains(response, "1")

    def test_result_page_renders_created_entities_with_labels(self):
        user = get_user_model().objects.create_user("result_created", password="x", is_staff=True)
        batch = ImportBatch.objects.create(title="Result created", raw_input="")
        ImportApplyLog.objects.create(
            import_batch=batch,
            applied_by=user,
            summary_json={"created": 1, "updated": 0, "update_noop": 0, "relations": 0, "backup_path": "/tmp/backup.sqlite"},
            created_entities_json=[{"type": "book", "id": "work-999001", "label": "Временная тестовая книга"}],
        )

        self.client.force_login(user)
        response = self.client.get(f"/imports/{batch.pk}/result/")

        self.assertContains(response, "Книга")
        self.assertContains(response, "Временная тестовая книга")
        self.assertContains(response, "work-999001")

    def test_result_page_renders_updated_fields_and_skipped_fields(self):
        user = get_user_model().objects.create_user("result_updated", password="x", is_staff=True)
        batch = ImportBatch.objects.create(title="Result updated", raw_input="")
        ImportApplyLog.objects.create(
            import_batch=batch,
            applied_by=user,
            summary_json={"created": 0, "updated": 1, "update_noop": 0, "relations": 0, "backup_path": "/tmp/backup.sqlite"},
            updated_entities_json=[
                {
                    "type": "work",
                    "id": "work-999002",
                    "label": "Существующая запись",
                    "status": "updated",
                    "updated_fields": [{"model": "work", "field": "host_title", "value": "Сборник"}],
                    "skipped_fields": [{"label": "Год", "reason": "replacement_not_supported"}],
                }
            ],
        )

        self.client.force_login(user)
        response = self.client.get(f"/imports/{batch.pk}/result/")

        self.assertContains(response, "Существующая запись")
        self.assertContains(response, "work.host_title = Сборник")
        self.assertContains(response, "Год: replacement_not_supported")

    def test_result_page_handles_missing_apply_log_gracefully(self):
        user = get_user_model().objects.create_user("result_missing", password="x", is_staff=True)
        batch = ImportBatch.objects.create(title="No result", raw_input="")

        self.client.force_login(user)
        response = self.client.get(f"/imports/{batch.pk}/result/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Результата применения нет")

    def test_applied_batch_detail_shows_result_link(self):
        user = get_user_model().objects.create_user("result_link", password="x", is_staff=True)
        batch = ImportBatch.objects.create(title="Applied detail", raw_input="", status=ImportBatch.Status.APPLIED)
        ImportApplyLog.objects.create(
            import_batch=batch,
            applied_by=user,
            summary_json={"created": 0, "updated": 0, "update_noop": 0, "relations": 0, "backup_path": "/tmp/backup.sqlite"},
        )

        self.client.force_login(user)
        response = self.client.get(f"/imports/{batch.pk}/")

        self.assertContains(response, "Импорт применён")
        self.assertContains(response, "Показать результат применения")
        self.assertContains(response, f"/imports/{batch.pk}/result/")

    def test_work_inspect_requires_staff_login(self):
        work, *_ = self.make_journal_article_inspect_fixture()

        response = self.client.get(f"/works/{work.pk}/inspect/")

        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/login/", response["Location"])

    def test_work_inspect_page_renders_article_author_journal_and_issue(self):
        user = get_user_model().objects.create_user("inspect_staff", password="x", is_staff=True)
        work, author, journal, legacy_issue, periodical, target_issue, article, source, placement = self.make_journal_article_inspect_fixture()

        self.client.force_login(user)
        response = self.client.get(f"/works/{work.pk}/inspect/")

        self.assertContains(response, "Проверка связей записи")
        self.assertContains(response, "По поводу полемики о бумажных деньгах")
        self.assertContains(response, "Мец Н.")
        self.assertContains(response, "статья")
        self.assertContains(response, "Экономический журнал")
        self.assertContains(response, "Экономический журнал, 1887, № 8–9")
        self.assertContains(response, "article-placement-inspect")
        self.assertContains(response, "Редактировать Work")
        self.assertContains(response, "редактировать ArticlePlacement")

    def test_work_list_links_to_inspect_screen(self):
        user = get_user_model().objects.create_user("inspect_list", password="x", is_staff=True)
        work, *_ = self.make_journal_article_inspect_fixture()

        self.client.force_login(user)
        response = self.client.get("/works/?q=полемики")

        self.assertContains(response, "Проверить связи")
        self.assertContains(response, f"/works/{work.pk}/inspect/")

    def test_work_list_renders_editor_facing_bibliographic_description(self):
        user = get_user_model().objects.create_user("work_list_bib", password="x", is_staff=True)
        child, parent, author = self.make_article_in_book_inspect_fixture()

        self.client.force_login(user)
        response = self.client.get("/works/?q=Авчухов")

        self.assertContains(response, "Запись")
        self.assertContains(
            response,
            "Авчухов А.Ю. Боны Волгоградской области // Энциклопедия Волгоградской области. — Волгоград, 2008.",
        )
        self.assertContains(response, "Техническая строка")
        self.assertContains(response, "Название, автор, журнал, сборник, год, ID")
        self.assertNotContains(response, "publication_details")

    def test_work_list_renders_standalone_book_description_without_raw_parent_as_main_line(self):
        user = get_user_model().objects.create_user("work_list_book_bib", password="x", is_staff=True)
        work = Work.objects.create(
            work_id="work-list-book",
            source_number=7720,
            source_sequence=7720,
            source_section=self.section,
            language=self.language,
            work_type=Work.WorkType.BOOK,
            raw_author_string="Зерчанинов Л.",
            title="Комендант эшелона особого назначения",
            publication_details="___Революцией призванные.— Горький: Волго-Вятское из-во, 1987 — 1987",
            inferred_year=1987,
        )

        self.client.force_login(user)
        response = self.client.get("/works/?q=Комендант")

        self.assertContains(response, "Зерчанинов Л. Комендант эшелона особого назначения. — Горький: Волго-Вятское из-во, 1987.")
        self.assertContains(response, "Техническая строка")

    def test_issue_string_includes_periodical_year_and_number(self):
        work, author, journal, legacy_issue, periodical, target_issue, article, source, placement = self.make_journal_article_inspect_fixture()

        self.assertEqual(str(target_issue), "Экономический журнал, 1887, № 8–9")

    def test_work_inspect_warns_when_description_has_multiple_issues_but_one_placement(self):
        user = get_user_model().objects.create_user("inspect_warning", password="x", is_staff=True)
        work, *_ = self.make_journal_article_inspect_fixture(volume_number="№ 8–9; № 5–6")

        self.client.force_login(user)
        response = self.client.get(f"/works/{work.pk}/inspect/")

        self.assertContains(response, "В описании указано")
        self.assertContains(response, "№ 8–9; № 5–6")
        self.assertContains(response, "В базе связано")
        self.assertContains(response, "Возможная проблема: описание содержит несколько выпусков, но заведено одно размещение")

    def test_work_inspect_warns_for_article_without_placement(self):
        user = get_user_model().objects.create_user("inspect_no_placement", password="x", is_staff=True)
        work, *_ = self.make_journal_article_inspect_fixture(with_placement=False, volume_number="№ 8–9")

        self.client.force_login(user)
        response = self.client.get(f"/works/{work.pk}/inspect/")

        self.assertContains(response, "Статья не связана с выпуском или сборником.")
        self.assertContains(response, "Target-запись статьи не имеет ArticlePlacement.")

    def test_multi_issue_split_helper_recognizes_economic_journal_example(self):
        result = split_multi_issue_bibliographic_line(
            "Мец Н. По поводу полемики о бумажных деньгах // Экономический журнал. — 1887. — № 8–9; 1888. — № 5–6."
        )

        self.assertTrue(result["can_split"])
        self.assertEqual(
            result["suggested_lines"],
            [
                "Мец Н. По поводу полемики о бумажных деньгах // Экономический журнал. — 1887. — № 8–9.",
                "Мец Н. По поводу полемики о бумажных деньгах // Экономический журнал. — 1888. — № 5–6.",
            ],
        )
        self.assertEqual(result["issues"][0], {"journal": "Экономический журнал", "year": "1887", "issue": "8–9"})

    def test_multi_issue_split_helper_ignores_single_issue(self):
        result = split_multi_issue_bibliographic_line(
            "Мец Н. По поводу полемики о бумажных деньгах // Экономический журнал. — 1887. — № 8–9."
        )

        self.assertFalse(result["can_split"])

    def test_multi_issue_split_helper_ignores_unclear_second_part(self):
        result = split_multi_issue_bibliographic_line(
            "Мец Н. По поводу полемики о бумажных деньгах // Экономический журнал. — 1887. — № 8–9; дополнение редакции."
        )

        self.assertFalse(result["can_split"])

    def test_work_inspect_shows_multi_issue_split_block(self):
        user = get_user_model().objects.create_user("inspect_split", password="x", is_staff=True)
        work, *_ = self.make_journal_article_inspect_fixture(volume_number="№ 8–9; № 5–6")

        self.client.force_login(user)
        response = self.client.get(f"/works/{work.pk}/inspect/")

        self.assertContains(response, "Можно разделить исходную строку")
        self.assertContains(response, "База не меняется")
        self.assertContains(response, "Мец Н. По поводу полемики о бумажных деньгах // Экономический журнал. — 1887. — № 8–9.")
        self.assertContains(response, "Мец Н. По поводу полемики о бумажных деньгах // Экономический журнал. — 1888. — № 5–6.")
        self.assertContains(response, "Создать новый импорт")

    def test_work_inspect_does_not_show_split_block_for_single_issue_article(self):
        user = get_user_model().objects.create_user("inspect_no_split", password="x", is_staff=True)
        work, *_ = self.make_journal_article_inspect_fixture(volume_number="№ 8–9")

        self.client.force_login(user)
        response = self.client.get(f"/works/{work.pk}/inspect/")

        self.assertNotContains(response, "Можно разделить исходную строку")

    def test_work_relations_requires_staff_login(self):
        child, container, author = self.make_book_container_relation_fixture()

        response = self.client.get(f"/works/{child.pk}/relations/")

        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/login/", response["Location"])

    def test_work_relations_page_extracts_parent_and_suggests_container(self):
        user = get_user_model().objects.create_user("relations_staff", password="x", is_staff=True)
        child, container, author = self.make_book_container_relation_fixture()

        self.client.force_login(user)
        response = self.client.get(f"/works/{child.pk}/relations/")

        self.assertContains(response, "Редактирование связей записи")
        self.assertContains(response, "Тип записи")
        self.assertContains(response, "самостоятельная книга")
        self.assertContains(response, "Акционерные и паевые общества")
        self.assertContains(response, "Голицын Ю.П.")
        self.assertContains(response, "Страницы/место в контейнере")
        self.assertContains(response, "31")
        self.assertContains(response, "В описании найдена родительская запись")
        self.assertContains(response, "История России XIX-XX веков в облигациях")
        self.assertContains(response, "work-044508")
        self.assertContains(response, "История России XIX-XX веков в акциях, паях и облигациях")
        self.assertContains(response, "Год совпадает: 2017")
        self.assertContains(response, "Часть совпадает: Ч.3. Т.1")
        self.assertContains(response, "Название родительской записи в описании отличается")
        self.assertContains(response, "Что будет изменено")
        self.assertContains(response, "Связать как статью/раздел")
        self.assertContains(response, "work-044506")
        self.assertContains(response, "work-044509")

    def test_work_relations_post_creates_article_links_container_and_fills_pages(self):
        user = get_user_model().objects.create_user("relations_post", password="x", is_staff=True)
        child, container, author = self.make_book_container_relation_fixture()

        self.client.force_login(user)
        with patch("sources.views.backup_sqlite_database", return_value="/tmp/editor.before-work-container-link.sqlite") as backup_mock:
            response = self.client.post(
                f"/works/{child.pk}/relations/",
                {"target_container_id": container.pk},
                follow=True,
            )

        self.assertContains(response, "связана с книгой work-044508")
        self.assertContains(response, "Уже связано с этим контейнером")
        backup_mock.assert_called_once_with("before-work-container-link")
        child.refresh_from_db()
        article = Article.objects.get(work=child)
        self.assertEqual(child.work_type, Work.WorkType.ARTICLE)
        self.assertEqual(article.container_work_id, container.pk)
        self.assertEqual(article.pages, "31")
        self.assertEqual(article.pages_raw, "31")

    def test_work_relations_post_does_not_overwrite_existing_different_container(self):
        user = get_user_model().objects.create_user("relations_conflict", password="x", is_staff=True)
        child, container, author = self.make_book_container_relation_fixture()
        other = Work.objects.create(
            work_id="work-other-container",
            source_number=9991,
            source_sequence=9991,
            source_section=self.section,
            language=self.language,
            work_type=Work.WorkType.BOOK,
            title="Другой контейнер",
            inferred_year=2017,
        )
        Article.objects.create(article_id="article-existing-container", work=child, container_work=other)

        self.client.force_login(user)
        with patch("sources.views.backup_sqlite_database") as backup_mock:
            response = self.client.post(
                f"/works/{child.pk}/relations/",
                {"target_container_id": container.pk},
                follow=True,
            )

        self.assertContains(response, "уже связана с другим контейнером")
        backup_mock.assert_not_called()
        self.assertEqual(Article.objects.get(work=child).container_work_id, other.pk)

    def test_work_relations_post_rejects_self_link(self):
        user = get_user_model().objects.create_user("relations_self", password="x", is_staff=True)
        child, container, author = self.make_book_container_relation_fixture()

        self.client.force_login(user)
        with patch("sources.views.backup_sqlite_database") as backup_mock:
            response = self.client.post(
                f"/works/{child.pk}/relations/",
                {"target_container_id": child.pk},
                follow=True,
            )

        self.assertContains(response, "нельзя связать сама с собой")
        backup_mock.assert_not_called()
        self.assertFalse(Article.objects.filter(work=child).exists())

    def test_work_inspect_links_to_relations_editor(self):
        user = get_user_model().objects.create_user("inspect_relations_link", password="x", is_staff=True)
        child, container, author = self.make_book_container_relation_fixture()

        self.client.force_login(user)
        response = self.client.get(f"/works/{child.pk}/inspect/")

        self.assertContains(response, "Редактировать связи")
        self.assertContains(response, f"/works/{child.pk}/relations/")

    def test_work_relations_page_shows_create_parent_block_when_no_candidate(self):
        user = get_user_model().objects.create_user("relations_create_block", password="x", is_staff=True)
        child, author = self.make_missing_parent_relation_fixture()

        self.client.force_login(user)
        response = self.client.get(f"/works/{child.pk}/relations/")

        self.assertContains(response, "Подходящий контейнер не найден автоматически.")
        self.assertContains(response, "Родительская запись не найдена")
        self.assertContains(response, "Можно создать новую родительскую запись")
        self.assertContains(response, 'name="parent_title" value="Энциклопедия Волгоградской области"', html=False)
        self.assertContains(response, 'name="parent_publication_place" value="Волгоград"', html=False)
        self.assertContains(response, 'name="parent_publication_date" value="2008"', html=False)
        self.assertContains(response, "Родительская книга / сборник")
        self.assertContains(response, "Создать родительскую запись и связать")
        self.assertContains(response, "Исходная запись не удаляется")

    def test_parent_fragment_parser_extracts_title_place_and_year(self):
        parsed = parse_parent_fragment("Энциклопедия Волгоградской области.— Волгоград, 2008 — 2008")

        self.assertEqual(parsed["title"], "Энциклопедия Волгоградской области")
        self.assertEqual(parsed["publication_place"], "Волгоград")
        self.assertEqual(parsed["year"], 2008)

    def test_parent_fragment_parser_extracts_publisher_from_parent_fragment(self):
        parsed = parse_parent_fragment("Революцией призванные.— Горький: Волго-Вятское из-во, 1987 — 1987")

        self.assertEqual(parsed["title"], "Революцией призванные")
        self.assertEqual(parsed["publication_place"], "Горький")
        self.assertEqual(parsed["publisher"], "Волго-Вятское из-во")
        self.assertEqual(parsed["year"], 1987)

    def test_work_relations_renders_current_bibliographic_record_and_vertical_parent_form(self):
        user = get_user_model().objects.create_user("relations_current_bib", password="x", is_staff=True)
        work = Work.objects.create(
            work_id="work-relations-current",
            source_number=7721,
            source_sequence=7721,
            source_section=self.section,
            language=self.language,
            work_type=Work.WorkType.BOOK,
            raw_author_string="Зерчанинов Л.",
            title="Комендант эшелона особого назначения",
            publication_details="___Революцией призванные.— Горький: Волго-Вятское из-во, 1987 — 1987",
            inferred_year=1987,
        )

        self.client.force_login(user)
        response = self.client.get(f"/works/{work.pk}/relations/")

        self.assertContains(response, "Текущая библиографическая запись")
        self.assertContains(response, "Зерчанинов Л. Комендант эшелона особого назначения. — Горький: Волго-Вятское из-во, 1987.")
        self.assertContains(response, "В описании также найден возможный родитель: Революцией призванные.")
        self.assertContains(response, 'class="parent-draft-form"', html=False)
        self.assertContains(response, 'name="parent_title" value="Революцией призванные"', html=False)
        self.assertContains(response, 'name="parent_publication_place" value="Горький"', html=False)
        self.assertContains(response, 'name="parent_publisher" value="Волго-Вятское из-во"', html=False)
        self.assertContains(response, 'name="parent_publication_date" value="1987"', html=False)

    def test_work_relations_create_parent_post_creates_parent_source_book_and_links_child(self):
        user = get_user_model().objects.create_user("relations_create_post", password="x", is_staff=True)
        child, author = self.make_missing_parent_relation_fixture()

        self.client.force_login(user)
        with patch("sources.views.backup_sqlite_database", return_value="/tmp/editor.before-create-parent.sqlite") as backup_mock:
            response = self.client.post(
                f"/works/{child.pk}/relations/",
                {
                    "action": "create_parent_and_link",
                    "parent_title": "Энциклопедия Волгоградской области",
                    "parent_publication_place": "Волгоград",
                    "parent_publication_date": "2008",
                    "parent_work_type": "book",
                },
                follow=True,
            )

        self.assertContains(response, "Создана родительская запись")
        backup_mock.assert_called_once_with("before-create-parent-container-link")
        child.refresh_from_db()
        article = Article.objects.get(work=child)
        parent = article.container_work
        self.assertEqual(child.work_type, Work.WorkType.ARTICLE)
        self.assertEqual(parent.title, "Энциклопедия Волгоградской области")
        self.assertEqual(parent.publication_place, "Волгоград")
        self.assertEqual(parent.publication_date, "2008")
        self.assertEqual(parent.inferred_year, 2008)
        self.assertEqual(parent.work_type, Work.WorkType.BOOK)
        self.assertFalse(parent.is_container)
        self.assertTrue(Book.objects.filter(work=parent).exists())
        source = Source.objects.get(legacy_work=parent)
        self.assertEqual(source.source_id, parent.work_id)
        self.assertEqual(source.title, parent.title)
        self.assertEqual(source.publication_place, "Волгоград")
        self.assertEqual(source.inferred_year, 2008)
        self.assertEqual(article.container_work_id, parent.pk)
        self.assertEqual(child.title, "Боны Волгоградской области")
        self.assertEqual(list(child.authors.values_list("display_name", flat=True)), ["Авчухов А.Ю."])
        self.assertContains(response, "Родительская запись")
        self.assertContains(response, "Энциклопедия Волгоградской области")
        self.assertNotContains(response, "Создать родительскую запись и связать")

    def test_work_relations_create_parent_rejects_empty_title(self):
        user = get_user_model().objects.create_user("relations_empty_parent", password="x", is_staff=True)
        child, author = self.make_missing_parent_relation_fixture()

        self.client.force_login(user)
        with patch("sources.views.backup_sqlite_database") as backup_mock:
            response = self.client.post(
                f"/works/{child.pk}/relations/",
                {"action": "create_parent_and_link", "parent_title": "", "parent_publication_date": "2008"},
                follow=True,
            )

        self.assertContains(response, "Укажите название родительской записи")
        backup_mock.assert_not_called()
        self.assertFalse(Article.objects.filter(work=child).exists())

    def test_work_relations_create_parent_blocks_duplicate_title_and_year(self):
        user = get_user_model().objects.create_user("relations_duplicate_parent", password="x", is_staff=True)
        child, author = self.make_missing_parent_relation_fixture()
        Work.objects.create(
            work_id="work-existing-parent",
            source_number=8881,
            source_sequence=8881,
            source_section=self.section,
            language=self.language,
            work_type=Work.WorkType.BOOK,
            title="Энциклопедия Волгоградской области",
            publication_place="Волгоград",
            publication_date="2008",
            inferred_year=2008,
        )

        self.client.force_login(user)
        with patch("sources.views.backup_sqlite_database") as backup_mock:
            response = self.client.post(
                f"/works/{child.pk}/relations/",
                {
                    "action": "create_parent_and_link",
                    "parent_title": "Энциклопедия Волгоградской области",
                    "parent_publication_place": "Волгоград",
                    "parent_publication_date": "2008",
                },
                follow=True,
            )

        self.assertContains(response, "Похоже, такая родительская запись уже есть")
        self.assertContains(response, "work-existing-parent")
        backup_mock.assert_not_called()
        self.assertFalse(Article.objects.filter(work=child).exists())

    def test_work_relations_create_parent_refuses_existing_different_parent(self):
        user = get_user_model().objects.create_user("relations_existing_parent", password="x", is_staff=True)
        child, author = self.make_missing_parent_relation_fixture()
        existing_parent = Work.objects.create(
            work_id="work-current-parent",
            source_number=8882,
            source_sequence=8882,
            source_section=self.section,
            language=self.language,
            work_type=Work.WorkType.BOOK,
            title="Текущий контейнер",
        )
        Article.objects.create(article_id="article-current-parent", work=child, container_work=existing_parent)

        self.client.force_login(user)
        with patch("sources.views.backup_sqlite_database") as backup_mock:
            response = self.client.post(
                f"/works/{child.pk}/relations/",
                {
                    "action": "create_parent_and_link",
                    "parent_title": "Энциклопедия Волгоградской области",
                    "parent_publication_date": "2008",
                },
                follow=True,
            )

        self.assertContains(response, "уже связана с родительской записью")
        backup_mock.assert_not_called()
        self.assertEqual(Article.objects.get(work=child).container_work_id, existing_parent.pk)

    def test_work_inspect_renders_article_in_book_bibliographic_description(self):
        user = get_user_model().objects.create_user("inspect_book_article", password="x", is_staff=True)
        child, parent, author = self.make_article_in_book_inspect_fixture()

        self.client.force_login(user)
        response = self.client.get(f"/works/{child.pk}/inspect/")

        self.assertContains(response, "Библиографическое описание")
        self.assertContains(
            response,
            "Авчухов А.Ю. Боны Волгоградской области // Энциклопедия Волгоградской области. — Волгоград, 2008.",
        )
        self.assertContains(response, "Размещение в книге или сборнике")
        self.assertContains(response, "work-parent-book")
        self.assertNotContains(response, "Журнальное размещение")
        self.assertContains(response, "Исходная техническая строка")

    def test_work_relations_detects_journal_title_with_issue_suffix(self):
        user = get_user_model().objects.create_user("relations_journal_split", password="x", is_staff=True)
        work, article, wrong_journal, wrong_issue, wrong_periodical, wrong_target_issue, placement, context_work, author = self.make_wrong_journal_issue_fixture()

        self.client.force_login(user)
        response = self.client.get(f"/works/{work.pk}/relations/")

        self.assertContains(response, "Название журнала похоже содержит номер выпуска")
        self.assertContains(response, "Журнал: <strong>Разыскания.— Вып. 7</strong>", html=True)
        self.assertContains(response, "Журнал: <strong>Разыскания</strong>", html=True)
        self.assertContains(response, "Выпуск: <strong>Вып. 7</strong>", html=True)
        self.assertContains(response, "Год: <strong>2007</strong>", html=True)
        self.assertContains(response, "Похожая родительская запись")
        self.assertContains(response, "work-049299")
        self.assertContains(response, "Разделить журнал и выпуск")

    def test_work_relations_split_journal_issue_post_moves_article_and_placement(self):
        user = get_user_model().objects.create_user("relations_journal_split_post", password="x", is_staff=True)
        work, article, wrong_journal, wrong_issue, wrong_periodical, wrong_target_issue, placement, context_work, author = self.make_wrong_journal_issue_fixture()

        self.client.force_login(user)
        with patch("sources.views.backup_sqlite_database", return_value="/tmp/editor.before-split-journal.sqlite") as backup_mock:
            response = self.client.post(
                f"/works/{work.pk}/relations/",
                {"action": "split_journal_issue_title"},
                follow=True,
            )

        self.assertContains(response, "Журнал и выпуск разделены")
        backup_mock.assert_called_once_with("before-split-journal-issue-title")
        article.refresh_from_db()
        placement.refresh_from_db()
        self.assertEqual(article.work.title, "Денежные знаки Мариинского и Томского уездов")
        self.assertEqual(list(article.work.authors.values_list("display_name", flat=True)), ["Рогов Г.И."])
        self.assertEqual(article.journal_issue.journal.title, "Разыскания")
        self.assertEqual(article.journal_issue.issue_number, "Вып. 7")
        self.assertEqual(article.journal_issue.year, 2007)
        self.assertEqual(placement.issue.periodical.title, "Разыскания")
        self.assertEqual(placement.issue.issue_number, "Вып. 7")
        self.assertEqual(placement.issue.year, 2007)
        self.assertTrue(Journal.objects.filter(pk=wrong_journal.pk).exists())
        self.assertTrue(Periodical.objects.filter(pk=wrong_periodical.pk).exists())

    def test_issue_to_collection_preview_shows_candidate_and_articles(self):
        user = get_user_model().objects.create_user("issue_collection_preview", password="x", is_staff=True)
        work, article, legacy_issue, target_issue, placement, periodical = self.make_issue_to_collection_fixture()

        plan = build_issue_to_collection_plan(legacy_issue)
        self.assertTrue(plan["can_apply"])
        self.assertEqual(plan["collection"]["title"], "Краеведение и музей")
        self.assertEqual(plan["collection"]["publication_place"], "Петрозаводск")
        self.assertEqual(plan["collection"]["year"], 1992)
        self.assertEqual(len(plan["articles"]), 1)
        self.assertEqual(plan["placement_count"], 1)

        self.client.force_login(user)
        response = self.client.get(f"/issues/{legacy_issue.pk}/convert-to-collection/")

        self.assertContains(response, "Преобразовать выпуск журнала в сборник")
        self.assertContains(response, "Краеведение и музей. — Петрозаводск, 1992")
        self.assertContains(response, "Из истории бон первых лет советской власти")
        self.assertContains(response, "129–135")

    def test_issue_to_collection_apply_creates_collection_and_moves_article_and_placement(self):
        work, article, legacy_issue, target_issue, placement, periodical = self.make_issue_to_collection_fixture()

        with patch("sources.issue_collection_conversion.backup_sqlite_database", return_value="/tmp/editor.before-issue.sqlite") as backup_mock, patch(
            "sources.issue_collection_conversion.write_operation_report", return_value="/tmp/issue-report.json"
        ):
            result = apply_issue_to_collection(legacy_issue)

        self.assertEqual(result["error"], "")
        backup_mock.assert_called_once_with("before-issue-to-collection")
        article.refresh_from_db()
        placement.refresh_from_db()
        self.assertIsNone(article.journal_issue_id)
        self.assertIsNone(article.collection_id)
        self.assertIsNotNone(article.container_work_id)
        self.assertEqual(article.container_work.title, "Краеведение и музей")
        self.assertEqual(article.container_work.publication_place, "Петрозаводск")
        self.assertEqual(article.container_work.inferred_year, 1992)
        self.assertEqual(placement.issue.issue_type, Issue.IssueType.COLLECTION)
        self.assertEqual(placement.issue.legacy_container_work_id, article.container_work_id)
        self.assertTrue(JournalIssue.objects.filter(pk=legacy_issue.pk).exists())
        self.assertTrue(Journal.objects.filter(pk=legacy_issue.journal_id).exists())

    def test_issue_to_collection_apply_reuses_existing_collection(self):
        work, article, legacy_issue, target_issue, placement, periodical = self.make_issue_to_collection_fixture()
        existing = Work.objects.create(
            work_id="work-existing-collection",
            source_number=99001,
            source_section=self.section,
            language=self.language,
            work_type=Work.WorkType.CONTAINER,
            is_container=True,
            title="Краеведение и музей",
            publication_place="Петрозаводск",
            publication_date="1992",
            inferred_year=1992,
        )

        with patch("sources.issue_collection_conversion.backup_sqlite_database", return_value="/tmp/editor.before-issue.sqlite"), patch(
            "sources.issue_collection_conversion.write_operation_report", return_value="/tmp/issue-report.json"
        ):
            result = apply_issue_to_collection(legacy_issue)

        self.assertEqual(result["error"], "")
        article.refresh_from_db()
        self.assertEqual(article.container_work_id, existing.work_id)
        self.assertEqual(Work.objects.filter(title="Краеведение и музей", is_container=True).count(), 1)

    def test_issue_to_collection_ambiguous_collection_candidates_block_apply(self):
        work, article, legacy_issue, target_issue, placement, periodical = self.make_issue_to_collection_fixture()
        for index in range(2):
            Work.objects.create(
                work_id=f"work-ambiguous-collection-{index}",
                source_number=99010 + index,
                source_section=self.section,
                language=self.language,
                work_type=Work.WorkType.CONTAINER,
                is_container=True,
                title="Краеведение и музей",
                publication_place="Петрозаводск",
                publication_date="1992",
                inferred_year=1992,
            )

        plan = build_issue_to_collection_plan(legacy_issue)
        self.assertFalse(plan["can_apply"])
        self.assertIn("Найдено несколько похожих сборников", " ".join(plan["blockers"]))
        with patch("sources.issue_collection_conversion.backup_sqlite_database") as backup_mock:
            result = apply_issue_to_collection(legacy_issue)

        backup_mock.assert_not_called()
        self.assertTrue(result["error"])
        article.refresh_from_db()
        placement.refresh_from_db()
        self.assertEqual(article.journal_issue_id, legacy_issue.pk)
        self.assertEqual(placement.issue_id, target_issue.pk)

    def test_work_relations_page_links_to_issue_to_collection_workflow(self):
        user = get_user_model().objects.create_user("issue_collection_link", password="x", is_staff=True)
        work, article, legacy_issue, target_issue, placement, periodical = self.make_issue_to_collection_fixture()

        self.client.force_login(user)
        response = self.client.get(f"/works/{work.pk}/relations/")

        self.assertContains(response, "Преобразовать выпуск в сборник")
        self.assertContains(response, f"/issues/{legacy_issue.pk}/convert-to-collection/")

    def test_editor_journal_list_links_to_journal_and_issue_detail(self):
        user = get_user_model().objects.create_user("journal_browser", password="x", is_staff=True)
        work, article, legacy_issue, target_issue, placement, periodical = self.make_issue_to_collection_fixture()

        self.client.force_login(user)
        list_response = self.client.get("/periodicals/?q=Краеведение")
        self.assertContains(list_response, "Краеведение и музей")
        self.assertContains(list_response, "Открыть")
        self.assertContains(list_response, f"/journals/{legacy_issue.journal_id}/")

        journal_response = self.client.get(f"/journals/{legacy_issue.journal_id}/")
        self.assertContains(journal_response, "Журнал: Краеведение и музей")
        self.assertContains(journal_response, "Похоже на сборник")
        self.assertContains(journal_response, f"/journals/{legacy_issue.journal_id}/issues/{legacy_issue.pk}/")

    def test_editor_journal_issue_detail_lists_articles_and_convert_action(self):
        user = get_user_model().objects.create_user("journal_issue_browser", password="x", is_staff=True)
        work, article, legacy_issue, target_issue, placement, periodical = self.make_issue_to_collection_fixture()

        self.client.force_login(user)
        response = self.client.get(f"/journals/{legacy_issue.journal_id}/issues/{legacy_issue.pk}/")

        self.assertContains(response, "Краеведение и музей, 1992")
        self.assertContains(response, "Из истории бон первых лет советской власти")
        self.assertContains(response, "129–135")
        self.assertContains(response, "Преобразовать выпуск в сборник")
        self.assertContains(response, f"/issues/{legacy_issue.pk}/convert-to-collection/")

    def test_work_inspect_after_journal_split_shows_normalized_issue_label(self):
        user = get_user_model().objects.create_user("inspect_journal_after_split", password="x", is_staff=True)
        work, article, wrong_journal, wrong_issue, wrong_periodical, wrong_target_issue, placement, context_work, author = self.make_wrong_journal_issue_fixture()
        with patch("sources.views.backup_sqlite_database", return_value="/tmp/editor.before-split-journal.sqlite"):
            split_journal_issue_title_for_work(work, build_work_relations_context(work))

        self.client.force_login(user)
        response = self.client.get(f"/works/{work.pk}/inspect/")

        self.assertContains(response, "Разыскания, 2007, Вып. 7")
        self.assertNotContains(response, "№ Вып. 7")

    def test_item_detail_for_unresolved_book_weak_match_renders_main_decision(self):
        user = get_user_model().objects.create_user("weak_item_detail", password="x", is_staff=True)
        batch, item, book_entity, author_entity, existing_work = self.make_weak_book_match_import()

        self.client.force_login(user)
        response = self.client.get(f"/imports/{batch.pk}/items/{item.pk}/")

        self.assertContains(response, "Осталось решить: книга похожа на существующую запись")
        self.assertContains(response, "Совпадение найдено, но оно недостаточно уверенное")
        self.assertContains(response, "Мигулин П.П. — Наша банковская политика")
        self.assertContains(response, "Совпадение: 84%")
        self.assertContains(response, "Да, связать с найденной записью")
        self.assertContains(response, "Нет, создать новую запись")
        self.assertContains(response, "Отложить решение")
        self.assertNotContains(response, "Изменить: создать новую")

    def test_item_detail_separates_already_linked_author_from_main_decision(self):
        user = get_user_model().objects.create_user("weak_author_block", password="x", is_staff=True)
        batch, item, book_entity, author_entity, existing_work = self.make_weak_book_match_import()

        self.client.force_login(user)
        response = self.client.get(f"/imports/{batch.pk}/items/{item.pk}/")

        self.assertContains(response, "Авторы")
        self.assertContains(response, "Автор уже связан")
        self.assertContains(response, "Мигулин П.П.")
        self.assertContains(response, "Изменить решение по автору")

    def test_posting_primary_link_action_links_book_entity(self):
        user = get_user_model().objects.create_user("weak_link_action", password="x", is_staff=True)
        batch, item, book_entity, author_entity, existing_work = self.make_weak_book_match_import()

        self.client.force_login(user)
        response = self.client.post(
            f"/imports/{batch.pk}/entities/{book_entity.pk}/decision/",
            {
                "decision_type": "link_existing",
                "target_type": "work",
                "target_id": existing_work.work_id,
                "next": f"/imports/{batch.pk}/items/{item.pk}/",
            },
        )
        book_entity.refresh_from_db()

        self.assertEqual(response.status_code, 302)
        self.assertEqual(book_entity.status, ImportEntity.Status.LINKED_EXISTING)
        self.assertEqual(book_entity.matched_existing_id, existing_work.work_id)

    def test_review_page_shows_weak_match_label_and_action(self):
        user = get_user_model().objects.create_user("weak_review", password="x", is_staff=True)
        batch, item, book_entity, author_entity, existing_work = self.make_weak_book_match_import()

        self.client.force_login(user)
        response = self.client.get(f"/imports/{batch.pk}/review/")

        self.assertContains(response, "Похожа на запись в базе")
        self.assertContains(response, "Найдена похожая запись, подтвердите совпадение")
        self.assertContains(response, "Мигулин П.П. — Наша банковская политика")
        self.assertContains(response, "84%")
        self.assertContains(response, "Проверить запись")

    def test_item_detail_for_new_record_without_candidates_explains_create_plan(self):
        user = get_user_model().objects.create_user("new_no_candidate", password="x", is_staff=True)
        batch = ImportBatch.objects.create(
            title="No candidate import",
            raw_input="Петров П.П. Совершенно новая книга без совпадений. — М., 2026. — 10 с.",
        )
        parse_import_batch(batch)
        item = batch.items.get()

        self.client.force_login(user)
        response = self.client.get(f"/imports/{batch.pk}/items/{item.pk}/")

        self.assertContains(response, "Будет создана новая запись")
        self.assertContains(response, "Похожих записей в базе не найдено")
        self.assertContains(response, "При применении импорта эта строка будет создана как новая запись")

    def test_applied_item_page_does_not_show_main_decision_buttons(self):
        user = get_user_model().objects.create_user("weak_applied", password="x", is_staff=True)
        batch, item, book_entity, author_entity, existing_work = self.make_weak_book_match_import(batch_status=ImportBatch.Status.APPLIED)
        ImportApplyLog.objects.create(
            import_batch=batch,
            applied_by=user,
            summary_json={"created": 0, "updated": 0, "update_noop": 0, "relations": 0, "backup_path": "/tmp/backup.sqlite"},
        )

        self.client.force_login(user)
        response = self.client.get(f"/imports/{batch.pk}/items/{item.pk}/")

        self.assertContains(response, "Импорт уже применён")
        self.assertNotContains(response, "Да, связать с найденной записью")
        self.assertNotContains(response, "Нет, создать новую запись")

    def test_manual_parsed_field_post_updates_parsed_data_and_rebuilds_entities(self):
        user = get_user_model().objects.create_user("manual_parse_update", password="x", is_staff=True)
        batch = ImportBatch.objects.create(title="Manual parse", raw_input="Г.Б.К. Деньги. — СПб., 1900.")
        item = ImportItem.objects.create(
            import_batch=batch,
            raw_text="Г.Б.К. Деньги. — СПб., 1900.",
            detected_type=ImportItem.DetectedType.BOOK,
            status=ImportItem.Status.PARSED,
            parsed_data_json={"authors": [], "title": "Г.Б.К. Деньги", "year": "1900"},
        )
        old_entity = ImportEntity.objects.create(
            import_batch=batch,
            entity_type=ImportEntity.EntityType.BOOK,
            label="Г.Б.К. Деньги",
            normalized_key="old-manual-book",
            data_json={"item_id": item.id, "title": "Г.Б.К. Деньги"},
            status=ImportEntity.Status.WILL_CREATE,
        )

        self.client.force_login(user)
        response = self.client.post(
            f"/imports/{batch.pk}/items/{item.pk}/parse-edit/",
            {
                "parsed_authors": "Г.Б.К.",
                "parsed_title": "Деньги",
                "parsed_title_remainder": "",
                "parsed_responsibility_statement": "",
                "parsed_edition_statement": "",
                "parsed_publication_place": "СПб",
                "parsed_publisher": "",
                "parsed_year": "1900",
                "parsed_extent": "",
                "parsed_dimensions": "",
                "parsed_notes": "",
            },
        )
        item.refresh_from_db()

        self.assertEqual(response.status_code, 302)
        self.assertEqual(item.parsed_data_json["authors"], ["Г.Б.К."])
        self.assertEqual(item.parsed_data_json["title"], "Деньги")
        self.assertFalse(ImportEntity.objects.filter(pk=old_entity.pk).exists())
        self.assertTrue(ImportEntity.objects.filter(import_batch=batch, label="Деньги", data_json__item_id=item.id).exists())

    def test_comparison_source_post_updates_parsed_field(self):
        user = get_user_model().objects.create_user("comparison_parse_update", password="x", is_staff=True)
        batch = ImportBatch.objects.create(title="Comparison parse", raw_input="Книга. — 10 с.")
        item = ImportItem.objects.create(
            import_batch=batch,
            raw_text="Книга. — 10 с.",
            detected_type=ImportItem.DetectedType.BOOK,
            status=ImportItem.Status.FOUND_EXISTING_WITH_DIFFERENCES,
            parsed_data_json={"authors": [], "title": "Книга", "extent": "10 с."},
            comparison_json={"fields": [{"label": "Страницы", "existing": "8 с.", "source": "10 с.", "status": "different"}]},
        )

        self.client.force_login(user)
        response = self.client.post(
            f"/imports/{batch.pk}/items/{item.pk}/parse-edit/",
            {"comparison_extent": "12 с."},
        )
        item.refresh_from_db()

        self.assertEqual(response.status_code, 302)
        self.assertEqual(item.parsed_data_json["extent"], "12 с")

    def test_manual_parsed_field_post_reruns_matching_for_item(self):
        user = get_user_model().objects.create_user("manual_parse_match", password="x", is_staff=True)
        author = Author.objects.create(author_id="author-manual-match", display_name="Г.Б.К.", sort_name="Г.Б.К.")
        work = Work.objects.create(
            work_id="work-manual-match",
            source_number=93001,
            source_sequence=93001,
            source_section=self.section,
            language=self.language,
            work_type=Work.WorkType.BOOK,
            title="Деньги",
            publication_place="СПб",
            publication_date="1900",
            inferred_year=1900,
        )
        WorkAuthor.objects.create(work=work, author=author, sort_order=1)
        batch = ImportBatch.objects.create(title="Manual parse match", raw_input="Г.Б.К. Деньги. — СПб., 1900.")
        item = ImportItem.objects.create(
            import_batch=batch,
            raw_text="Г.Б.К. Деньги. — СПб., 1900.",
            detected_type=ImportItem.DetectedType.BOOK,
            status=ImportItem.Status.PARSED,
            parsed_data_json={"authors": [], "title": "Г.Б.К. Деньги", "year": "1900"},
        )

        self.client.force_login(user)
        self.client.post(
            f"/imports/{batch.pk}/items/{item.pk}/parse-edit/",
            {
                "parsed_authors": "Г.Б.К.",
                "parsed_title": "Деньги",
                "parsed_title_remainder": "",
                "parsed_responsibility_statement": "",
                "parsed_edition_statement": "",
                "parsed_publication_place": "СПб",
                "parsed_publisher": "",
                "parsed_year": "1900",
                "parsed_extent": "",
                "parsed_dimensions": "",
                "parsed_notes": "",
            },
        )
        item.refresh_from_db()

        self.assertEqual(item.status, ImportItem.Status.FOUND_EXISTING_NO_CHANGES)
        self.assertEqual(item.matched_existing_id, work.work_id)
        self.assertFalse(ImportEntity.objects.filter(import_batch=batch, data_json__item_id=item.id).exists())

    def test_collection_article_comparison_uses_container_work_title(self):
        parent = Work.objects.create(
            work_id="work-comparison-parent",
            source_number=93011,
            source_sequence=93011,
            source_section=self.section,
            language=self.language,
            work_type=Work.WorkType.CONTAINER,
            title="Сб. стат. сведений о России",
        )
        child = Work.objects.create(
            work_id="work-comparison-child",
            source_number=93012,
            source_sequence=93012,
            source_section=self.section,
            language=self.language,
            work_type=Work.WorkType.ARTICLE,
            title="Исторический очерк денежного обращения России с 1650 по 1817 г.",
        )
        Article.objects.create(article_id="article-comparison-child", work=child, container_work=parent)

        comparison = compare_work_to_parsed_data(child, {"title": child.title, "collection_title": "Сб. стат. сведений о России"})
        parent_row = next(row for row in comparison["fields"] if row["label"] == "Родительское издание")

        self.assertEqual(parent_row["existing"], "Сб. стат. сведений о России")
        self.assertEqual(parent_row["status"], "same")

    def test_item_detail_refreshes_stale_parent_comparison_from_container_work(self):
        user = get_user_model().objects.create_user("stale_parent_viewer", password="x", is_staff=True)
        parent = Work.objects.create(
            work_id="work-stale-parent",
            source_number=93013,
            source_sequence=93013,
            source_section=self.section,
            language=self.language,
            work_type=Work.WorkType.CONTAINER,
            title="Сб. стат. сведений о России",
        )
        child = Work.objects.create(
            work_id="work-stale-child",
            source_number=93014,
            source_sequence=93014,
            source_section=self.section,
            language=self.language,
            work_type=Work.WorkType.ARTICLE,
            title="Исторический очерк денежного обращения России с 1650 по 1817 г.",
        )
        Article.objects.create(article_id="article-stale-child", work=child, container_work=parent)
        batch = ImportBatch.objects.create(title="Stale parent", raw_input="")
        item = ImportItem.objects.create(
            import_batch=batch,
            raw_text="Ламанский Е.И. Исторический очерк // Сб. стат. сведений о России. — СПб., 1854.",
            detected_type=ImportItem.DetectedType.COLLECTION_ARTICLE,
            status=ImportItem.Status.STRUCTURAL_CONFLICT,
            matched_existing_type="work",
            matched_existing_id=child.work_id,
            parsed_data_json={"title": child.title, "collection_title": "Сб. стат. сведений о России", "authors": []},
            comparison_json={"fields": [{"label": "Родительское издание", "existing": "", "source": "Сб. стат. сведений о России", "status": "new_in_source"}]},
        )

        self.client.force_login(user)
        response = self.client.get(f"/imports/{batch.pk}/items/{item.pk}/")
        item.refresh_from_db()
        parent_row = next(row for row in item.comparison_json["fields"] if row["label"] == "Родительское издание")

        self.assertContains(response, "Сб. стат. сведений о России")
        self.assertEqual(parent_row["existing"], "Сб. стат. сведений о России")
        self.assertEqual(parent_row["status"], "same")

    def test_postponed_item_is_excluded_from_apply_blockers(self):
        batch = ImportBatch.objects.create(title="Postponed blocker", raw_input="")
        item = ImportItem.objects.create(import_batch=batch, raw_text="Сложная строка", detected_type=ImportItem.DetectedType.BOOK, status=ImportItem.Status.PARSED)
        ImportEntity.objects.create(
            import_batch=batch,
            entity_type=ImportEntity.EntityType.BOOK,
            label="Сложная строка",
            normalized_key="postponed-book",
            data_json={"item_id": item.id},
            status=ImportEntity.Status.UNRESOLVED,
        )

        apply_item_decision(item, ImportDecision.DecisionType.POSTPONE)

        self.assertFalse(readiness_problems(batch))
        plan = build_import_plan(batch)
        self.assertTrue(plan["can_apply"])

    def test_review_page_hides_container_block_for_book_only_import(self):
        user = get_user_model().objects.create_user("review_no_containers", password="x", is_staff=True)
        batch = ImportBatch.objects.create(title="Books only", raw_input="Иванов И.И. Новая книга. — М., 2026.")
        parse_import_batch(batch)

        self.client.force_login(user)
        response = self.client.get(f"/imports/{batch.pk}/review/")

        self.assertNotContains(response, "Журналы и сборники для проверки")
        self.assertContains(response, "Все обработанные строки")

    def test_review_page_groups_rows_by_short_status_and_column_order(self):
        user = get_user_model().objects.create_user("review_grouped", password="x", is_staff=True)
        batch = ImportBatch.objects.create(title="Grouped review", raw_input="")
        found = ImportItem.objects.create(
            import_batch=batch,
            raw_text="Уже найденная книга",
            detected_type=ImportItem.DetectedType.BOOK,
            status=ImportItem.Status.FOUND_EXISTING_NO_CHANGES,
            comparison_json={"summary": "Изменений не требуется."},
        )
        new = ImportItem.objects.create(import_batch=batch, raw_text="Новая книга", detected_type=ImportItem.DetectedType.BOOK, status=ImportItem.Status.PARSED)

        self.client.force_login(user)
        response = self.client.get(f"/imports/{batch.pk}/review/")
        content = response.content.decode("utf-8")

        self.assertLess(content.index("Новые"), content.index("Найдены"))
        self.assertContains(response, "<th>Строка источника</th>", html=True)
        self.assertLess(content.index("<th>Найдена запись</th>"), content.index("<th>Статус</th>"))
        self.assertContains(response, ">Новая<", html=False)
        self.assertContains(response, ">Найдена<", html=False)
        self.assertNotContains(response, "Исправить разбор")
        self.assertNotContains(response, "Найдена в базе")

    def test_review_action_column_does_not_include_database_record_link(self):
        user = get_user_model().objects.create_user("review_no_db_action", password="x", is_staff=True)
        work = Work.objects.create(
            work_id="work-review-action",
            source_number=93002,
            source_sequence=93002,
            source_section=self.section,
            language=self.language,
            work_type=Work.WorkType.BOOK,
            title="Уже найденная книга",
        )
        batch = ImportBatch.objects.create(title="Action review", raw_input="")
        ImportItem.objects.create(
            import_batch=batch,
            raw_text="Уже найденная книга",
            detected_type=ImportItem.DetectedType.BOOK,
            status=ImportItem.Status.FOUND_EXISTING_NO_CHANGES,
            matched_existing_type="work",
            matched_existing_id=work.work_id,
        )

        self.client.force_login(user)
        response = self.client.get(f"/imports/{batch.pk}/review/")

        self.assertContains(response, "Уже найденная книга")
        self.assertNotContains(response, "Запись в базе")
