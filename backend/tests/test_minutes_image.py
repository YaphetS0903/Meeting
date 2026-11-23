from io import BytesIO

import pytest
from PIL import Image
from reportlab.pdfgen.canvas import Canvas

from app.core.config import get_settings
from app.services.exports import generate_mail_exports, minutes_image
from tests.test_business import published


def test_every_page_is_stitched_in_order_without_cropping():
    source = BytesIO()
    canvas = Canvas(source, pagesize=(600, 400))
    for color in [(1, 0, 0), (0, 1, 0), (0, 0, 1)]:
        canvas.setFillColorRGB(*color)
        canvas.rect(0, 0, 600, 400, fill=1, stroke=0)
        canvas.showPage()
    canvas.save()
    with Image.open(BytesIO(minutes_image(source.getvalue()))) as image:
        assert image.size == (1500, 3000)
        for y, color in [(500, (255, 0, 0)), (1500, (0, 255, 0)), (2500, (0, 0, 255))]:
            assert image.getpixel((750, y)) == color


def test_mail_exports_are_versioned_and_reused(db, team, draft_factory, tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "storage_dir", tmp_path)
    meeting = published(db, team, draft_factory())
    first = generate_mail_exports(db, meeting.id, 1)
    assert [a.kind for a in first] == ["tasks_xlsx", "minutes_png"]
    assert [a.id for a in generate_mail_exports(db, meeting.id, 1)] == [a.id for a in first]
    assert first[1].meeting_version == 1
    assert (tmp_path / first[1].storage_key).read_bytes().startswith(b"\x89PNG")


def test_invalid_pdf_does_not_silently_create_an_empty_image():
    with pytest.raises(Exception):
        minutes_image(b"broken")


def test_published_image_is_readable_but_unpublished_version_is_private(
    db, team, draft_factory, tmp_path, monkeypatch
):
    from app.services.access import meeting_attachment_visible

    monkeypatch.setattr(get_settings(), "storage_dir", tmp_path)
    meeting = published(db, team, draft_factory())
    image = generate_mail_exports(db, meeting.id, 1)[1]
    assert meeting_attachment_visible(team["a"], meeting, image, set())
    image.meeting_version = 2
    assert not meeting_attachment_visible(team["a"], meeting, image, set())
