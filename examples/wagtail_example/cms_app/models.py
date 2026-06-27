"""Example Wagtail models."""

from wagtail.models import Page


class HomePage(Page):
    """Home page model."""

    class Meta:
        verbose_name = "Home Page"





