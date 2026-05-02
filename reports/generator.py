"""
Report generation: Excel and CSV exports.
"""
import os
import json
from datetime import datetime
from typing import List, Dict, Any

try:
    import openpyxl
    from openpyxl.styles import (
        Font, PatternFill, Alignment, Border, Side, numbers
    )
    from openpyxl.utils import get_column_letter
    from openpyxl.chart import BarChart, LineChart, Reference
    EXCEL_AVAILABLE = True
except ImportError:
    EXCEL_AVAILABLE = False

REPORTS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "reports")

COLORS = {
    "header_bg": "1F4E79",
    "header_fg": "FFFFFF",
    "subheader_bg": "2E75B6",
    "subheader_fg": "FFFFFF",
    "good": "C6EFCE",
    "warn": "FFEB9C",
    "bad": "FFC7CE",
    "alt_row": "D6E4F0",
    "border": "BDD7EE",
}


def _header_style(ws, row, col, value, bg=None, fg=None, bold=True):
    cell = ws.cell(row=row, column=col, value=value)
    if not EXCEL_AVAILABLE:
        return cell
    bg = bg or COLORS["header_bg"]
    fg = fg or COLORS["header_fg"]
    cell.font = Font(bold=bold, color=fg, size=10)
    cell.fill = PatternFill("solid", fgColor=bg)
    cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    return cell


def _value_cell(ws, row, col, value, number_format=None, color=None):
    cell = ws.cell(row=row, column=col, value=value)
    if not EXCEL_AVAILABLE:
        return cell
    cell.alignment = Alignment(horizontal="center", vertical="center")
    if number_format:
        cell.number_format = number_format
    if color:
        cell.fill = PatternFill("solid", fgColor=color)
    return cell


def _thin_border():
    s = Side(style="thin", color=COLORS["border"])
    return Border(left=s, right=s, top=s, bottom=s)


