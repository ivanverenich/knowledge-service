"""Build small PDFs in tests, so fixtures are readable code instead of binaries.

Text uses the built-in Helvetica font, which covers ASCII only.
"""

import secrets
from io import BytesIO

from pypdf import PdfWriter

IMAGE_ONLY = b"q 100 0 0 100 72 600 cm BI /W 1 /H 1 /CS /G /BPC 8 ID \xff EI Q"


def text_page(*lines: tuple[int, int, str]) -> bytes:
    """Return page drawing commands that place each `(x, y, text)` line."""
    commands = [b"BT /F1 12 Tf"]
    for x, y, text in lines:
        escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        commands.append(f"1 0 0 1 {x} {y} Tm ({escaped}) Tj".encode("ascii"))
    commands.append(b"ET")
    return b"\n".join(commands)


def build_pdf(*pages: bytes, form: bytes | None = None) -> bytes:
    """Return a valid PDF whose pages draw the given commands.

    With `form`, the PDF also holds a form object, a reusable drawing that
    any page can paint with the command `/X1 Do`.
    """
    page_ids = [4 + 2 * index for index in range(len(pages))]
    form_id = 4 + 2 * len(pages)
    resources = "/Font << /F1 3 0 R >>"
    if form is not None:
        resources += f" /XObject << /X1 {form_id} 0 R >>"
    kids = " ".join(f"{page_id} 0 R" for page_id in page_ids)
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode(),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    for page_id, commands in zip(page_ids, pages, strict=True):
        objects.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            + f"/Resources << {resources} >> /Contents {page_id + 1} 0 R >>".encode()
        )
        objects.append(_stream(b"", commands))
    if form is not None:
        objects.append(
            _stream(
                b"/Type /XObject /Subtype /Form /BBox [0 0 612 792] "
                b"/Resources << /Font << /F1 3 0 R >> >> ",
                form,
            )
        )
    out = BytesIO()
    out.write(b"%PDF-1.7\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(f"{number} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets:
        out.write(f"{offset:010d} 00000 n \n".encode())
    out.write(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref}\n%%EOF\n".encode()
    )
    return out.getvalue()


def _stream(dictionary: bytes, data: bytes) -> bytes:
    return (
        b"<< "
        + dictionary
        + f"/Length {len(data)} >>\nstream\n".encode()
        + data
        + b"\nendstream"
    )


def encrypt(pdf: bytes, *, require_password: bool) -> bytes:
    """Return `pdf` encrypted with AES-256 under random passwords.

    Without `require_password`, the user password is empty, which is how
    "protected" PDFs that anyone may open but not edit are made.
    """
    writer = PdfWriter(clone_from=BytesIO(pdf))
    writer.encrypt(
        user_password=secrets.token_urlsafe(16) if require_password else "",
        owner_password=secrets.token_urlsafe(16),
        algorithm="AES-256",
    )
    out = BytesIO()
    writer.write(out)
    return out.getvalue()
