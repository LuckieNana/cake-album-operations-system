"""
Sends the Cake Album daily report by email. Meant to be run on a schedule (cron or a
systemd timer) — see DAILY_REPORT_SETUP.md for how to wire that up. Can also be run
by hand any time to send today's report immediately:

    python3 send_daily_report.py

Reads the same database the main app uses, and the same SMTP_EMAIL / SMTP_APP_PASSWORD
environment variables — nothing here needs separate configuration from the main app.
"""
import sys
import importlib.util
from pathlib import Path
from datetime import date

APP_FILE = Path(__file__).parent / "operations_system_app_v1_3.py"


def load_app_module():
    """Imports the main app file as a plain module, without actually running the
    Streamlit UI - we only need its data-loading and email-sending functions."""
    spec = importlib.util.spec_from_file_location("cake_album_app", APP_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    app = load_app_module()
    success, message = app.send_daily_report_email(
        to_address="cakealbum@gmail.com",
        report_date=date.today(),
    )
    print(message)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