def export_inbound_excel(campaign_name: str, plan: List[Dict], summary: Dict, params: Dict) -> str:
    """Export inbound staffing plan to Excel."""
    os.makedirs(REPORTS_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"inbound_{campaign_name.replace(' ', '_')}_{timestamp}.xlsx"
    filepath = os.path.join(REPORTS_DIR, filename)

    if not EXCEL_AVAILABLE:
        # Fallback: CSV
        return export_plan_csv(campaign_name, plan, "inbound")

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Plan de Dotación"

    # Title
    ws.merge_cells("A1:I1")
    title_cell = ws["A1"]
    title_cell.value = f"WFM — Campaña Entrante: {campaign_name}"
    title_cell.font = Font(bold=True, size=14, color=COLORS["header_fg"])
    title_cell.fill = PatternFill("solid", fgColor=COLORS["header_bg"])
    title_cell.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 28

    # Parameters block
    ws.merge_cells("A2:I2")
    ws["A2"].value = f"Parámetros: AHT={params.get('aht_seconds',0)}s | NS={params.get('target_sl',0)*100:.0f}% en {params.get('target_seconds',0)}s | Shrinkage={params.get('shrinkage',0)*100:.0f}% | Intervalo={params.get('interval_minutes',30)}min"
    ws["A2"].font = Font(size=9, italic=True, color="444444")
    ws["A2"].alignment = Alignment(horizontal="center")

    # Summary row
    row = 3
    ws.merge_cells(f"A{row}:I{row}")
    ws[f"A{row}"].value = (
        f"Resumen: Total llamadas={summary.get('total_calls',0):,.0f} | "
        f"Pico={summary.get('peak_interval','')} ({summary.get('peak_calls',0):.0f} calls) | "
        f"NS Prom={summary.get('average_service_level',0):.1f}% | "
        f"Ocu Prom={summary.get('average_occupancy',0):.1f}% | "
        f"ASA Prom={summary.get('average_asa_seconds',0):.0f}s"
    )
    ws[f"A{row}"].font = Font(size=9, bold=True, color=COLORS["header_fg"])
    ws[f"A{row}"].fill = PatternFill("solid", fgColor=COLORS["subheader_bg"])
    ws[f"A{row}"].alignment = Alignment(horizontal="center")

    # Headers
    headers = ["Intervalo", "Llamadas", "Intensidad (Erl)", "Agentes Prod.", "Agentes Brutos",
               "Nivel Servicio", "ASA (seg)", "Ocupación", "Troncales"]
    row = 4
    for c, h in enumerate(headers, 1):
        cell = _header_style(ws, row, c, h)
        cell.border = _thin_border()
        ws.column_dimensions[get_column_letter(c)].width = 14

    # Data rows
    row = 5
    for i, interval in enumerate(plan):
        bg = COLORS["alt_row"] if i % 2 == 0 else None
        _value_cell(ws, row, 1, interval["interval"], color=bg)
        _value_cell(ws, row, 2, interval["calls"], "#,##0.0", color=bg)
        _value_cell(ws, row, 3, interval["traffic_intensity"], "0.000", color=bg)
        _value_cell(ws, row, 4, interval["productive_agents"], "#,##0", color=bg)
        _value_cell(ws, row, 5, interval["gross_agents"], "#,##0", color=bg)

        sl = interval["service_level"]
        sl_color = COLORS["good"] if sl >= 0.80 else (COLORS["warn"] if sl >= 0.70 else COLORS["bad"])
        _value_cell(ws, row, 6, sl, "0.0%", color=sl_color)

        _value_cell(ws, row, 7, interval["asa_seconds"], "#,##0.0", color=bg)

        occ = interval["occupancy"]
        occ_color = COLORS["good"] if occ <= 0.85 else (COLORS["warn"] if occ <= 0.90 else COLORS["bad"])
        _value_cell(ws, row, 8, occ, "0.0%", color=occ_color)
        _value_cell(ws, row, 9, interval["trunks_needed"], "#,##0", color=bg)

        for c in range(1, 10):
            ws.cell(row=row, column=c).border = _thin_border()
        row += 1

    # Chart — agents and calls
    chart = BarChart()
    chart.type = "col"
    chart.title = "Dotación por Intervalo"
    chart.y_axis.title = "Agentes"
    chart.x_axis.title = "Intervalo"
    chart.style = 10
    chart.width = 28
    chart.height = 14

    agents_data = Reference(ws, min_col=5, min_row=4, max_row=row - 1)
    calls_data = Reference(ws, min_col=2, min_row=4, max_row=row - 1)
    cats = Reference(ws, min_col=1, min_row=5, max_row=row - 1)
    chart.add_data(agents_data, titles_from_data=True)
    chart.set_categories(cats)

    chart_ws = wb.create_sheet("Gráfico Dotación")
    chart_ws.add_chart(chart, "A1")

    wb.save(filepath)
    return filepath


def export_outbound_excel(campaign_name: str, result: Dict, plan: List[Dict]) -> str:
    """Export outbound campaign plan."""
    os.makedirs(REPORTS_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"outbound_{campaign_name.replace(' ', '_')}_{timestamp}.xlsx"
    filepath = os.path.join(REPORTS_DIR, filename)

    if not EXCEL_AVAILABLE:
        return export_plan_csv(campaign_name, plan, "outbound")

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Plan Campaña Saliente"

    ws.merge_cells("A1:F1")
    ws["A1"].value = f"WFM — Campaña Saliente: {campaign_name}"
    ws["A1"].font = Font(bold=True, size=13, color="FFFFFF")
    ws["A1"].fill = PatternFill("solid", fgColor="C55A11")
    ws["A1"].alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 26

    # Summary block
    params_labels = [
        ("Modo de Marcación", result.get("dial_mode", "")),
        ("Registros a contactar", f"{result.get('records_to_contact', 0):,}"),
        ("Tasa de Contacto", f"{result.get('contact_rate_pct', 0):.1f}%"),
        ("Tasa de Persona Correcta", f"{result.get('right_party_rate_pct', 0):.1f}%"),
        ("AHT (segundos)", result.get("aht_seconds", 0)),
        ("Horas disponibles", result.get("available_hours", 0)),
        ("Agentes Productivos", result.get("productive_agents", 0)),
        ("Contactos Persona Correcta", f"{result.get('right_party_contacts', 0):,.0f}"),
        ("Abandono Estimado", f"{result.get('estimated_abandonment_pct', 0):.2f}%"),
        ("Horas de campaña estimadas", f"{result.get('estimated_campaign_hours', 0):.1f}h"),
        ("Cumple norma de abandono", "✓ SÍ" if result.get("abandonment_compliant") else "✗ NO"),
    ]
    for r, (label, value) in enumerate(params_labels, 2):
        ws.cell(row=r, column=1, value=label).font = Font(bold=True, size=9)
        ws.cell(row=r, column=2, value=str(value)).alignment = Alignment(horizontal="left")

    # Hourly plan
    if plan:
        start_row = len(params_labels) + 4
        headers = ["Hora", "Meta Contactos", "Marcaciones", "Agentes Prod.", "Agentes Brutos", "Ocupación"]
        for c, h in enumerate(headers, 1):
            _header_style(ws, start_row, c, h, bg="C55A11")
            ws.column_dimensions[get_column_letter(c)].width = 16
        for i, row_data in enumerate(plan, start_row + 1):
            bg = COLORS["alt_row"] if i % 2 == 0 else None
            _value_cell(ws, i, 1, row_data.get("hour"), color=bg)
            _value_cell(ws, i, 2, row_data.get("target_contacts"), "#,##0", color=bg)
            _value_cell(ws, i, 3, row_data.get("dials_needed"), "#,##0", color=bg)
            _value_cell(ws, i, 4, row_data.get("productive_agents"), "#,##0", color=bg)
            _value_cell(ws, i, 5, row_data.get("gross_agents"), "#,##0", color=bg)
            _value_cell(ws, i, 6, row_data.get("occupancy", 0) / 100, "0.0%", color=bg)

    wb.save(filepath)
    return filepath


def export_plan_csv(campaign_name: str, plan: List[Dict], campaign_type: str) -> str:
    """Fallback CSV export."""
    os.makedirs(REPORTS_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{campaign_type}_{campaign_name.replace(' ', '_')}_{timestamp}.csv"
    filepath = os.path.join(REPORTS_DIR, filename)

    if not plan:
        return filepath

    import csv
    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=plan[0].keys())
        writer.writeheader()
        writer.writerows(plan)
    return filepath
