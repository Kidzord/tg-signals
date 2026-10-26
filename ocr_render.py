"""Рендер страниц PDF в PNG для OCR.

    python ocr_render.py "<pdf>" "<out_dir>" [dpi]
"""
import os
import sys

import pymupdf


def main() -> None:
    pdf, out_dir = sys.argv[1], sys.argv[2]
    dpi = int(sys.argv[3]) if len(sys.argv) > 3 else 200
    os.makedirs(out_dir, exist_ok=True)
    doc = pymupdf.open(pdf)
    for i, page in enumerate(doc, 1):
        path = os.path.join(out_dir, f"p{i:03d}.png")
        if os.path.exists(path):
            continue
        page.get_pixmap(dpi=dpi).save(path)
    print(f"{os.path.basename(pdf)}: страниц {len(doc)} → {out_dir}")


if __name__ == "__main__":
    main()
