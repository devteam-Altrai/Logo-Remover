import fitz

def sanmina_pdf(input_path, output_path):
    doc = fitz.open(input_path)

    for page in doc:
        logo_rect = fitz.Rect(868, 588, 1175, 640)
        page.add_redact_annot(logo_rect, fill=(1, 1, 1))

        description_rect = fitz.Rect(542, 718, 665, 764)
        page.add_redact_annot(description_rect, fill=(1, 1, 1))

        page.apply_redactions()

    doc.save(output_path, garbage=4, clean=True)
    doc.close()