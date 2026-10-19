"""Build small PDFs in tests, so fixtures are readable code instead of binaries.

Text uses the built-in Helvetica font, which covers ASCII only.
"""

import secrets
import struct
import zlib
from io import BytesIO
from typing import Literal

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
    return _assemble(objects)


def compressed_streams_pdf(
    place: Literal["contents", "forms", "fonts"], *, count: int, size: int
) -> bytes:
    """Return a one-page PDF with `count` streams that each inflate to `size` bytes.

    The streams hold only spaces, so each compresses to a few kilobytes.
    `place` makes them the page's content streams, forms the page paints, or
    the /ToUnicode maps of fonts the page uses.
    """
    blob = zlib.compress(b" " * size, 9)
    ids = [5 + index for index in range(count)]
    contents = "/Contents 4 0 R"
    resources = ""
    commands = b""
    prefix = b""
    fonts: list[bytes] = []
    if place == "contents":
        contents = "/Contents [" + " ".join(f"{i} 0 R" for i in ids) + "]"
    elif place == "forms":
        resources = "/XObject << " + " ".join(f"/R{i} {i} 0 R" for i in ids) + " >>"
        commands = b" ".join(b"/R%d Do" % i for i in ids)
        prefix = b"/Type /XObject /Subtype /Form /BBox [0 0 1 1] "
    else:
        font_ids = [i + count for i in ids]
        resources = "/Font << " + " ".join(f"/R{i} {i} 0 R" for i in font_ids) + " >>"
        commands = (
            b"BT " + b" ".join(b"/R%d 12 Tf (a) Tj" % i for i in font_ids) + b" ET"
        )
        font = (
            "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /ToUnicode {} 0 R >>"
        )
        fonts = [font.format(i).encode() for i in ids]
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        + f"/Resources << {resources} >> {contents} >>".encode(),
        _stream(b"", commands),
        *(_stream(prefix + b"/Filter /FlateDecode ", blob) for _ in ids),
        *fonts,
    ]
    return _assemble(objects)


def _assemble(objects: list[bytes]) -> bytes:
    """Number the objects from 1 and write them with a cross-reference table."""
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


def object_stream_pdf(pages: int, filler: int) -> bytes:
    """Return a PDF whose dictionaries are packed into one object stream.

    Most PDF 1.5+ producers write files this way. Each page draws one line of
    text, and `filler` extra dictionaries make the object stream large.
    """
    page_ids = [3 + index for index in range(pages)]
    content_ids = [3 + pages + index for index in range(pages)]
    font_id = 3 + 2 * pages
    packed: dict[int, bytes] = {
        2: f"<< /Type /Pages /Kids [{' '.join(f'{i} 0 R' for i in page_ids)}] "
        f"/Count {pages} >>".encode(),
        font_id: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    }
    for page_id, content_id in zip(page_ids, content_ids, strict=True):
        packed[page_id] = (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            + f"/Resources << /Font << /F1 {font_id} 0 R >> >> "
            f"/Contents {content_id} 0 R >>".encode()
        )
    for number in range(font_id + 1, font_id + 1 + filler):
        packed[number] = b"<< /Filler [1 2 3 4] >>"
    stream_id = font_id + 1 + filler
    xref_id = stream_id + 1
    header = b""
    body = b""
    for number, dictionary in packed.items():
        header += b"%d %d " % (number, len(body))
        body += dictionary + b"\n"
    out = BytesIO()
    out.write(b"%PDF-1.7\n")
    offsets = {1: out.tell()}
    out.write(b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n")
    for index, content_id in enumerate(content_ids):
        offsets[content_id] = out.tell()
        commands = text_page((72, 720, f"Page {index + 1}."))
        out.write(f"{content_id} 0 obj\n".encode() + _stream(b"", commands))
        out.write(b"\nendobj\n")
    offsets[stream_id] = out.tell()
    data = zlib.compress(header + b"\n" + body)
    out.write(
        f"{stream_id} 0 obj\n".encode()
        + _stream(
            f"/Type /ObjStm /N {len(packed)} /First {len(header) + 1} "
            "/Filter /FlateDecode ".encode(),
            data,
        )
        + b"\nendobj\n"
    )
    offsets[xref_id] = out.tell()
    positions = {number: index for index, number in enumerate(packed)}
    rows = struct.pack(">BIH", 0, 0, 65535)
    for number in range(1, xref_id + 1):
        if number in positions:
            rows += struct.pack(">BIH", 2, stream_id, positions[number])
        else:
            rows += struct.pack(">BIH", 1, offsets[number], 0)
    out.write(
        f"{xref_id} 0 obj\n".encode()
        + _stream(
            f"/Type /XRef /Size {xref_id + 1} /W [1 4 2] /Root 1 0 R ".encode(),
            rows,
        )
        + f"\nendobj\nstartxref\n{offsets[xref_id]}\n%%EOF\n".encode()
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
