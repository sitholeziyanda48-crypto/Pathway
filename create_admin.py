
import sqlite3
from werkzeug.security import generate_password_hash
from getpass import getpass

DATABASE = "pathway.db"

print("\n================================")
print("     PATHWAY ADMIN CREATION")
print("================================\n")

full_name = input("Enter administrator full name: ").strip()
email = input("Enter administrator email: ").strip().lower()
password = getpass("Create administrator password: ")
confirm_password = getpass("Confirm administrator password: ")

if not full_name or not email or not password:
    print("\nAll fields are required.")
    raise SystemExit

if password != confirm_password:
    print("\nPasswords do not match.")
    raise SystemExit

if len(password) < 8:
    print("\nPassword must contain at least 8 characters.")
    raise SystemExit

connection = sqlite3.connect(DATABASE)

try:
    existing_admin = connection.execute(
        "SELECT id FROM admins WHERE email = ?",
        (email,)
    ).fetchone()

    if existing_admin:
        print("\nAn administrator with this email already exists.")

    else:
        password_hash = generate_password_hash(password)

        connection.execute("""
            INSERT INTO admins (
                full_name,
                email,
                password_hash,
                created_at
            )
            VALUES (?, ?, ?, datetime('now'))
        """, (full_name, email, password_hash))

        connection.commit()

        print("\nAdministrator account created successfully!")
        print("You can now log in to Pathway.")
        print("Email:", email)

except sqlite3.Error as error:
    print("\nDatabase error:", error)

finally:
    connection.close()
