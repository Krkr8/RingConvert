# SPDX-License-Identifier: GPL-3.0-or-later
import pymupdf
import pytest

from conftest import needs
from ringconvert.core import convert

pytestmark = pytest.mark.slow


def pdf_text(path) -> str:
    with pymupdf.open(path) as doc:
        return "".join(page.get_text() for page in doc)


@needs("libreoffice")
@pytest.mark.parametrize("target", ["pdf", "odt", "rtf"])
def test_docx_via_libreoffice(docx_file, target):
    [out] = convert(docx_file, target).outputs
    assert out.name == f"Notes.{target}" and out.stat().st_size > 0
    if target == "pdf":
        assert "Quarterly Notes" in pdf_text(out)


@needs("libreoffice")
def test_odt_back_to_docx(docx_file):
    import docx

    [odt] = convert(docx_file, "odt").outputs
    [out] = convert(odt, "docx").outputs
    assert out.name == "Notes 2.docx"  # Notes.docx already exists
    assert "Quarterly Notes" in [p.text for p in docx.Document(out).paragraphs]


@needs("pandoc")
@pytest.mark.parametrize("target, expect", [("md", "# Quarterly Notes"), ("html", "<h1"), ("txt", "Quarterly Notes")])
def test_docx_via_pandoc(docx_file, target, expect):
    [out] = convert(docx_file, target).outputs
    assert expect in out.read_text()


@needs("pandoc", "docx")
@pytest.mark.parametrize("target", ["docx", "odt", "html", "txt"])
def test_markdown(md_file, target):
    [out] = convert(md_file, target).outputs
    assert out.suffix == f".{target}" and out.stat().st_size > 0


@needs("pandoc", "libreoffice")
def test_markdown_to_pdf(md_file):
    [out] = convert(md_file, "pdf").outputs
    text = pdf_text(out)
    assert "Title" in text and "bold" in text


@needs("pandoc")
def test_html_to_md(html_file):
    [out] = convert(html_file, "md").outputs
    assert "# Hello" in out.read_text()


@needs("libreoffice")
def test_txt(txt_file):
    [pdf] = convert(txt_file, "pdf").outputs
    assert "Line two" in pdf_text(pdf)
    [html] = convert(txt_file, "html").outputs
    assert "&lt;with&gt;" in html.read_text()
    [docx] = convert(txt_file, "docx").outputs
    assert docx.stat().st_size > 0


@needs("libreoffice")
@pytest.mark.parametrize("target", ["xlsx", "ods", "pdf"])
def test_spreadsheet(csv_file, target):
    [out] = convert(csv_file, target).outputs
    assert out.name == f"Table.{target}" and out.stat().st_size > 0
    if target == "pdf":
        assert "apple" in pdf_text(out)


FLAT_ODP = """<?xml version="1.0" encoding="UTF-8"?>
<office:document xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
  xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0"
  xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"
  xmlns:svg="urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0"
  office:version="1.3" office:mimetype="application/vnd.oasis.opendocument.presentation">
 <office:body><office:presentation>
  <draw:page draw:name="One"><draw:frame svg:x="2cm" svg:y="2cm" svg:width="20cm" svg:height="3cm">
   <draw:text-box><text:p>Slide One</text:p></draw:text-box></draw:frame></draw:page>
  <draw:page draw:name="Two"><draw:frame svg:x="2cm" svg:y="2cm" svg:width="20cm" svg:height="3cm">
   <draw:text-box><text:p>Slide Two</text:p></draw:text-box></draw:frame></draw:page>
 </office:presentation></office:body>
</office:document>
"""


@pytest.fixture
def pptx_file(tmp_path):
    from ringconvert.core import backends
    from ringconvert.core.office import libreoffice

    if not backends.which("libreoffice"):
        pytest.skip("needs libreoffice")
    flat = tmp_path / "src" / "Deck.fodp"
    flat.parent.mkdir()
    flat.write_text(FLAT_ODP, encoding="utf-8")
    pptx = libreoffice(flat, "pptx", tmp_path, "presentation")
    return pptx


def test_presentation_to_pdf_and_odp(pptx_file):
    [pdf] = convert(pptx_file, "pdf").outputs
    assert "Slide Two" in pdf_text(pdf)
    [odp] = convert(pptx_file, "odp").outputs
    assert odp.name == "Deck.odp"


def test_presentation_to_images(pptx_file):
    outs = convert(pptx_file, "png").outputs
    assert [o.name for o in outs] == ["Deck 1.png", "Deck 2.png"]
    assert all(o.parent == pptx_file.parent for o in outs)
