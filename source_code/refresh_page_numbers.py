"""
refresh_page_numbers.py -- Refresh the page numbers in the report's contents tables.

The table of contents, list of figures and list of tables are plain tables (not Word
fields), so their page numbers go stale whenever the text changes. This script renders
the report to PDF with Word, finds each heading/caption in the PDF, writes the page
numbers back into the .docx, then renders again to confirm nothing moved.

Needs Microsoft Word (it is driven through PowerShell COM) and PyMuPDF.
Also writes the *_PREVIEW.pdf next to the report.
"""

import os
import re
import subprocess
import sys

import docx
import fitz  # PyMuPDF

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORT = os.path.join(ROOT, "PBL_Report_EV_Charging_Demand_Forecasting.docx")
PREVIEW = os.path.join(ROOT, "PBL_Report_EV_Charging_Demand_Forecasting_PREVIEW.pdf")


def export_pdf(docx_path, pdf_path):
    script = (
        "$ErrorActionPreference='Stop';"
        "$w=New-Object -ComObject Word.Application;$w.Visible=$false;$w.DisplayAlerts=0;"
        "try{{$d=$w.Documents.Open('{0}',$false,$true);"
        "$d.ExportAsFixedFormat('{1}',17);$d.Close(0)}}finally{{$w.Quit()}}"
    ).format(docx_path.replace("'", "''"), pdf_path.replace("'", "''"))
    result = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                            capture_output=True, text=True, timeout=240)
    if result.returncode != 0 or not os.path.exists(pdf_path):
        raise SystemExit("Word export failed:\n" + result.stdout + result.stderr)


def norm(s):
    return re.sub(r"\s+", " ", s).strip()


def page_lines(pdf_path):
    pdf = fitz.open(pdf_path)
    pages = [[norm(line) for line in page.get_text().splitlines() if line.strip()] for page in pdf]
    return pages


def find_pages(pages):
    """Map every contents-table key to its printed page number."""
    start = next(i for i, lines in enumerate(pages) if "CHAPTER 1" in lines and "INTRODUCTION" in lines)

    def first_page(predicate):
        for i in range(start, len(pages)):
            if any(predicate(line) for line in pages[i]):
                return i - start + 1
        return None

    return start, first_page


def main():
    tmp_pdf = os.path.join(os.environ.get("TEMP", ROOT), "report_pagecheck.pdf")
    previous = None
    for attempt in range(3):
        export_pdf(REPORT, tmp_pdf)
        pages = page_lines(tmp_pdf)
        start, first_page = find_pages(pages)

        d = docx.Document(REPORT)
        changed, missing = 0, []

        def write(cell, number):
            nonlocal changed
            if number is None:
                return False
            p = cell.paragraphs[0]
            if p.text.strip() != str(number):
                p.runs[0].text = str(number)
                for r in p.runs[1:]:
                    r._r.getparent().remove(r._r)
                changed += 1
            return True

        # table of contents: [number | title | page]
        for row in d.tables[2].rows[1:]:
            title = norm(row.cells[1].text)
            if not title:
                continue
            num = norm(row.cells[0].text)
            if re.match(r"^\d+\.\d", title):          # a subsection heading
                key = title
                pg = first_page(lambda ln, k=key: ln == k)
            elif num:                                   # a chapter: "CHAPTER n" then its title
                pg = first_page(lambda ln, n=num: ln == "CHAPTER " + n)
            else:                                       # REFERENCES / APPENDIX
                pg = first_page(lambda ln, t=title: ln == t)
            if not write(row.cells[2], pg):
                missing.append(title)

        # list of figures / list of tables: caption lines begin with "Figure n.n" / "Table n.n"
        for tbl, word in ((d.tables[3], "Figure"), (d.tables[4], "Table")):
            for row in tbl.rows[1:]:
                key = norm(row.cells[0].text)
                if not key:
                    continue
                pat = re.compile(r"^{0} {1}\s+[A-Z]".format(word, re.escape(key)))
                pg = first_page(lambda ln, p=pat: bool(p.match(ln)))
                if not write(row.cells[2], pg):
                    missing.append(word + " " + key)

        if missing:
            print("Could not locate:", ", ".join(missing))
        d.save(REPORT)
        print("pass {0}: {1} page numbers changed; body starts at PDF page {2}; {3} PDF pages total".format(
            attempt + 1, changed, start + 1, len(pages)))
        if changed == 0:
            break
        previous = changed
    export_pdf(REPORT, PREVIEW)
    print("Wrote", os.path.basename(PREVIEW))
    return 0


if __name__ == "__main__":
    sys.exit(main())
