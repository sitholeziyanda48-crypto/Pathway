
import os
import uuid
import sqlite3
import smtplib
import ssl
from email.message import EmailMessage

from functools import wraps
from datetime import date

from flask import (
    Flask, render_template, request, redirect,
    url_for, session, flash, send_from_directory,
    abort
)

from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

from database import create_database


app = Flask(__name__)

# Local development uses the project folder.
# On Render, set PATHWAY_DATA_FOLDER=/var/data for persistent storage.
DATA_FOLDER = os.environ.get("PATHWAY_DATA_FOLDER", app.root_path)
os.makedirs(DATA_FOLDER, exist_ok=True)
DATABASE_PATH = os.path.join(DATA_FOLDER, "pathway.db")
app.secret_key = os.environ.get(
    "PATHWAY_SECRET_KEY",
    "pathway-dev-secret-change-later"
)

# Email settings. Set these environment variables before enabling email:
# PATHWAY_MAIL_SERVER, PATHWAY_MAIL_PORT, PATHWAY_MAIL_USERNAME,
# PATHWAY_MAIL_PASSWORD, PATHWAY_MAIL_DEFAULT_SENDER.
MAIL_SERVER = os.environ.get("PATHWAY_MAIL_SERVER", "")
MAIL_PORT = int(os.environ.get("PATHWAY_MAIL_PORT", "587"))
MAIL_USERNAME = os.environ.get("PATHWAY_MAIL_USERNAME", "")
MAIL_PASSWORD = os.environ.get("PATHWAY_MAIL_PASSWORD", "")
MAIL_DEFAULT_SENDER = os.environ.get("PATHWAY_MAIL_DEFAULT_SENDER", MAIL_USERNAME)


# ==========================================
# APPLICATION DOCUMENT UPLOAD SETTINGS
# ==========================================

UPLOAD_FOLDER = os.path.join(
    DATA_FOLDER, "uploads", "private_documents"
)

LEGACY_CV_FOLDER = os.path.join(
    DATA_FOLDER, "static", "uploads", "cvs"
)

ALLOWED_EXTENSIONS = {"pdf", "doc", "docx"}

app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(LEGACY_CV_FOLDER, exist_ok=True)


def send_pathway_email(recipient, subject, body, application_id, email_type):
    """
    Send an email if SMTP settings are configured.
    Record whether it was sent, skipped, or failed.
    """
    delivery_status = "Skipped"

    try:
        if not all([MAIL_SERVER, MAIL_USERNAME, MAIL_PASSWORD, MAIL_DEFAULT_SENDER]):
            print(
                f"Email not sent ({email_type}): configure the PATHWAY_MAIL_* "
                "environment variables."
            )
        else:
            message = EmailMessage()
            message["Subject"] = subject
            message["From"] = MAIL_DEFAULT_SENDER
            message["To"] = recipient
            message.set_content(body)

            context = ssl.create_default_context()
            with smtplib.SMTP(MAIL_SERVER, MAIL_PORT, timeout=20) as server:
                server.starttls(context=context)
                server.login(MAIL_USERNAME, MAIL_PASSWORD)
                server.send_message(message)

            delivery_status = "Sent"
            print(f"Email sent: {email_type} to {recipient}")

    except Exception as error:
        delivery_status = "Failed"
        print(f"Email sending error ({email_type}):", error)

    # Log the attempt where the notification table exists.
    try:
        connection = get_database()
        try:
            connection.execute("""
                INSERT INTO email_notifications
                    (application_id, email_type, recipient_email,
                     sent_at, delivery_status)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP, ?)
            """, (application_id, email_type, recipient, delivery_status))
            connection.commit()
        finally:
            connection.close()
    except sqlite3.Error as error:
        print("Could not record email notification:", error)

    return delivery_status


def allowed_file(filename):
    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS
    )


# ==========================================
# DATABASE CONNECTION
# ==========================================

def get_database():
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


# Create all required tables automatically before running migrations.
# The CREATE TABLE IF NOT EXISTS statements preserve existing tables and data.
create_database()


# ==========================================
# RECRUITER ACCOUNT STATUS MIGRATION
# ==========================================

def ensure_recruiter_status_column():

    connection = get_database()

    try:
        columns = connection.execute(
            "PRAGMA table_info(recruiters)"
        ).fetchall()

        column_names = [column["name"] for column in columns]

        if "is_active" not in column_names:
            connection.execute("""
                ALTER TABLE recruiters
                ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1
            """)
            connection.commit()
            print("Recruiter account status column added successfully.")

    finally:
        connection.close()


ensure_recruiter_status_column()

