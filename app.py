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

from models import init_db, SessionLocal, Campaign, IntervalVolume, HistoricalVolume, StaffingPlan
from calculations.inbound import build_interval_plan, summary_stats, sensitivity_analysis
from calculations.outbound import agents_needed, build_outbound_interval_plan, compare_dial_modes
from calculations.chat import agents_for_chat, build_chat_interval_plan, concurrency_sensitivity
from calculations.forecast import forecast_volume, shrinkage_components

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

@app.route("/campaigns/<int:campaign_id>/volumes", methods=["POST"])
def save_volumes(campaign_id):
    db = SessionLocal()
    campaign = db.get(Campaign, campaign_id)
    if not campaign:
        db.close()
        return jsonify({"error": "not found"}), 404

    data = request.json
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
    for iv in intervals:
        if iv.interval_index < len(volumes):
            volumes[iv.interval_index] = iv.volume

    result = {}

    if campaign.campaign_type == "inbound":
        plan = build_interval_plan(
            interval_volumes=volumes,
            aht_seconds=campaign.aht_seconds,
            interval_minutes=campaign.interval_minutes,
            target_sl=campaign.target_sl,
            target_seconds=campaign.target_seconds,
            shrinkage=campaign.shrinkage,
            max_occupancy=campaign.max_occupancy,
        )
        summ = summary_stats(plan)
        result = {"plan": plan, "summary": summ, "type": "inbound"}

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
        }

    db.close()
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


# ─── Shrinkage Reference ──────────────────────────────────────────────────────

@app.route("/api/shrinkage-reference")
def shrinkage_reference():
    return jsonify(shrinkage_components())


# ─── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("WFM_PORT", 5000))
    debug = os.environ.get("WFM_DEBUG", "0") == "1"
    print(f"\n  WFM Contact Center — http://localhost:{port}\n")
    app.run(host="0.0.0.0", port=port, debug=debug)
