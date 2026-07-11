from __future__ import annotations

import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from wagtail.models import Page, Site

from cmsdemo.models import ArticlePage, HomePage


class Command(BaseCommand):
    help = "Create the default Wagtail admin user, Site config, and starter content."
    home_page_slug = "observe-kit-home"

    def add_arguments(self, parser) -> None:
        parser.add_argument("--hostname", default=os.getenv("OBSERVE_KIT_SITE_HOSTNAME", "localhost"))
        parser.add_argument("--port", type=int, default=int(os.getenv("OBSERVE_KIT_SITE_PORT", "8000")))
        parser.add_argument(
            "--username",
            default=os.getenv("OBSERVE_KIT_WAGTAIL_ADMIN_USERNAME", "editor"),
        )
        parser.add_argument(
            "--email",
            default=os.getenv("OBSERVE_KIT_WAGTAIL_ADMIN_EMAIL", "editor@example.com"),
        )
        parser.add_argument(
            "--password",
            default=os.getenv("OBSERVE_KIT_WAGTAIL_ADMIN_PASSWORD", "Admin123!@#$"),
        )

    def handle(self, *args, **options) -> None:
        self._ensure_superuser(
            username=options["username"],
            email=options["email"],
            password=options["password"],
        )
        home_page = self._ensure_home_page()
        self._ensure_article_page(home_page)
        self._ensure_site(
            root_page=home_page,
            hostname=options["hostname"],
            port=options["port"],
        )

        self.stdout.write(self.style.SUCCESS("Wagtail demo content is ready."))

    def _ensure_superuser(self, username: str, email: str, password: str) -> None:
        user_model = get_user_model()
        user = user_model.objects.filter(username=username).first()
        if user is None:
            user_model.objects.create_superuser(username=username, email=email, password=password)
            self.stdout.write(f"Created superuser '{username}'.")
            return

        changed = False
        if email and user.email != email:
            user.email = email
            changed = True
        if not user.is_staff:
            user.is_staff = True
            changed = True
        if not user.is_superuser:
            user.is_superuser = True
            changed = True

        if changed:
            user.save(update_fields=["email", "is_staff", "is_superuser"])
            self.stdout.write(f"Updated superuser flags for '{username}'.")

    def _ensure_home_page(self) -> HomePage:
        root = Page.get_first_root_node()
        home_page = HomePage.objects.child_of(root).filter(slug=self.home_page_slug).first()

        if home_page is None:
            home_page = HomePage(
                title="Observe Kit Wagtail Demo",
                slug=self.home_page_slug,
                intro=(
                    "Use the Wagtail admin to edit the launch announcement, then open the "
                    "public page and inspect the resulting metrics, traces, and logs."
                ),
            )
            root.add_child(instance=home_page)
            home_page.save_revision().publish()
            self.stdout.write("Created the home page.")
            return home_page

        updated_fields: list[str] = []
        if home_page.title != "Observe Kit Wagtail Demo":
            home_page.title = "Observe Kit Wagtail Demo"
            updated_fields.append("title")
        intro = (
            "Use the Wagtail admin to edit the launch announcement, then open the "
            "public page and inspect the resulting metrics, traces, and logs."
        )
        if home_page.intro != intro:
            home_page.intro = intro
            updated_fields.append("intro")

        if updated_fields:
            home_page.save()
            home_page.save_revision().publish()
            self.stdout.write("Updated the home page.")

        return home_page

    def _ensure_article_page(self, home_page: HomePage) -> None:
        article_page = ArticlePage.objects.child_of(home_page).filter(slug="launch-announcement").first()
        body = (
            "Edit this page in Wagtail admin, publish the update, and then open the public "
            "URL to generate request traces, correlated logs, and Prometheus metrics."
        )

        if article_page is None:
            article_page = ArticlePage(
                title="Launch Announcement",
                slug="launch-announcement",
                body=body,
            )
            home_page.add_child(instance=article_page)
            article_page.save_revision().publish()
            self.stdout.write("Created the launch announcement page.")
            return

        updated_fields: list[str] = []
        if article_page.title != "Launch Announcement":
            article_page.title = "Launch Announcement"
            updated_fields.append("title")
        if article_page.body != body:
            article_page.body = body
            updated_fields.append("body")

        if updated_fields:
            article_page.save()
            article_page.save_revision().publish()
            self.stdout.write("Updated the launch announcement page.")

    def _ensure_site(self, root_page: HomePage, hostname: str, port: int) -> None:
        site = Site.objects.filter(is_default_site=True).first()
        if site is None:
            Site.objects.create(
                hostname=hostname,
                port=port,
                site_name="Observe Kit Wagtail Demo",
                root_page=root_page,
                is_default_site=True,
            )
            self.stdout.write("Created the default Wagtail Site record.")
            return

        site.hostname = hostname
        site.port = port
        site.site_name = "Observe Kit Wagtail Demo"
        site.root_page = root_page
        site.is_default_site = True
        site.save()
        self.stdout.write("Updated the default Wagtail Site record.")
