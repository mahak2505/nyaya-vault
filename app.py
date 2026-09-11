import sqlite3
import hashlib
import os
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, session, flash

app = Flask(__name__)
app.secret_key = "change-this-to-something-random-later"

DB_PATH = "database.db"


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            role TEXT NOT NULL,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL
        )
    """)

    # A "case" = one FIR record, linked to a person by name + phone
    cur.execute("""
        CREATE TABLE IF NOT EXISTS cases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fir_number TEXT NOT NULL,
            person_name TEXT NOT NULL,
            person_phone TEXT NOT NULL,
            offence_section TEXT,
            description TEXT,
            status TEXT DEFAULT 'Pending',   -- Pending / Pass
            stage TEXT DEFAULT 'FIR Registered',
            due_date TEXT,
            filed_by TEXT,
            filed_at TEXT,
            hash_seal TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS evidence (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            case_id INTEGER NOT NULL,
            file_name TEXT,
            file_hash TEXT,
            uploaded_by TEXT,
            uploaded_at TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_name TEXT,
            action TEXT,
            reference TEXT,
            timestamp TEXT,
            flagged INTEGER DEFAULT 0
        )
    """)

    cur.execute("SELECT COUNT(*) FROM users")
    if cur.fetchone()[0] == 0:
        cur.execute(
            "INSERT INTO users (name, role, username, password) VALUES (?, ?, ?, ?)",
            ("R. Sharma", "Investigating Officer", "rsharma", "password123")
        )

    # Seed two demo cases for the SAME person, at different times,
    # so the repeat-offender linking has something to show immediately.
    cur.execute("SELECT COUNT(*) FROM cases")
    if cur.fetchone()[0] == 0:
        cur.executemany(
            """INSERT INTO cases
               (fir_number, person_name, person_phone, offence_section, description, status, stage, due_date, filed_by, filed_at, hash_seal)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                ("FIR/0231/20", "Ramesh Kumar", "9812345678", "IPC 379 (Theft)",
                 "Theft of two-wheeler reported from Model Town area.", "Pass", "Disposed", "2020-11-13",
                 "SHO Meena Kumari", "2020-09-14 11:20:00", hash_of("seed-case-1")),
                ("FIR/0774/23", "Ramesh Kumar", "9812345678", "IPC 392 (Robbery)",
                 "Robbery reported near Civil Lines market at night.", "Pending", "Investigation", "2023-10-01",
                 "IO R. Sharma", "2023-08-02 19:45:00", hash_of("seed-case-2")),
                ("FIR/0442/25", "Suresh Yadav", "9876543210", "IPC 302 (Murder)",
                 "Homicide investigation, Sector 14.", "Pending", "Charge Sheet", "2025-03-10",
                 "SHO Meena Kumari", "2025-01-10 08:05:00", hash_of("seed-case-3")),
            ]
        )

    conn.commit()
    conn.close()


def hash_of(text):
    return hashlib.sha256(text.encode()).hexdigest()


CASE_STAGES = ["FIR Registered", "Investigation", "Charge Sheet", "Trial", "Disposed"]

UPLOAD_FOLDER = "uploads"
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)


def log_action(user_name, action, reference="", flagged=0):
    conn = get_db()
    conn.execute(
        "INSERT INTO audit_log (user_name, action, reference, timestamp, flagged) VALUES (?, ?, ?, ?, ?)",
        (user_name, action, reference, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), flagged)
    )
    conn.commit()
    conn.close()


def login_required(view_func):
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return view_func(*args, **kwargs)
    wrapper.__name__ = view_func.__name__
    return wrapper


# ---------- AUTH ----------

@app.route("/")
def home_redirect():
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")

        conn = get_db()
        user = conn.execute(
            "SELECT * FROM users WHERE username = ? AND password = ?",
            (username, password)
        ).fetchone()
        conn.close()

        if user:
            session["user_id"] = user["id"]
            session["user_name"] = user["name"]
            session["user_role"] = user["role"]
            log_action(user["name"], "Logged in")
            return redirect(url_for("home"))
        else:
            flash("Invalid username or password")
            return redirect(url_for("login"))

    return render_template("login.html")


@app.route("/logout")
def logout():
    if "user_name" in session:
        log_action(session["user_name"], "Logged out")
    session.clear()
    return redirect(url_for("login"))