def ensure_email_notifications_table():
    connection = get_database()
    try:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS email_notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                application_id INTEGER,
                email_type TEXT NOT NULL,
                recipient_email TEXT NOT NULL,
                sent_at TEXT DEFAULT CURRENT_TIMESTAMP,
                delivery_status TEXT NOT NULL
            )
        """)
        connection.commit()
    finally:
        connection.close()


ensure_email_notifications_table()



# ==========================================
# ROLE-BASED ACCESS PROTECTION
# ==========================================

def login_required(role):

    def decorator(function):

        @wraps(function)
        def wrapper(*args, **kwargs):

            if "user_id" not in session:
                flash("Please log in to continue. ♡")
                return redirect(url_for("login"))

            if session.get("user_role") != role:
                flash("You do not have permission to access that page. ♡")
                return redirect(url_for("login"))

            if role == "recruiter":

                recruiter_id = session.get("recruiter_id")
                connection = get_database()

                try:
                    recruiter = connection.execute("""
                        SELECT is_active
                        FROM recruiters
                        WHERE id = ?
                    """, (recruiter_id,)).fetchone()

                finally:
                    connection.close()

                if recruiter is None or recruiter["is_active"] != 1:
                    session.clear()
                    flash(
                        "This company account is deactivated. "
                        "Please contact the administrator. ♡"
                    )
                    return redirect(url_for("login"))

            return function(*args, **kwargs)

        return wrapper

    return decorator


# ==========================================
# HOMEPAGE
# ==========================================

@app.route("/")
def home():
    return render_template("index.html")


# ==========================================
# CHOOSE ACCOUNT TYPE
# ==========================================

@app.route("/register")
def register():
    return render_template("choose_account.html")


# ==========================================
# STUDENT REGISTRATION
# ==========================================

@app.route("/register/student", methods=["GET", "POST"])
def student_register():

    if request.method == "POST":

        full_name = request.form.get("full_name", "").strip()
        student_number = request.form.get("student_number", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        if not full_name or not student_number or not email or not password:
            return render_template(
                "register.html",
                error="Please complete all required fields."
            )

        if len(student_number) != 8 or not student_number.isdigit():
            return render_template(
                "register.html",
                error="Please enter exactly 8 digits for your student number."
            )

        if password != confirm_password:
            return render_template(
                "register.html",
                error="Your passwords do not match."
            )

        password_hash = generate_password_hash(password)
        connection = get_database()

        try:
            connection.execute("""
                INSERT INTO students
                (full_name, student_number, email, password_hash)
                VALUES (?, ?, ?, ?)
            """, (
                full_name,
                student_number,
                email,
                password_hash
            ))

            connection.commit()
            flash("Registration successful! You can now log in. ♡")
            return redirect(url_for("login"))

        except sqlite3.IntegrityError:
            return render_template(
                "register.html",
                error="This student number or email is already registered."
            )

        finally:
            connection.close()

    return render_template("register.html")


# ==========================================
# RECRUITER REGISTRATION
# ==========================================

@app.route("/recruiter/register", methods=["GET", "POST"])
def recruiter_register():

    if request.method == "POST":

        company_name = request.form.get("company_name", "").strip()
        contact_name = request.form.get("contact_name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        company_description = request.form.get(
            "company_description", ""
        ).strip()

        if not company_name or not contact_name or not email or not password:
            return render_template(
                "recruiter_register.html",
                error="Please complete all required fields."
            )

        if len(password) < 8:
            return render_template(
                "recruiter_register.html",
                error="Your password must contain at least 8 characters."
            )

        password_hash = generate_password_hash(password)
        connection = get_database()

        try:
            connection.execute("""
                INSERT INTO recruiters
                (
                    company_name,
                    contact_name,
                    email,
                    password_hash,
                    company_description
                )
                VALUES (?, ?, ?, ?, ?)
            """, (
                company_name,
                contact_name,
                email,
                password_hash,
                company_description
            ))

            connection.commit()
            flash("Your recruiter account has been created! You can now log in. ♡")
            return redirect(url_for("login"))

        except sqlite3.IntegrityError:
            return render_template(
                "recruiter_register.html",
                error="This email address is already registered."
            )

        finally:
            connection.close()

    return render_template("recruiter_register.html")


# ==========================================
# LOGIN FOR ALL THREE USER TYPES
# ==========================================

@app.route("/login", methods=["GET", "POST"])
def login():

    if request.method == "POST":

        role = request.form.get("role", "student")
        allowed_roles = ["student", "recruiter", "admin"]

        if role not in allowed_roles:
            return render_template(
                "login.html",
                error="Please select a valid account type.",
                selected_role="student"
            )

        if role == "student":
            identifier = request.form.get("student_number", "").strip()
        else:
            identifier = request.form.get("email", "").strip().lower()

        password = request.form.get("password", "")

        if not identifier or not password:
            return render_template(
                "login.html",
                error="Please enter your login details and password.",
                selected_role=role,
                identifier=identifier
            )

        table_details = {
            "student": ("students", "student_number", "full_name"),
            "recruiter": ("recruiters", "email", "company_name"),
            "admin": ("admins", "email", "full_name")
        }

        table, login_column, name_column = table_details[role]
        connection = get_database()

        try:
            user = connection.execute(
                f"SELECT * FROM {table} WHERE {login_column} = ?",
                (identifier,)
            ).fetchone()

        finally:
            connection.close()

        if user and check_password_hash(user["password_hash"], password):

            if role == "recruiter" and user["is_active"] != 1:
                return render_template(
                    "login.html",
                    error=(
                        "This company account has been deactivated. "
                        "Please contact the administrator."
                    ),
                    selected_role=role,
                    identifier=identifier
                )

            session.clear()
            session["user_id"] = user["id"]
            session["user_role"] = role
            session["user_name"] = user[name_column]

            if role == "student":
                session["student_id"] = user["id"]
                session["student_name"] = user["full_name"]
                session["user_email"] = user["email"]
                return redirect(url_for("dashboard"))

            elif role == "recruiter":
                session["recruiter_id"] = user["id"]
                session["user_email"] = user["email"]
                return redirect(url_for("recruiter_dashboard"))

            elif role == "admin":
                session["admin_id"] = user["id"]
                session["user_email"] = user["email"]
                return redirect(url_for("admin_dashboard"))

        return render_template(
            "login.html",
            error="Incorrect login details or password. Please try again.",
            selected_role=role,
            identifier=identifier
        )

    return render_template("login.html", selected_role="student")


# ==========================================
# STUDENT DASHBOARD
# ==========================================

@app.route("/dashboard")
@login_required("student")
def dashboard():
    connection = get_database()

    try:
        student_id = session["student_id"]

        applications = connection.execute("""
            SELECT
                applications.id AS application_id,
                applications.application_date,
                applications.status,
                applications.company_response,
                opportunities.position,
                opportunities.company_name,
                opportunities.location,
                opportunities.deadline
            FROM applications
            INNER JOIN opportunities
                ON applications.opportunity_id = opportunities.id
            WHERE applications.student_id = ?
            ORDER BY applications.application_date DESC
        """, (student_id,)).fetchall()

        application_counts = connection.execute("""
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN status = 'Pending' THEN 1 ELSE 0 END) AS pending,
                SUM(CASE WHEN status = 'Under Review' THEN 1 ELSE 0 END) AS under_review,
                SUM(CASE WHEN status = 'Accepted' THEN 1 ELSE 0 END) AS accepted,
                SUM(CASE WHEN status = 'Rejected' THEN 1 ELSE 0 END) AS rejected
            FROM applications
            WHERE student_id = ?
        """, (student_id,)).fetchone()

        total_applications = application_counts["total"] or 0
        pending_applications = application_counts["pending"] or 0
        under_review_applications = application_counts["under_review"] or 0
        accepted_applications = application_counts["accepted"] or 0
        rejected_applications = application_counts["rejected"] or 0

        return render_template(
            "dashboard.html",
            applications=applications,
            total_applications=total_applications,
            pending_applications=pending_applications,
            under_review_applications=under_review_applications,
            accepted_applications=accepted_applications,
            rejected_applications=rejected_applications
        )

    finally:
        connection.close()


# ==========================================
# STUDENT: BROWSE OPPORTUNITIES
# ==========================================

@app.route("/opportunities")
@login_required("student")
def opportunities():

    connection = get_database()

    try:

        available_opportunities = connection.execute("""
            SELECT opportunities.*
            FROM opportunities
            INNER JOIN recruiters
                ON opportunities.recruiter_id = recruiters.id
            WHERE recruiters.is_active = 1
            AND opportunities.approval_status = 'Approved'
            AND opportunities.is_active = 1
            AND opportunities.deadline >= date('now')
            ORDER BY opportunities.deadline ASC
        """).fetchall()

        return render_template(
            "opportunities.html",
            opportunities=available_opportunities
        )

    finally:
        connection.close()


# ==========================================
# STUDENT: APPLY FOR AN OPPORTUNITY
# ==========================================

@app.route("/apply/<int:opportunity_id>", methods=["POST"])
@login_required("student")
def apply(opportunity_id):

    connection = get_database()
    saved_files = []

    document_fields = {
        "cv": "cv_filename",
        "id_document": "id_filename",
        "academic_record": "academic_record_filename",
        "matric_certificate": "matric_certificate_filename"
    }

    try:

        opportunity = connection.execute("""
            SELECT opportunities.id
            FROM opportunities
            INNER JOIN recruiters
                ON opportunities.recruiter_id = recruiters.id
            WHERE opportunities.id = ?
            AND recruiters.is_active = 1
            AND opportunities.approval_status = 'Approved'
            AND opportunities.is_active = 1
            AND opportunities.deadline >= date('now')
        """, (opportunity_id,)).fetchone()

        if opportunity is None:
            flash("This opportunity is no longer available. ♡")
            return redirect(url_for("opportunities"))

        existing_application = connection.execute("""
            SELECT id
            FROM applications
            WHERE student_id = ?
            AND opportunity_id = ?
        """, (
            session["student_id"],
            opportunity_id
        )).fetchone()

        if existing_application:
            flash("You have already applied for this opportunity. ♡")
            return redirect(url_for("dashboard"))

        uploaded_names = {}

        for form_name, database_column in document_fields.items():

            uploaded_file = request.files.get(form_name)

            if uploaded_file is None or uploaded_file.filename == "":
                raise ValueError("Please upload all four required documents.")

            if not allowed_file(uploaded_file.filename):
                raise ValueError("Documents must be PDF, DOC or DOCX files.")

            original_name = secure_filename(uploaded_file.filename)

            if "." not in original_name:
                raise ValueError("One of your documents has an invalid filename.")

            extension = original_name.rsplit(".", 1)[1].lower()
            unique_filename = f"{uuid.uuid4().hex}.{extension}"

            uploaded_file.save(
                os.path.join(UPLOAD_FOLDER, unique_filename)
            )

            saved_files.append(unique_filename)
            uploaded_names[database_column] = unique_filename

        connection.execute("""
            INSERT INTO applications (
                student_id,
                opportunity_id,
                cv_filename,
                id_filename,
                academic_record_filename,
                matric_certificate_filename
            )
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            session["student_id"],
            opportunity_id,
            uploaded_names["cv_filename"],
            uploaded_names["id_filename"],
            uploaded_names["academic_record_filename"],
            uploaded_names["matric_certificate_filename"]
        ))

        connection.commit()

        application_id = connection.execute("""
            SELECT id
            FROM applications
            WHERE student_id = ? AND opportunity_id = ?
        """, (session["student_id"], opportunity_id)).fetchone()["id"]

        student = connection.execute("""
            SELECT full_name, email FROM students WHERE id = ?
        """, (session["student_id"],)).fetchone()

        opportunity_details = connection.execute("""
            SELECT position, company_name
            FROM opportunities
            WHERE id = ?
        """, (opportunity_id,)).fetchone()

        if student and opportunity_details:
            email_body = f"""Hello {student['full_name']},

Your application has been submitted successfully.

Position: {opportunity_details['position']}
Company: {opportunity_details['company_name']}
Current status: Pending

You can log in to Pathway to track your application.

Kind regards,
Pathway Team
"""
            email_status = send_pathway_email(
                student["email"],
                "Pathway application confirmation",
                email_body,
                application_id,
                "Application Confirmation"
            )
        else:
            email_status = "Skipped"

        flash("Your application was submitted successfully! You can track it on your dashboard. 🌸")
        if email_status != "Sent":
            flash("Your application was saved, but the confirmation email could not be sent yet.")
        return redirect(url_for("dashboard"))

    except ValueError as error:

        connection.rollback()

        for filename in saved_files:
            file_path = os.path.join(UPLOAD_FOLDER, filename)
            if os.path.exists(file_path):
                os.remove(file_path)

        flash(f"{error} ♡")
        return redirect(url_for("opportunities"))

    except sqlite3.IntegrityError:

        connection.rollback()

        for filename in saved_files:
            file_path = os.path.join(UPLOAD_FOLDER, filename)
            if os.path.exists(file_path):
                os.remove(file_path)

        flash("You have already applied for this opportunity. ♡")
        return redirect(url_for("dashboard"))

    except (sqlite3.Error, OSError) as error:

        connection.rollback()
        print("Application error:", error)

        for filename in saved_files:
            file_path = os.path.join(UPLOAD_FOLDER, filename)
            if os.path.exists(file_path):
                os.remove(file_path)

        flash("There was a problem submitting your application. Please try again. ♡")
        return redirect(url_for("opportunities"))

    finally:
        connection.close()


