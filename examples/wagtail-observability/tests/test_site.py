from __future__ import annotations

from django.core.management import call_command
import pytest

from cmsdemo.models import ArticlePage, HomePage
from wagtail.models import Site


@pytest.mark.django_db
def test_bootstrap_command_is_idempotent():
    call_command(
        "bootstrap_wagtail_demo",
        hostname="testserver",
        port=80,
        username="editor",
        email="editor@example.com",
        password="Admin123!@#$",
    )
    call_command(
        "bootstrap_wagtail_demo",
        hostname="testserver",
        port=80,
        username="editor",
        email="editor@example.com",
        password="Admin123!@#$",
    )

    assert HomePage.objects.count() == 1
    assert ArticlePage.objects.count() == 1

    site = Site.objects.get(is_default_site=True)
    assert site.hostname == "testserver"
    assert site.port == 80
    assert site.root_page.specific_class is HomePage


@pytest.mark.django_db
def test_wagtail_admin_and_public_pages_render(client):
    call_command("bootstrap_wagtail_demo", hostname="testserver", port=80)

    admin_response = client.get("/admin/login/", HTTP_HOST="testserver")
    home_response = client.get("/", HTTP_HOST="testserver")
    article_response = client.get("/launch-announcement/", HTTP_HOST="testserver")

    assert admin_response.status_code == 200
    assert home_response.status_code == 200
    assert article_response.status_code == 200
    assert "Observe Kit Wagtail Demo" in home_response.content.decode()
    assert "Launch Announcement" in article_response.content.decode()


@pytest.mark.django_db
def test_metrics_cover_admin_and_public_requests(client):
    call_command("bootstrap_wagtail_demo", hostname="testserver", port=80)

    admin_response = client.get("/admin/login/", HTTP_HOST="testserver")
    public_response = client.get("/launch-announcement/", HTTP_HOST="testserver")

    assert admin_response.status_code == 200
    assert public_response.status_code == 200

    response = client.get("/metrics", HTTP_HOST="testserver")
    assert response.status_code == 200

    content = response.content.decode()
    assert "http_requests_total" in content
    assert "http_request_duration_seconds" in content
