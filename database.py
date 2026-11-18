
import os
import sqlite3


def create_database():
    """
    Create the Pathway database and all required tables.

    The PATHWAY_DATA_FOLDER environment variable allows the database
    to be stored in a persistent location when the application is deployed.
    """

    data_folder = os.environ.get(
        "PATHWAY_DATA_FOLDER",
        os.path.dirname(os.path.abspath(__file__))
    )

    os.makedirs(data_folder, exist_ok=True)

    database_path = os.path.join(data_folder, "pathway.db")

    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row

    try:
        # Enable foreign-key support once for this connection.
        connection.execute("PRAGMA foreign_keys = ON")

        cursor = connection.cursor()

        # ==========================================
        # STUDENTS
        # ==========================================

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS students (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                full_name TEXT NOT NULL,
                student_number TEXT NOT NULL UNIQUE,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # ==========================================
        # RECRUITERS
        # ==========================================

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS recruiters (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_name TEXT NOT NULL,
                contact_name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                company_description TEXT,
                company_industry TEXT,
                company_phone TEXT,
                company_address TEXT,
                company_website TEXT,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # ==========================================
        # ADMINS
        # ==========================================

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS admins (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                full_name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # ==========================================
        # OPPORTUNITIES
        # ==========================================

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS opportunities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recruiter_id INTEGER,
                company_name TEXT NOT NULL,
                position TEXT NOT NULL,
                description TEXT NOT NULL,
                location TEXT,
                deadline TEXT,
                requirements TEXT,
                approval_status TEXT NOT NULL DEFAULT 'Approved',
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,

                FOREIGN KEY (recruiter_id)
                    REFERENCES recruiters(id)
                    ON DELETE SET NULL
            )
        """)

        # ==========================================
        # APPLICATIONS
        # ==========================================

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS applications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id INTEGER NOT NULL,
                opportunity_id INTEGER NOT NULL,
                application_date TEXT DEFAULT CURRENT_TIMESTAMP,
                status TEXT NOT NULL DEFAULT 'Pending',
                company_response TEXT,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,

                cv_filename TEXT,
                id_filename TEXT,
                academic_record_filename TEXT,
                matric_certificate_filename TEXT,

                FOREIGN KEY (student_id)
                    REFERENCES students(id)
                    ON DELETE CASCADE,

                FOREIGN KEY (opportunity_id)
                    REFERENCES opportunities(id)
                    ON DELETE CASCADE,

                UNIQUE(student_id, opportunity_id)
            )
        """)

        # ==========================================
        # APPLICATION HISTORY
        # ==========================================

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS application_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                application_id INTEGER NOT NULL,
                old_status TEXT,
                new_status TEXT NOT NULL,
                recruiter_message TEXT,
                changed_at TEXT DEFAULT CURRENT_TIMESTAMP,

                FOREIGN KEY (application_id)
                    REFERENCES applications(id)
                    ON DELETE CASCADE
            )
        """)

        # ==========================================
        # EMAIL NOTIFICATIONS
        # ==========================================

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS email_notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                application_id INTEGER NOT NULL,
                email_type TEXT NOT NULL,
                recipient_email TEXT NOT NULL,
                sent_at TEXT DEFAULT CURRENT_TIMESTAMP,
                delivery_status TEXT NOT NULL,

                FOREIGN KEY (application_id)
                    REFERENCES applications(id)
                    ON DELETE CASCADE
            )
        """)

        # ==========================================
        # DATABASE MIGRATIONS
        # Add missing columns to older databases.
        # Existing records are preserved.
        # ==========================================

        def add_missing_columns(table_name, columns):
            existing_columns = {
                row["name"]
                for row in connection.execute(
                    f"PRAGMA table_info({table_name})"
                ).fetchall()
            }

            for column_name, column_definition in columns.items():
                if column_name not in existing_columns:
                    connection.execute(
                        f"ALTER TABLE {table_name} "
                        f"ADD COLUMN {column_name} {column_definition}"
                    )
                    print(
                        f"Added column {column_name} "
                        f"to {table_name}."
                    )

        # Recruiter profile and account status migrations.
        add_missing_columns("recruiters", {
            "company_description": "TEXT",
            "company_industry": "TEXT",
            "company_phone": "TEXT",
            "company_address": "TEXT",
            "company_website": "TEXT",
            "is_active": "INTEGER NOT NULL DEFAULT 1"
        })

        # Opportunity migrations.
        add_missing_columns("opportunities", {
            "recruiter_id": "INTEGER",
            "approval_status": "TEXT NOT NULL DEFAULT 'Approved'",
            "is_active": "INTEGER NOT NULL DEFAULT 1"
        })

        # Student document migrations.
        add_missing_columns("applications", {
            "cv_filename": "TEXT",
            "id_filename": "TEXT",
            "academic_record_filename": "TEXT",
            "matric_certificate_filename": "TEXT",
            "company_response": "TEXT",
            "updated_at": "TEXT"
        })

        connection.commit()

        print("Pathway database created successfully!")
        print("All required tables and migrations are ready.")

    except sqlite3.Error as error:
        connection.rollback()
        print("Database creation error:", error)
        raise

    finally:
        connection.close()


if __name__ == "__main__":
    create_database()