# ==========================================
# RECRUITER DASHBOARD
# ==========================================

@app.route("/recruiter/dashboard")
@login_required("recruiter")
def recruiter_dashboard():

    connection = get_database()

    try:
        opportunity_count = connection.execute("""
            SELECT COUNT(*) AS total
            FROM opportunities
            WHERE recruiter_id = ?
        """, (session["recruiter_id"],)).fetchone()["total"]

        application_count = connection.execute("""
            SELECT COUNT(applications.id) AS total
            FROM applications
            INNER JOIN opportunities
                ON applications.opportunity_id = opportunities.id
            WHERE opportunities.recruiter_id = ?
        """, (session["recruiter_id"],)).fetchone()["total"]

        pending_count = connection.execute("""
            SELECT COUNT(applications.id) AS total
            FROM applications
            INNER JOIN opportunities
                ON applications.opportunity_id = opportunities.id
            WHERE opportunities.recruiter_id = ?
            AND applications.status = 'Pending'
        """, (session["recruiter_id"],)).fetchone()["total"]

        return render_template(
            "recruiter_dashboard.html",
            opportunity_count=opportunity_count,
            application_count=application_count,
            pending_count=pending_count
        )

    finally:
        connection.close()


# ==========================================
# RECRUITER OPPORTUNITY MANAGEMENT
# ==========================================

