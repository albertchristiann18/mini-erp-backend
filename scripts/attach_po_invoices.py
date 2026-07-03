"""
attach_po_invoices.py

Extracts the opex docs zip, uploads purchase order invoice PDFs to Cloudflare R2,
and sets the purchase_order_invoice_file field on the 12 imported POs that have matching PDFs.

Run from mini-erp-backend/ with:
    uv run python scripts/attach_po_invoices.py
"""

import os
import shutil
import sys
import zipfile

# Step 0 — Load Django settings to get R2 storage
sys.path.insert(0, "/Users/jtf01644/personal/mini-erp-project/mini-erp-backend")
os.environ["DJANGO_SETTINGS_MODULE"] = "core.settings"

import django

django.setup()

import psycopg
from django.core.files import File
from django.core.files.storage import default_storage

IMPORTED_PO_NUMBERS = [f"PO-2025-{i:03d}" for i in range(1, 8)] + [
    f"PO-2026-{i:03d}" for i in range(1, 8)
]

# Step 1 — Extract zip to /tmp/opex_docs/ (overwrite if exists)
ZIP_PATH = "/Users/jtf01644/personal/mini-erp-project/data/opex docs-20260702T175419Z-3-001.zip"
EXTRACT_DIR = "/tmp/opex_docs/"

if os.path.exists(EXTRACT_DIR):
    shutil.rmtree(EXTRACT_DIR)

with zipfile.ZipFile(ZIP_PATH, "r") as zf:
    zf.extractall(EXTRACT_DIR)

INVOICE_DIR = os.path.join(EXTRACT_DIR, "opex docs", "purchase order invoice")
print("Extracted zip to", EXTRACT_DIR)
print("Invoice dir:", INVOICE_DIR)

# Step 2 — Idempotency: clear existing purchase_order_invoice_file on imported POs
conn = psycopg.connect(
    host="localhost",
    port=5433,
    dbname="mini_erp",
    user="postgres",
    password="postgres",
    autocommit=True,
)
cur = conn.cursor()

cur.execute(
    """
    UPDATE purchasing_purchaseorder
    SET purchase_order_invoice_file = NULL
    WHERE purchase_order_number = ANY(%s)
    AND purchase_order_invoice_file IS NOT NULL
""",
    (IMPORTED_PO_NUMBERS,),
)
print("Cleared existing file references on imported POs")

# Step 3 — MAPPING dict (hardcoded)
MAPPING = {
    "mirako 1.pdf": "PO-2025-001",
    "mirako 3 compress.pdf": "PO-2025-003",
    "Sorakids 4 (1).pdf": "PO-2025-004",
    "Mirako 5.pdf": "PO-2025-005",
    "Mirako 6.pdf": "PO-2025-006",
    "Mirako 7.pdf": "PO-2025-007",
    "Mirako 8.pdf": "PO-2026-001",
    "Mirako 9.pdf": "PO-2026-002",
    "Mirako 9 PO.pdf": "PO-2026-003",
    "Mirako 10.pdf": "PO-2026-004",
    "Mirako 11.pdf": "PO-2026-005",
    "Mirako 12.pdf": "PO-2026-006",
}

# Step 4 — For each entry in MAPPING, upload and update DB
attached_count = 0

for filename, po_number in MAPPING.items():
    local_path = os.path.join(INVOICE_DIR, filename)

    # a. Check file exists
    if not os.path.exists(local_path):
        print(f"WARNING: File not found: {local_path} — skipping {po_number}")
        continue

    # b. Sanitize storage key: replace spaces with underscores
    sanitized_filename = filename.replace(" ", "_")
    storage_key = "po/invoices/" + sanitized_filename

    # c. Upload to R2 using default_storage
    try:
        if default_storage.exists(storage_key):
            default_storage.delete(storage_key)

        with open(local_path, "rb") as f:
            saved_name = default_storage.save(storage_key, File(f))

        # d. Update DB
        cur.execute(
            "UPDATE purchasing_purchaseorder SET purchase_order_invoice_file = %s WHERE purchase_order_number = %s",
            (saved_name, po_number),
        )

        print(f"✓ {po_number} ← {filename}")
        attached_count += 1

    except Exception as e:
        print(f"ERROR uploading {filename} for {po_number}: {e}")
        continue

# Step 5 — Print summary
print(f"\nSuccessfully attached {attached_count} invoice files.")
print("No PDF for PO-2025-002 (Jun 2 2025) and PO-2026-007 (Jun 29 2026)")

# Step 6 — Cleanup
shutil.rmtree(EXTRACT_DIR)
conn.close()
print("Done.")
