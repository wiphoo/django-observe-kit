import importlib.util

from .sentry_breadcrumbs import add_wagtail_breadcrumb

__all__ = [
    "add_wagtail_breadcrumb",
]

# Only import wagtail hooks if wagtail is installed
if importlib.util.find_spec("wagtail"):
    from .wagtail_hooks import audit_delete_page, audit_publish_page, audit_unpublish_page

    __all__ += [
        "audit_delete_page",
        "audit_publish_page",
        "audit_unpublish_page",
    ]