@app.route("/recruiter/opportunities", methods=["GET", "POST"])
@login_required("recruiter")
def recruiter_opportunities():

    connection = get_database()

    try:

        recruiter = connection.execute("""
            SELECT id, company_name, is_active
            FROM recruiters
            WHERE id = ?
        """, (session["recruiter_id"],)).fetchone()

        if recruiter is None:
            session.clear()
            flash("Recruiter account not found. Please log in again. ♡")
            return redirect(url_for("login"))

        if request.method == "POST":

            if recruiter["is_active"] != 1:
                flash("Your company account is not active. ♡")
                return redirect(url_for("recruiter_dashboard"))

            position = request.form.get("position", "").strip()
            description = request.form.get("description", "").strip()
            location = request.form.get("location", "").strip()
            deadline = request.form.get("deadline", "").strip()
            requirements = request.form.get("requirements", "").strip()

            if not position or not description or not location or not deadline:
                flash("Please complete all required fields. ♡")

            else:

                try:
                    deadline_date = date.fromisoformat(deadline)
                except ValueError:
                    flash("Please enter a valid application deadline. ♡")
                else:

                    if deadline_date < date.today():
                        flash("The application deadline cannot be in the past. ♡")
                    else:

                        connection.execute("""
                            INSERT INTO opportunities (
                                company_name,
                                position,
                                description,
                                location,
                                deadline,
                                requirements,
                                recruiter_id
                            )
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                        """, (
                            recruiter["company_name"],
                            position,
                            description,
                            location,
                            deadline,
                            requirements,
                            recruiter["id"]
                        ))

                        connection.commit()
                        flash("Your opportunity has been posted successfully! 🌸")
                        return redirect(url_for("recruiter_opportunities"))

        my_opportunities = connection.execute("""
            SELECT *
            FROM opportunities
            WHERE recruiter_id = ?
            ORDER BY created_at DESC
        """, (recruiter["id"],)).fetchall()

        return render_template(
            "recruiter_opportunities.html",
            opportunities=my_opportunities,
            company_name=recruiter["company_name"]
        )

    finally:
        connection.close()


