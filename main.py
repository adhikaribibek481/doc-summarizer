"""
FastAPI Document Summarizer Application
Routes:
  GET  /            — Home page (enter folder ID)
  POST /summarize   — Run the full pipeline
  GET  /download/csv  — Download CSV report
  GET  /download/pdf  — Download PDF report
"""

import os
import io
import csv
import logging
import tempfile
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Form, Request, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from dotenv import load_dotenv

from google_drive import authenticate, list_files, download_file
from document_parser import parse_document
from summarizer import SummarizationEngine

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(name)s | %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="📄 Document Summarizer", version="1.0.0")
templates = Jinja2Templates(directory="templates")

# In-memory store for latest results (single-user app)
_latest_results: list[dict] = []
_engine = SummarizationEngine()


# ─── Home ──────────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    folder_id = os.getenv("GOOGLE_DRIVE_FOLDER_ID", "")
    return templates.TemplateResponse(
        "index.html", {"request": request, "default_folder_id": folder_id}
    )


# ─── Summarize Pipeline ────────────────────────────────────────────────────────
@app.post("/summarize", response_class=HTMLResponse)
async def summarize(request: Request, folder_id: str = Form(...)):
    global _latest_results
    _latest_results = []

    if not folder_id.strip():
        raise HTTPException(status_code=400, detail="Folder ID cannot be empty.")

    errors: list[str] = []

    try:
        # 1. Authenticate with Google Drive
        service = authenticate()

        # 2. List supported files in folder
        files = list_files(service, folder_id.strip())
        if not files:
            return templates.TemplateResponse(
                "results.html",
                {
                    "request": request,
                    "results": [],
                    "folder_id": folder_id,
                    "message": "No supported files (PDF, DOCX, TXT) found in that folder.",
                },
            )

        # 3. Download → Parse → Summarize each file
        for file_meta in files:
            file_id   = file_meta["id"]
            file_name = file_meta["name"]
            mime_type = file_meta["mimeType"]
            web_link  = file_meta.get("webViewLink", "#")

            try:
                # Download
                local_path = download_file(service, file_id, file_name, mime_type)

                # Parse text
                text = parse_document(local_path, mime_type)

                # Summarize
                result = _engine.summarize(text)

                _latest_results.append(
                    {
                        "file_name":  file_name,
                        "summary":    result["summary"],
                        "model_used": result["model_used"],
                        "web_link":   web_link,
                        "error":      result.get("error"),
                    }
                )
            except Exception as exc:
                logger.error(f"Failed to process '{file_name}': {exc}")
                errors.append(f"{file_name}: {exc}")
                _latest_results.append(
                    {
                        "file_name":  file_name,
                        "summary":    f"Processing failed: {exc}",
                        "model_used": "none",
                        "web_link":   web_link,
                        "error":      str(exc),
                    }
                )

    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    except Exception as exc:
        logger.error(f"Pipeline error: {exc}")
        raise HTTPException(status_code=500, detail=f"Pipeline error: {exc}")

    return templates.TemplateResponse(
        "results.html",
        {
            "request":   request,
            "results":   _latest_results,
            "folder_id": folder_id,
            "errors":    errors,
            "message":   None,
        },
    )


# ─── CSV Download ──────────────────────────────────────────────────────────────
@app.get("/download/csv")
async def download_csv():
    if not _latest_results:
        raise HTTPException(status_code=404, detail="No results to export. Run /summarize first.")

    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=["file_name", "summary", "model_used", "web_link", "error"],
        extrasaction="ignore",
    )
    writer.writeheader()
    writer.writerows(_latest_results)
    output.seek(0)

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=summaries.csv"},
    )


# ─── PDF Download ──────────────────────────────────────────────────────────────
@app.get("/download/pdf")
async def download_pdf():
    if not _latest_results:
        raise HTTPException(status_code=404, detail="No results to export. Run /summarize first.")

    from fpdf import FPDF

    class PDF(FPDF):
        def header(self):
            self.set_font("Helvetica", "B", 15)
            self.set_fill_color(30, 30, 46)
            self.set_text_color(205, 214, 244)
            self.cell(0, 12, "Document Summarizer Report", border=0, align="C", fill=True)
            self.ln(8)

        def footer(self):
            self.set_y(-15)
            self.set_font("Helvetica", "I", 8)
            self.set_text_color(150, 150, 150)
            self.cell(0, 10, f"Page {self.page_no()}", align="C")

    pdf = PDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    for i, item in enumerate(_latest_results, 1):
        # File heading
        pdf.set_font("Helvetica", "B", 12)
        pdf.set_text_color(30, 30, 46)
        pdf.set_fill_color(203, 166, 247)
        pdf.cell(0, 9, f"{i}. {item['file_name']}", border=0, fill=True)
        pdf.ln(4)

        # Model badge
        pdf.set_font("Helvetica", "I", 9)
        pdf.set_text_color(100, 100, 150)
        pdf.cell(0, 6, f"Model: {item['model_used']}")
        pdf.ln(5)

        # Summary
        pdf.set_font("Helvetica", "", 10)
        pdf.set_text_color(30, 30, 46)
        pdf.multi_cell(0, 6, item["summary"])
        pdf.ln(6)

        # Separator
        pdf.set_draw_color(200, 200, 220)
        pdf.line(10, pdf.get_y(), 200, pdf.get_y())
        pdf.ln(4)

    pdf_bytes = pdf.output()

    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": "attachment; filename=summaries.pdf"},
    )


# ─── Run ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