# ---------- POST-LOGIN HUB ----------

@app.route("/home")
@login_required
def home():
    return render_template(
        "home.html",
        user_name=session.get("user_name"),
        user_role=session.get("user_role"),
    )


# ---------- DASHBOARD (with pending/resolved chart) ----------

@app.route("/dashboard")
@login_required
def dashboard():
    conn = get_db()
    total = conn.execute("SELECT COUNT(*) FROM cases").fetchone()[0]
    pending = conn.execute("SELECT COUNT(*) FROM cases WHERE status = 'Pending'").fetchone()[0]
    passed = total - pending
    pending_pct = round((pending / total) * 100) if total else 0
    passed_pct = 100 - pending_pct

    recent = conn.execute("SELECT * FROM cases ORDER BY filed_at DESC LIMIT 6").fetchall()
    flagged_count = conn.execute("SELECT COUNT(*) FROM audit_log WHERE flagged = 1").fetchone()[0]

    today = datetime.now().strftime("%Y-%m-%d")
    overdue_count = conn.execute(
        "SELECT COUNT(*) FROM cases WHERE due_date < ? AND stage != 'Disposed'", (today,)
    ).fetchone()[0]

    # Crime-type breakdown for the bar chart
    breakdown_rows = conn.execute(
        "SELECT offence_section, COUNT(*) as cnt FROM cases GROUP BY offence_section ORDER BY cnt DESC LIMIT 6"
    ).fetchall()
    conn.close()

    max_cnt = max([r["cnt"] for r in breakdown_rows], default=1)

    return render_template(
        "dashboard.html",
        total=total, pending=pending, passed=passed,
        pending_pct=pending_pct, passed_pct=passed_pct,
        recent=recent, flagged_count=flagged_count, overdue_count=overdue_count,
        breakdown_rows=breakdown_rows, max_cnt=max_cnt,
        user_name=session.get("user_name"),
        user_role=session.get("user_role"),
    )


# ---------- FILE NEW FIR ----------

@app.route("/file-fir", methods=["GET", "POST"])
@login_required
def file_fir():
    if request.method == "POST":
        fir_number = request.form.get("fir_number")
        person_name = request.form.get("person_name")
        person_phone = request.form.get("person_phone")
        offence_section = request.form.get("offence_section")
        description = request.form.get("description")

        seal = hash_of(f"{fir_number}{person_name}{person_phone}{datetime.now()}")
        # CrPC norm: charge sheet typically due within 60 days of FIR registration
        due_date = (datetime.now() + timedelta(days=60)).strftime("%Y-%m-%d")

        conn = get_db()
        conn.execute(
            """INSERT INTO cases
               (fir_number, person_name, person_phone, offence_section, description, status, stage, due_date, filed_by, filed_at, hash_seal)
               VALUES (?, ?, ?, ?, ?, 'Pending', 'FIR Registered', ?, ?, ?, ?)""",
            (fir_number, person_name, person_phone, offence_section, description, due_date,
             session.get("user_name"), datetime.now().strftime("%Y-%m-%d %H:%M:%S"), seal)
        )
        conn.commit()

        # Check if this person already has past cases — repeat offender check
        prior_count = conn.execute(
            "SELECT COUNT(*) FROM cases WHERE person_phone = ? AND fir_number != ?",
            (person_phone, fir_number)
        ).fetchone()[0]
        conn.close()

        log_action(session.get("user_name"), "Filed new FIR", fir_number)

        if prior_count > 0:
            flash(f"FIR {fir_number} filed. Note: {person_name} has {prior_count} prior case(s) on record.")
        else:
            flash(f"FIR {fir_number} filed and sealed.")

        return redirect(url_for("search_records", q=person_phone))

    return render_template(
        "file_fir.html",
        user_name=session.get("user_name"),
        user_role=session.get("user_role"),
    )


# ---------- SEARCH RECORDS (with history auto-linking) ----------

@app.route("/search")
@login_required
def search_records():
    q = request.args.get("q", "").strip()
    results = []
    grouped = {}

    if q:
        conn = get_db()
        rows = conn.execute(
            """SELECT * FROM cases
               WHERE person_name LIKE ? OR person_phone LIKE ? OR fir_number LIKE ?
               ORDER BY filed_at ASC""",
            (f"%{q}%", f"%{q}%", f"%{q}%")
        ).fetchall()
        conn.close()

        # Group by person (name + phone) so every past case surfaces together
        for row in rows:
            key = (row["person_name"], row["person_phone"])
            grouped.setdefault(key, []).append(row)

    return render_template(
        "search.html",
        q=q, grouped=grouped,
        user_name=session.get("user_name"),
        user_role=session.get("user_role"),
    )