# ==========================================
# RECRUITER: EDIT AN OPPORTUNITY
# ==========================================

@app.route(
    "/recruiter/opportunity/<int:opportunity_id>/edit",
    methods=["GET", "POST"]
)
@login_required("recruiter")
def edit_opportunity(opportunity_id):

    connection = get_database()

    try:

        opportunity = connection.execute("""
            SELECT *
            FROM opportunities
            WHERE id = ?
            AND recruiter_id = ?
        """, (
            opportunity_id,
            session["recruiter_id"]
        )).fetchone()

        if opportunity is None:
            flash("Opportunity not found or you do not have permission to edit it. ♡")
            return redirect(url_for("recruiter_opportunities"))

        if request.method == "POST":

            position = request.form.get("position", "").strip()
            description = request.form.get("description", "").strip()
            location = request.form.get("location", "").strip()
            deadline = request.form.get("deadline", "").strip()
            requirements = request.form.get("requirements", "").strip()

            if not position or not description or not location or not deadline:
                flash("Please complete all required fields. ♡")

            else:

                try:
                    deadline_date = date.fromisoformat(deadline)
                except ValueError:
                    flash("Please enter a valid application deadline. ♡")
                else:

                    if deadline_date < date.today():
                        flash("The application deadline cannot be in the past. ♡")
                    else:

                        connection.execute("""
                            UPDATE opportunities
                            SET
                                position = ?,
                                description = ?,
                                location = ?,
                                deadline = ?,
                                requirements = ?
                            WHERE id = ?
                            AND recruiter_id = ?
                        """, (
                            position,
                            description,
                            location,
                            deadline,
                            requirements,
                            opportunity_id,
                            session["recruiter_id"]
                        ))

                        connection.commit()
                        flash("Your opportunity has been updated successfully! 🌸")
                        return redirect(url_for("recruiter_opportunities"))

        return render_template(
            "edit_opportunity.html",
            opportunity=opportunity
        )

    except sqlite3.Error as error:
        connection.rollback()
        print("Opportunity editing error:", error)
        flash("There was a problem editing this opportunity. Please try again. ♡")
        return redirect(url_for("recruiter_opportunities"))

    finally:
        connection.close()


# ==========================================
# RECRUITER: CLOSE AN OPPORTUNITY
# ==========================================

@app.route(
    "/recruiter/opportunity/<int:opportunity_id>/close",
    methods=["POST"]
)
@login_required("recruiter")
def close_opportunity(opportunity_id):

    connection = get_database()

    try:

        opportunity = connection.execute("""
            SELECT id
            FROM opportunities
            WHERE id = ?
            AND recruiter_id = ?
        """, (
            opportunity_id,
            session["recruiter_id"]
        )).fetchone()

        if opportunity is None:
            flash("Opportunity not found or you do not have permission to close it. ♡")

        else:

            connection.execute("""
                UPDATE opportunities
                SET is_active = 0
                WHERE id = ?
                AND recruiter_id = ?
            """, (
                opportunity_id,
                session["recruiter_id"]
            ))

            connection.commit()
            flash("Your opportunity has been closed successfully! 🌸")

    except sqlite3.Error as error:
        connection.rollback()
        print("Opportunity closing error:", error)
        flash("There was a problem closing this opportunity. Please try again. ♡")

    finally:
        connection.close()

    return redirect(url_for("recruiter_opportunities"))


# ==========================================
# RECRUITER: VIEW STUDENT APPLICANTS
# ==========================================

@app.route("/recruiter/applicants")
@login_required("recruiter")
def recruiter_applicants():

    connection = get_database()

    try:

        applicants = connection.execute("""
            SELECT
                applications.id,
                applications.application_date,
                applications.status,
                applications.company_response,
                applications.cv_filename,
                applications.id_filename,
                applications.academic_record_filename,
                applications.matric_certificate_filename,
                students.full_name,
                students.student_number,
                students.email,
                opportunities.position,
                opportunities.company_name
            FROM applications
            INNER JOIN students
                ON applications.student_id = students.id
            INNER JOIN opportunities
                ON applications.opportunity_id = opportunities.id
            WHERE opportunities.recruiter_id = ?
            ORDER BY applications.application_date DESC
        """, (session["recruiter_id"],)).fetchall()

        return render_template(
            "recruiter_applicants.html",
            applicants=applicants
        )

    finally:
        connection.close()


# ==========================================
# RECRUITER: APPLICATION MANAGEMENT
# ==========================================

