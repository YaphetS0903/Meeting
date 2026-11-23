import base64
import json
from io import BytesIO

import pytest
from PIL import Image, ImageDraw

from app.services import document_sheet_import as document


def page(items):
    return dict(complete=True, items=items)


def response(value):
    return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(value)}}]}


@pytest.fixture
def long_table(tmp_path, monkeypatch):
    monkeypatch.setattr(document, "configured", lambda: True)
    path = tmp_path / "long.png"
    image = Image.new("RGB", (878, 5752), "white")
    draw = ImageDraw.Draw(image)
    for index in range(55):
        y = 40 + index * 95
        draw.line((60, y, 814, y), fill="black")
        if index < 54:
            draw.text((80, y + 20), f"Task {index + 1}", fill="black")
            draw.text((80, y + 50), "Full row must survive", fill="black")
    image.save(path)
    image.close()
    return path


def test_long_table_keeps_original_width_and_every_row_exactly_once(long_table):
    with Image.open(long_table) as image:
        regions = document.image_regions(image)
    assert len(regions) > 1
    assert regions[0][0] == 0 and regions[-1][1] == 5752
    assert all(a[1] == b[0] for a, b in zip(regions, regions[1:]))
    for index in range(54):
        top = 40 + index * 95 + 10
        bottom = top + 70
        assert sum(a <= top and bottom <= b for a, b in regions) == 1
    parts = list(document.pages([long_table]))
    assert len(parts) == len(regions)
    for _, _, url in parts:
        with Image.open(BytesIO(base64.b64decode(url.split(",")[1]))) as image:
            assert image.width == 878
            assert image.height < 1500


def test_human_confirms_all_history_without_inventing_results(long_table):
    count = len(list(document.pages([long_table])))
    bodies = []

    def sender(body):
        bodies.append(body)
        # A restarted row number and blank result must not force a new task.
        return response(
            page([dict(section="new", number="1", content=f"row {len(bodies)}", owners="张三", status="")])
        )

    parsed = document.parse_documents([long_table], sender=sender, section_hint="review")
    rows = parsed.candidates[0].payload["items"]
    assert len(rows) == count and len({r["id"] for r in rows}) == count
    assert all(r["section"] == "review" and r["status"] is None for r in rows)
    assert all("用户已明确确认" in body["messages"][1]["content"][0]["text"] for body in bodies)
    assert "上一段末行" in bodies[1]["messages"][1]["content"][0]["text"]
    assert sum(i.code == "invalid_status" for i in parsed.candidates[0].issues) == count


def test_unknown_sections_remain_unresolved_without_human_confirmation(long_table):
    parsed = document.parse_documents(
        [long_table],
        sender=lambda _: response(
            page(
                [
                    dict(section="unknown", number="1", content="不推断分区", status=""),
                ]
            )
        ),
    )
    assert all(r["section"] == "unknown" for r in parsed.candidates[0].payload["items"])
    assert any(i.code == "section_unknown" for i in parsed.candidates[0].issues)


def test_segment_failure_exposes_location_and_never_returns_partial_rows(long_table):
    calls = 0

    def sender(body):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise document.AIRecognitionFailed("AI 识别超时")
        return response(page([dict(section="review", content="第一段正常")]))

    with pytest.raises(document.AIRecognitionFailed, match=r"分段 2/.*超时"):
        document.parse_documents([long_table], sender=sender)
    assert calls == 2


def test_long_image_exif_orientation_is_applied_before_splitting(long_table):
    path = long_table.with_suffix(".jpg")
    with Image.open(long_table) as image:
        rotated = image.transpose(Image.Transpose.ROTATE_90)
        exif = Image.Exif()
        exif[274] = 6
        rotated.save(path, exif=exif)
        rotated.close()
    parts = list(document.pages([path]))
    assert len(parts) > 1
    for _, _, data in parts:
        with Image.open(BytesIO(base64.b64decode(data.split(",")[1]))) as image:
            assert image.width == 878 and not image.getexif()


def test_extreme_length_rejected_before_provider_and_no_arbitrary_text_cuts(tmp_path):
    with Image.new("RGB", (40, 40000), "white") as image:
        with pytest.raises(document.DocumentInvalid, match="分段过多"):
            document.image_regions(image)
    with Image.new("RGB", (800, 5000), (120, 120, 120)) as image:
        draw = ImageDraw.Draw(image)
        for x in range(0, 800, 4):
            draw.line((x, 0, x, 4999), fill="black", width=2)
        with pytest.raises(document.DocumentInvalid, match="安全分割"):
            document.image_regions(image)


def test_tall_pdf_is_rendered_at_readable_width_then_segmented(tmp_path):
    from reportlab.pdfgen import canvas

    path = tmp_path / "tall.pdf"
    pdf = canvas.Canvas(str(path), pagesize=(400, 2400))
    for y in range(40, 2400, 100):
        pdf.line(30, y, 370, y)
        pdf.drawString(35, y + 30, f"Complete task at {y}")
    pdf.save()
    parts = list(document.pages([path]))
    assert len(parts) > 1
    for _, page_number, data in parts:
        assert page_number == 1
        with Image.open(BytesIO(base64.b64decode(data.split(",")[1]))) as image:
            assert image.width == 1000
