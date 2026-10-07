"""
PDF Export Utility for Parle-G Supply Chain Executive Reports.
=============================================================
Converts markdown reports and RAG incident resolutions into clean,
professionally-styled PDF documents using fpdf2.
"""

from datetime import datetime
import re
from typing import Any, Dict, Optional
from fpdf import FPDF


def clean_text_for_pdf(text: str) -> str:
    """Replaces emojis and unicode symbols with clean text equivalents for PDF rendering."""
    emoji_map = {
        "🟢": "[PASS] ",
        "🟡": "[WARNING] ",
        "🔴": "[CRITICAL] ",
        "🟠": "[PENDING] ",
        "✅": "[PASS] ",
        "❌": "[FAIL] ",
        "⚠️": "[ALERT] ",
        "📦": "",
        "🤖": "",
        "📋": "",
        "🔁": "",
        "🏭": "",
        "⚙️": "",
        "📝": "",
        "🧠": "",
        "🔍": "",
        "🚀": "",
        "💡": "",
        "🛡️": "",
        "🚚": "",
        "📈": "",
        "📉": "",
        "🎯": "",
        "🎖️": "",
        "📌": "",
        "⬇": "",
        "▶": "",
        "—": "-",
        "–": "-",
        "“": '"',
        "”": '"',
        "‘": "'",
        "’": "'",
        "…": "...",
        "➔": "->",
        "→": "->",
    }
    for emoji, rep in emoji_map.items():
        text = text.replace(emoji, rep)

    # Replace any non-latin1 characters with '?' to ensure safe rendering
    return text.encode("latin-1", "replace").decode("latin-1")


class ParleGReportPDF(FPDF):
    """Custom PDF layout with branded Parle-G header, footer, and styling."""

    def __init__(self, title: str = "Parle-G Executive Report"):
        super().__init__(orientation="P", unit="mm", format="A4")
        self.doc_title = title
        self.set_auto_page_break(auto=True, margin=18)
        self.set_margins(left=18, top=18, right=18)

    def header(self):
        # Top banner accent
        self.set_fill_color(30, 58, 138)  # Deep Navy Blue
        self.rect(0, 0, 210, 8, "F")

        # Header Title
        self.set_font("Helvetica", "B", 13)
        self.set_text_color(30, 41, 59)
        self.set_y(12)
        self.cell(0, 7, clean_text_for_pdf("PARLE-G AGENTIC AI SUPPLY CHAIN ENGINE"), ln=True)

        self.set_font("Helvetica", "", 9)
        self.set_text_color(100, 116, 139)
        self.cell(0, 5, clean_text_for_pdf(f"{self.doc_title} | Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"), ln=True)

        # Subtle divider line
        self.set_draw_color(226, 232, 240)
        self.set_line_width(0.4)
        self.line(18, 26, 192, 26)
        self.ln(6)

    def footer(self):
        self.set_y(-14)
        self.set_font("Helvetica", "", 8)
        self.set_text_color(148, 163, 184)
        self.cell(0, 10, f"Page {self.page_no()} of {{nb}}  |  Confidential - Internal Operations", align="C")


