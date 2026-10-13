"""PDF helpers: pdfplumber text extraction and PyMuPDF page rendering."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class DocumentContent:
    path: str
    text: str = ""
    page_count: int = 0
    images: list[bytes] = field(default_factory=list)


def extract_text(path: str, max_pages: int = 5) -> tuple[str, int]:
    import pdfplumber

    with pdfplumber.open(path) as pdf:
        pages = pdf.pages[:max_pages]
        return "\n".join((p.extract_text() or "") for p in pages), len(pdf.pages)


def render_pages(path: str, dpi: int = 150, max_pages: int = 5) -> list[bytes]:
    import pymupdf

    out = []
    with pymupdf.open(path) as doc:
        for page in list(doc)[:max_pages]:
            out.append(page.get_pixmap(dpi=dpi).tobytes("png"))
    return out


def load_document(path: str, with_images: bool, dpi: int = 150, max_pages: int = 5) -> DocumentContent:
    text, count = extract_text(path, max_pages)
    images = render_pages(path, dpi, max_pages) if with_images else []
    return DocumentContent(path=path, text=text, page_count=count, images=images)
