"""Database models and SQLite connection handling with versioned migrations."""
import os
import sqlite3
from datetime import datetime
from typing import Generator, Optional, List, Dict, Any
from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    String,
    Float,
    Boolean,
    DateTime,
    ForeignKey,
    Text,
)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship, Session
from backend.security import hash_password, verify_password

Base = declarative_base()


class SystemSetting(Base):
    __tablename__ = "system_settings"

    key = Column(String(100), primary_key=True)
    value = Column(Text, nullable=False)
    description = Column(String(255), nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class PrintSize(Base):
    __tablename__ = "print_sizes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(50), unique=True, nullable=False)  # e.g., "10x15"
    width_mm = Column(Float, nullable=False)
    height_mm = Column(Float, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    sort_order = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)


class PaperFinish(Base):
    __tablename__ = "paper_finishes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(50), unique=True, nullable=False)  # "MATTE", "GLOSSY"
    is_active = Column(Boolean, default=True, nullable=False)
    sort_order = Column(Integer, default=0)


class Order(Base):
    __tablename__ = "orders"

    order_id = Column(String(50), primary_key=True)  # ORD-YYYYMMDD-HHMMSS-XXXX
    status = Column(String(20), default="PENDING", nullable=False)  # PENDING, PROCESSING, COMPLETED, FAILED
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    completed_at = Column(DateTime, nullable=True)
    total_items = Column(Integer, default=0)
    total_prints = Column(Integer, default=0)
    manifest_path = Column(String(255), nullable=True)
    error_message = Column(Text, nullable=True)

    items = relationship("OrderItem", back_populates="order", cascade="all, delete-orphan")


class OrderItem(Base):
    __tablename__ = "order_items"

    id = Column(Integer, primary_key=True, autoincrement=True)
    order_id = Column(String(50), ForeignKey("orders.order_id"), nullable=False)
    original_filename = Column(String(255), nullable=False)
    original_rel_path = Column(String(255), nullable=False)
    print_size_name = Column(String(50), nullable=False)
    paper_finish = Column(String(50), nullable=False)
    crop_mode = Column(String(50), nullable=False)
    quantity = Column(Integer, default=1, nullable=False)
    output_rel_path = Column(String(255), nullable=True)
    status = Column(String(20), default="PENDING")

    order = relationship("Order", back_populates="items")


class NetworkShare(Base):
    __tablename__ = "network_shares"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False)
    server = Column(String(255), nullable=False)
    share_name = Column(String(255), nullable=False)
    username = Column(String(100), nullable=True)
    encrypted_password = Column(Text, nullable=True)
    mount_point = Column(String(255), nullable=True)
    is_active = Column(Boolean, default=True)


class SchemaVersion(Base):
    __tablename__ = "schema_versions"

    version = Column(Integer, primary_key=True)
    applied_at = Column(DateTime, default=datetime.utcnow)


CURRENT_SCHEMA_VERSION = 1


_DB_PATH = "storage/photokiosk.db"

def set_db_path(path: str):
    global _DB_PATH
    _DB_PATH = path

def get_db_engine(db_path: Optional[str] = None):
    target_path = db_path or _DB_PATH
    os.makedirs(os.path.dirname(os.path.abspath(target_path)), exist_ok=True)
    return create_engine(
        f"sqlite:///{target_path}",
        connect_args={"check_same_thread": False},
        echo=False
    )


def init_database(db_path: str = "storage/photokiosk.db", initial_admin_password: Optional[str] = None):
    """Initializes tables and seeds default records idempotently."""
    set_db_path(db_path)
    engine = get_db_engine(db_path)
    Base.metadata.create_all(bind=engine)

    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = SessionLocal()

    try:
        # Check / record schema version
        ver = db.query(SchemaVersion).filter_by(version=CURRENT_SCHEMA_VERSION).first()
        if not ver:
            db.add(SchemaVersion(version=CURRENT_SCHEMA_VERSION))

        # Seed default print sizes if empty
        if db.query(PrintSize).count() == 0:
            default_sizes = [
                ("9x13", 89.0, 127.0, 1),
                ("10x15", 102.0, 152.0, 2),
                ("13x18", 127.0, 178.0, 3),
                ("15x21", 152.0, 210.0, 4),
                ("20x30", 203.0, 305.0, 5),
                ("A4", 210.0, 297.0, 6),
            ]
            for name, w, h, sort_order in default_sizes:
                db.add(PrintSize(name=name, width_mm=w, height_mm=h, sort_order=sort_order, is_active=True))

        # Seed paper finishes if empty
        if db.query(PaperFinish).count() == 0:
            db.add(PaperFinish(name="MATTE", sort_order=1, is_active=True))
            db.add(PaperFinish(name="GLOSSY", sort_order=2, is_active=True))

        # Seed system settings
        default_settings = {
            "default_dpi": "300",
            "border_width_mm": "4.0",
            "auto_orientation_matching": "true",
            "admin_password_must_change": "true",
            "failed_login_attempts": "0",
            "storage_base_dir": "storage",
        }
        for k, v in default_settings.items():
            if not db.query(SystemSetting).filter_by(key=k).first():
                db.add(SystemSetting(key=k, value=v))

        # Admin password
        admin_pass_setting = db.query(SystemSetting).filter_by(key="admin_password_hash").first()
        if not admin_pass_setting:
            if initial_admin_password:
                pwd_hash = hash_password(initial_admin_password)
            else:
                import secrets
                import string
                chars = string.ascii_letters + string.digits
                gen_pass = ''.join(secrets.choice(chars) for _ in range(12))
                pwd_hash = hash_password(gen_pass)
                # print for installer discovery
                print(f"[INSTALLER] Initial Admin Password: {gen_pass}")
            db.add(SystemSetting(key="admin_password_hash", value=pwd_hash))

        db.commit()
    finally:
        db.close()

    return engine
