import fitz

def vvdn_pdf(input_path, output_path):
    doc = fitz.open(input_path)

    for page in doc:
        logo_rect = fitz.Rect(968, 784.5, 1130, 813.5)
        page.add_redact_annot(logo_rect, fill=(1, 1, 1))

        description_rect = fitz.Rect(1, 300, 35, 960)
        page.add_redact_annot(description_rect, fill=(1, 1, 1))

        if page.search_for("PROJECT ID"):
            project_id_rect = fitz.Rect(970, 720, 1155, 738)
            page.add_redact_annot(project_id_rect, fill=(1, 1, 1))

        page.apply_redactions()

    doc.save(output_path, garbage=4, clean=True)
    doc.close()