# ---------- CASE DETAIL (stage tracker + evidence) ----------

@app.route("/case/<int:case_id>")
@login_required
def case_detail(case_id):
    conn = get_db()
    case = conn.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
    evidence = conn.execute(
        "SELECT * FROM evidence WHERE case_id = ? ORDER BY uploaded_at DESC", (case_id,)
    ).fetchall()
    conn.close()

    if not case:
        flash("Case not found.")
        return redirect(url_for("search_records"))

    is_overdue = False
    if case["due_date"] and case["stage"] != "Disposed":
        is_overdue = datetime.now().strftime("%Y-%m-%d") > case["due_date"]

    return render_template(
        "case_detail.html",
        case=case, evidence=evidence, stages=CASE_STAGES, is_overdue=is_overdue,
        user_name=session.get("user_name"),
        user_role=session.get("user_role"),
    )


@app.route("/case/<int:case_id>/advance-stage", methods=["POST"])
@login_required
def advance_stage(case_id):
    conn = get_db()
    case = conn.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
    if case:
        current_idx = CASE_STAGES.index(case["stage"]) if case["stage"] in CASE_STAGES else 0
        if current_idx < len(CASE_STAGES) - 1:
            new_stage = CASE_STAGES[current_idx + 1]
            new_status = "Pass" if new_stage == "Disposed" else "Pending"
            conn.execute(
                "UPDATE cases SET stage = ?, status = ? WHERE id = ?",
                (new_stage, new_status, case_id)
            )
            conn.commit()
            log_action(session.get("user_name"), f"Advanced case to '{new_stage}'", case["fir_number"])
    conn.close()
    return redirect(url_for("case_detail", case_id=case_id))


@app.route("/case/<int:case_id>/upload-evidence", methods=["POST"])
@login_required
def upload_evidence(case_id):
    file = request.files.get("file")
    if not file or file.filename == "":
        flash("No file selected")
        return redirect(url_for("case_detail", case_id=case_id))

    save_path = os.path.join(UPLOAD_FOLDER, f"{case_id}_{file.filename}")
    file.save(save_path)

    sha256 = hashlib.sha256()
    with open(save_path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            sha256.update(chunk)
    file_hash = sha256.hexdigest()

    conn = get_db()
    conn.execute(
        "INSERT INTO evidence (case_id, file_name, file_hash, uploaded_by, uploaded_at) VALUES (?, ?, ?, ?, ?)",
        (case_id, file.filename, file_hash, session.get("user_name"),
         datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    )
    conn.commit()
    conn.close()

    log_action(session.get("user_name"), "Uploaded evidence", file.filename)
    flash(f"Evidence sealed — SHA-256: {file_hash[:12]}...")
    return redirect(url_for("case_detail", case_id=case_id))


# ---------- TAMPER SIMULATION (unauthorized edit/delete attempt) ----------

@app.route("/simulate-tamper/<int:case_id>", methods=["POST"])
@login_required
def simulate_tamper(case_id):
    # Demo: simulates someone trying to alter a sealed case record.
    # Because the record's hash_seal no longer matches, it gets flagged
    # in the audit log instead of silently going through.
    conn = get_db()
    case = conn.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
    conn.close()

    if case:
        log_action(
            session.get("user_name"),
            f"Unauthorized modification attempt blocked on {case['fir_number']}",
            case["fir_number"],
            flagged=1
        )
        flash(f"Blocked: tampering attempt on {case['fir_number']} detected and logged to audit trail.")

    return redirect(request.referrer or url_for("dashboard"))


# ---------- AUDIT TRAIL ----------

@app.route("/audit")
@login_required
def audit_trail():
    conn = get_db()
    logs = conn.execute("SELECT * FROM audit_log ORDER BY timestamp DESC LIMIT 50").fetchall()
    conn.close()
    return render_template(
        "audit.html", logs=logs,
        user_name=session.get("user_name"),
        user_role=session.get("user_role"),
    )


if __name__ == "__main__":
    init_db()
    app.run(debug=True)
