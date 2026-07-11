from __future__ import annotations

from datetime import date

from django.db import models
from wagtail.admin.panels import FieldPanel
from wagtail.models import Page


class HomePage(Page):
    intro = models.TextField(blank=True)

    max_count = 1
    subpage_types = ["cmsdemo.ArticlePage"]

    content_panels = Page.content_panels + [
        FieldPanel("intro"),
    ]


class ArticlePage(Page):
    published_on = models.DateField(default=date.today)
    body = models.TextField(blank=True)

    parent_page_types = ["cmsdemo.HomePage"]
    subpage_types: list[str] = []

    content_panels = Page.content_panels + [
        FieldPanel("published_on"),
        FieldPanel("body"),
    ]
