
import fitz


def asm_pdf(input_path, output_path):
    doc = fitz.open(input_path)

    words_to_white = [
        "ASM RELEASED FOR PRODUCTION ON 31-01-2026",
        "PROPRIETARY STATEMENT:  THIS DOCUMENT IS CONFIDENTIAL AND CONTAINS INFORMATION PROPRIETARY TO ASM TECHNOLOGIES LTD AND SHALL NOT BE REPRODUCED OR DISCLOSED TO THIRD PARTIES WITHOUT PRIOR WRITTEN CONSENT FROM ASM TECHNOLOGIES LTD.",
    ]

    for page in doc:
        logo_rect = fitz.Rect(945, 675, 1158, 738)
        page.add_redact_annot(logo_rect, fill=(1, 1, 1))

        for word in words_to_white:
            matches = page.search_for(word)

            for rect in matches:
                page.add_redact_annot(rect, fill=(1, 1, 1))

        # vertical_rect = fitz.Rect(1177, 170, 1190, 710)
        # page.add_redact_annot(vertical_rect, fill=(1, 1, 1))

        # description_rect = fitz.Rect(738.5, 658.5, 1160, 670)
        # page.add_redact_annot(description_rect, fill=(1, 1, 1))

        page.apply_redactions()

    doc.save(output_path, garbage=4, clean=True)
    doc.close()
