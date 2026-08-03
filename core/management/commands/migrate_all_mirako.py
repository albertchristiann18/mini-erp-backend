"""
Management command: migrate_all_mirako

Runs the full Mirako data migration pipeline. Each step can be skipped with the
corresponding --skip-* flag, enabling incremental re-runs after partial failures.

Usage:
    uv run python manage.py migrate_all_mirako \\
        --finops-file /path/to/Mirakokids_FinOps.xlsx \\
        --po-detail-file /path/to/PO_Detail.xlsx \\
        --po-tracker-file /path/to/PO_Tracker.xlsx \\
        --sales-file /path/to/Sales_Tracker.xlsx
"""

from argparse import ArgumentParser
from pathlib import Path
from typing import Any

import openpyxl
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

from core.models import Company
from core.services.migrate_service import (
    step_attach_files,
    step_enrich_po_tracker,
    step_product_links,
    step_reconciliation,
    step_reset,
    step_seed_mirako_user,
    step_set_product_dimensions,
    step_split_deliveries,
)


class Command(BaseCommand):
    help = "Run the full Mirako data migration pipeline"

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument("--finops-file", required=True, help="Mirakokids FinOps.xlsx")
        parser.add_argument(
            "--po-detail-file",
            required=True,
            help="Purchase Order Detail Overview (1).xlsx",
        )
        parser.add_argument(
            "--po-tracker-file",
            required=True,
            help="Purchase Order Tracker (1).xlsx",
        )
        parser.add_argument("--sales-file", required=True, help="Sales Order Tracker.xlsx")
        parser.add_argument("--company", default="Mirako", help="Company name (default: Mirako)")
        parser.add_argument("--skip-files", action="store_true", help="Skip R2 file upload step")
        parser.add_argument("--skip-reset", action="store_true", help="Skip DB reset step")
        parser.add_argument("--skip-seed", action="store_true", help="Skip user/company seed step")
        parser.add_argument(
            "--skip-master", action="store_true", help="Skip import_master_data + dimension update"
        )
        parser.add_argument(
            "--skip-links", action="store_true", help="Skip product supplier links step"
        )
        parser.add_argument(
            "--skip-pos", action="store_true", help="Skip import_purchase_orders step"
        )
        parser.add_argument(
            "--skip-enrich", action="store_true", help="Skip PO tracker enrichment step"
        )
        parser.add_argument("--skip-split", action="store_true", help="Skip split deliveries step")
        parser.add_argument("--skip-sales", action="store_true", help="Skip import_sales step")
        parser.add_argument(
            "--skip-cash", action="store_true", help="Skip import_cash_transactions step"
        )
        parser.add_argument(
            "--skip-reconcile", action="store_true", help="Skip reconciliation report"
        )

    def handle(self, *args: Any, **options: Any) -> None:
        company_name: str = options["company"]
        finops_file: str = options["finops_file"]
        po_detail_file: str = options["po_detail_file"]
        po_tracker_file: str = options["po_tracker_file"]
        sales_file: str = options["sales_file"]

        for name, path in [
            ("--finops-file", finops_file),
            ("--po-detail-file", po_detail_file),
            ("--po-tracker-file", po_tracker_file),
            ("--sales-file", sales_file),
        ]:
            if not Path(path).exists():
                raise CommandError(f"{name}: file not found: {path}")

        if not options["skip_reset"]:
            self.stdout.write("=== Step 1: Reset ===\n")
            step_reset(self.stdout)

        if not options["skip_seed"]:
            self.stdout.write("=== Step 2: Seed user & company ===\n")
            step_seed_mirako_user(company_name, self.stdout)

        try:
            company = Company.objects.get(name=company_name)
        except Company.DoesNotExist:
            raise CommandError(
                f"Company '{company_name}' not found after seed step. "
                "Run without --skip-seed or create it manually first."
            )

        if not options["skip_master"]:
            self.stdout.write("=== Step 3: Master data ===\n")
            call_command(
                "import_master_data", file=finops_file, company=company_name, stdout=self.stdout
            )
            step_set_product_dimensions(company, self.stdout)

        # Only load the PO workbooks if at least one workbook-dependent step is active
        need_po_workbooks = not (
            options["skip_links"]
            and options["skip_enrich"]
            and options["skip_split"]
            and options["skip_files"]
        )

        po_detail_wb = None
        po_tracker_wb = None

        if need_po_workbooks:
            try:
                po_detail_wb = openpyxl.load_workbook(po_detail_file, data_only=True)
                po_tracker_wb = openpyxl.load_workbook(po_tracker_file, data_only=True)
            except Exception as exc:
                raise CommandError(f"Cannot open PO workbooks: {exc}") from exc

        if not options["skip_links"]:
            self.stdout.write("=== Step 3b: Product supplier links ===\n")
            step_product_links(company, po_detail_wb, self.stdout)

        if not options["skip_pos"]:
            self.stdout.write("=== Step 4: Purchase orders ===\n")
            call_command(
                "import_purchase_orders",
                file=po_detail_file,
                company=company_name,
                stdout=self.stdout,
            )

        if not options["skip_enrich"]:
            self.stdout.write("=== Step 5: PO tracker enrichment ===\n")
            step_enrich_po_tracker(company, po_tracker_wb, self.stdout)

        if not options["skip_split"]:
            self.stdout.write("=== Step 5b: Split deliveries ===\n")
            step_split_deliveries(company, po_tracker_wb, po_detail_wb, self.stdout)

        if not options["skip_sales"]:
            self.stdout.write("=== Step 6: Sales orders ===\n")
            call_command("import_sales", file=sales_file, company=company_name, stdout=self.stdout)

        if not options["skip_cash"]:
            self.stdout.write("=== Step 7: Cash transactions ===\n")
            call_command(
                "import_cash_transactions",
                file=finops_file,
                company=company_name,
                stdout=self.stdout,
            )

        if not options["skip_files"]:
            self.stdout.write("=== Step 8: File attachments ===\n")
            step_attach_files(company, po_tracker_wb, po_detail_wb, False, self.stdout)

        if not options["skip_reconcile"]:
            self.stdout.write("=== Step 9: Reconciliation ===\n")
            step_reconciliation(self.stdout)

        self.stdout.write(self.style.SUCCESS("\nMigration complete.\n"))
