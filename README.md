# Happy Care

Happy Care is a patient-centric hospital system built for an agile/Scrum coursework project.

## Features

- role-based login for `admin`, `doctor`, and `patient`
- dynamic appointment booking with live doctor availability
- patient access to personal medical records
- admin controls for user creation and appointment oversight
- login confirmation emails through SMTP, with a local `mail_outbox` fallback
- SQLite database with seeded demo data

## Tech stack

- Frontend: HTML, CSS, JavaScript
- Backend: Python Flask
- Database: SQLite

## Run the project

1. Install dependencies:

```powershell
python -m pip install -r requirements.txt
```

2. Optional: configure Gmail SMTP if you want real confirmation emails:

```powershell
$env:SMTP_SERVER="smtp.gmail.com"
$env:SMTP_PORT="587"
$env:SMTP_USERNAME="yourgmail@gmail.com"
$env:SMTP_PASSWORD="your_app_password"
$env:SMTP_SENDER="yourgmail@gmail.com"
```

If SMTP is not configured, login confirmation emails are saved in `mail_outbox`.

3. Start the app:

```powershell
python app.py
```

4. Open `http://127.0.0.1:5000`

## Demo logins

- Admin: `admin@happycare.com` / `Admin@123`
- Doctor: `doctor@happycare.com` / `Doctor@123`
- Patient: `patient@happycare.com` / `Patient@123`

## Database

The SQLite file is created automatically at `data/happy_care.db` the first time the app starts.

## Scrum report support

Use `docs/agile-report.md` as the base for your coursework report.
