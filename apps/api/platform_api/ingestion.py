"""Bounded public HTTPS ingestion with DNS pinning; local-only file parsing."""

import http.client
import ipaddress
import socket
import ssl
from io import BytesIO
from urllib.parse import urldefrag, urljoin, urlsplit
from urllib.robotparser import RobotFileParser

from bs4 import BeautifulSoup
from pypdf import PdfReader

MAX_BYTES = 10 * 1024 * 1024
MAX_WEB_BYTES = 2 * 1024 * 1024
MAX_TEXT = 2_000_000
MAX_CHUNKS = 2000
USER_AGENT = "LocalKnowledgeBot/0.1"


class SourceError(Exception):
    def __init__(self, code, retryable=False):
        super().__init__(code)
        self.code = code
        self.retryable = retryable


def public_target(url):
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
        or len(url) > 2000
    ):
        raise SourceError("PUBLIC_HTTPS_URL_REQUIRED")
    try:
        addresses = sorted(
            {
                item[4][0]
                for item in socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
            }
        )
    except OSError:
        raise SourceError("DNS_UNAVAILABLE", retryable=True) from None
    if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
        raise SourceError("NON_PUBLIC_TARGET_BLOCKED")
    return parsed, addresses[0]


def fetch(url, limit=MAX_WEB_BYTES):
    """Validate every redirect, connect to checked IP, verify TLS for original hostname."""
    for _ in range(5):
        parsed, address = public_target(url)
        connection = http.client.HTTPSConnection(parsed.hostname, timeout=15)
        sock = None
        try:
            sock = socket.create_connection((address, 443), timeout=15)
            connection.sock = ssl.create_default_context().wrap_socket(
                sock, server_hostname=parsed.hostname
            )
            target = parsed.path or "/"
            if parsed.query:
                target += "?" + parsed.query
            connection.request(
                "GET", target, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity"}
            )
            response = connection.getresponse()
            if response.status in {301, 302, 303, 307, 308}:
                location = response.getheader("Location")
                if not location:
                    raise SourceError("INVALID_REDIRECT")
                url = urljoin(url, location)
                continue
            if response.status == 404:
                return url, 404, "", b""
            if response.status != 200:
                raise SourceError(
                    "REMOTE_HTTP_ERROR", retryable=response.status in {429, 500, 502, 503, 504}
                )
            body = response.read(limit + 1)
            if len(body) > limit:
                raise SourceError("REMOTE_PAGE_TOO_LARGE")
            return url, response.status, response.getheader("Content-Type", ""), body
        except (OSError, ssl.SSLError, http.client.HTTPException):
            raise SourceError("REMOTE_UNAVAILABLE", retryable=True) from None
        finally:
            connection.close()
            if sock:
                sock.close()
    raise SourceError("TOO_MANY_REDIRECTS")


def html_text(body):
    soup = BeautifulSoup(body, "html.parser")
    title = soup.title.get_text(" ", strip=True)[:160] if soup.title else "Web page"
    links = [tag.get("href", "") for tag in soup.find_all("a", href=True)]
    for node in soup(["script", "style", "nav", "header", "footer", "aside", "form", "noscript"]):
        node.decompose()
    main = soup.find("main") or soup.find("article") or soup.body or soup
    return title, main.get_text(" ", strip=True), links


def website_pages(start, max_pages=5):
    public_target(start)
    queue = [urldefrag(start)[0]]
    seen = set()
    robots = {}
    pages = []
    origin_host = urlsplit(start).hostname
    while queue and len(pages) < max_pages:
        url = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        parsed = urlsplit(url)
        if parsed.hostname != origin_host:
            continue
        if parsed.hostname not in robots:
            robots_url = f"https://{parsed.hostname}/robots.txt"
            _, status, _, body = fetch(robots_url, limit=256_000)
            parser = RobotFileParser()
            parser.parse(
                [] if status == 404 else body.decode("utf-8", errors="replace").splitlines()
            )
            robots[parsed.hostname] = parser
        if not robots[parsed.hostname].can_fetch(USER_AGENT, url):
            if not pages:
                raise SourceError("ROBOTS_DISALLOWED")
            continue
        actual, status, content_type, body = fetch(url)
        if status == 404:
            continue
        actual_host = urlsplit(actual).hostname
        if actual_host != origin_host:
            # Permit the initial canonical www redirect, but never follow a new site.
            if not pages and actual_host.removeprefix("www.") == origin_host.removeprefix("www."):
                origin_host = actual_host
            else:
                raise SourceError("CROSS_HOST_REDIRECT_BLOCKED")
        seen.add(actual)
        if "text/html" not in content_type:
            continue
        title, text, links = html_text(body)
        if text:
            pages.append({"title": title, "text": text, "page": None, "locator": actual})
        for link in links:
            candidate = urldefrag(urljoin(actual, link))[0]
            target = urlsplit(candidate)
            if (
                target.scheme == "https"
                and target.hostname == origin_host
                and not target.query
                and candidate not in seen
                and len(queue) < 100
            ):
                queue.append(candidate)
    if not pages:
        raise SourceError("NO_READABLE_WEB_PAGES")
    return pages


def parse_file(data, filename):
    if len(data) > MAX_BYTES:
        raise SourceError("FILE_TOO_LARGE")
    if filename.lower().endswith(".pdf"):
        try:
            reader = PdfReader(BytesIO(data))
            if reader.is_encrypted:
                raise SourceError("ENCRYPTED_PDF_UNSUPPORTED")
            if len(reader.pages) > 400:
                raise SourceError("PDF_PAGE_LIMIT_400")
            pages = [
                {"text": page.extract_text() or "", "page": i + 1, "locator": ""}
                for i, page in enumerate(reader.pages)
            ]
        except SourceError:
            raise
        except Exception:
            raise SourceError("PDF_PARSE_FAILED") from None
        if not any(p["text"].strip() for p in pages):
            raise SourceError("PDF_NEEDS_OCR")
        return pages
    if not filename.lower().endswith((".txt", ".md")):
        raise SourceError("UNSUPPORTED_FILE_TYPE")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeError:
        raise SourceError("UTF8_TEXT_REQUIRED") from None
    return [{"text": text, "page": None, "locator": ""}]


def chunks(pages):
    if sum(len(page["text"]) for page in pages) > MAX_TEXT:
        raise SourceError("TEXT_LIMIT_EXCEEDED")
    result = []
    for page in pages:
        text = " ".join(page["text"].split())
        for offset in range(0, len(text), 1050):
            content = text[offset : offset + 1200]
            if not content.strip() or not any(c.isalnum() for c in content):
                continue
            result.append(
                {"content": content, "page": page.get("page"), "locator": page.get("locator", "")}
            )
            if len(result) > MAX_CHUNKS:
                raise SourceError("CHUNK_LIMIT_EXCEEDED")
    if not result:
        raise SourceError("NO_READABLE_TEXT")
    return result
