from django.core.management.base import BaseCommand, CommandError

from sources.author_merge import apply_author_merge, merge_authors_plan


class Command(BaseCommand):
    help = "Safely merges duplicate Author rows into one canonical author."

    def add_arguments(self, parser):
        parser.add_argument("--target", required=True, help="Canonical author_id to keep.")
        parser.add_argument("--source", action="append", required=True, help="Duplicate author_id to merge/delete. Repeat as needed.")
        parser.add_argument("--apply", action="store_true", help="Apply merge. Default is dry-run.")
        parser.add_argument("--skip-target-refresh", action="store_true", help="Do not rebuild target tables after apply.")

    def handle(self, *args, **options):
        if options["apply"]:
            result = apply_author_merge(
                source_ids=options["source"],
                target_id=options["target"],
                refresh_target=not options["skip_target_refresh"],
            )
            if result["error"]:
                raise CommandError(result["error"])
            self.stdout.write(self.style.SUCCESS(result["message"]))
            return

        plan = merge_authors_plan(options["source"], options["target"])
        if plan["errors"]:
            raise CommandError(" ".join(plan["errors"]))
        self.stdout.write(f"target: {plan['target'].author_id} — {plan['target'].display_name}")
        self.stdout.write("sources:")
        for author in plan["sources"]:
            self.stdout.write(f"- {author.author_id} — {author.display_name}")
        self.stdout.write(f"work_author_links_to_move: {plan['work_link_count']}")
        self.stdout.write(f"source_author_links_to_move: {plan['source_link_count']}")
        self.stdout.write("aliases_after_merge:")
        for alias in plan["aliases"]:
            self.stdout.write(f"- {alias}")
        self.stdout.write(self.style.WARNING("Dry run: ничего не изменено. Добавьте --apply для слияния."))
