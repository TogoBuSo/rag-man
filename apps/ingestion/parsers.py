from __future__ import annotations

import ipaddress
import socket
import tempfile
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
import trafilatura
from markitdown import MarkItDown

MAX_REDIRECTS = 5
FETCH_TIMEOUT_SECONDS = 20.0


class SSRFBlockedError(ValueError):
    """Raised when a URL points to a disallowed scheme or private/internal network address."""


def _check_ip_allowed(ip_str: str) -> None:
    ip = ipaddress.ip_address(ip_str)
    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    ):
        raise SSRFBlockedError(f"Blocked IP address: {ip_str}")


def validate_url_ssrf(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise SSRFBlockedError(f"Disallowed URL scheme: '{parsed.scheme}'")
    if not parsed.hostname:
        raise SSRFBlockedError("URL must include a hostname.")

    hostname = parsed.hostname
    try:
        _check_ip_allowed(hostname)
        return url
    except ValueError:
        pass

    try:
        addr_infos = socket.getaddrinfo(hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
    except socket.gaierror as exc:
        raise SSRFBlockedError(f"Unable to resolve hostname '{hostname}': {exc}") from exc

    if not addr_infos:
        raise SSRFBlockedError(f"No DNS records found for hostname '{hostname}'.")

    for _, _, _, _, sockaddr in addr_infos:
        _check_ip_allowed(str(sockaddr[0]))

    return url


def parse_url_to_markdown(url: str) -> str:
    current_url = validate_url_ssrf(url)
    headers = {"User-Agent": "rag-man-ingestion/1.0"}

    with httpx.Client(follow_redirects=False, timeout=FETCH_TIMEOUT_SECONDS) as client:
        for _ in range(MAX_REDIRECTS + 1):
            response = client.get(current_url, headers=headers)
            if response.status_code in (301, 302, 303, 307, 308):
                location = response.headers.get("location")
                if not location:
                    break
                current_url = validate_url_ssrf(urljoin(current_url, location))
                continue
            response.raise_for_status()
            html = response.text
            extracted = trafilatura.extract(
                html,
                url=current_url,
                output_format="markdown",
                include_links=True,
                include_tables=True,
            )
            if extracted and extracted.strip():
                return extracted.strip()
            return html.strip()

    raise SSRFBlockedError("Exceeded maximum redirect hops.")


def parse_file_to_markdown(
    filename: str,
    content: bytes | None = None,
    file_path: Path | None = None,
) -> str:
    suffix = Path(filename).suffix.lower()

    if file_path is not None and file_path.exists():
        if suffix in (".md", ".markdown", ".txt"):
            return file_path.read_text(encoding="utf-8", errors="replace").strip()
        md = MarkItDown()
        result = md.convert(str(file_path))
        text = getattr(result, "text_content", "") or ""
        if text.strip():
            return text.strip()
        return file_path.read_text(encoding="utf-8", errors="replace").strip()

    raw = content or b""
    if suffix in (".md", ".markdown", ".txt"):
        return raw.decode("utf-8", errors="replace").strip()

    with tempfile.NamedTemporaryFile(suffix=suffix or ".bin", delete=True) as tmp:
        tmp.write(raw)
        tmp.flush()
        md = MarkItDown()
        result = md.convert(tmp.name)
        text = getattr(result, "text_content", "") or ""
        if text.strip():
            return text.strip()

    return raw.decode("utf-8", errors="replace").strip()
