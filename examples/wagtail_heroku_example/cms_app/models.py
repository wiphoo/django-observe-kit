"""Minimal Wagtail pages for the Heroku example."""

from wagtail.admin.panels import FieldPanel
from wagtail.fields import RichTextField
from wagtail.models import Page


class HomePage(Page):
    """Simple Home page with an optional rich text body."""

    body = RichTextField(blank=True)

    content_panels = Page.content_panels + [FieldPanel("body")]
