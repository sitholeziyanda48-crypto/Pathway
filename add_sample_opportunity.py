import sqlite3


connection = sqlite3.connect("pathway.db")

cursor = connection.cursor()

# Check whether sample opportunities have already been added
count = cursor.execute(
    "SELECT COUNT(*) FROM opportunities"
).fetchone()[0]

if count == 0:

    sample_opportunities = [
    (
        "BrightFuture Technologies",
        "Junior Web Developer Intern",
        "Learn how to build websites and assist with real software development projects.",
        "Durban, South Africa",
        "2026-12-15",
        "Basic HTML, CSS and Python knowledge"
    ),
    (
        "GreenSpark Solutions",
        "IT Support Intern",
        "Assist with technical support, troubleshooting and computer maintenance.",
        "Johannesburg, South Africa",
        "2026-12-20",
        "Basic computer skills and problem-solving"
    ),
    (
        "Creative Minds Studio",
        "Digital Marketing Intern",
        "Help create social media content, research marketing trends and support campaigns.",
        "Remote",
        "2026-12-25",
        "Interest in digital marketing and social media"
    )
]
  

    cursor.executemany("""
        INSERT INTO opportunities
        (company_name, position, description, location, deadline, requirements)
        VALUES (?, ?, ?, ?, ?, ?)
    """, sample_opportunities)

    connection.commit()

    print("Sample opportunities added successfully! 🌸")

else:
    print("Opportunities already exist. No new samples were added.")

connection.close()