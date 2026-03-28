import os
import secrets
import sqlite3
from datetime import date, datetime, time as daytime, timedelta
from functools import wraps
from pathlib import Path

from flask import (
    Flask,
    abort,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash

from data_access import (
    create_user,
    db_connection,
    init_db,
    now_iso,
    seed_demo_data,
    seed_future_slots,
)
from email_utils import send_login_confirmation


def create_app(test_config=None):
    base_dir = Path(__file__).resolve().parent
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=os.environ.get("SECRET_KEY", "happy-care-dev-secret-change-me"),
        DATABASE=str(base_dir / "data" / "happy_care.db"),
        OUTBOX_DIR=str(base_dir / "mail_outbox"),
        LOGIN_EMAIL_ENABLED=os.environ.get("LOGIN_EMAIL_ENABLED", "true").lower() != "false",
        SMTP_SERVER=os.environ.get("SMTP_SERVER", ""),
        SMTP_PORT=int(os.environ.get("SMTP_PORT", "587")),
        SMTP_USERNAME=os.environ.get("SMTP_USERNAME", ""),
        SMTP_PASSWORD=os.environ.get("SMTP_PASSWORD", ""),
        SMTP_SENDER=os.environ.get("SMTP_SENDER", "no-reply@happycare.local"),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
    )

    if test_config:
        app.config.update(test_config)

    Path(app.config["DATABASE"]).parent.mkdir(parents=True, exist_ok=True)
    Path(app.config["OUTBOX_DIR"]).mkdir(parents=True, exist_ok=True)

    with app.app_context():
        init_db()
        seed_demo_data()

    register_template_tools(app)
    register_request_hooks(app)
    register_routes(app)
    return app


def register_template_tools(app):
    @app.template_filter("pretty_datetime")
    def pretty_datetime_filter(value):
        if not value:
            return "Not set"
        return datetime.fromisoformat(value).strftime("%d %b %Y, %H:%M")

    @app.template_filter("pretty_date")
    def pretty_date_filter(value):
        if not value:
            return "Not set"
        parsed = datetime.fromisoformat(value).date() if "T" in value else date.fromisoformat(value)
        return parsed.strftime("%d %b %Y")

    @app.context_processor
    def inject_template_context():
        return {"csrf_token": get_csrf_token(), "current_year": datetime.now().year}


def register_request_hooks(app):
    @app.before_request
    def load_current_user():
        g.user = None
        user_id = session.get("user_id")
        if not user_id:
            return
        with db_connection() as conn:
            g.user = conn.execute(
                "SELECT id, full_name, email, role, last_login_at FROM users WHERE id = ?",
                (user_id,),
            ).fetchone()
        if g.user is None:
            session.clear()


