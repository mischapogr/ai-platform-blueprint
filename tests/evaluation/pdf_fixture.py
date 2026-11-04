"""One-page synthetic PDF, with an inert attachment for upload-size regression."""

from io import BytesIO

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

CANARY = "OBSERVABILITY-CONTENT-CANARY-731"


def synthetic_pdf(padding_bytes):
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
    content.set_data(
        ("BT /F1 12 Tf 30 200 Td (UploadQuartz synthetic PDF " + CANARY + ") Tj ET").encode()
    )
    page[NameObject("/Contents")] = writer._add_object(content)
    writer.add_attachment("synthetic-padding.bin", b"X" * padding_bytes)
    result = BytesIO()
    writer.write(result)
    return result.getvalue()