def export_report_to_pdf(
    report_text: str,
    title: str = "Parle-G Executive Supply Chain Report",
    summary_stats: Optional[Dict[str, Any]] = None,
) -> bytes:
    """
    Parses a markdown report and renders it into a professionally styled PDF byte stream.
    """
    pdf = ParleGReportPDF(title=title)
    pdf.alias_nb_pages()
    pdf.add_page()

    # Optional Summary KPI Bar
    if summary_stats:
        total = summary_stats.get("total_orders", 0)
        fulfilled = summary_stats.get("fulfilled_directly", 0)
        transfers = summary_stats.get("transfers_created", 0)
        unfulfilled = summary_stats.get("unfulfillable", 0)

        pdf.set_fill_color(241, 245, 249)  # Light slate box
        pdf.set_draw_color(203, 213, 225)
        pdf.rect(18, pdf.get_y(), 174, 16, "FD")

        pdf.set_font("Helvetica", "B", 9)
        pdf.set_text_color(30, 41, 59)
        pdf.set_xy(22, pdf.get_y() + 2)
        kpi_line = f"Total Orders: {total}   |   Directly Fulfilled: {fulfilled}   |   Transfers: {transfers}   |   Unfulfilled: {unfulfilled}"
        pdf.cell(166, 6, clean_text_for_pdf(kpi_line), ln=True)
        pdf.set_font("Helvetica", "", 8)
        pdf.set_text_color(100, 116, 139)
        pdf.set_x(22)
        pdf.cell(166, 5, clean_text_for_pdf("Autonomous Fulfillment Engine  *  Spatial Routing  *  Rebalancing  *  Guardrails"), ln=True)
        pdf.ln(6)

    # Parse and render markdown lines
    lines = report_text.split("\n")
    in_code_block = False

    for raw_line in lines:
        line = raw_line.strip()

        # Handle code blocks
        if line.startswith("```"):
            in_code_block = not in_code_block
            continue

        if not line:
            pdf.ln(2.5)
            continue

        cleaned = clean_text_for_pdf(line)

        # Main Header (# Header)
        if line.startswith("# "):
            heading = clean_text_for_pdf(re.sub(r"^#\s+", "", line))
            pdf.ln(3)
            pdf.set_font("Helvetica", "B", 14)
            pdf.set_text_color(30, 58, 138)  # Blue
            pdf.cell(0, 8, heading, ln=True)
            pdf.set_draw_color(191, 219, 254)
            pdf.set_line_width(0.3)
            pdf.line(18, pdf.get_y(), 192, pdf.get_y())
            pdf.ln(2)

        # Sub Header (## Subheader)
        elif line.startswith("## "):
            heading = clean_text_for_pdf(re.sub(r"^##\s+", "", line))
            pdf.ln(3)
            pdf.set_font("Helvetica", "B", 12)
            pdf.set_text_color(15, 23, 42)  # Dark slate
            pdf.cell(0, 7, heading, ln=True)
            pdf.ln(1)

        # Section (### Section or #### Sub-section)
        elif line.startswith("### ") or line.startswith("#### "):
            heading = clean_text_for_pdf(re.sub(r"^#{3,4}\s+", "", line))
            pdf.ln(2)
            pdf.set_font("Helvetica", "B", 10.5)
            pdf.set_text_color(51, 65, 85)
            pdf.cell(0, 6, heading, ln=True)
            pdf.ln(1)

        # Bullet points (- item or * item)
        elif line.startswith("- ") or line.startswith("* "):
            bullet_text = clean_text_for_pdf(re.sub(r"^[-*]\s+", "", line))
            # Remove bold markdown asterisks for clean PDF reading
            clean_item = re.sub(r"\*\*(.*?)\*\*", r"\1", bullet_text)
            pdf.set_font("Helvetica", "", 9.5)
            pdf.set_text_color(30, 41, 59)
            pdf.set_x(22)
            pdf.cell(4, 5, clean_text_for_pdf("-"), ln=False)
            pdf.multi_cell(166, 5, clean_item)

        # Numbered list (1. item)
        elif re.match(r"^\d+\.\s+", line):
            num_text = clean_text_for_pdf(re.sub(r"^\d+\.\s+", "", line))
            clean_item = re.sub(r"\*\*(.*?)\*\*", r"\1", num_text)
            pdf.set_font("Helvetica", "", 9.5)
            pdf.set_text_color(30, 41, 59)
            prefix = re.match(r"^\d+\.", line).group(0) + " "
            pdf.set_x(22)
            pdf.cell(6, 5, clean_text_for_pdf(prefix), ln=False)
            pdf.multi_cell(164, 5, clean_item)

        # Regular Paragraph
        else:
            clean_para = re.sub(r"\*\*(.*?)\*\*", r"\1", cleaned)
            pdf.set_font("Helvetica", "", 9.5)
            pdf.set_text_color(51, 65, 85)
            pdf.set_x(18)
            pdf.multi_cell(174, 5, clean_para)

    # Return bytes buffer
    return bytes(pdf.output())
