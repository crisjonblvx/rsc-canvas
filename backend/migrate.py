#!/usr/bin/env python3
"""
Database Migration Script for Railway
Run this after deploying new code with migrations

Usage:
  python migrate.py                    # Run all migrations
  python migrate.py --migration 003    # Run specific migration
"""
import os
import sys
import psycopg2
from glob import glob

def run_migration(migration_file: str, conn, cursor) -> bool:
    """Run a single migration file"""
    migration_name = os.path.basename(migration_file)
    print(f"🔄 Running migration: {migration_name}")

    with open(migration_file, 'r') as f:
        migration_sql = f.read()

    try:
        cursor.execute(migration_sql)
        conn.commit()
        print(f"✅ {migration_name} completed successfully!\n")
        return True

    except Exception as e:
        print(f"❌ {migration_name} failed: {e}")
        conn.rollback()
        return False


def main():
    DATABASE_URL = os.getenv('DATABASE_URL')

    if not DATABASE_URL:
        print("❌ DATABASE_URL not set")
        print("   Set it with: export DATABASE_URL='postgresql://...'")
        return 1

    # Fix postgres:// to postgresql://
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

    print("🔄 Connecting to database...")
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cursor = conn.cursor()
        print("✅ Connected to database\n")
    except Exception as e:
        print(f"❌ Connection failed: {e}")
        return 1

    # Get migration files
    migrations_dir = os.path.join(os.path.dirname(__file__), 'migrations')

    # Check if specific migration requested
    if len(sys.argv) > 2 and sys.argv[1] == '--migration':
        migration_num = sys.argv[2]
        pattern = os.path.join(migrations_dir, f"{migration_num}_*.sql")
        migration_files = glob(pattern)
        if not migration_files:
            print(f"❌ Migration {migration_num} not found")
            return 1
    else:
        migration_files = sorted(glob(os.path.join(migrations_dir, '*.sql')))

    if not migration_files:
        print("⚠️  No migration files found")
        return 0

    print(f"📁 Found {len(migration_files)} migration file(s) to run\n")

    # Run migrations
    for migration_file in migration_files:
        success = run_migration(migration_file, conn, cursor)
        if not success:
            print("⚠️  Stopping migrations due to error")
            cursor.close()
            conn.close()
            return 1

    cursor.close()
    conn.close()
    print("🎉 All migrations completed successfully!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
