"""
Run all database migrations for ReadySetClass
Executes migrations in order: 001, 002, 003, etc.
"""
import os
import psycopg2
from glob import glob

def run_all_migrations():
    """Execute all migration files in order"""

    DATABASE_URL = os.getenv('DATABASE_URL')

    if not DATABASE_URL:
        print("❌ DATABASE_URL not set")
        return

    # Fix postgres:// to postgresql://
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

    print("🔄 Connecting to database...")
    conn = psycopg2.connect(DATABASE_URL)
    cursor = conn.cursor()

    # Get all migration files
    migrations_dir = os.path.join(os.path.dirname(__file__), 'migrations')
    migration_files = sorted(glob(os.path.join(migrations_dir, '*.sql')))

    if not migration_files:
        print("⚠️  No migration files found")
        return

    print(f"📁 Found {len(migration_files)} migration file(s)\n")

    for migration_file in migration_files:
        migration_name = os.path.basename(migration_file)
        print(f"🔄 Running migration: {migration_name}")

        with open(migration_file, 'r') as f:
            migration_sql = f.read()

        try:
            cursor.execute(migration_sql)
            conn.commit()
            print(f"✅ {migration_name} completed successfully!\n")

        except Exception as e:
            print(f"❌ {migration_name} failed: {e}")
            conn.rollback()
            print("⚠️  Stopping migrations due to error")
            break

    cursor.close()
    conn.close()
    print("🎉 All migrations completed!")

if __name__ == "__main__":
    run_all_migrations()
