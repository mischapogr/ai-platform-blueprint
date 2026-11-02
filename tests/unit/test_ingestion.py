import socket

import pytest
from platform_api.ingestion import SourceError, chunks, html_text, parse_file, public_target


def test_web_extraction_discards_navigation_and_scripts():
    title, text, links = html_text(
        b"<title>Reference</title><nav>Ignore</nav><main><h1>Orbit</h1><p>Evidence</p>"
        b'<script>secret</script><a href="/next">Next</a></main>'
    )
    assert title == "Reference"
    assert "Orbit Evidence" in text
    assert "Ignore" not in text and "secret" not in text
    assert links == ["/next"]


@pytest.mark.parametrize(
    "url", ["http://example.com", "https://user:password@example.com", "https://example.com:444"]
)
def test_non_public_https_shapes_blocked(url):
    with pytest.raises(SourceError):
        public_target(url)


@pytest.mark.parametrize(
    "addresses", [["127.0.0.1"], ["169.254.169.254"], ["::1"], ["8.8.8.8", "10.0.0.1"]]
)
def test_dns_private_and_mixed_answers_blocked(monkeypatch, addresses):
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *a, **kw: [(0, 0, 0, "", (ip, 443)) for ip in addresses]
    )
    with pytest.raises(SourceError, match="NON_PUBLIC_TARGET_BLOCKED"):
        public_target("https://example.com")


def test_text_chunking_preserves_page_metadata_and_overlap():
    text = "word " * 500
    parts = chunks([{"text": text, "page": 7, "locator": "https://example.com/chapter"}])
    assert len(parts) > 1
    assert all(p["page"] == 7 for p in parts)
    assert parts[0]["content"][-150:] == parts[1]["content"][:150]
    assert parse_file(b"local knowledge", "book.txt")[0]["text"] == "local knowledge"
    with pytest.raises(SourceError, match="UNSUPPORTED_FILE_TYPE"):
        parse_file(b"anything", "book.exe")


def test_pdf_extraction_preserves_page_number():
    from io import BytesIO

    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    page = writer.add_blank_page(width=300, height=300)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})}
    )
    content = DecodedStreamObject()
    content.set_data(b"BT /F1 12 Tf 30 200 Td (Synthetic book reference AMBER-204) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(content)
    buffer = BytesIO()
    writer.write(buffer)
    pages = parse_file(buffer.getvalue(), "synthetic-book.pdf")
    assert pages[0]["page"] == 1
    assert "AMBER-204" in pages[0]["text"]


def test_initial_canonical_www_redirect_allowed_but_other_site_rejected(monkeypatch):
    from platform_api import ingestion

    monkeypatch.setattr(ingestion, "public_target", lambda url: (None, "8.8.8.8"))

    def canonical(url, limit=0):
        if url.endswith("robots.txt"):
            return url, 404, "", b""
        return "https://www.example.com/", 200, "text/html", b"<main>Synthetic reference</main>"

    monkeypatch.setattr(ingestion, "fetch", canonical)
    assert len(ingestion.website_pages("https://example.com/", max_pages=1)) == 1

    def other_site(url, limit=0):
        if url.endswith("robots.txt"):
            return url, 404, "", b""
        return "https://other.example/", 200, "text/html", b"<main>Other site</main>"

    monkeypatch.setattr(ingestion, "fetch", other_site)
    with pytest.raises(SourceError, match="CROSS_HOST_REDIRECT_BLOCKED"):
        ingestion.website_pages("https://example.com/", max_pages=1)
