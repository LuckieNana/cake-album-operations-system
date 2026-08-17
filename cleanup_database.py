"""
Cleans the Cake Album database down to only today's orders, for a genuine fresh start.

SAFETY:
  - Automatically makes a full backup copy of the database before touching anything.
  - Shows exactly what will be deleted (a dry run) and asks for a typed confirmation
    before actually deleting a single row.
  - Deliberately does NOT touch staff accounts, phone push subscriptions, or cash
    clearance records (a financial audit trail worth keeping regardless).

USAGE:
    python3 cleanup_database.py                  # keeps only today's orders
    python3 cleanup_database.py --date 2026-08-17  # keeps only a specific date's orders
"""
import argparse
import shutil
import sqlite3
from datetime import date, datetime
from pathlib import Path

DB_FILE = Path(__file__).parent / "cake_album_operations_v1_1_TEST.db"

# Every table that has a genuine order_id column - these get cleaned to match
# whichever orders survive. Tables NOT in this list (staff_accounts, push_subscriptions,
# cash_clearances, etc.) are never touched.
ORDER_LINKED_TABLES = [
    "audit_logs", "stage_quality_checks", "complaints", "delivery_run_orders",
    "order_material_requirements", "procurement_requisitions", "reassignment_requests",
    "baking_batch_orders", "order_videos", "oven_logs", "baked_cookie_inventory",
    "baked_cake_inventory", "layer_inventory_usage", "notifications", "stage_material_usage",
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="Keep only orders from this date (YYYY-MM-DD). Defaults to today.")
    parser.add_argument("--yes", action="store_true", help="Skip the confirmation prompt (for non-interactive use).")
    args = parser.parse_args()
    keep_date = args.date or date.today().isoformat()

    if not DB_FILE.exists():
        print(f"Database not found at {DB_FILE}")
        return

    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row

    all_orders = conn.execute("SELECT order_id, order_created_at FROM orders").fetchall()
    keep_ids = [r["order_id"] for r in all_orders if str(r["order_created_at"]).startswith(keep_date)]
    delete_ids = [r["order_id"] for r in all_orders if r["order_id"] not in keep_ids]

    print(f"Keeping orders created on {keep_date}: {len(keep_ids)} order(s)")
    print(f"Deleting everything else: {len(delete_ids)} order(s)")
    if delete_ids:
        print("Sample of order IDs being deleted:", delete_ids[:10], "..." if len(delete_ids) > 10 else "")

    if not delete_ids:
        print("Nothing to delete — every order already matches the keep date.")
        conn.close()
        return

    if not args.yes:
        answer = input(f"\nType YES to permanently delete {len(delete_ids)} order(s) and everything linked to them: ")
        if answer.strip() != "YES":
            print("Cancelled — nothing was deleted.")
            conn.close()
            return

    backup_path = DB_FILE.parent / f"{DB_FILE.stem}_backup_before_cleanup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
    shutil.copy2(DB_FILE, backup_path)
    print(f"Backup saved to {backup_path}")

    placeholders = ",".join("?" * len(delete_ids))
    total_deleted_rows = 0
    for table in ORDER_LINKED_TABLES:
        try:
            cur = conn.execute(f"DELETE FROM {table} WHERE order_id IN ({placeholders})", delete_ids)
            total_deleted_rows += cur.rowcount
            if cur.rowcount:
                print(f"  {table}: {cur.rowcount} row(s) deleted")
        except sqlite3.OperationalError as e:
            print(f"  {table}: skipped ({e})")

    cur = conn.execute(f"DELETE FROM orders WHERE order_id IN ({placeholders})", delete_ids)
    print(f"  orders: {cur.rowcount} row(s) deleted")
    total_deleted_rows += cur.rowcount

    conn.commit()
    conn.close()
    print(f"\nDone. {total_deleted_rows} total row(s) deleted across all tables.")
    print(f"If anything looks wrong, restore from the backup: {backup_path}")


if __name__ == "__main__":
    main()
