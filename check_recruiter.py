import sqlite3

connection = sqlite3.connect("pathway.db")
connection.row_factory = sqlite3.Row

recruiters = connection.execute(
    "SELECT id, company_name, email FROM recruiters"
).fetchall()

print("NUMBER OF RECRUITERS:", len(recruiters))

for recruiter in recruiters:
    print("-------------------------")
    print("ID:", recruiter["id"])
    print("COMPANY:", recruiter["company_name"])
    print("EMAIL:", recruiter["email"])

connection.close()