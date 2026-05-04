"""
Database models for WFM system.
SQLite via SQLAlchemy — no server needed.
"""
from sqlalchemy import create_engine, Column, Integer, Float, String, DateTime, Text, Boolean, ForeignKey, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker, relationship
from datetime import datetime
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "wfm.db")
engine = create_engine(f"sqlite:///{DB_PATH}", echo=False)
SessionLocal = sessionmaker(bind=engine)


class Base(DeclarativeBase):
    pass


class Campaign(Base):
    __tablename__ = "campaigns"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False)
    campaign_type = Column(String(20), nullable=False)  # inbound | outbound | chat
    description = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    is_active = Column(Boolean, default=True)

    # Common parameters
    aht_seconds = Column(Float, default=180.0)
    shrinkage = Column(Float, default=0.30)
    interval_minutes = Column(Integer, default=30)

    # Inbound / Chat SL parameters
    target_sl = Column(Float, default=0.80)
    target_seconds = Column(Float, default=20.0)
    max_occupancy = Column(Float, default=0.85)

    # Outbound parameters
    contact_rate = Column(Float, default=0.35)
    right_party_rate = Column(Float, default=0.70)
    dial_mode = Column(String(20), default="progressive")
    available_hours = Column(Float, default=8.0)
    occupancy_target = Column(Float, default=0.80)
    records_to_contact = Column(Integer, default=1000)

    # Chat parameters
    max_concurrency = Column(Float, default=2.0)
    target_response_seconds = Column(Float, default=30.0)
    target_response_rate = Column(Float, default=0.80)

    intervals = relationship("IntervalVolume", back_populates="campaign", cascade="all, delete-orphan")
    historical = relationship("HistoricalVolume", back_populates="campaign", cascade="all, delete-orphan")


class IntervalVolume(Base):
    __tablename__ = "interval_volumes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    campaign_id = Column(Integer, ForeignKey("campaigns.id"), nullable=False)
    interval_label = Column(String(10), nullable=False)  # "08:00", "08:30", etc.
    interval_index = Column(Integer, nullable=False)
    volume = Column(Float, default=0.0)
    aht_seconds = Column(Float, nullable=True)          # per-interval AHT; None → use campaign default
    day_type = Column(String(20), default="weekday")    # weekday | saturday | sunday

    campaign = relationship("Campaign", back_populates="intervals")


class HistoricalVolume(Base):
    __tablename__ = "historical_volumes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    campaign_id = Column(Integer, ForeignKey("campaigns.id"), nullable=False)
    record_date = Column(String(10), nullable=False)  # YYYY-MM-DD
    total_volume = Column(Float, default=0.0)
    notes = Column(Text)

    campaign = relationship("Campaign", back_populates="historical")


class StaffingPlan(Base):
    __tablename__ = "staffing_plans"

    id = Column(Integer, primary_key=True, autoincrement=True)
    campaign_id = Column(Integer, ForeignKey("campaigns.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    plan_name = Column(String(200))
    plan_json = Column(Text)  # Stored as JSON string
    summary_json = Column(Text)


class ShiftDefinition(Base):
    """
    Plantilla de jornada — solo define duración, sin hora de inicio.
    El gestor de turnos coloca cada plantilla en la curva de demanda.
    """
    __tablename__ = "shift_definitions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False)
    paid_hours = Column(Float, default=8.0)
    lunch_minutes = Column(Integer, default=45)
    days_of_week = Column(String(20), default="1,2,3,4,5")
    color = Column(String(7), default="#6366f1")
    created_at = Column(DateTime, default=datetime.utcnow)


class DBConnector(Base):
    __tablename__ = "db_connectors"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False)
    db_type = Column(String(20), nullable=False, default="mysql")
    host = Column(String(200), nullable=False)
    port = Column(Integer, default=3306)
    database = Column(String(200), nullable=False)
    username = Column(String(200), nullable=False)
    password_plain = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

    templates = relationship("ImportTemplate", back_populates="connector", cascade="all, delete-orphan")


class ImportTemplate(Base):
    __tablename__ = "import_templates"

    id = Column(Integer, primary_key=True, autoincrement=True)
    connector_id = Column(Integer, ForeignKey("db_connectors.id"), nullable=False)
    name = Column(String(200), nullable=False)
    target_type = Column(String(20), nullable=False)  # interval_volumes | historical_volumes
    query_sql = Column(Text, nullable=False)
    # JSON: {campaign_id, day_type, col_interval, col_volume, col_date, col_day_type}
    config_json = Column(Text, default="{}")
    created_at = Column(DateTime, default=datetime.utcnow)

    connector = relationship("DBConnector", back_populates="templates")


def init_db():
    """Create all tables and apply lightweight column migrations."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    Base.metadata.create_all(engine)
    with engine.connect() as conn:
        # Add aht_seconds to interval_volumes for existing databases
        try:
            conn.execute(text("ALTER TABLE interval_volumes ADD COLUMN aht_seconds REAL"))
            conn.commit()
        except Exception:
            pass
        # Recreate shift_definitions with the new schema (no start_hour / start_minute)
        try:
            cols = [r[1] for r in conn.execute(text("PRAGMA table_info(shift_definitions)")).fetchall()]
            if "start_hour" in cols:
                conn.execute(text("DROP TABLE shift_definitions"))
                conn.commit()
                Base.metadata.tables["shift_definitions"].create(engine)
        except Exception:
            pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
