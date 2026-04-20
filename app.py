import json
import os
import secrets
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, g, jsonify, request, send_from_directory

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
PUBLIC_DIR = BASE_DIR / "public"
SCHEDULE_PATH = DATA_DIR / "schedule.json"
STUDENTS_PATH = DATA_DIR / "students.json"

with open(SCHEDULE_PATH, "r", encoding="utf-8") as schedule_file:
    SCHEDULE = json.load(schedule_file)

with open(STUDENTS_PATH, "r", encoding="utf-8") as students_file:
    STUDENTS = json.load(students_file)

STUDENTS_BY_ID = {student["id"]: student for student in STUDENTS}
TOKEN_STORE = {}

database_path_raw = os.environ.get("DATABASE_PATH", "data/attendance.db")
if os.path.isabs(database_path_raw):
    DATABASE_PATH = Path(database_path_raw)
else:
    DATABASE_PATH = BASE_DIR / database_path_raw
DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)

app = Flask(__name__, static_folder=str(PUBLIC_DIR), static_url_path="")


def get_db():
    db = getattr(g, "_database", None)
    if db is None:
        db = sqlite3.connect(DATABASE_PATH)
        db.row_factory = sqlite3.Row
        g._database = db
    return db


@app.teardown_appcontext
def close_connection(exception):  # noqa: ARG001
    db = getattr(g, "_database", None)
    if db is not None:
        db.close()


