#!/usr/bin/env python3
"""
Import sales orders directly from the Google Drive MCP cache file.

No XLSX download or API credentials needed — reads the already-cached JSON
from the MCP Google Drive tool result.

Run from mini-erp-backend/:
    uv run python scripts/import_sales_from_cache.py
    uv run python scripts/import_sales_from_cache.py --dry-run
"""

import argparse
import json
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg

# ── Configuration ──────────────────────────────────────────────────────────────

DB = dict(dbname="mini_erp", user="postgres", password="postgres",
          host="localhost", port=5433)

CACHE_FILE = Path(
    "/Users/jtf01644/.claude/projects/"
    "-Users-jtf01644-personal-mini-erp-project/"
    "228f7bce-8982-45c2-b0ce-2f4e98fe7ded/"
    "tool-results/mcp-claude_ai_Google_Drive-read_file_content-1779981783106.txt"
)

WIB = ZoneInfo("Asia/Jakarta")
NS  = uuid.NAMESPACE_OID

# channel name (lowercase) → (ERP source_platform, marketplace display name)
CHANNEL_MAP: dict[str, tuple[str, str]] = {
    "shopee - mirako kids":     ("SHOPEE", "Shopee - Mirako Kids"),
    "shopee - sora kids":       ("SHOPEE", "Shopee - Mirako Kids"),
    "tiktok - mirakokids":      ("TIKTOK", "TikTok - MirakoKids"),
    "tiktok shop - mirakokids": ("TIKTOK", "TikTok - MirakoKids"),
    "tiktok shop":              ("TIKTOK", "TikTok - MirakoKids"),
}

STATUS_MAP: dict[str, str] = {
    "completed":     "COMPLETED",
    "delivered":     "DELIVERED",
    "in_delivery":   "SHIPPING",
    "cancellations": "CANCELLED",
    "cancelled":     "CANCELLED",
    "pending":       "PENDING",
    "confirmed":     "CONFIRMED",
    "process":       "CONFIRMED",
    "returns":       "RETURNED",
    "returned":      "RETURNED",
    "refunded":      "RETURNED",
}


# ── Helpers ────────────────────────────────────────────────────────────────────

def uid(seed: str) -> str:
    return str(uuid.uuid5(NS, f"mirako-kids:{seed}"))


def unescape_md(s: str) -> str:
    """Remove markdown escapes: \\- → -, \\| → |, \\* → *, etc."""
    return re.sub(r"\\(.)", r"\1", s)


def parse_idr(raw: str | None) -> int:
    if not raw:
        return 0
    # strip markdown escapes first, then keep only digits and minus
    clean = re.sub(r"[^\d\-]", "", unescape_md(str(raw)).strip())
    try:
        v = int(clean) if clean and clean not in ("-", "") else 0
        return max(0, v)   # ignore negative fees
    except ValueError:
        return 0


def parse_dt(raw: str | None) -> datetime | None:
    if not raw:
        return None
    s = unescape_md(str(raw)).strip()
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y %H:%M",
        "%d/%m/%Y",
    ):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=WIB)
        except ValueError:
            continue
    return None


def parse_province_city(address: str) -> tuple[str, str]:
    """
    Extract province and city from Desty address string.
    Format tail: ..., CITY, DISTRICT, PROVINCE, ID, POSTAL
    """
    if not address:
        return "", ""
    parts = [p.strip() for p in address.split(",")]
    parts = [p for p in parts if p and p != "ID" and not re.match(r"^\d{4,6}$", p)]
    if len(parts) >= 3:
        return parts[-1].title(), parts[-3].title()
    if len(parts) == 2:
        return parts[-1].title(), parts[-2].title()
    return "", ""


# ── Markdown table parsing ─────────────────────────────────────────────────────

def split_md_row(line: str) -> list[str]:
    """
    Split a markdown table row on unescaped pipes.
    '| a | b\\|c | d |' → ['a', 'b|c', 'd']
    """
    # Replace escaped pipes temporarily
    placeholder = "\x00PIPE\x00"
    line = line.replace("\\|", placeholder)
    cells = line.split("|")[1:-1]           # trim leading/trailing empty splits
    return [c.replace(placeholder, "|").strip() for c in cells]


def normalise_header(h: str) -> str:
    """Decode &#10; HTML entity and collapse whitespace in a header cell."""
    h = h.replace("&#10;", " ")
    return re.sub(r"\s+", " ", h).strip()


