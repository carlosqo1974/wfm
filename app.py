"""
WFM — Workforce Management System for Contact Centers
Standalone web application — Flask + SQLite
"""
import json
import os
import sys
import math
from datetime import datetime
from flask import Flask, render_template, request, jsonify, redirect, url_for, send_file, flash

# Ensure local packages are importable
sys.path.insert(0, os.path.dirname(__file__))
os.environ["PATH"] = os.path.expanduser("~/.local/bin") + ":" + os.environ.get("PATH", "")

from models import init_db, SessionLocal, Campaign, IntervalVolume, HistoricalVolume, StaffingPlan, DBConnector, ImportTemplate, ShiftDefinition
from calculations.inbound import build_interval_plan, summary_stats, sensitivity_analysis
from calculations.outbound import agents_needed, build_outbound_interval_plan, compare_dial_modes
from calculations.chat import agents_for_chat, build_chat_interval_plan, concurrency_sensitivity
from calculations.forecast import forecast_volume, shrinkage_components
from calculations.peru_shifts import dimension_shifts_peru

app = Flask(__name__)
app.secret_key = "wfm-standalone-secret-2024"

init_db()


# ─── Helpers ──────────────────────────────────────────────────────────────────

def get_campaign_or_404(campaign_id: int):
    db = SessionLocal()
    c = db.get(Campaign, campaign_id)
    db.close()
    return c


def default_intervals(interval_minutes: int) -> list:
    intervals_per_day = (24 * 60) // interval_minutes
    return [0.0] * intervals_per_day