def init_db():
    db = sqlite3.connect(DATABASE_PATH)
    try:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS attendance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id TEXT NOT NULL,
                student_name TEXT NOT NULL,
                date TEXT NOT NULL,
                lesson_index INTEGER NOT NULL,
                present INTEGER NOT NULL DEFAULT 0,
                marked_by TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(student_id, date, lesson_index)
            )
            """
        )
        db.commit()
    finally:
        db.close()


def start_of_week(date_value: datetime) -> datetime:
    weekday = date_value.weekday()
    monday = date_value - timedelta(days=weekday)
    return monday.replace(hour=0, minute=0, second=0, microsecond=0)


def parse_date(date_text: str):
    try:
        return datetime.strptime(date_text, "%Y-%m-%d")
    except (TypeError, ValueError):
        return None


def get_week_type(date_text: str):
    date_value = parse_date(date_text)
    if not date_value:
        return None

    anchor_date = parse_date(SCHEDULE["anchor"]["date"])
    current_week_start = start_of_week(date_value)
    anchor_week_start = start_of_week(anchor_date)
    diff_weeks = round((current_week_start - anchor_week_start).days / 7)

    anchor_is_denominator = SCHEDULE["anchor"]["type"] == "denominator"
    is_even = abs(diff_weeks) % 2 == 0

    if anchor_is_denominator:
        return "denominator" if is_even else "numerator"
    return "numerator" if is_even else "denominator"


def get_lessons_by_date(date_text: str):
    date_value = parse_date(date_text)
    if not date_value:
        return None

    week_type = get_week_type(date_text)
    # JS getDay: Sun=0..Sat=6. Python weekday: Mon=0..Sun=6.
    day_js = (date_value.weekday() + 1) % 7
    lessons = SCHEDULE.get(week_type, {}).get(str(day_js), [])
    return {"weekType": week_type, "lessons": lessons}


def public_student(student):
    return {
        "id": student["id"],
        "fullName": student["fullName"],
        "role": student["role"],
    }


def get_auth_user():
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return None
    token = auth_header[7:]
    return TOKEN_STORE.get(token)


def auth_required():
    user = get_auth_user()
    if not user:
        return None, (jsonify({"error": "Unauthorized"}), 401)
    return user, None


def headman_only(user):
    if user["role"] != "headman":
        return jsonify({"error": "Forbidden"}), 403
    return None


@app.route("/")
def index():
    return send_from_directory(PUBLIC_DIR, "index.html")


@app.post("/api/login")
def login():
    payload = request.get_json(silent=True) or {}
    student = STUDENTS_BY_ID.get(payload.get("id"))

    if not student or student["password"] != payload.get("password"):
        return jsonify({"error": "Invalid credentials"}), 401

    token = secrets.token_hex(24)
    user_public = public_student(student)
    TOKEN_STORE[token] = user_public
    return jsonify({"token": token, "user": user_public})


@app.get("/api/me")
def me():
    user, error = auth_required()
    if error:
        return error
    return jsonify({"user": user})


@app.get("/api/schedule")
def schedule():
    user, error = auth_required()
    if error:
        return error

    _ = user
    date_text = request.args.get("date")
    lessons_data = get_lessons_by_date(date_text)
    if not date_text or not lessons_data:
        return jsonify({"error": "Invalid or missing date"}), 400

    return jsonify(
        {
            "date": date_text,
            "weekType": lessons_data["weekType"],
            "lessons": lessons_data["lessons"],
        }
    )


@app.post("/api/attendance")
def attendance_upsert():
    user, error = auth_required()
    if error:
        return error

    payload = request.get_json(silent=True) or {}
    date_text = payload.get("date")
    lesson_index = payload.get("lessonIndex")
    present = payload.get("present")

    if not date_text or not isinstance(lesson_index, int) or not isinstance(present, bool):
        return jsonify({"error": "date, lessonIndex, present are required"}), 400

    lesson_info = get_lessons_by_date(date_text)
    if not lesson_info:
        return jsonify({"error": "Invalid date"}), 400

    lesson_exists = any(lesson["index"] == lesson_index for lesson in lesson_info["lessons"])
    if not lesson_exists:
        return jsonify({"error": "Lesson does not exist for selected date"}), 400

    db = get_db()
    db.execute(
        """
        INSERT INTO attendance (student_id, student_name, date, lesson_index, present, marked_by)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(student_id, date, lesson_index) DO UPDATE SET
            student_name=excluded.student_name,
            present=excluded.present,
            marked_by=excluded.marked_by,
            updated_at=CURRENT_TIMESTAMP
        """,
        (
            user["id"],
            user["fullName"],
            date_text,
            lesson_index,
            1 if present else 0,
            user["id"],
        ),
    )
    db.commit()

    row = db.execute(
        """
        SELECT id, student_id, student_name, date, lesson_index, present, marked_by, created_at, updated_at
        FROM attendance
        WHERE student_id = ? AND date = ? AND lesson_index = ?
        """,
        (user["id"], date_text, lesson_index),
    ).fetchone()

    attendance = dict(row)
    attendance["present"] = bool(attendance["present"])
    return jsonify({"attendance": attendance})


@app.get("/api/attendance/my")
def attendance_my():
    user, error = auth_required()
    if error:
        return error

    date_text = request.args.get("date")
    if not date_text:
        return jsonify({"error": "date is required"}), 400

    db = get_db()
    rows = db.execute(
        """
        SELECT id, student_id, student_name, date, lesson_index, present, marked_by, created_at, updated_at
        FROM attendance
        WHERE student_id = ? AND date = ?
        ORDER BY lesson_index ASC
        """,
        (user["id"], date_text),
    ).fetchall()

    records = [dict(row) for row in rows]
    for record in records:
        record["present"] = bool(record["present"])
    return jsonify({"records": records})


@app.get("/api/attendance/day")
def attendance_day():
    user, error = auth_required()
    if error:
        return error

    permission_error = headman_only(user)
    if permission_error:
        return permission_error

    date_text = request.args.get("date")
    if not date_text:
        return jsonify({"error": "date is required"}), 400

    db = get_db()
    rows = db.execute(
        """
        SELECT id, student_id, student_name, date, lesson_index, present, marked_by, created_at, updated_at
        FROM attendance
        WHERE date = ?
        ORDER BY lesson_index ASC, student_name ASC
        """,
        (date_text,),
    ).fetchall()

    records = [dict(row) for row in rows]
    for record in records:
        record["present"] = bool(record["present"])
    return jsonify({"records": records})


@app.get("/api/group")
def group():
    user, error = auth_required()
    if error:
        return error

    permission_error = headman_only(user)
    if permission_error:
        return permission_error

    group_students = [public_student(student) for student in STUDENTS]
    return jsonify({"students": group_students})


@app.errorhandler(404)
def not_found(_error):
    if request.path.startswith("/api/"):
        return jsonify({"error": "Route not found"}), 404
    return send_from_directory(PUBLIC_DIR, "index.html")


init_db()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=True)