def parse_markdown_section(section: str) -> list[dict]:
    """
    Parse a single markdown table section into a list of row-dicts.
    Expects the section to start with the header row (| NO. | ... ).
    """
    lines = [l for l in section.split("\n") if l.strip().startswith("|")]
    if len(lines) < 3:
        return []

    headers = [normalise_header(h) for h in split_md_row(lines[0])]

    rows = []
    for line in lines[2:]:          # lines[1] is the |:---:|:---:| alignment row
        cells = split_md_row(line)
        if not any(cells):
            continue
        # Pad or trim to match header count
        while len(cells) < len(headers):
            cells.append("")
        row = {headers[i]: unescape_md(cells[i]) for i in range(len(headers))}
        rows.append(row)

    return rows


def load_all_tabs(content: str) -> list[list[dict]]:
    """
    Split the full fileContent into per-tab sections and parse each one.
    Returns a list of row-dict lists, one per tab.
    """
    positions = [m.start() for m in re.finditer(
        r"\| NO\. \|[^\n]+Tanggal Pesanan", content
    )]
    if not positions:
        return []

    tabs = []
    for i, start in enumerate(positions):
        end = positions[i + 1] if i + 1 < len(positions) else len(content)
        section = content[start:end]
        rows = parse_markdown_section(section)
        if rows:
            tabs.append(rows)

    return tabs


# ── Order grouping ─────────────────────────────────────────────────────────────

def group_rows_into_orders(rows: list[dict]) -> dict[str, dict]:
    orders: dict[str, dict] = {}

    for row in rows:
        # Skip rows with no parseable date — catches alignment/summary rows from non-sales tabs
        if not parse_dt(row.get("Tanggal Pesanan Dibuat")):
            continue

        pkg_num  = row.get("Nomor Paket", "").strip()
        mkt_num  = row.get("Nomor Pesanan (di Marketplace)", "").strip()
        order_key = pkg_num or mkt_num
        if not order_key:
            continue

        if order_key not in orders:
            channel    = row.get("Channel - Nama Toko", "").strip()
            platform   = CHANNEL_MAP.get(channel.lower(), ("SHOPEE", ""))[0]
            status_raw = row.get("Status Pesanan", "").strip().lower()
            status     = STATUS_MAP.get(status_raw, "COMPLETED")
            address    = row.get("Alamat Pembeli", "").strip()
            province, city = parse_province_city(address)

            orders[order_key] = {
                "package_number":           pkg_num,
                "marketplace_order_id":     mkt_num or pkg_num,
                "marketplace_order_number": pkg_num or mkt_num,
                "channel":                  channel,
                "source_platform":          platform,
                "status":                   status,
                "order_date":               parse_dt(row.get("Tanggal Pesanan Dibuat")),
                "customer_name":            row.get("Nama Pembeli", "").strip(),
                "customer_phone":           row.get("Nomor Telepon Pembeli", "").strip(),
                "shipping_address":         address,
                "shipping_province":        province,
                "shipping_city":            city,
                "courier_name":             row.get("Kurir", "").strip(),
                "tracking_number":          row.get("Nomor AWB/Resi", "").strip(),
                "shipping_fee":             parse_idr(row.get("Biaya Pengiriman Final")),
                "items": {},
            }

        sku = row.get("SKU Master", "").strip()
        # Only accept proper SKU codes like SEG-005-100-RED, DRS-008-120-BLU
        if not sku or not re.match(r"^[A-Z]+-\d{3}-\d{2,3}-[A-Z]+$", sku):
            continue

        unit_price  = parse_idr(row.get("Harga Satuan"))
        paid_price  = parse_idr(row.get("Harga Dibayar")) or unit_price
        item_qty    = max(parse_idr(row.get("Jumlah")), 1)
        service_fee = parse_idr(row.get("Biaya Layanan"))
        discount    = max(0, unit_price - paid_price) * item_qty
        line_total  = parse_idr(row.get("Subtotal Produk")) or (paid_price * item_qty)

        if sku in orders[order_key]["items"]:
            it = orders[order_key]["items"][sku]
            it["quantity"]               += item_qty
            it["discount_amount"]        += discount
            it["service_fee"]            += service_fee
            it["total_marketplace_fee"]  += service_fee
            it["line_total"]             += line_total
        else:
            orders[order_key]["items"][sku] = {
                "sku":                   sku,
                "quantity":              item_qty,
                "selling_price":         unit_price,
                "discount_amount":       discount,
                "service_fee":           service_fee,
                "total_marketplace_fee": service_fee,
                "line_total":            line_total,
            }

    return orders