def interval_labels(interval_minutes: int) -> list:
    labels = []
    for i in range((24 * 60) // interval_minutes):
        h = (i * interval_minutes) // 60
        m = (i * interval_minutes) % 60
        labels.append(f"{h:02d}:{m:02d}")
    return labels


# ─── Dashboard ────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    db = SessionLocal()
    campaigns = db.query(Campaign).filter(Campaign.is_active == True).order_by(Campaign.created_at.desc()).all()
    stats = {
        "inbound": db.query(Campaign).filter(Campaign.campaign_type == "inbound", Campaign.is_active == True).count(),
        "outbound": db.query(Campaign).filter(Campaign.campaign_type == "outbound", Campaign.is_active == True).count(),
        "chat": db.query(Campaign).filter(Campaign.campaign_type == "chat", Campaign.is_active == True).count(),
    }
    db.close()
    return render_template("index.html", campaigns=campaigns, stats=stats)


# ─── Campaigns CRUD ───────────────────────────────────────────────────────────

@app.route("/campaigns/new", methods=["GET", "POST"])
def new_campaign():
    if request.method == "POST":
        data = request.form
        ctype = data.get("campaign_type", "inbound")
        db = SessionLocal()
        campaign = Campaign(
            name=data.get("name", "Nueva campaña"),
            campaign_type=ctype,
            description=data.get("description", ""),
            aht_seconds=float(data.get("aht_seconds", 180)),
            shrinkage=float(data.get("shrinkage", 30)) / 100,
            interval_minutes=int(data.get("interval_minutes", 30)),
            target_sl=float(data.get("target_sl", 80)) / 100,
            target_seconds=float(data.get("target_seconds", 20)),
            max_occupancy=float(data.get("max_occupancy", 85)) / 100,
            contact_rate=float(data.get("contact_rate", 35)) / 100,
            right_party_rate=float(data.get("right_party_rate", 70)) / 100,
            dial_mode=data.get("dial_mode", "progressive"),
            available_hours=float(data.get("available_hours", 8)),
            occupancy_target=float(data.get("occupancy_target", 80)) / 100,
            records_to_contact=int(data.get("records_to_contact", 1000)),
            max_concurrency=float(data.get("max_concurrency", 2.0)),
            target_response_seconds=float(data.get("target_response_seconds", 30)),
            target_response_rate=float(data.get("target_response_rate", 80)) / 100,
        )
        db.add(campaign)
        db.commit()
        campaign_id = campaign.id
        db.close()
        return redirect(url_for("campaign_detail", campaign_id=campaign_id))
    return render_template("campaign_form.html", campaign=None, edit=False)


@app.route("/campaigns/<int:campaign_id>")
def campaign_detail(campaign_id):
    db = SessionLocal()
    campaign = db.get(Campaign, campaign_id)
    if not campaign:
        db.close()
        return "Campaña no encontrada", 404

    intervals = db.query(IntervalVolume).filter(
        IntervalVolume.campaign_id == campaign_id,
        IntervalVolume.day_type == "weekday"
    ).order_by(IntervalVolume.interval_index).all()

    historical = db.query(HistoricalVolume).filter(
        HistoricalVolume.campaign_id == campaign_id
    ).order_by(HistoricalVolume.record_date.desc()).limit(30).all()

    plans = db.query(StaffingPlan).filter(
        StaffingPlan.campaign_id == campaign_id
    ).order_by(StaffingPlan.created_at.desc()).limit(5).all()

    db.close()

    labels = interval_labels(campaign.interval_minutes)
    volumes = [0.0] * len(labels)
    for iv in intervals:
        if iv.interval_index < len(volumes):
            volumes[iv.interval_index] = iv.volume

    return render_template(
        "campaign_detail.html",
        campaign=campaign,
        labels=labels,
        volumes=volumes,
        historical=historical,
        plans=plans,
        now_date=datetime.utcnow().strftime("%Y-%m-%d"),
    )


@app.route("/campaigns/<int:campaign_id>/edit", methods=["GET", "POST"])
def edit_campaign(campaign_id):
    db = SessionLocal()
    campaign = db.get(Campaign, campaign_id)
    if not campaign:
        db.close()
        return "Campaña no encontrada", 404

    if request.method == "POST":
        data = request.form
        campaign.name = data.get("name", campaign.name)
        campaign.description = data.get("description", campaign.description)
        campaign.aht_seconds = float(data.get("aht_seconds", campaign.aht_seconds))
        campaign.shrinkage = float(data.get("shrinkage", campaign.shrinkage * 100)) / 100
        campaign.interval_minutes = int(data.get("interval_minutes", campaign.interval_minutes))
        campaign.target_sl = float(data.get("target_sl", campaign.target_sl * 100)) / 100
        campaign.target_seconds = float(data.get("target_seconds", campaign.target_seconds))
        campaign.max_occupancy = float(data.get("max_occupancy", campaign.max_occupancy * 100)) / 100
        campaign.contact_rate = float(data.get("contact_rate", campaign.contact_rate * 100)) / 100
        campaign.right_party_rate = float(data.get("right_party_rate", campaign.right_party_rate * 100)) / 100
        campaign.dial_mode = data.get("dial_mode", campaign.dial_mode)
        campaign.available_hours = float(data.get("available_hours", campaign.available_hours))
        campaign.occupancy_target = float(data.get("occupancy_target", campaign.occupancy_target * 100)) / 100
        campaign.records_to_contact = int(data.get("records_to_contact", campaign.records_to_contact))
        campaign.max_concurrency = float(data.get("max_concurrency", campaign.max_concurrency))
        campaign.target_response_seconds = float(data.get("target_response_seconds", campaign.target_response_seconds))
        campaign.target_response_rate = float(data.get("target_response_rate", campaign.target_response_rate * 100)) / 100
        campaign.updated_at = datetime.utcnow()
        db.commit()
        db.close()
        return redirect(url_for("campaign_detail", campaign_id=campaign_id))

    db.close()
    return render_template("campaign_form.html", campaign=campaign, edit=True)


@app.route("/campaigns/<int:campaign_id>/delete", methods=["POST"])
def delete_campaign(campaign_id):
    db = SessionLocal()
    campaign = db.get(Campaign, campaign_id)
    if campaign:
        db.delete(campaign)
        db.commit()
    db.close()
    return redirect(url_for("index"))


# ─── Volume Input ──────────────────────────────────────────────────────────────

@app.route("/campaigns/<int:campaign_id>/volumes", methods=["GET", "POST"])
def save_volumes(campaign_id):
    db = SessionLocal()
    campaign = db.get(Campaign, campaign_id)
    if not campaign:
        db.close()
        return jsonify({"error": "not found"}), 404

    if request.method == "GET":
        day_type = request.args.get("day_type", "weekday")
        labels = interval_labels(campaign.interval_minutes)
        volumes = [0.0] * len(labels)
        intervals = db.query(IntervalVolume).filter(
            IntervalVolume.campaign_id == campaign_id,
            IntervalVolume.day_type == day_type
        ).order_by(IntervalVolume.interval_index).all()
        for iv in intervals:
            if iv.interval_index < len(volumes):
                volumes[iv.interval_index] = iv.volume
        db.close()
        return jsonify({"day_type": day_type, "volumes": volumes})

    data = request.json or {}
    volumes = data.get("volumes", [])
    day_type = data.get("day_type", "weekday")

    db.query(IntervalVolume).filter(
        IntervalVolume.campaign_id == campaign_id,
        IntervalVolume.day_type == day_type
    ).delete()

    labels = interval_labels(campaign.interval_minutes)
    for idx, vol in enumerate(volumes):
        if idx < len(labels):
            iv = IntervalVolume(
                campaign_id=campaign_id,
                interval_label=labels[idx],
                interval_index=idx,
                volume=float(vol or 0),
                day_type=day_type,
            )
            db.add(iv)

    db.commit()
    db.close()
    return jsonify({"status": "ok"})


# ─── Calculate ────────────────────────────────────────────────────────────────

@app.route("/campaigns/<int:campaign_id>/calculate", methods=["POST"])
def calculate(campaign_id):
    db = SessionLocal()
    campaign = db.get(Campaign, campaign_id)
    if not campaign:
        db.close()
        return jsonify({"error": "not found"}), 404

    data = request.json or {}
    day_type = data.get("day_type", "weekday")

    intervals = db.query(IntervalVolume).filter(
        IntervalVolume.campaign_id == campaign_id,
        IntervalVolume.day_type == day_type
    ).order_by(IntervalVolume.interval_index).all()

    labels = interval_labels(campaign.interval_minutes)
    volumes = [0.0] * len(labels)
    aht_per_interval: list = [None] * len(labels)
    for iv in intervals:
        if iv.interval_index < len(volumes):
            volumes[iv.interval_index] = iv.volume
            if iv.aht_seconds is not None:
                aht_per_interval[iv.interval_index] = iv.aht_seconds

    # Use per-interval AHT when at least one interval has it; fill gaps with campaign default
    has_per_interval_aht = any(v is not None for v in aht_per_interval)
    aht_input = (
        [v if v is not None else campaign.aht_seconds for v in aht_per_interval]
        if has_per_interval_aht
        else campaign.aht_seconds
    )

    result = {}

    if campaign.campaign_type == "inbound":
        plan = build_interval_plan(
            interval_volumes=volumes,
            aht_seconds=aht_input,
            interval_minutes=campaign.interval_minutes,
            target_sl=campaign.target_sl,
            target_seconds=campaign.target_seconds,
            shrinkage=campaign.shrinkage,
            max_occupancy=campaign.max_occupancy,
        )
        summ = summary_stats(plan)
        result = {"plan": plan, "summary": summ, "type": "inbound", "day_type": day_type}

        # Save plan
        sp = StaffingPlan(
            campaign_id=campaign_id,
            plan_name=f"Plan {day_type} {datetime.now().strftime('%d/%m/%Y %H:%M')}",
            plan_json=json.dumps(plan),
            summary_json=json.dumps(summ),
        )
        db.add(sp)
        db.commit()

    elif campaign.campaign_type == "outbound":
        outbound_result = agents_needed(
            records_to_contact=campaign.records_to_contact,
            contact_rate=campaign.contact_rate,
            right_party_rate=campaign.right_party_rate,
            aht_seconds=campaign.aht_seconds,
            available_hours=campaign.available_hours,
            occupancy_target=campaign.occupancy_target,
            dial_mode=campaign.dial_mode,
        )
        # Build hourly distribution
        hourly = [outbound_result["right_party_contacts"] / campaign.available_hours] * int(campaign.available_hours)
        hourly_plan = build_outbound_interval_plan(
            hourly_targets=hourly,
            contact_rate=campaign.contact_rate,
            right_party_rate=campaign.right_party_rate,
            aht_seconds=campaign.aht_seconds,
            occupancy_target=campaign.occupancy_target,
            dial_mode=campaign.dial_mode,
            shrinkage=campaign.shrinkage,
        )
        comparison = compare_dial_modes(
            campaign.records_to_contact, campaign.contact_rate, campaign.right_party_rate,
            campaign.aht_seconds, campaign.available_hours, campaign.occupancy_target
        )
        result = {
            "type": "outbound",
            "result": outbound_result,
            "hourly_plan": hourly_plan,
            "comparison": comparison,
            "day_type": day_type,
        }

    elif campaign.campaign_type == "chat":
        chat_result = agents_for_chat(
            sessions_per_interval=sum(volumes) / max(1, len([v for v in volumes if v > 0])),
            session_aht_seconds=campaign.aht_seconds,
            interval_minutes=campaign.interval_minutes,
            max_concurrency=campaign.max_concurrency,
            target_response_seconds=campaign.target_response_seconds,
            target_response_rate=campaign.target_response_rate,
            shrinkage=campaign.shrinkage,
            max_occupancy=campaign.max_occupancy,
        )
        plan = build_chat_interval_plan(
            interval_sessions=volumes,
            session_aht_seconds=campaign.aht_seconds,
            interval_minutes=campaign.interval_minutes,
            max_concurrency=campaign.max_concurrency,
            target_response_seconds=campaign.target_response_seconds,
            target_response_rate=campaign.target_response_rate,
            shrinkage=campaign.shrinkage,
            max_occupancy=campaign.max_occupancy,
        )
        sensitivity = concurrency_sensitivity(
            sessions_per_interval=max(volumes) if volumes else 10,
            session_aht_seconds=campaign.aht_seconds,
            interval_minutes=campaign.interval_minutes,
            target_response_seconds=campaign.target_response_seconds,
            target_response_rate=campaign.target_response_rate,
            shrinkage=campaign.shrinkage,
            max_occupancy=campaign.max_occupancy,
        )
        result = {
            "type": "chat",
            "summary": chat_result,
            "plan": plan,
            "sensitivity": sensitivity,
            "day_type": day_type,
        }

    db.close()
    return jsonify(result)


@app.route("/campaigns/<int:campaign_id>/shift-plan-peru", methods=["POST"])
def shift_plan_peru(campaign_id):
    db = SessionLocal()
    campaign = db.get(Campaign, campaign_id)
    db.close()
    if not campaign:
        return jsonify({"error": "not found"}), 404

    data = request.json or {}
    plan = data.get("plan", [])
    interval_minutes = int(data.get("interval_minutes", campaign.interval_minutes or 30))
    day_type = data.get("day_type", "weekday")
    if not plan:
        return jsonify({"error": "plan vacío"}), 400

    # Días ISO activos según el tipo de día
    _DAY_TYPE_ISODAYS = {
        "weekday":  {1, 2, 3, 4, 5},
        "saturday": {6},
        "sunday":   {7},
    }
    active_iso = _DAY_TYPE_ISODAYS.get(day_type, {1, 2, 3, 4, 5, 6, 7})

    # Cargar solo las jornadas activas para ese tipo de día
    db2 = SessionLocal()
    defs = db2.query(ShiftDefinition).order_by(ShiftDefinition.created_at).all()

    def _applies(d):
        if not d.days_of_week:
            return True
        return bool({int(x) for x in d.days_of_week.split(",") if x.strip()} & active_iso)

    shift_defs = [
        {"id": d.id, "name": d.name, "paid_hours": d.paid_hours,
         "lunch_minutes": d.lunch_minutes, "color": d.color}
        for d in defs if _applies(d)
    ] or None
    db2.close()

    result = dimension_shifts_peru(plan, interval_minutes, shift_defs=shift_defs)
    result["day_type"] = day_type
    return jsonify(result)


@app.route("/campaigns/<int:campaign_id>/sensitivity", methods=["POST"])
def get_sensitivity(campaign_id):
    db = SessionLocal()
    campaign = db.get(Campaign, campaign_id)
    if not campaign:
        db.close()
        return jsonify({"error": "not found"}), 404

    data = request.json or {}
    calls = float(data.get("calls", 100))

    rows = sensitivity_analysis(
        calls_per_interval=calls,
        aht_seconds=campaign.aht_seconds,
        interval_minutes=campaign.interval_minutes,
        target_sl=campaign.target_sl,
        target_seconds=campaign.target_seconds,
        shrinkage=campaign.shrinkage,
        max_occupancy=campaign.max_occupancy,
    )
    db.close()
    return jsonify(rows)


# ─── Forecast ─────────────────────────────────────────────────────────────────

@app.route("/campaigns/<int:campaign_id>/forecast", methods=["POST"])
def run_forecast(campaign_id):
    db = SessionLocal()
    campaign = db.get(Campaign, campaign_id)
    if not campaign:
        db.close()
        return jsonify({"error": "not found"}), 404

    data = request.json or {}
    historical_data = db.query(HistoricalVolume).filter(
        HistoricalVolume.campaign_id == campaign_id
    ).order_by(HistoricalVolume.record_date).all()

    daily_volumes = [h.total_volume for h in historical_data] if historical_data else [100] * 14

    intervals = db.query(IntervalVolume).filter(
        IntervalVolume.campaign_id == campaign_id,
        IntervalVolume.day_type == "weekday"
    ).order_by(IntervalVolume.interval_index).all()
    intraday = [iv.volume for iv in intervals] if intervals else None

    result = forecast_volume(
        historical_daily=daily_volumes,
        forecast_days=int(data.get("forecast_days", 7)),
        method=data.get("method", "wma"),
        growth_rate=float(data.get("growth_rate", 0)) / 100,
        intraday_profile=intraday,
        interval_minutes=campaign.interval_minutes,
    )
    db.close()
    return jsonify(result)


@app.route("/campaigns/<int:campaign_id>/historical", methods=["POST"])
def save_historical(campaign_id):
    db = SessionLocal()
    data = request.json or {}
    records = data.get("records", [])
    for rec in records:
        hv = HistoricalVolume(
            campaign_id=campaign_id,
            record_date=rec.get("date"),
            total_volume=float(rec.get("volume", 0)),
            notes=rec.get("notes", ""),
        )
        db.add(hv)
    db.commit()
    db.close()
    return jsonify({"status": "ok"})


# ─── Export ───────────────────────────────────────────────────────────────────

@app.route("/campaigns/<int:campaign_id>/export", methods=["POST"])
def export_report(campaign_id):
    from reports.generator import export_inbound_excel, export_outbound_excel
    db = SessionLocal()
    campaign = db.get(Campaign, campaign_id)
    if not campaign:
        db.close()
        return jsonify({"error": "not found"}), 404

    data = request.json or {}
    plan_data = data.get("plan", [])
    summary_data = data.get("summary", {})
    outbound_result = data.get("result", {})

    params = {
        "aht_seconds": campaign.aht_seconds,
        "target_sl": campaign.target_sl,
        "target_seconds": campaign.target_seconds,
        "shrinkage": campaign.shrinkage,
        "interval_minutes": campaign.interval_minutes,
    }

    try:
        if campaign.campaign_type == "inbound":
            filepath = export_inbound_excel(campaign.name, plan_data, summary_data, params)
        elif campaign.campaign_type == "outbound":
            filepath = export_outbound_excel(campaign.name, outbound_result, data.get("hourly_plan", []))
        else:
            from reports.generator import export_plan_csv
            filepath = export_plan_csv(campaign.name, plan_data, "chat")

        db.close()
        return send_file(filepath, as_attachment=True, download_name=os.path.basename(filepath))
    except Exception as e:
        db.close()
        return jsonify({"error": str(e)}), 500


# ─── Erlang Calculator (standalone tool) ──────────────────────────────────────

@app.route("/calculator")
def calculator():
    return render_template("calculator.html")


@app.route("/api/erlang", methods=["POST"])
def api_erlang():
    from calculations.erlang import agents_for_service_level, service_level, average_speed_of_answer, occupancy, erlang_b
    data = request.json or {}
    mode = data.get("mode", "inbound")

    if mode == "inbound":
        result = agents_for_service_level(
            calls_per_interval=float(data.get("calls", 100)),
            aht_seconds=float(data.get("aht", 180)),
            interval_seconds=int(data.get("interval", 1800)),
            target_sl=float(data.get("target_sl", 80)) / 100,
            target_seconds=float(data.get("target_seconds", 20)),
            max_occupancy=float(data.get("max_occupancy", 85)) / 100,
            shrinkage=float(data.get("shrinkage", 30)) / 100,
        )
        return jsonify(result)

    elif mode == "trunks":
        from calculations.erlang import erlang_b
        traffic = float(data.get("traffic", 10))
        blocking = float(data.get("blocking", 2)) / 100
        for n in range(1, 2000):
            if erlang_b(n, traffic) <= blocking:
                return jsonify({"trunks": n, "traffic": traffic, "blocking_target": blocking})
        return jsonify({"trunks": 2000})

    return jsonify({"error": "unknown mode"}), 400


# ─── Jornadas ─────────────────────────────────────────────────────────────────

@app.route("/jornadas")
def jornadas_page():
    return render_template("jornadas.html")


@app.route("/api/shifts", methods=["GET"])
def list_shifts():
    db = SessionLocal()
    rows = db.query(ShiftDefinition).order_by(ShiftDefinition.created_at).all()
    result = [_shift_to_dict(s) for s in rows]
    db.close()
    return jsonify(result)


@app.route("/api/shifts", methods=["POST"])
def create_shift():
    data = request.json or {}
    db = SessionLocal()
    s = ShiftDefinition(
        name=data["name"],
        paid_hours=float(data.get("paid_hours", 8)),
        lunch_minutes=int(data.get("lunch_minutes", 45)),
        days_of_week=data.get("days_of_week", "1,2,3,4,5"),
        color=data.get("color", "#6366f1"),
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    result = _shift_to_dict(s)
    db.close()
    return jsonify(result), 201


@app.route("/api/shifts/<int:shift_id>", methods=["PUT"])
def update_shift(shift_id):
    db = SessionLocal()
    s = db.get(ShiftDefinition, shift_id)
    if not s:
        db.close()
        return jsonify({"error": "not found"}), 404
    data = request.json or {}
    s.name          = data.get("name", s.name)
    s.paid_hours    = float(data.get("paid_hours", s.paid_hours))
    s.lunch_minutes = int(data.get("lunch_minutes", s.lunch_minutes))
    s.days_of_week  = data.get("days_of_week", s.days_of_week)
    s.color         = data.get("color", s.color)
    db.commit()
    result = _shift_to_dict(s)
    db.close()
    return jsonify(result)


@app.route("/api/shifts/<int:shift_id>", methods=["DELETE"])
def delete_shift(shift_id):
    db = SessionLocal()
    s = db.get(ShiftDefinition, shift_id)
    if not s:
        db.close()
        return jsonify({"error": "not found"}), 404
    db.delete(s)
    db.commit()
    db.close()
    return jsonify({"status": "ok"})


def _shift_to_dict(s: ShiftDefinition) -> dict:
    from calculations.peru_shifts import _validate_shift, ShiftRule
    warnings = _validate_shift({"paid_hours": s.paid_hours, "lunch_minutes": s.lunch_minutes}, ShiftRule())
    span_h = s.paid_hours + s.lunch_minutes / 60
    return {
        "id": s.id, "name": s.name,
        "paid_hours": s.paid_hours,
        "lunch_minutes": s.lunch_minutes,
        "span_hours": round(span_h, 2),
        "days_of_week": s.days_of_week,
        "color": s.color,
        "warnings": warnings,
    }


# ─── Shrinkage Reference ──────────────────────────────────────────────────────

@app.route("/api/shrinkage-reference")
def shrinkage_reference():
    return jsonify(shrinkage_components())


# ─── Connectors ───────────────────────────────────────────────────────────────

def _normalize_interval(val) -> str:
    """Coerce any time-like value to HH:MM format.

    Handles:
      "08:00", "08:00:00"         → "08:00"
      "2024-01-15 08:30:00"       → "08:30"
      "2024-01-15T08:30:00"       → "08:30"
      8, "8"  (plain hour 0–23)   → "08:00"   ← HOUR() from MySQL/SQL
      480     (minutes ≥ 60)      → "08:00"
    """
    s = str(val).strip()
    # Strip date prefix from datetime strings
    if "T" in s:
        s = s.split("T")[1]
    if " " in s and len(s) > 8:
        s = s.split(" ")[-1]
    # HH:MM or HH:MM:SS
    parts = s.split(":")
    if len(parts) >= 2:
        try:
            return f"{int(parts[0]):02d}:{parts[1][:2]}"
        except ValueError:
            pass
    # Plain number
    try:
        n = int(float(s))
        if 0 <= n <= 23:          # treat as hour (e.g. HOUR() SQL function)
            return f"{n:02d}:00"
        return f"{n // 60:02d}:{n % 60:02d}"  # treat as minutes from midnight
    except ValueError:
        return s


def _normalize_date(val) -> str:
    """Coerce any date-like value to YYYY-MM-DD."""
    s = str(val).strip()
    # isoformat datetime: "2024-01-15T08:30:00" → "2024-01-15"
    return s[:10]


@app.route("/connectors")
def connectors_page():
    db = SessionLocal()
    campaigns = db.query(Campaign).filter(Campaign.is_active == True).order_by(Campaign.name).all()
    db.close()
    return render_template("connectors.html", campaigns=campaigns)


@app.route("/api/connectors", methods=["GET"])
def list_connectors():
    db = SessionLocal()
    rows = db.query(DBConnector).order_by(DBConnector.created_at.desc()).all()
    result = [
        {"id": c.id, "name": c.name, "db_type": c.db_type, "host": c.host,
         "port": c.port, "database": c.database, "username": c.username,
         "created_at": c.created_at.isoformat()}
        for c in rows
    ]
    db.close()
    return jsonify(result)


@app.route("/api/connectors", methods=["POST"])
def create_connector():
    data = request.json or {}
    db = SessionLocal()
    c = DBConnector(
        name=data["name"],
        db_type=data.get("db_type", "mysql"),
        host=data["host"],
        port=int(data.get("port", 3306)),
        database=data["database"],
        username=data["username"],
        password_plain=data.get("password", ""),
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    result = {"id": c.id, "name": c.name, "db_type": c.db_type, "host": c.host,
              "port": c.port, "database": c.database, "username": c.username}
    db.close()
    return jsonify(result), 201


@app.route("/api/connectors/<int:conn_id>", methods=["PUT"])
def update_connector(conn_id):
    db = SessionLocal()
    c = db.get(DBConnector, conn_id)
    if not c:
        db.close()
        return jsonify({"error": "not found"}), 404
    data = request.json or {}
    c.name     = data.get("name", c.name)
    c.db_type  = data.get("db_type", c.db_type)
    c.host     = data.get("host", c.host)
    c.port     = int(data.get("port", c.port))
    c.database = data.get("database", c.database)
    c.username = data.get("username", c.username)
    if data.get("password"):          # blank = keep existing
        c.password_plain = data["password"]
    db.commit()
    result = {"id": c.id, "name": c.name, "db_type": c.db_type, "host": c.host,
              "port": c.port, "database": c.database, "username": c.username}
    db.close()
    return jsonify(result)


@app.route("/api/connectors/<int:conn_id>", methods=["DELETE"])
def delete_connector(conn_id):
    db = SessionLocal()
    c = db.get(DBConnector, conn_id)
    if not c:
        db.close()
        return jsonify({"error": "not found"}), 404
    db.delete(c)
    db.commit()
    db.close()
    return jsonify({"status": "ok"})


@app.route("/api/connectors/<int:conn_id>/test", methods=["POST"])
def test_connector(conn_id):
    db = SessionLocal()
    c = db.get(DBConnector, conn_id)
    db.close()
    if not c:
        return jsonify({"error": "not found"}), 404
    try:
        from connectors import get_connector
        connector = get_connector(c.db_type, c.host, c.port, c.database, c.username, c.password_plain or "")
        ok, msg = connector.test_connection()
        return jsonify({"success": ok, "message": msg})
    except Exception as exc:
        return jsonify({"success": False, "message": str(exc)})


@app.route("/api/connectors/<int:conn_id>/preview", methods=["POST"])
def preview_query(conn_id):
    db = SessionLocal()
    c = db.get(DBConnector, conn_id)
    db.close()
    if not c:
        return jsonify({"error": "not found"}), 404
    data = request.json or {}
    sql = data.get("sql", "").strip()
    if not sql:
        return jsonify({"error": "query vacío"}), 400
    try:
        from connectors import get_connector
        connector = get_connector(c.db_type, c.host, c.port, c.database, c.username, c.password_plain or "")
        result = connector.execute_query(sql, max_rows=20)
        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


def _execute_import(db, campaign, columns, rows, target_type, day_type, mapping):
    """Normalize and persist imported rows. Returns (imported_count, errors_list)."""
    imported = 0
    errors = []

    if target_type == "interval_volumes":
        col_iv  = mapping.get("col_interval", "")
        col_vol = mapping.get("col_volume", "")
        col_dt  = mapping.get("col_day_type", "")
        col_aht = mapping.get("col_aht", "")

        if col_iv not in columns or col_vol not in columns:
            raise ValueError(f"Columnas no encontradas: '{col_iv}', '{col_vol}'")

        idx_iv  = columns.index(col_iv)
        idx_vol = columns.index(col_vol)
        idx_dt  = columns.index(col_dt)  if col_dt  and col_dt  in columns else None
        idx_aht = columns.index(col_aht) if col_aht and col_aht in columns else None

        labels = interval_labels(campaign.interval_minutes)
        label_to_idx = {lbl: i for i, lbl in enumerate(labels)}
        agg: dict = {}  # {(day_type, idx): [vol, aht_wsum, aht_wvol]}

        for row in rows:
            eff_dt = day_type
            if idx_dt is not None:
                raw_dt = str(row[idx_dt]).lower()
                if any(x in raw_dt for x in ("sat", "sab", "6")):
                    eff_dt = "saturday"
                elif any(x in raw_dt for x in ("sun", "dom", "7", "0")):
                    eff_dt = "sunday"
                else:
                    eff_dt = "weekday"

            norm_iv = _normalize_interval(row[idx_iv])
            if norm_iv not in label_to_idx:
                errors.append(f"Intervalo no reconocido: {row[idx_iv]!r} → {norm_iv!r}")
                continue
            try:
                vol = float(row[idx_vol] or 0)
            except (TypeError, ValueError):
                vol = 0.0

            aht_val = None
            if idx_aht is not None and row[idx_aht] is not None:
                try:
                    aht_val = float(row[idx_aht])
                except (TypeError, ValueError):
                    pass

            key = (eff_dt, label_to_idx[norm_iv])
            if key not in agg:
                agg[key] = [0.0, 0.0, 0.0]
            agg[key][0] += vol
            if aht_val is not None and vol > 0:
                agg[key][1] += vol * aht_val
                agg[key][2] += vol
            imported += 1

        for eff_dt in {k[0] for k in agg}:
            db.query(IntervalVolume).filter(
                IntervalVolume.campaign_id == campaign.id,
                IntervalVolume.day_type == eff_dt,
            ).delete()

        for (eff_dt, idx), (vol, aht_wsum, aht_wvol) in agg.items():
            eff_aht = round(aht_wsum / aht_wvol, 1) if aht_wvol > 0 else None
            db.add(IntervalVolume(
                campaign_id=campaign.id,
                interval_label=labels[idx],
                interval_index=idx,
                volume=vol,
                day_type=eff_dt,
                aht_seconds=eff_aht,
            ))

    elif target_type == "historical_volumes":
        col_date = mapping.get("col_date", "")
        col_vol  = mapping.get("col_volume", "")

        if col_date not in columns or col_vol not in columns:
            raise ValueError(f"Columnas no encontradas: '{col_date}', '{col_vol}'")

        idx_date = columns.index(col_date)
        idx_vol  = columns.index(col_vol)

        for row in rows:
            norm_date = _normalize_date(row[idx_date])
            try:
                vol = float(row[idx_vol] or 0)
            except (TypeError, ValueError):
                vol = 0.0
            existing = db.query(HistoricalVolume).filter(
                HistoricalVolume.campaign_id == campaign.id,
                HistoricalVolume.record_date == norm_date,
            ).first()
            if existing:
                existing.total_volume = vol
            else:
                db.add(HistoricalVolume(campaign_id=campaign.id, record_date=norm_date, total_volume=vol))
            imported += 1
    else:
        raise ValueError("target_type inválido")

    return imported, errors


@app.route("/api/templates", methods=["GET"])
def list_all_templates():
    """All import templates across connectors, optionally filtered by target_type."""
    target_type = request.args.get("target_type")
    db = SessionLocal()
    q = db.query(ImportTemplate, DBConnector).join(DBConnector, ImportTemplate.connector_id == DBConnector.id)
    if target_type:
        q = q.filter(ImportTemplate.target_type == target_type)
    rows = q.order_by(DBConnector.name, ImportTemplate.name).all()
    result = [
        {"id": t.id, "name": t.name, "target_type": t.target_type,
         "connector_id": t.connector_id, "connector_name": c.name, "connector_type": c.db_type}
        for t, c in rows
    ]
    db.close()
    return jsonify(result)


@app.route("/api/templates/<int:tmpl_id>/run", methods=["POST"])
def run_template(tmpl_id):
    """Run a saved template against a campaign, with optional day_type override."""
    data = request.json or {}
    campaign_id  = int(data.get("campaign_id", 0))
    day_type_ovr = data.get("day_type")

    db = SessionLocal()
    t = db.get(ImportTemplate, tmpl_id)
    if not t:
        db.close()
        return jsonify({"error": "Plantilla no encontrada"}), 404
    c = db.get(DBConnector, t.connector_id)
    if not c:
        db.close()
        return jsonify({"error": "Conector no encontrado"}), 404
    campaign = db.get(Campaign, campaign_id)
    if not campaign:
        db.close()
        return jsonify({"error": "Campaña no encontrada"}), 404

    # Read all ORM attributes before any commit/close
    cfg         = json.loads(t.config_json or "{}")
    target_type = t.target_type
    query_sql   = t.query_sql
    conn_type   = c.db_type
    conn_host   = c.host
    conn_port   = c.port
    conn_db     = c.database
    conn_user   = c.username
    conn_pass   = c.password_plain or ""

    mapping  = {k: v for k, v in cfg.items() if k.startswith("col_")}
    day_type = day_type_ovr or cfg.get("day_type", "weekday")

    try:
        from connectors import get_connector
        connector = get_connector(conn_type, conn_host, conn_port, conn_db, conn_user, conn_pass)
        result = connector.execute_query(query_sql, max_rows=10000)
    except Exception as exc:
        db.close()
        return jsonify({"error": str(exc)}), 400

    try:
        imported, errors = _execute_import(db, campaign, result["columns"], result["rows"], target_type, day_type, mapping)
    except ValueError as exc:
        db.close()
        return jsonify({"error": str(exc)}), 400

    db.commit()
    db.close()
    return jsonify({"status": "ok", "imported": imported, "errors": errors[:20],
                    "target_type": target_type})


@app.route("/api/connectors/<int:conn_id>/import", methods=["POST"])
def run_import(conn_id):
    db = SessionLocal()
    c = db.get(DBConnector, conn_id)
    if not c:
        db.close()
        return jsonify({"error": "not found"}), 404

    data = request.json or {}
    sql = data.get("sql", "").strip()
    target_type = data.get("target_type", "")
    campaign_id = int(data.get("campaign_id", 0))
    day_type = data.get("day_type", "weekday")
    mapping = data.get("mapping", {})

    campaign = db.get(Campaign, campaign_id)
    if not campaign:
        db.close()
        return jsonify({"error": "Campaña no encontrada"}), 404

    try:
        from connectors import get_connector
        connector = get_connector(c.db_type, c.host, c.port, c.database, c.username, c.password_plain or "")
        result = connector.execute_query(sql, max_rows=10000)
    except Exception as exc:
        db.close()
        return jsonify({"error": str(exc)}), 400

    columns = result["columns"]
    rows = result["rows"]

    try:
        imported, errors = _execute_import(db, campaign, columns, rows, target_type, day_type, mapping)
    except ValueError as exc:
        db.close()
        return jsonify({"error": str(exc)}), 400

    db.commit()
    db.close()
    return jsonify({"status": "ok", "imported": imported, "errors": errors[:20]})


@app.route("/api/connectors/<int:conn_id>/templates", methods=["GET"])
def list_templates(conn_id):
    db = SessionLocal()
    templates = db.query(ImportTemplate).filter(ImportTemplate.connector_id == conn_id).order_by(ImportTemplate.created_at.desc()).all()
    result = [
        {"id": t.id, "name": t.name, "target_type": t.target_type,
         "query_sql": t.query_sql, "config": json.loads(t.config_json or "{}"),
         "created_at": t.created_at.isoformat()}
        for t in templates
    ]
    db.close()
    return jsonify(result)


@app.route("/api/connectors/<int:conn_id>/templates", methods=["POST"])
def save_template(conn_id):
    data = request.json or {}
    db = SessionLocal()
    t = ImportTemplate(
        connector_id=conn_id,
        name=data["name"],
        target_type=data["target_type"],
        query_sql=data["query_sql"],
        config_json=json.dumps(data.get("config", {})),
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    result = {"id": t.id, "name": t.name, "target_type": t.target_type}
    db.close()
    return jsonify(result), 201


@app.route("/api/connectors/templates/<int:tmpl_id>", methods=["DELETE"])
def delete_template(tmpl_id):
    db = SessionLocal()
    t = db.get(ImportTemplate, tmpl_id)
    if not t:
        db.close()
        return jsonify({"error": "not found"}), 404
    db.delete(t)
    db.commit()
    db.close()
    return jsonify({"status": "ok"})


# ─── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("WFM_PORT", 5000))
    debug = os.environ.get("WFM_DEBUG", "0") == "1"
    print(f"\n  WFM Contact Center — http://localhost:{port}\n")
    app.run(host="0.0.0.0", port=port, debug=debug)