def register_routes(app):
    @app.route("/")
    def home():
        if g.user:
            return redirect(role_dashboard_endpoint(g.user["role"]))
        return redirect(url_for("login"))

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if g.user:
            return redirect(role_dashboard_endpoint(g.user["role"]))
        if request.method == "POST":
            validate_csrf()
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            with db_connection() as conn:
                user = conn.execute(
                    "SELECT id, full_name, email, password_hash, role FROM users WHERE email = ?",
                    (email,),
                ).fetchone()
                if user and check_password_hash(user["password_hash"], password):
                    conn.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (now_iso(), user["id"]))
                    session.clear()
                    session["user_id"] = user["id"]
                    session["csrf_token"] = secrets.token_hex(16)
                    email_status = send_login_confirmation(user["email"], user["full_name"], user["role"])
                    if email_status["status"] == "sent":
                        flash("Login successful. A confirmation email has been sent.", "success")
                    elif email_status["status"] == "staged":
                        flash(
                            "Login successful. SMTP is not configured, so the confirmation email was saved in mail_outbox.",
                            "info",
                        )
                    else:
                        flash("Login successful. Email confirmation is disabled for this build.", "info")
                    return redirect(role_dashboard_endpoint(user["role"]))
            flash("Invalid email or password. Try one of the demo accounts below.", "danger")
        return render_template("login.html", body_class="login-page")

    @app.post("/logout")
    @login_required
    def logout():
        validate_csrf()
        session.clear()
        flash("You have been logged out safely.", "success")
        return redirect(url_for("login"))

    @app.route("/patient/dashboard")
    @role_required("patient")
    def patient_dashboard():
        with db_connection() as conn:
            patient = conn.execute(
                """
                SELECT patients.id AS patient_id, users.full_name, users.email, users.last_login_at,
                       patients.date_of_birth, patients.phone, patients.emergency_contact
                FROM patients
                JOIN users ON users.id = patients.user_id
                WHERE users.id = ?
                """,
                (g.user["id"],),
            ).fetchone()
            appointments = conn.execute(
                """
                SELECT appointments.id, appointments.slot_time, appointments.reason, appointments.status,
                       doctor_user.full_name AS doctor_name, doctors.specialty
                FROM appointments
                JOIN doctors ON doctors.id = appointments.doctor_id
                JOIN users AS doctor_user ON doctor_user.id = doctors.user_id
                WHERE appointments.patient_id = ?
                ORDER BY appointments.slot_time DESC
                LIMIT 8
                """,
                (patient["patient_id"],),
            ).fetchall()
            records = conn.execute(
                """
                SELECT medical_records.record_date, medical_records.title, medical_records.notes,
                       medical_records.prescription, doctor_user.full_name AS doctor_name
                FROM medical_records
                JOIN doctors ON doctors.id = medical_records.doctor_id
                JOIN users AS doctor_user ON doctor_user.id = doctors.user_id
                WHERE medical_records.patient_id = ?
                ORDER BY medical_records.record_date DESC
                LIMIT 8
                """,
                (patient["patient_id"],),
            ).fetchall()
            doctors = conn.execute(
                """
                SELECT doctors.id, users.full_name, doctors.specialty, doctors.bio
                FROM doctors
                JOIN users ON users.id = doctors.user_id
                ORDER BY users.full_name
                """
            ).fetchall()
        return render_template(
            "patient_dashboard.html",
            body_class="dashboard-page",
            patient=patient,
            appointments=appointments,
            records=records,
            doctors=doctors,
            today=date.today().isoformat(),
        )

    @app.route("/doctor/dashboard")
    @role_required("doctor")
    def doctor_dashboard():
        with db_connection() as conn:
            doctor = conn.execute(
                """
                SELECT doctors.id AS doctor_id, users.full_name, users.email, users.last_login_at,
                       doctors.specialty, doctors.bio
                FROM doctors
                JOIN users ON users.id = doctors.user_id
                WHERE users.id = ?
                """,
                (g.user["id"],),
            ).fetchone()
            appointments = conn.execute(
                """
                SELECT appointments.id, appointments.slot_time, appointments.reason, appointments.status,
                       patient_user.full_name AS patient_name, patient_user.email AS patient_email
                FROM appointments
                JOIN patients ON patients.id = appointments.patient_id
                JOIN users AS patient_user ON patient_user.id = patients.user_id
                WHERE appointments.doctor_id = ?
                ORDER BY appointments.slot_time ASC
                LIMIT 10
                """,
                (doctor["doctor_id"],),
            ).fetchall()
            open_slots = conn.execute(
                """
                SELECT id, slot_time, is_available
                FROM availability_slots
                WHERE doctor_id = ?
                ORDER BY slot_time ASC
                LIMIT 14
                """,
                (doctor["doctor_id"],),
            ).fetchall()
            patients = conn.execute(
                """
                SELECT patients.id, users.full_name, users.email
                FROM patients
                JOIN users ON users.id = patients.user_id
                ORDER BY users.full_name
                """
            ).fetchall()
            record_count = conn.execute(
                "SELECT COUNT(*) AS total FROM medical_records WHERE doctor_id = ?",
                (doctor["doctor_id"],),
            ).fetchone()["total"]
        return render_template(
            "doctor_dashboard.html",
            body_class="dashboard-page",
            doctor=doctor,
            appointments=appointments,
            open_slots=open_slots,
            patients=patients,
            record_count=record_count,
            today=date.today().isoformat(),
        )

    @app.route("/admin/dashboard")
    @role_required("admin")
    def admin_dashboard():
        with db_connection() as conn:
            stats = {
                "users": conn.execute("SELECT COUNT(*) AS total FROM users").fetchone()["total"],
                "doctors": conn.execute("SELECT COUNT(*) AS total FROM doctors").fetchone()["total"],
                "patients": conn.execute("SELECT COUNT(*) AS total FROM patients").fetchone()["total"],
                "appointments": conn.execute("SELECT COUNT(*) AS total FROM appointments").fetchone()["total"],
            }
            users = conn.execute(
                """
                SELECT id, full_name, email, role, created_at, last_login_at
                FROM users
                ORDER BY created_at DESC
                LIMIT 10
                """
            ).fetchall()
            appointments = conn.execute(
                """
                SELECT appointments.id, appointments.slot_time, appointments.reason, appointments.status,
                       patient_user.full_name AS patient_name,
                       doctor_user.full_name AS doctor_name
                FROM appointments
                JOIN patients ON patients.id = appointments.patient_id
                JOIN users AS patient_user ON patient_user.id = patients.user_id
                JOIN doctors ON doctors.id = appointments.doctor_id
                JOIN users AS doctor_user ON doctor_user.id = doctors.user_id
                ORDER BY appointments.slot_time DESC
                LIMIT 12
                """
            ).fetchall()
        return render_template(
            "admin_dashboard.html",
            body_class="dashboard-page",
            stats=stats,
            users=users,
            appointments=appointments,
            today=date.today().isoformat(),
        )

    @app.post("/admin/users/create")
    @role_required("admin")
    def create_user_route():
        validate_csrf()
        full_name = request.form.get("full_name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        role = request.form.get("role", "").strip()
        specialty = request.form.get("specialty", "").strip()
        bio = request.form.get("bio", "").strip()
        date_of_birth = request.form.get("date_of_birth", "").strip()
        phone = request.form.get("phone", "").strip()
        emergency_contact = request.form.get("emergency_contact", "").strip()

        if not full_name or not email or not password or role not in {"admin", "doctor", "patient"}:
            flash("Full name, email, password, and a valid role are required.", "danger")
            return redirect(url_for("admin_dashboard"))

        with db_connection() as conn:
            existing = conn.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone()
            if existing:
                flash("That email address is already registered.", "danger")
                return redirect(url_for("admin_dashboard"))
            _, profile_id = create_user(
                conn,
                full_name=full_name,
                email=email,
                password=password,
                role=role,
                specialty=specialty or "General Practice",
                bio=bio or "Profile added by Happy Care administration.",
                date_of_birth=date_of_birth,
                phone=phone,
                emergency_contact=emergency_contact,
            )
            if role == "doctor":
                seed_future_slots(conn, profile_id, start_date=date.today(), days=10)
        flash("New account created successfully.", "success")
        return redirect(url_for("admin_dashboard"))

    @app.post("/doctor/availability")
    @role_required("doctor")
    def create_availability():
        validate_csrf()
        slot_date = request.form.get("slot_date", "").strip()
        slot_time = request.form.get("slot_time", "").strip()
        if not slot_date or not slot_time:
            flash("Choose both a date and time for the availability slot.", "danger")
            return redirect(url_for("doctor_dashboard"))
        slot_value = f"{slot_date}T{slot_time}:00"
        with db_connection() as conn:
            doctor = conn.execute("SELECT id FROM doctors WHERE user_id = ?", (g.user["id"],)).fetchone()
            conn.execute(
                "INSERT OR IGNORE INTO availability_slots (doctor_id, slot_time, is_available) VALUES (?, ?, 1)",
                (doctor["id"], slot_value),
            )
        flash("Availability updated.", "success")
        return redirect(url_for("doctor_dashboard"))

    @app.post("/doctor/records")
    @role_required("doctor")
    def create_medical_record():
        validate_csrf()
        patient_id = request.form.get("patient_id", "").strip()
        record_date = request.form.get("record_date", "").strip()
        title = request.form.get("title", "").strip()
        notes = request.form.get("notes", "").strip()
        prescription = request.form.get("prescription", "").strip()
        if not patient_id or not record_date or not title or not notes:
            flash("Patient, record date, title, and notes are required.", "danger")
            return redirect(url_for("doctor_dashboard"))
        with db_connection() as conn:
            doctor = conn.execute("SELECT id FROM doctors WHERE user_id = ?", (g.user["id"],)).fetchone()
            conn.execute(
                """
                INSERT INTO medical_records (patient_id, doctor_id, record_date, title, notes, prescription)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (patient_id, doctor["id"], record_date, title, notes, prescription),
            )
        flash("Medical record added to the patient portal.", "success")
        return redirect(url_for("doctor_dashboard"))

    @app.post("/appointments/<int:appointment_id>/status")
    @role_required("doctor", "admin")
    def update_appointment_status(appointment_id):
        validate_csrf()
        new_status = request.form.get("status", "").strip()
        if new_status not in {"scheduled", "completed", "cancelled"}:
            flash("Invalid appointment status.", "danger")
            return redirect(role_dashboard_endpoint(g.user["role"]))
        return apply_appointment_status_change(appointment_id, new_status)

    @app.get("/api/availability")
    @role_required("patient", "doctor", "admin")
    def get_availability():
        doctor_id = request.args.get("doctor_id", "").strip()
        selected_date = request.args.get("date", "").strip()
        start_date = date.fromisoformat(selected_date) if selected_date else date.today()
        start_value = datetime.combine(start_date, daytime.min).isoformat()
        end_value = datetime.combine(start_date + timedelta(days=14), daytime.min).isoformat()

        query = """
            SELECT availability_slots.id, availability_slots.slot_time, availability_slots.doctor_id,
                   users.full_name AS doctor_name, doctors.specialty
            FROM availability_slots
            JOIN doctors ON doctors.id = availability_slots.doctor_id
            JOIN users ON users.id = doctors.user_id
            WHERE availability_slots.is_available = 1
              AND availability_slots.slot_time >= ?
              AND availability_slots.slot_time < ?
        """
        params = [start_value, end_value]
        if doctor_id:
            query += " AND availability_slots.doctor_id = ?"
            params.append(doctor_id)
        query += " ORDER BY availability_slots.slot_time ASC"

        with db_connection() as conn:
            rows = conn.execute(query, params).fetchall()

        slots = []
        for row in rows:
            slot_time = datetime.fromisoformat(row["slot_time"])
            slots.append(
                {
                    "id": row["id"],
                    "doctor_id": row["doctor_id"],
                    "doctor_name": row["doctor_name"],
                    "specialty": row["specialty"],
                    "slot_time": row["slot_time"],
                    "day_label": slot_time.strftime("%a %d %b"),
                    "time_label": slot_time.strftime("%H:%M"),
                }
            )
        return jsonify({"slots": slots})

    @app.post("/api/appointments")
    @role_required("patient")
    def create_appointment_api():
        validate_csrf()
        payload = request.get_json(silent=True) or {}
        slot_id = payload.get("slot_id")
        reason = str(payload.get("reason", "")).strip()
        if not slot_id or not reason:
            return jsonify({"message": "Choose a slot and enter a reason for the visit."}), 400
        return book_appointment(slot_id, reason)


def book_appointment(slot_id, reason):
    with db_connection() as conn:
        patient = conn.execute("SELECT id FROM patients WHERE user_id = ?", (g.user["id"],)).fetchone()
        try:
            conn.execute("BEGIN IMMEDIATE")
            slot = conn.execute(
                """
                SELECT availability_slots.id, availability_slots.slot_time, availability_slots.doctor_id,
                       availability_slots.is_available, users.full_name AS doctor_name
                FROM availability_slots
                JOIN doctors ON doctors.id = availability_slots.doctor_id
                JOIN users ON users.id = doctors.user_id
                WHERE availability_slots.id = ?
                """,
                (slot_id,),
            ).fetchone()
            if slot is None or slot["is_available"] != 1:
                conn.rollback()
                return jsonify({"message": "That slot is no longer available."}), 409

            update_result = conn.execute(
                "UPDATE availability_slots SET is_available = 0 WHERE id = ? AND is_available = 1",
                (slot_id,),
            )
            if update_result.rowcount != 1:
                conn.rollback()
                return jsonify({"message": "That slot was just booked by someone else."}), 409

            conn.execute(
                """
                INSERT INTO appointments (slot_id, patient_id, doctor_id, slot_time, reason, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 'scheduled', ?, ?)
                """,
                (slot["id"], patient["id"], slot["doctor_id"], slot["slot_time"], reason, now_iso(), now_iso()),
            )
            conn.commit()
        except sqlite3.IntegrityError:
            conn.rollback()
            return jsonify({"message": "The appointment could not be saved."}), 400

    when = datetime.fromisoformat(slot["slot_time"]).strftime("%d %b %Y at %H:%M")
    return jsonify({"message": f"Appointment booked with {slot['doctor_name']} on {when}."})


def apply_appointment_status_change(appointment_id, new_status):
    with db_connection() as conn:
        appointment = conn.execute(
            "SELECT id, slot_id, doctor_id FROM appointments WHERE id = ?",
            (appointment_id,),
        ).fetchone()
        if appointment is None:
            flash("Appointment not found.", "danger")
            return redirect(role_dashboard_endpoint(g.user["role"]))

        if g.user["role"] == "doctor":
            doctor = conn.execute("SELECT id FROM doctors WHERE user_id = ?", (g.user["id"],)).fetchone()
            if appointment["doctor_id"] != doctor["id"]:
                abort(403)

        conn.execute(
            "UPDATE appointments SET status = ?, updated_at = ? WHERE id = ?",
            (new_status, now_iso(), appointment_id),
        )
        conn.execute(
            "UPDATE availability_slots SET is_available = ? WHERE id = ?",
            (1 if new_status == "cancelled" else 0, appointment["slot_id"]),
        )

    flash("Appointment status updated.", "success")
    return redirect(role_dashboard_endpoint(g.user["role"]))


def get_csrf_token():
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_hex(16)
        session["csrf_token"] = token
    return token


def validate_csrf():
    submitted = request.form.get("csrf_token", "")
    if not submitted:
        payload = request.get_json(silent=True) or {}
        submitted = payload.get("csrf_token", "")
    if not submitted or submitted != session.get("csrf_token"):
        abort(403)


def login_required(view_func):
    @wraps(view_func)
    def wrapped_view(*args, **kwargs):
        if g.user is None:
            flash("Please sign in to continue.", "info")
            return redirect(url_for("login"))
        return view_func(*args, **kwargs)

    return wrapped_view


def role_required(*roles):
    def decorator(view_func):
        @wraps(view_func)
        @login_required
        def wrapped_view(*args, **kwargs):
            if g.user["role"] not in roles:
                abort(403)
            return view_func(*args, **kwargs)

        return wrapped_view

    return decorator


def role_dashboard_endpoint(role):
    return {
        "admin": url_for("admin_dashboard"),
        "doctor": url_for("doctor_dashboard"),
        "patient": url_for("patient_dashboard"),
    }[role]


app = create_app()


if __name__ == "__main__":
    app.run(debug=True)