@app.route("/recruiter/application-management")
@login_required("recruiter")
def recruiter_application_management():

    connection = get_database()

    try:

        applications = connection.execute("""
            SELECT
                applications.*,
                students.full_name,
                students.student_number,
                students.email,
                opportunities.position,
                opportunities.company_name
            FROM applications
            INNER JOIN students
                ON applications.student_id = students.id
            INNER JOIN opportunities
                ON applications.opportunity_id = opportunities.id
            WHERE opportunities.recruiter_id = ?
            ORDER BY applications.application_date DESC
        """, (session["recruiter_id"],)).fetchall()

        return render_template(
            "recruiter_application_management.html",
            applications=applications
        )

    finally:
        connection.close()


# ==========================================
# RECRUITER: DOWNLOAD STUDENT CV
# ==========================================

@app.route("/recruiter/application/<int:application_id>/cv")
@login_required("recruiter")
def download_cv(application_id):

    connection = get_database()

    try:

        application = connection.execute("""
            SELECT applications.cv_filename
            FROM applications
            INNER JOIN opportunities
                ON applications.opportunity_id = opportunities.id
            WHERE applications.id = ?
            AND opportunities.recruiter_id = ?
        """, (
            application_id,
            session["recruiter_id"]
        )).fetchone()

        if application is None or not application["cv_filename"]:
            flash("CV not found. ♡")
            return redirect(url_for("recruiter_application_management"))

        filename = application["cv_filename"]

    finally:
        connection.close()

    if not allowed_file(filename):
        abort(404)

    if os.path.isfile(os.path.join(UPLOAD_FOLDER, filename)):
        folder = UPLOAD_FOLDER
    elif os.path.isfile(os.path.join(LEGACY_CV_FOLDER, filename)):
        folder = LEGACY_CV_FOLDER
    else:
        abort(404)

    return send_from_directory(
        folder,
        filename,
        as_attachment=True
    )


# ==========================================
# RECRUITER: DOWNLOAD OTHER STUDENT DOCUMENTS
# ==========================================

@app.route(
    "/recruiter/application/<int:application_id>/document/<document_type>"
)
@login_required("recruiter")
def download_applicant_document(application_id, document_type):

    # These keys must match the document_type values
    # used in recruiter_applicants.html.

    document_columns = {
        "id": "id_filename",
        "academic_record": "academic_record_filename",
        "matric_certificate": "matric_certificate_filename"
    }

    if document_type not in document_columns:
        abort(404)

    column = document_columns[document_type]
    connection = get_database()

    try:

        application = connection.execute(f"""
            SELECT applications.{column} AS filename
            FROM applications
            INNER JOIN opportunities
                ON applications.opportunity_id = opportunities.id
            WHERE applications.id = ?
            AND opportunities.recruiter_id = ?
        """, (
            application_id,
            session["recruiter_id"]
        )).fetchone()

    finally:
        connection.close()

    if application is None:
        abort(404)

    filename = application["filename"]

    if not filename or not allowed_file(filename):
        flash("This document is not available. ♡")
        return redirect(url_for("recruiter_application_management"))

    file_path = os.path.join(UPLOAD_FOLDER, filename)

    if not os.path.isfile(file_path):
        abort(404)

    return send_from_directory(
        UPLOAD_FOLDER,
        filename,
        as_attachment=True
    )


# ==========================================
# RECRUITER: UPDATE APPLICATION STATUS
# ==========================================

