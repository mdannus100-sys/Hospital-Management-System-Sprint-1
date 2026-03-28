import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, time as daytime, timedelta

from flask import current_app
from werkzeug.security import generate_password_hash


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('admin', 'doctor', 'patient')),
    created_at TEXT NOT NULL,
    last_login_at TEXT
);

CREATE TABLE IF NOT EXISTS doctors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    specialty TEXT NOT NULL,
    bio TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS patients (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    date_of_birth TEXT,
    phone TEXT,
    emergency_contact TEXT
);

CREATE TABLE IF NOT EXISTS availability_slots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doctor_id INTEGER NOT NULL REFERENCES doctors(id) ON DELETE CASCADE,
    slot_time TEXT NOT NULL,
    is_available INTEGER NOT NULL DEFAULT 1,
    UNIQUE(doctor_id, slot_time)
);

CREATE TABLE IF NOT EXISTS appointments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slot_id INTEGER NOT NULL REFERENCES availability_slots(id) ON DELETE CASCADE,
    patient_id INTEGER NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    doctor_id INTEGER NOT NULL REFERENCES doctors(id) ON DELETE CASCADE,
    slot_time TEXT NOT NULL,
    reason TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'scheduled' CHECK(status IN ('scheduled', 'completed', 'cancelled')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS medical_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id INTEGER NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    doctor_id INTEGER NOT NULL REFERENCES doctors(id) ON DELETE CASCADE,
    record_date TEXT NOT NULL,
    title TEXT NOT NULL,
    notes TEXT NOT NULL,
    prescription TEXT
);

CREATE INDEX IF NOT EXISTS idx_users_role ON users(role);
CREATE INDEX IF NOT EXISTS idx_slots_time ON availability_slots(slot_time);
CREATE INDEX IF NOT EXISTS idx_slots_doctor ON availability_slots(doctor_id);
CREATE INDEX IF NOT EXISTS idx_appointments_patient ON appointments(patient_id);
CREATE INDEX IF NOT EXISTS idx_appointments_doctor ON appointments(doctor_id);
CREATE INDEX IF NOT EXISTS idx_records_patient ON medical_records(patient_id);
"""


def get_db_connection():
    conn = sqlite3.connect(current_app.config["DATABASE"])
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def db_connection():
    conn = get_db_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def now_iso():
    return datetime.now().replace(microsecond=0).isoformat()


def init_db():
    with db_connection() as conn:
        conn.executescript(SCHEMA)


def create_user(
    conn,
    *,
    full_name,
    email,
    password,
    role,
    specialty="General Practice",
    bio="",
    date_of_birth="",
    phone="",
    emergency_contact="",
):
    cursor = conn.execute(
        """
        INSERT INTO users (full_name, email, password_hash, role, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (full_name, email, generate_password_hash(password), role, now_iso()),
    )
    user_id = cursor.lastrowid
    profile_id = None

    if role == "doctor":
        profile_id = conn.execute(
            "INSERT INTO doctors (user_id, specialty, bio) VALUES (?, ?, ?)",
            (user_id, specialty, bio),
        ).lastrowid
    elif role == "patient":
        profile_id = conn.execute(
            """
            INSERT INTO patients (user_id, date_of_birth, phone, emergency_contact)
            VALUES (?, ?, ?, ?)
            """,
            (user_id, date_of_birth, phone, emergency_contact),
        ).lastrowid

    return user_id, profile_id


def seed_future_slots(conn, doctor_id, *, start_date, days):
    slot_times = [(9, 0), (10, 30), (13, 30), (15, 0)]
    for offset in range(days):
        working_day = start_date + timedelta(days=offset)
        if working_day.weekday() >= 5:
            continue
        for hours, minutes in slot_times:
            slot_value = datetime.combine(working_day, daytime(hour=hours, minute=minutes)).replace(second=0)
            conn.execute(
                """
                INSERT OR IGNORE INTO availability_slots (doctor_id, slot_time, is_available)
                VALUES (?, ?, 1)
                """,
                (doctor_id, slot_value.isoformat()),
            )


def seed_demo_data():
    with db_connection() as conn:
        has_data = conn.execute("SELECT COUNT(*) AS total FROM users").fetchone()["total"]
        if has_data:
            return

        create_user(
            conn,
            full_name="Amina Yusuf",
            email="admin@happycare.com",
            password="Admin@123",
            role="admin",
        )
        _, doctor_one_id = create_user(
            conn,
            full_name="Dr Grace Okafor",
            email="doctor@happycare.com",
            password="Doctor@123",
            role="doctor",
            specialty="Cardiology",
            bio="Focuses on preventative heart care and long-term treatment plans.",
        )
        _, doctor_two_id = create_user(
            conn,
            full_name="Dr Daniel Chen",
            email="doctor2@happycare.com",
            password="Doctor@123",
            role="doctor",
            specialty="General Medicine",
            bio="Supports same-day consultations and continuity of care for adults.",
        )
        _, patient_one_id = create_user(
            conn,
            full_name="Maya Patel",
            email="patient@happycare.com",
            password="Patient@123",
            role="patient",
            date_of_birth="1996-05-11",
            phone="+44 7700 900111",
            emergency_contact="Rohan Patel",
        )
        _, patient_two_id = create_user(
            conn,
            full_name="Kwame Mensah",
            email="patient2@happycare.com",
            password="Patient@123",
            role="patient",
            date_of_birth="1989-10-02",
            phone="+44 7700 900222",
            emergency_contact="Afia Mensah",
        )

        seed_future_slots(conn, doctor_one_id, start_date=date.today(), days=21)
        seed_future_slots(conn, doctor_two_id, start_date=date.today(), days=21)

        first_slot = conn.execute(
            "SELECT id, slot_time FROM availability_slots WHERE doctor_id = ? ORDER BY slot_time LIMIT 1",
            (doctor_one_id,),
        ).fetchone()
        second_slot = conn.execute(
            "SELECT id, slot_time FROM availability_slots WHERE doctor_id = ? ORDER BY slot_time LIMIT 1 OFFSET 2",
            (doctor_two_id,),
        ).fetchone()

        for slot, patient_id, doctor_id, reason, status in [
            (first_slot, patient_one_id, doctor_one_id, "Cardiology follow-up", "scheduled"),
            (second_slot, patient_two_id, doctor_two_id, "Medication review", "completed"),
        ]:
            conn.execute("UPDATE availability_slots SET is_available = 0 WHERE id = ?", (slot["id"],))
            conn.execute(
                """
                INSERT INTO appointments (slot_id, patient_id, doctor_id, slot_time, reason, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (slot["id"], patient_id, doctor_id, slot["slot_time"], reason, status, now_iso(), now_iso()),
            )

        conn.executemany(
            """
            INSERT INTO medical_records (patient_id, doctor_id, record_date, title, notes, prescription)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    patient_one_id,
                    doctor_one_id,
                    date.today().isoformat(),
                    "Heart health review",
                    "Vitals stable. Continue lifestyle plan and monitor blood pressure twice weekly.",
                    "Continue current prescription for 30 days.",
                ),
                (
                    patient_two_id,
                    doctor_two_id,
                    (date.today() - timedelta(days=5)).isoformat(),
                    "General consultation",
                    "Recovered well from recent viral infection. Encourage hydration and rest.",
                    "Paracetamol as needed.",
                ),
            ],
        )
