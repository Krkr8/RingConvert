# SPDX-License-Identifier: GPL-3.0-or-later
import pymupdf
import pytest
from PIL import Image

from conftest import needs
from ringconvert.core import convert, metadata, pdf
from ringconvert.core.detect import detect


def test_single_page_keeps_name(pdf1):
    [out] = convert(pdf1, "jpg").outputs
    assert out.name == "Page 001.jpg"
    # 200 DPI A4-ish page (pymupdf default page is 595x842 pt).
    assert Image.open(out).size == (1653, 2339)


@pytest.mark.parametrize("target", ["png", "tiff"])
def test_multi_page_numbered(pdf3, target):
    outs = convert(pdf3, target).outputs
    assert [o.name for o in outs] == [f"Report {i}.{target}" for i in (1, 2, 3)]


def test_progress_reported(pdf3):
    seen = []
    convert(pdf3, "png", progress=seen.append)
    assert seen[-1] == 1.0 and len(seen) == 3


def test_to_txt(pdf3):
    [out] = convert(pdf3, "txt").outputs
    text = out.read_text()
    assert "Page 1 of the sample" in text and "Page 3 of the sample" in text


@needs("pdf2docx", "docx")
def test_to_docx(pdf1):
    import docx

    [out] = convert(pdf1, "docx").outputs
    text = "\n".join(p.text for p in docx.Document(out).paragraphs)
    assert "Page 1 of the sample" in text


def test_true_redaction_removes_text(pdf1):
    with pymupdf.open(pdf1) as doc:
        [hit] = doc[0].search_for("SECRET-NUMBER 4242")
    rect = (0, hit.x0 - 2, hit.y0 - 2, hit.width + 4, hit.height + 4)
    [out] = pdf.redact(pdf1, [rect]).outputs
    assert out.name == "Page 001 Redacted.pdf"
    with pymupdf.open(out) as doc:
        text = doc[0].get_text()
        assert "SECRET" not in text and "4242" not in text
        assert "Page 1 of the sample" in text
        assert not doc.metadata.get("author")


def test_annotations_added(pdf1):
    shapes = [
        {"type": "rect", "points": [[60, 60], [300, 110]], "color": "#e8462a"},
        {"type": "arrow", "points": [[400, 300], [300, 150]]},
        {"type": "highlight", "points": [[70, 125], [260, 150]], "color": "#ffd400"},
        {"type": "pen", "points": [[100, 400], [150, 420], [200, 400]]},
        {"type": "text", "points": [[100, 500]], "text": "Look here", "size": 18},
    ]
    [out] = pdf.annotate(pdf1, shapes).outputs
    assert out.name == "Page 001 Annotated.pdf"
    with pymupdf.open(out) as doc:
        kinds = sorted(a.type[1] for a in doc[0].annots())
    assert kinds == sorted(["Square", "Line", "Square", "Ink", "FreeText"])


def test_strip_metadata(pdf1):
    [out] = metadata.strip(detect(pdf1)).outputs
    assert out.name == "Page 001 Clean.pdf"
    with pymupdf.open(out) as doc:
        assert not doc.metadata.get("author") and not doc.metadata.get("title")


@needs("gs")
def test_compress_pdf(pdf1):
    from ringconvert.core.compress import compress

    [out] = compress(detect(pdf1)).outputs
    assert out.name == "Page 001 Compressed.pdf"
    with pymupdf.open(out) as doc:
        assert "Page 1 of the sample" in doc[0].get_text()