@app.route(
    "/recruiter/application/<int:application_id>/update",
    methods=["POST"]
)
@login_required("recruiter")
def update_application_status(application_id):

    new_status = request.form.get("status", "").strip()
    recruiter_message = request.form.get(
        "company_response", ""
    ).strip()

    allowed_statuses = [
        "Pending",
        "Under Review",
        "Accepted",
        "Rejected"
    ]

    if new_status not in allowed_statuses:
        flash("Please select a valid application status. ♡")
        return redirect(url_for("recruiter_application_management"))

    connection = get_database()

    try:

        application = connection.execute("""
            SELECT
                applications.id,
                applications.status,
                students.email,
                students.full_name,
                opportunities.position,
                opportunities.company_name
            FROM applications
            INNER JOIN students
                ON applications.student_id = students.id
            INNER JOIN opportunities
                ON applications.opportunity_id = opportunities.id
            WHERE applications.id = ?
            AND opportunities.recruiter_id = ?
        """, (
            application_id,
            session["recruiter_id"]
        )).fetchone()

        if application is None:
            flash("Application not found or you do not have permission to update it. ♡")
            return redirect(url_for("recruiter_application_management"))

        old_status = application["status"]

        connection.execute("""
            UPDATE applications
            SET
                status = ?,
                company_response = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (
            new_status,
            recruiter_message,
            application_id
        ))

        connection.execute("""
            INSERT INTO application_history (
                application_id,
                old_status,
                new_status,
                recruiter_message
            )
            VALUES (?, ?, ?, ?)
        """, (
            application_id,
            old_status,
            new_status,
            recruiter_message
        ))

        connection.commit()

        status_email_body = f"""Hello {application['full_name']},

The status of your Pathway application has been updated.

Position: {application['position']}
Company: {application['company_name']}
New status: {new_status}

Message from the recruiter:
{recruiter_message if recruiter_message else "No additional message was provided."}

Log in to Pathway to view your application details.

Kind regards,
Pathway Team
"""
        email_status = send_pathway_email(
            application["email"],
            "Your Pathway application status has changed",
            status_email_body,
            application_id,
            "Application Status Update"
        )

        flash("Application status updated successfully! 🌸")
        if email_status != "Sent":
            flash("The status was updated, but the email notification could not be sent yet.")

    except sqlite3.Error as error:

        connection.rollback()
        print("Application status update error:", error)
        flash("There was a problem updating the application. ♡")

    finally:
        connection.close()

    return redirect(url_for("recruiter_application_management"))


# ==========================================
# RECRUITER: COMPANY PROFILE
# ==========================================

@app.route("/recruiter/company-profile", methods=["GET", "POST"])
@login_required("recruiter")
def company_profile():

    connection = get_database()

    try:

        recruiter = connection.execute("""
            SELECT *
            FROM recruiters
            WHERE id = ?
        """, (session["recruiter_id"],)).fetchone()

        if recruiter is None:
            flash("Company profile not found. ♡")
            return redirect(url_for("login"))

        if request.method == "POST":

            company_name = request.form.get(
                "company_name", ""
            ).strip()

            contact_name = request.form.get(
                "contact_name", ""
            ).strip()

            company_description = request.form.get(
                "company_description", ""
            ).strip()

            company_industry = request.form.get(
                "company_industry", ""
            ).strip()

            company_phone = request.form.get(
                "company_phone", ""
            ).strip()

            company_address = request.form.get(
                "company_address", ""
            ).strip()

            company_website = request.form.get(
                "company_website", ""
            ).strip()

            if not company_name or not contact_name:
                flash("Company name and contact name are required. ♡")

            else:

                connection.execute("""
                    UPDATE recruiters
                    SET
                        company_name = ?,
                        contact_name = ?,
                        company_description = ?,
                        company_industry = ?,
                        company_phone = ?,
                        company_address = ?,
                        company_website = ?
                    WHERE id = ?
                """, (
                    company_name,
                    contact_name,
                    company_description,
                    company_industry,
                    company_phone,
                    company_address,
                    company_website,
                    session["recruiter_id"]
                ))

                connection.commit()

                session["user_name"] = company_name

                flash("Company profile updated successfully! 🌸")
                return redirect(url_for("company_profile"))

        return render_template(
            "company_profile.html",
            recruiter=recruiter
        )

    except sqlite3.Error as error:
        connection.rollback()
        print("Company profile error:", error)
        flash("There was a problem updating your company profile. ♡")
        return redirect(url_for("recruiter_dashboard"))

    finally:
        connection.close()


# ==========================================
# ADMIN DASHBOARD
# ==========================================

@app.route("/admin/dashboard")
@login_required("admin")
def admin_dashboard():

    connection = get_database()

    try:

        total_students = connection.execute("""
            SELECT COUNT(*) AS total
            FROM students
        """).fetchone()["total"]

        total_recruiters = connection.execute("""
            SELECT COUNT(*) AS total
            FROM recruiters
        """).fetchone()["total"]

        total_opportunities = connection.execute("""
            SELECT COUNT(*) AS total
            FROM opportunities
        """).fetchone()["total"]

        active_opportunities = connection.execute("""
            SELECT COUNT(*) AS total
            FROM opportunities
            WHERE is_active = 1
            AND deadline >= date('now')
        """).fetchone()["total"]

        total_applications = connection.execute("""
            SELECT COUNT(*) AS total
            FROM applications
        """).fetchone()["total"]

        total_applicants = connection.execute("""
            SELECT COUNT(DISTINCT student_id) AS total
            FROM applications
        """).fetchone()["total"]

        pending_applications = connection.execute("""
            SELECT COUNT(*) AS total
            FROM applications
            WHERE status = 'Pending'
        """).fetchone()["total"]

        under_review_applications = connection.execute("""
            SELECT COUNT(*) AS total
            FROM applications
            WHERE status = 'Under Review'
        """).fetchone()["total"]

        accepted_applications = connection.execute("""
            SELECT COUNT(*) AS total
            FROM applications
            WHERE status = 'Accepted'
        """).fetchone()["total"]

        rejected_applications = connection.execute("""
            SELECT COUNT(*) AS total
            FROM applications
            WHERE status = 'Rejected'
        """).fetchone()["total"]

        success_rate = (
            round(
                (accepted_applications / total_applications) * 100,
                1
            )
            if total_applications > 0 else 0
        )

        popular_opportunities = connection.execute("""
            SELECT
                opportunities.position,
                opportunities.company_name,
                COUNT(applications.id) AS applicant_count
            FROM opportunities
            LEFT JOIN applications
                ON opportunities.id = applications.opportunity_id
            GROUP BY opportunities.id
            ORDER BY applicant_count DESC
            LIMIT 5
        """).fetchall()

        recruiter_stats = connection.execute("""
            SELECT
                recruiters.company_name,
                COUNT(DISTINCT opportunities.id) AS opportunity_count,
                COUNT(applications.id) AS application_count
            FROM recruiters
            LEFT JOIN opportunities
                ON recruiters.id = opportunities.recruiter_id
            LEFT JOIN applications
                ON opportunities.id = applications.opportunity_id
            GROUP BY recruiters.id
            ORDER BY application_count DESC
            LIMIT 5
        """).fetchall()

        recent_applications = connection.execute("""
            SELECT
                applications.application_date,
                applications.status,
                students.full_name,
                opportunities.position,
                opportunities.company_name
            FROM applications
            INNER JOIN students
                ON applications.student_id = students.id
            INNER JOIN opportunities
                ON applications.opportunity_id = opportunities.id
            ORDER BY applications.application_date DESC
            LIMIT 10
        """).fetchall()

        monthly_applications = connection.execute("""
            SELECT
                strftime('%Y-%m', application_date) AS month,
                COUNT(*) AS total
            FROM applications
            GROUP BY strftime('%Y-%m', application_date)
            ORDER BY month ASC
        """).fetchall()

        top_recruiters = recruiter_stats

        return render_template(
            "admin_dashboard.html",

            total_students=total_students,
            total_recruiters=total_recruiters,
            total_opportunities=total_opportunities,
            active_opportunities=active_opportunities,

            total_applications=total_applications,
            total_applicants=total_applicants,

            pending_applications=pending_applications,
            under_review_applications=under_review_applications,
            accepted_applications=accepted_applications,
            rejected_applications=rejected_applications,

            success_rate=success_rate,

            popular_opportunities=popular_opportunities,
            recruiter_stats=recruiter_stats,
            top_recruiters=top_recruiters,

            recent_applications=recent_applications,
            monthly_applications=monthly_applications
        )

    except sqlite3.Error as error:

        print("Admin dashboard database error:", error)
        flash("There was a problem loading the administrator dashboard. ♡")
        return redirect(url_for("home"))

    finally:
        connection.close()


# ==========================================
# ADMIN: MANAGE COMPANIES
# ==========================================

@app.route("/admin/companies")
@login_required("admin")
def admin_companies():

    connection = get_database()

    try:

        companies = connection.execute("""
            SELECT
                recruiters.id,
                recruiters.company_name,
                recruiters.contact_name,
                recruiters.email,
                recruiters.created_at,
                recruiters.is_active,
                COUNT(DISTINCT opportunities.id) AS opportunity_count
            FROM recruiters
            LEFT JOIN opportunities
                ON recruiters.id = opportunities.recruiter_id
            GROUP BY recruiters.id
            ORDER BY recruiters.company_name ASC
        """).fetchall()

        return render_template(
            "admin_companies.html",
            companies=companies
        )

    except sqlite3.Error as error:
        print("Admin companies page error:", error)
        flash("There was a problem loading the company list. ♡")
        return redirect(url_for("admin_dashboard"))

    finally:
        connection.close()


# ==========================================
# ADMIN: DEACTIVATE COMPANY
# ==========================================

@app.route(
    "/admin/company/<int:company_id>/deactivate",
    methods=["POST"]
)
@login_required("admin")
def deactivate_company(company_id):

    connection = get_database()

    try:

        company = connection.execute("""
            SELECT id, company_name, is_active
            FROM recruiters
            WHERE id = ?
        """, (company_id,)).fetchone()

        if company is None:
            flash("Company not found. ♡")

        elif company["is_active"] == 0:
            flash("This company is already deactivated. ♡")

        else:

            connection.execute("""
                UPDATE recruiters
                SET is_active = 0
                WHERE id = ?
            """, (company_id,))

            connection.commit()

            flash(
                f"{company['company_name']} has been deactivated. "
                "Its records and application history have been preserved. ♡"
            )

    except sqlite3.Error as error:

        connection.rollback()
        print("Company deactivation error:", error)
        flash("There was a problem deactivating this company. ♡")

    finally:
        connection.close()

    return redirect(url_for("admin_companies"))


# ==========================================
# ADMIN: REACTIVATE COMPANY
# ==========================================

@app.route(
    "/admin/company/<int:company_id>/reactivate",
    methods=["POST"]
)
@login_required("admin")
def reactivate_company(company_id):

    connection = get_database()

    try:

        company = connection.execute("""
            SELECT id, company_name, is_active
            FROM recruiters
            WHERE id = ?
        """, (company_id,)).fetchone()

        if company is None:
            flash("Company not found. ♡")

        elif company["is_active"] == 1:
            flash("This company is already active. ♡")

        else:

            connection.execute("""
                UPDATE recruiters
                SET is_active = 1
                WHERE id = ?
            """, (company_id,))

            connection.commit()
            flash(f"{company['company_name']} has been reactivated! 🌸")

    except sqlite3.Error as error:

        connection.rollback()
        print("Company reactivation error:", error)
        flash("There was a problem reactivating this company. ♡")

    finally:
        connection.close()

    return redirect(url_for("admin_companies"))


# ==========================================
# FILE SIZE ERROR
# ==========================================

@app.errorhandler(413)
def file_too_large(error):

    flash("Your documents are too large. The combined maximum is 20 MB. ♡")

    if session.get("user_role") == "student":
        return redirect(url_for("opportunities"))

    return redirect(url_for("home"))


# ==========================================
# LOGOUT
# ==========================================

@app.route("/logout")
def logout():

    session.clear()
    flash("You have been logged out successfully. ♡")

    return redirect(url_for("home"))


# ==========================================
# RUN APPLICATION
# ==========================================

if __name__ == "__main__":
    app.run(debug=True)
