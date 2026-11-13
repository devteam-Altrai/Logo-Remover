import fitz


def anora_pdf(input_path, output_path):
    doc = fitz.open(input_path)

    for page in doc:
        logo_rect = fitz.Rect(868.5, 685.5, 1160, 718.5)
        page.add_redact_annot(logo_rect, fill=(1, 1, 1))

        description_rect = fitz.Rect(735.5, 773, 865, 812)
        page.add_redact_annot(description_rect, fill=(1, 1, 1))

        page.apply_redactions()

    doc.save(output_path, garbage=4, clean=True)
    doc.close()