# ── DB helpers ─────────────────────────────────────────────────────────────────

def load_refs(cur: psycopg.Cursor) -> dict:
    cur.execute("SELECT company_id::text FROM core_company LIMIT 1")
    row = cur.fetchone()
    if not row:
        raise RuntimeError("No company in DB — run import_master_data.py first")
    company_id = row[0]

    cur.execute("""
        SELECT warehouse_id::text FROM inventory_warehouse
        WHERE is_active = TRUE ORDER BY cdate LIMIT 1
    """)
    row = cur.fetchone()
    if not row:
        raise RuntimeError("No active warehouse in DB — run import_master_data.py first")
    warehouse_id = row[0]

    cur.execute("SELECT marketplace_id::text, name FROM core_marketplace")
    marketplaces: dict[str, str] = {r[1].lower(): r[0] for r in cur.fetchall()}

    cur.execute("SELECT product_variant_id::text, sku_variant_code FROM inventory_productvariant")
    variants: dict[str, str] = {r[1]: r[0] for r in cur.fetchall()}

    return {
        "company_id":   company_id,
        "warehouse_id": warehouse_id,
        "marketplaces": marketplaces,
        "variants":     variants,
        "unknown_skus": set(),
    }


def ensure_marketplace(
    cur: psycopg.Cursor,
    channel_raw: str,
    refs: dict,
    now: datetime,
    dry_run: bool,
) -> str | None:
    info = CHANNEL_MAP.get(channel_raw.strip().lower())
    if not info:
        if channel_raw:
            print(f"  WARNING: unknown channel {channel_raw!r}")
        return None
    _, mp_name = info
    key = mp_name.lower()
    if key in refs["marketplaces"]:
        return refs["marketplaces"][key]
    mp_id = uid(f"marketplace:{key}")
    if not dry_run:
        cur.execute("""
            INSERT INTO core_marketplace
                (marketplace_id, name, url, status, connected_time,
                 is_active, shipping_config, cdate, udate)
            VALUES (%s::uuid, %s, NULL, 'active', NULL, TRUE, '{}'::jsonb, %s, %s)
            ON CONFLICT (marketplace_id) DO NOTHING
        """, (mp_id, mp_name, now, now))
    refs["marketplaces"][key] = mp_id
    print(f"  [+] Marketplace created: {mp_name}")
    return mp_id


