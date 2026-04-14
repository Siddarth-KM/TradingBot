#!/usr/bin/env python3
"""
Health report for signal generation.

Runs after trading_bot.py to validate the output and send an SMTP alert
if anything is wrong. Writes signals/run_status.json heartbeat so
external tooling can tell if a weekly run succeeded.

Usage:
  python health_report.py              # normal post-run check
  python health_report.py --test-alert # force an alert email (wiring test)

Exits 0 on success, 1 on failure. run_signals.sh reads the exit code.
"""
import json
import os
import smtplib
import ssl
import sys
import traceback
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

SCRIPT_DIR = Path(__file__).resolve().parent
BOT_DIR = SCRIPT_DIR.parent
SIGNALS_PATH = BOT_DIR / "signals" / "current_signals.json"
STATUS_PATH = BOT_DIR / "signals" / "run_status.json"
ENV_PATH = BOT_DIR / ".env"
MAX_AGE_DAYS = 7


def load_env():
    if load_dotenv and ENV_PATH.exists():
        load_dotenv(ENV_PATH)


def send_alert(subject: str, body: str) -> bool:
    load_env()
    sender = os.environ.get("GMAIL_FROM")
    password = os.environ.get("GMAIL_APP_PASSWORD")
    if not sender or not password:
        print(f"[health_report] SMTP creds missing; cannot alert. Subject was: {subject}")
        return False
    # strip any stray whitespace some editors leave in env files
    password = password.replace(" ", "").strip()
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = sender
    msg.set_content(body)
    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as server:
            server.starttls(context=ctx)
            server.login(sender, password)
            server.send_message(msg)
        print(f"[health_report] Alert sent: {subject}")
        return True
    except Exception as e:
        print(f"[health_report] Alert FAILED: {e}")
        return False


def validate() -> tuple[bool, list[str], dict]:
    errors: list[str] = []
    info: dict = {}
    if not SIGNALS_PATH.exists():
        errors.append(f"signals file missing: {SIGNALS_PATH}")
        return False, errors, info
    try:
        data = json.loads(SIGNALS_PATH.read_text())
    except Exception as e:
        errors.append(f"signals file unreadable JSON: {e}")
        return False, errors, info

    ts_str = data.get("timestamp")
    all_signals = data.get("all_signals", [])
    summary = data.get("summary", {})
    info["n_signals"] = len(all_signals)
    info["timestamp"] = ts_str
    info["best_signal"] = summary.get("best_signal") if isinstance(summary, dict) else None

    if not ts_str:
        errors.append("top-level 'timestamp' missing")
    else:
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            age = datetime.now(timezone.utc) - ts
            info["age_hours"] = round(age.total_seconds() / 3600, 1)
            if age > timedelta(days=MAX_AGE_DAYS):
                errors.append(f"signals too old: {age.days} days")
        except Exception as e:
            errors.append(f"bad timestamp format: {e}")

    if not all_signals:
        errors.append("all_signals array is empty")
    else:
        for i, s in enumerate(all_signals):
            for field in ("ticker", "last_close", "predicted_return"):
                if field not in s:
                    errors.append(f"signal[{i}] missing field '{field}'")
                    break
            price = s.get("last_close", 0)
            if isinstance(price, (int, float)) and price <= 0:
                errors.append(f"signal[{i}] ticker={s.get('ticker')} has non-positive last_close")

    if not isinstance(summary, dict) or not summary:
        errors.append("summary section missing or empty")

    return len(errors) == 0, errors, info


def write_status(ok: bool, errors: list[str], info: dict):
    now = datetime.now(timezone.utc).isoformat()
    prev = {}
    if STATUS_PATH.exists():
        try:
            prev = json.loads(STATUS_PATH.read_text())
        except Exception:
            prev = {}
    status = {
        "last_attempt": now,
        "last_success": now if ok else prev.get("last_success"),
        "ok": ok,
        "errors": errors,
        "info": info,
    }
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATUS_PATH.write_text(json.dumps(status, indent=2))


def main():
    args = sys.argv[1:]
    if "--test-alert" in args:
        ok = send_alert(
            subject="INDEX-LAB | health_report test alert",
            body=f"This is a wiring test of the VM health alert path.\nGenerated at {datetime.now(timezone.utc).isoformat()} from {BOT_DIR}",
        )
        sys.exit(0 if ok else 1)

    try:
        ok, errors, info = validate()
    except Exception:
        tb = traceback.format_exc()
        send_alert(
            subject="INDEX-LAB | health_report CRASHED",
            body=f"health_report.py raised an exception:\n\n{tb}",
        )
        write_status(False, [f"crash: {tb.splitlines()[-1]}"], {})
        sys.exit(1)

    write_status(ok, errors, info)

    now_iso = datetime.now(timezone.utc).isoformat()
    if ok:
        body_lines = [
            f"Signal generation SUCCEEDED on VM at {now_iso}",
            "",
            f"File: {SIGNALS_PATH}",
            f"Signals:      {info.get('n_signals')}",
            f"Age:          {info.get('age_hours')}h",
            f"Best signal:  {info.get('best_signal')}",
            f"Timestamp:    {info.get('timestamp')}",
        ]
        send_alert(
            subject=f"INDEX-LAB | VM signal run OK ({info.get('n_signals')} signals)",
            body="\n".join(body_lines),
        )
        print(f"[health_report] OK — {info.get('n_signals')} signals, age {info.get('age_hours')}h")
        sys.exit(0)

    body_lines = [
        f"Signal generation validation FAILED on VM at {now_iso}",
        "",
        f"File: {SIGNALS_PATH}",
        f"Info: {json.dumps(info, indent=2)}",
        "",
        "Errors:",
        *[f"  - {e}" for e in errors],
    ]
    send_alert(
        subject="INDEX-LAB | VM signal generation FAILED",
        body="\n".join(body_lines),
    )
    print(f"[health_report] FAIL — {len(errors)} errors")
    for e in errors:
        print(f"  - {e}")
    sys.exit(1)


if __name__ == "__main__":
    main()
