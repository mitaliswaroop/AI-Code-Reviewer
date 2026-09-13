import os
from sqlalchemy import create_engine, Column, Integer, String, JSON, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker
from datetime import datetime, timezone

# 1. Fetch the connection string from Render Environment Variables
raw_db_url = os.getenv("DATABASE_URL")

# Safety Check: SQLAlchemy 1.4+ requires 'postgresql://' not 'postgres://'
if raw_db_url and raw_db_url.startswith("postgres://"):
    DATABASE_URL = raw_db_url.replace("postgres://", "postgresql://", 1)
else:
    DATABASE_URL = raw_db_url

# 2. Configure the Engine
# Neon requires SSL, so we pass sslmode='require' directly to the connection arguments
engine = create_engine(DATABASE_URL, connect_args={'sslmode': 'require'})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


# 3. Define the Database Table (Schema)
class PRReview(Base):
    __tablename__ = "pr_reviews"

    id = Column(Integer, primary_key=True, index=True)
    repo = Column(String(255), index=True)
    pr_number = Column(Integer, index=True)
    reports = Column(JSON)  # Native JSON support for nested scanner results
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


# 4. Initialize the database table if it doesn't exist yet
Base.metadata.create_all(bind=engine)


# 5. Helper function to save the data from main.py
def save_review_to_db(repo: str, pr_num: int, f_report: str, s_report: str, g_review: str):
    """
    Combines the Flake8, Semgrep, and Gemini reports into a single JSON payload
    and saves the record to the PostgreSQL database.
    """
    db = SessionLocal()

    try:
        # Aggregate all results into a single structured JSON payload
        json_payload = {
            "scans": {
                "flake8": f_report,
                "semgrep": s_report
            },
            "ai_analysis": g_review
        }

        # Create the new database record
        new_review = PRReview(
            repo=repo,
            pr_number=pr_num,
            reports=json_payload
        )

        # Save to the database
        db.add(new_review)
        db.commit()
        db.refresh(new_review)

    except Exception as e:
        print(f"[Database Error] Failed to save review: {e}")
        db.rollback()
    finally:
        db.close()