def insert_orders(
    cur: psycopg.Cursor,
    orders: dict[str, dict],
    refs: dict,
    now: datetime,
    dry_run: bool,
) -> tuple[int, int, int]:
    inserted = skipped_existing = skipped_no_items = 0

    for order_key, order in orders.items():
        label        = order["package_number"] or order["marketplace_order_id"]
        order_number = f"SO-{label}"

        cur.execute("SELECT 1 FROM sales_salesorder WHERE order_number = %s", (order_number,))
        if cur.fetchone():
            skipped_existing += 1
            continue

        valid_items: list[dict] = []
        for it in order["items"].values():
            variant_id = refs["variants"].get(it["sku"])
            if not variant_id:
                refs["unknown_skus"].add(it["sku"])
                continue
            valid_items.append({**it, "product_variant_id": variant_id})

        if not valid_items and order["status"] not in ("CANCELLED", "RETURNED"):
            skipped_no_items += 1
            continue

        subtotal               = sum(i["line_total"]      for i in valid_items)
        total_discount         = sum(i["discount_amount"] for i in valid_items)
        total_marketplace_fee  = sum(i["service_fee"]     for i in valid_items)
        net_revenue            = subtotal - total_discount - total_marketplace_fee

        marketplace_id = ensure_marketplace(cur, order["channel"], refs, now, dry_run)
        so_id          = uid(f"so:{order_key}")
        order_date     = order["order_date"] or now

        if not dry_run:
            cur.execute("""
                INSERT INTO sales_salesorder (
                    sales_order_id, company_id, order_number,
                    marketplace_id, marketplace_order_id, marketplace_order_number,
                    status, source_platform, warehouse_id,
                    customer_name, customer_phone, shipping_address,
                    shipping_province, shipping_city,
                    courier_name, tracking_number,
                    shipping_fee, shipping_fee_seller,
                    subtotal, total_discount, total_marketplace_fee,
                    total_cogs, net_revenue, gross_profit,
                    order_date, note, cdate, udate
                ) VALUES (
                    %s::uuid, %s::uuid, %s,
                    %s::uuid, %s, %s,
                    %s, %s, %s::uuid,
                    %s, %s, %s,
                    %s, %s,
                    %s, %s,
                    %s, %s,
                    %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s, %s
                ) ON CONFLICT (order_number) DO NOTHING
            """, (
                so_id, refs["company_id"], order_number,
                marketplace_id, order["marketplace_order_id"], order["marketplace_order_number"],
                order["status"], order["source_platform"], refs["warehouse_id"],
                order["customer_name"], order["customer_phone"], order["shipping_address"],
                order["shipping_province"], order["shipping_city"],
                order["courier_name"], order["tracking_number"],
                order["shipping_fee"], 0,
                subtotal, total_discount, total_marketplace_fee,
                0, net_revenue, 0,
                order_date, "", now, now,
            ))

            for item in valid_items:
                item_id = uid(f"so-item:{order_key}:{item['sku']}")
                cur.execute("""
                    INSERT INTO sales_salesorderitem (
                        sales_order_item_id, company_id, sales_order_id,
                        product_variant_id, quantity,
                        selling_price, discount_amount, commission_fee,
                        service_fee, total_marketplace_fee,
                        actual_cogs_per_unit, actual_cogs_total,
                        line_total, cdate, udate
                    ) VALUES (
                        %s::uuid, %s::uuid, %s::uuid,
                        %s::uuid, %s,
                        %s, %s, %s,
                        %s, %s,
                        %s, %s,
                        %s, %s, %s
                    ) ON CONFLICT (sales_order_item_id) DO NOTHING
                """, (
                    item_id, refs["company_id"], so_id,
                    item["product_variant_id"], item["quantity"],
                    item["selling_price"], item["discount_amount"], 0,
                    item["service_fee"], item["total_marketplace_fee"],
                    0, 0,
                    item["line_total"], now, now,
                ))

        inserted += 1

    return inserted, skipped_existing, skipped_no_items


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import sales orders from Google Drive MCP cache into mini-erp DB"
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Show counts without writing to DB")
    parser.add_argument("--cache", type=Path, default=CACHE_FILE,
                        help="Path to the MCP tool-result JSON cache file")
    args = parser.parse_args()

    if not args.cache.exists():
        sys.exit(f"Cache file not found: {args.cache}")

    print(f"Reading cache: {args.cache}")
    with open(args.cache) as f:
        data = json.load(f)
    content: str = data["fileContent"]
    print(f"Content size: {len(content):,} chars")

    print("Parsing monthly tabs ...")
    tabs = load_all_tabs(content)
    print(f"Tabs found: {len(tabs)}")
    for i, rows in enumerate(tabs):
        # Detect month from first order date
        first_date = ""
        for r in rows:
            d = r.get("Tanggal Pesanan Dibuat", "").strip()
            if d:
                first_date = d[:7]   # YYYY-MM
                break
        print(f"  Tab {i+1:2d}: {len(rows):4d} rows   first date: {first_date}")

    now = datetime.now(timezone.utc)

    print("\nConnecting to database ...")
    conn = psycopg.connect(**DB)
    cur  = conn.cursor()

    try:
        refs = load_refs(cur)
        print(f"  Company:   {refs['company_id']}")
        print(f"  Warehouse: {refs['warehouse_id']}")
        print(f"  Variants:  {len(refs['variants'])} loaded")
        if args.dry_run:
            print("  [DRY RUN — nothing will be written]\n")

        total_inserted = total_existing = total_no_items = 0
        all_orders: dict[str, dict] = {}

        for rows in tabs:
            tab_orders = group_rows_into_orders(rows)
            # Merge across tabs — if same order_key seen twice, keep first
            for k, v in tab_orders.items():
                if k not in all_orders:
                    all_orders[k] = v

        print(f"\nTotal unique orders across all tabs: {len(all_orders)}")

        ins, ex, ni = insert_orders(cur, all_orders, refs, now, args.dry_run)
        total_inserted += ins
        total_existing += ex
        total_no_items += ni

        if not args.dry_run:
            conn.commit()

    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()

    mode = "DRY RUN" if args.dry_run else "DONE"
    print(f"\n{'─' * 55}")
    print(f"{mode}")
    print(f"  Orders inserted:                   {total_inserted}")
    print(f"  Orders already in DB:              {total_existing}")
    print(f"  Orders skipped (no SKU match):     {total_no_items}")

    if refs["unknown_skus"]:
        print(f"\n  Unrecognised SKUs ({len(refs['unknown_skus'])}):")
        for sku in sorted(refs["unknown_skus"]):
            print(f"    {sku}")


if __name__ == "__main__":
    main()
