import smtplib
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

from flask import current_app


def send_login_confirmation(email, full_name, role):
    if not current_app.config["LOGIN_EMAIL_ENABLED"]:
        return {"status": "disabled"}

    message = EmailMessage()
    message["Subject"] = "Happy Care login confirmation"
    message["From"] = current_app.config["SMTP_SENDER"]
    message["To"] = email
    message.set_content(
        f"""Hello {full_name},

This is a confirmation that your Happy Care {role} portal was accessed on {datetime.now().strftime('%d %b %Y at %H:%M')}.

If this was not you, please contact the Happy Care administration team immediately.

Kind regards,
Happy Care
"""
    )

    smtp_server = current_app.config["SMTP_SERVER"]
    smtp_username = current_app.config["SMTP_USERNAME"]
    smtp_password = current_app.config["SMTP_PASSWORD"]
    smtp_port = current_app.config["SMTP_PORT"]

    outbox = Path(current_app.config["OUTBOX_DIR"])
    safe_email = email.replace("@", "_at_").replace(".", "_")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    outbox_file = outbox / f"{timestamp}_{safe_email}.txt"

    if smtp_server and smtp_username and smtp_password:
        try:
            with smtplib.SMTP(smtp_server, smtp_port, timeout=15) as server:
                server.starttls()
                server.login(smtp_username, smtp_password)
                server.send_message(message)
            return {"status": "sent"}
        except Exception as exc:
            outbox_file.write_text(
                f"SMTP delivery failed: {exc}\n\n{message.as_string()}",
                encoding="utf-8",
            )
            return {"status": "staged", "path": str(outbox_file)}

    outbox_file.write_text(message.as_string(), encoding="utf-8")
    return {"status": "staged", "path": str(outbox_file)}
