# Purpose: Test fixture generator for modern and legacy document formats.
# What the code does:
#   - Creates valid test documents (.pdf, .docx, .xlsx, .pptx, .txt, .csv, .xls) with known keywords.
#   - Uses CJK font for PDF generation so Chinese characters are properly embedded.
# Usage notes, dependencies, or assumptions:
#   - Requires pymupdf, docx, openpyxl, pptx, xlwt.

import os
from pathlib import Path


def generate_all_samples(target_dir: str):
    """Generate sample files of various formats containing target test keywords."""
    out_dir = Path(target_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. Text sample
    txt_path = out_dir / "sample_readme.txt"
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write("這是普通文字測試文件。\n關鍵字：專案預算 2026年度。\n機密等級：內部公開。")

    # 2. PDF sample (PyMuPDF with china-t font)
    try:
        import pymupdf

        pdf_path = out_dir / "sample_report.pdf"
        doc = pymupdf.open()
        page1 = doc.new_page()
        page1.insert_text(
            (50, 72),
            "PDF 測試文件 第一頁\n專案預算 審查會議記錄\n重點關注 2026年度 資本支出。",
            fontname="china-t",
            fontsize=12,
        )
        page2 = doc.new_page()
        page2.insert_text(
            (50, 72),
            "PDF 測試文件 第二頁\n決議事項：核准 研發投資 計畫案。",
            fontname="china-t",
            fontsize=12,
        )
        doc.save(str(pdf_path))
        doc.close()
    except Exception as e:
        print(f"Failed to generate sample PDF: {e}")

    # 3. Word DOCX sample
    try:
        import docx

        docx_path = out_dir / "sample_contract.docx"
        doc = docx.Document()
        doc.add_heading("合作意向合約書", 0)
        doc.add_paragraph("本合約由甲方與乙方共同簽署，有效期限至 2026年度 結束。")
        doc.add_paragraph("關鍵字：保密協定條款 與 智財權歸屬。")

        table = doc.add_table(rows=2, cols=2)
        table.cell(0, 0).text = "項目"
        table.cell(0, 1).text = "金額"
        table.cell(1, 0).text = "專案預算"
        table.cell(1, 1).text = "NT$ 5,000,000"
        doc.save(str(docx_path))
    except Exception as e:
        print(f"Failed to generate sample DOCX: {e}")

    # 4. Excel XLSX sample
    try:
        import openpyxl

        xlsx_path = out_dir / "sample_financial.xlsx"
        wb = openpyxl.Workbook()
        ws1 = wb.active
        ws1.title = "損益表"
        ws1.append(["年度", "科目", "金額"])
        ws1.append(["2026年度", "專案預算", 8800000])
        ws1.append(["2026年度", "研發投資", 3200000])

        ws2 = wb.create_sheet(title="資本支出")
        ws2.append(["編號", "說明", "核准狀態"])
        ws2.append(["CAP-01", "機房升級採購案", "審查通過"])
        wb.save(str(xlsx_path))
    except Exception as e:
        print(f"Failed to generate sample XLSX: {e}")

    # 5. Legacy Excel XLS sample (xlwt)
    try:
        import xlwt

        xls_path = out_dir / "sample_legacy_financial.xls"
        book = xlwt.Workbook(encoding="utf-8")
        sheet1 = book.add_sheet("舊版損益表")
        sheet1.write(0, 0, "年度")
        sheet1.write(0, 1, "科目")
        sheet1.write(0, 2, "金額")
        sheet1.write(1, 0, "2026年度")
        sheet1.write(1, 1, "專案預算")
        sheet1.write(1, 2, "6600000")
        book.save(str(xls_path))
    except Exception as e:
        print(f"Failed to generate sample XLS: {e}")

    # 6. PowerPoint PPTX sample
    try:
        import pptx
        from pptx.util import Inches

        pptx_path = out_dir / "sample_presentation.pptx"
        prs = pptx.Presentation()
        blank_slide_layout = prs.slide_layouts[6]
        slide1 = prs.slides.add_slide(blank_slide_layout)
        txBox = slide1.shapes.add_textbox(Inches(1), Inches(1), Inches(6), Inches(2))
        tf = txBox.text_frame
        p = tf.paragraphs[0]
        p.text = "季度營運業務報告"
        p2 = tf.add_paragraph()
        p2.text = "主題：2026年度 專案預算 與 組織願景。"

        slide2 = prs.slides.add_slide(blank_slide_layout)
        txBox2 = slide2.shapes.add_textbox(Inches(1), Inches(1), Inches(6), Inches(2))
        tf2 = txBox2.text_frame
        tf2.text = "第二投影片：行動方針與進度追蹤。"
        notes = slide2.notes_slide
        notes.notes_text_frame.text = "主講人備忘錄：特別提醒保密協定條款。"

        prs.save(str(pptx_path))
    except Exception as e:
        print(f"Failed to generate sample PPTX: {e}")


if __name__ == "__main__":
    generate_all_samples(os.path.join(os.path.dirname(__file__), "sample_files"))
