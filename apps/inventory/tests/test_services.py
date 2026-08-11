from decimal import Decimal

from django.test import TestCase

from apps.catalog.tests.factories import CategoryFactory, ProductFactory, ProductVariantFactory
from apps.inventory.models import (
    ProductCogs,
    ProductVariantWarehouse,
    StockMovement,
)
from apps.inventory.services.inventory_service import InventoryService, allocate_largest_remainder
from apps.inventory.tests.factories import (
    ProductCogsFactory,
    ProductVariantWarehouseFactory,
)
from apps.purchasing.models import PurchaseOrder
from apps.purchasing.tests.factories import PurchaseOrderFactory
from core.factories import CompanyFactory, WarehouseFactory


class InventoryServiceStockUpdateTest(TestCase):
    """Test cases for InventoryService.update_stock_on_po method"""

    def setUp(self):
        self.company = CompanyFactory()
        self.warehouse = WarehouseFactory(company=self.company)
        self.category = CategoryFactory(company=self.company)
        self.product = ProductFactory(category=self.category, company=self.company)
        self.product_variant = ProductVariantFactory(product=self.product)
        self.service = InventoryService()

    def test_update_stock_on_po_ordered_status(self):
        """Test stock update when PO status changes to ORDERED."""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.DRAFT,
        )

        data = [
            {
                "product_variant_id": str(self.product_variant.id),
                "ordered_qty": 100,
                "received_qty": 0,
                "updated_qty": 0,
            }
        ]

        self.service.update_stock_on_po(
            po=po,
            new_status=PurchaseOrder.POStatus.ORDERED,
            data=data,
        )

        pvw = ProductVariantWarehouse.objects.get(
            product_variant=self.product_variant,
            warehouse=self.warehouse,
        )
        self.assertEqual(pvw.incoming_qty, 100)
        self.assertEqual(pvw.physical_qty, 0)

        self.product_variant.refresh_from_db()
        self.assertEqual(self.product_variant.total_incoming_qty, 100)

    def test_update_stock_on_po_delivered_first_time(self):
        """Test stock update when PO status changes to DELIVERED (first time)."""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.SHIPPED,
        )

        pvw = ProductVariantWarehouseFactory(
            product_variant=self.product_variant,
            warehouse=self.warehouse,
            company=self.company,
            incoming_qty=100,
            physical_qty=0,
        )

        data = [
            {
                "product_variant_id": str(self.product_variant.id),
                "ordered_qty": 100,
                "received_qty": 50,
                "updated_qty": 0,
            }
        ]

        self.service.update_stock_on_po(
            po=po,
            new_status=PurchaseOrder.POStatus.DELIVERED,
            data=data,
        )

        pvw.refresh_from_db()
        self.assertEqual(pvw.incoming_qty, 50)
        self.assertEqual(pvw.physical_qty, 50)

        self.product_variant.refresh_from_db()
        self.assertEqual(self.product_variant.total_incoming_qty, 50)
        self.assertEqual(self.product_variant.total_available_qty, 50)

    def test_update_stock_on_po_delivered_subsequent(self):
        """Test stock update when PO is already DELIVERED and receives more."""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.DELIVERED,
        )

        pvw = ProductVariantWarehouseFactory(
            product_variant=self.product_variant,
            warehouse=self.warehouse,
            company=self.company,
            incoming_qty=90,
            physical_qty=10,
        )
        self.product_variant.total_incoming_qty = 90
        self.product_variant.total_available_qty = 10
        self.product_variant.save()

        data = [
            {
                "product_variant_id": str(self.product_variant.id),
                "ordered_qty": 100,
                "received_qty": 30,
                "updated_qty": 10,
            }
        ]

        self.service.update_stock_on_po(
            po=po,
            new_status=PurchaseOrder.POStatus.DELIVERED,
            data=data,
        )

        pvw.refresh_from_db()
        self.assertEqual(pvw.incoming_qty, 70)
        self.assertEqual(pvw.physical_qty, 30)

        self.product_variant.refresh_from_db()
        self.assertEqual(self.product_variant.total_incoming_qty, 70)
        self.assertEqual(self.product_variant.total_available_qty, 30)

    def test_update_stock_on_po_with_empty_data(self):
        """Test that empty data does nothing."""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.DRAFT,
        )

        self.service.update_stock_on_po(
            po=po,
            new_status=PurchaseOrder.POStatus.ORDERED,
            data=[],
        )

        self.assertEqual(ProductVariantWarehouse.objects.count(), 0)

    def test_update_stock_on_po_delivered_incoming_qty_decreased(self):
        """Test incoming_qty is adjusted when received_qty decreases from SHIPPED to DELIVERED."""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.SHIPPED,
        )

        pvw = ProductVariantWarehouseFactory(
            product_variant=self.product_variant,
            warehouse=self.warehouse,
            company=self.company,
            incoming_qty=100,
            physical_qty=0,
        )
        self.product_variant.total_incoming_qty = 100
        self.product_variant.save()

        data = [
            {
                "product_variant_id": str(self.product_variant.id),
                "ordered_qty": 100,
                "received_qty": 50,
                "updated_qty": 0,
            }
        ]

        self.service.update_stock_on_po(
            po=po,
            new_status=PurchaseOrder.POStatus.DELIVERED,
            data=data,
        )

        pvw.refresh_from_db()
        self.assertEqual(pvw.incoming_qty, 50)
        self.assertEqual(pvw.physical_qty, 50)

        self.product_variant.refresh_from_db()
        self.assertEqual(self.product_variant.total_incoming_qty, 50)
        self.assertEqual(self.product_variant.total_available_qty, 50)

    def test_update_stock_on_po_delivered_received_qty_decreased(self):
        """Test physical_qty decreases when received_qty is decreased on subsequent delivery."""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.DELIVERED,
        )

        pvw = ProductVariantWarehouseFactory(
            product_variant=self.product_variant,
            warehouse=self.warehouse,
            company=self.company,
            incoming_qty=50,
            physical_qty=50,
        )
        self.product_variant.total_incoming_qty = 50
        self.product_variant.total_available_qty = 50
        self.product_variant.save()

        data = [
            {
                "product_variant_id": str(self.product_variant.id),
                "ordered_qty": 100,
                "received_qty": 30,
                "updated_qty": 50,
            }
        ]

        self.service.update_stock_on_po(
            po=po,
            new_status=PurchaseOrder.POStatus.DELIVERED,
            data=data,
        )

        pvw.refresh_from_db()
        self.assertEqual(pvw.incoming_qty, 70)
        self.assertEqual(pvw.physical_qty, 30)

        self.product_variant.refresh_from_db()
        self.assertEqual(self.product_variant.total_incoming_qty, 70)
        self.assertEqual(self.product_variant.total_available_qty, 30)

    def test_update_stock_on_po_received_qty_exceeds_ordered(self):
        """Test incoming_qty becomes 0 when received_qty exceeds ordered_qty."""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.SHIPPED,
        )

        pvw = ProductVariantWarehouseFactory(
            product_variant=self.product_variant,
            warehouse=self.warehouse,
            company=self.company,
            incoming_qty=10,
            physical_qty=0,
        )
        self.product_variant.total_incoming_qty = 10
        self.product_variant.save()

        data = [
            {
                "product_variant_id": str(self.product_variant.id),
                "ordered_qty": 10,
                "received_qty": 15,
                "updated_qty": 0,
            }
        ]

        self.service.update_stock_on_po(
            po=po,
            new_status=PurchaseOrder.POStatus.DELIVERED,
            data=data,
        )

        pvw.refresh_from_db()
        self.assertEqual(pvw.incoming_qty, 0)
        self.assertEqual(pvw.physical_qty, 15)

        self.product_variant.refresh_from_db()
        self.assertEqual(self.product_variant.total_incoming_qty, 0)
        self.assertEqual(self.product_variant.total_available_qty, 15)

    def test_update_stock_on_po_full_delivery(self):
        """Test incoming_qty becomes 0 when fully received."""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.SHIPPED,
        )

        pvw = ProductVariantWarehouseFactory(
            product_variant=self.product_variant,
            warehouse=self.warehouse,
            company=self.company,
            incoming_qty=100,
            physical_qty=0,
        )
        self.product_variant.total_incoming_qty = 100
        self.product_variant.save()

        data = [
            {
                "product_variant_id": str(self.product_variant.id),
                "ordered_qty": 100,
                "received_qty": 100,
                "updated_qty": 0,
            }
        ]

        self.service.update_stock_on_po(
            po=po,
            new_status=PurchaseOrder.POStatus.DELIVERED,
            data=data,
        )

        pvw.refresh_from_db()
        self.assertEqual(pvw.incoming_qty, 0)
        self.assertEqual(pvw.physical_qty, 100)

        self.product_variant.refresh_from_db()
        self.assertEqual(self.product_variant.total_incoming_qty, 0)
        self.assertEqual(self.product_variant.total_available_qty, 100)

    def test_update_stock_on_po_no_changes_received_qty_same(self):
        """Test that stock is not changed when received_qty is the same as updated_qty."""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.DELIVERED,
        )

        pvw = ProductVariantWarehouseFactory(
            product_variant=self.product_variant,
            warehouse=self.warehouse,
            company=self.company,
            incoming_qty=50,
            physical_qty=50,
        )
        self.product_variant.total_incoming_qty = 50
        self.product_variant.total_available_qty = 50
        self.product_variant.save()

        data = [
            {
                "product_variant_id": str(self.product_variant.id),
                "ordered_qty": 100,
                "received_qty": 50,
                "updated_qty": 50,
            }
        ]

        self.service.update_stock_on_po(
            po=po,
            new_status=PurchaseOrder.POStatus.DELIVERED,
            data=data,
        )

        pvw.refresh_from_db()
        self.assertEqual(pvw.incoming_qty, 50)
        self.assertEqual(pvw.physical_qty, 50)

        self.product_variant.refresh_from_db()
        self.assertEqual(self.product_variant.total_incoming_qty, 50)
        self.assertEqual(self.product_variant.total_available_qty, 50)

    def test_update_stock_on_po_completed_clears_incoming(self):
        """Test that COMPLETED status clears remaining incoming_qty."""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.DELIVERED,
        )

        pvw = ProductVariantWarehouseFactory(
            product_variant=self.product_variant,
            warehouse=self.warehouse,
            company=self.company,
            incoming_qty=20,
            physical_qty=80,
        )
        self.product_variant.total_incoming_qty = 20
        self.product_variant.total_available_qty = 80
        self.product_variant.save()

        data = [
            {
                "product_variant_id": str(self.product_variant.id),
                "ordered_qty": 100,
                "received_qty": 80,
                "updated_qty": 80,
            }
        ]

        self.service.update_stock_on_po(
            po=po,
            new_status=PurchaseOrder.POStatus.COMPLETED,
            data=data,
        )

        pvw.refresh_from_db()
        self.assertEqual(pvw.incoming_qty, 0)
        self.assertEqual(pvw.physical_qty, 80)

        self.product_variant.refresh_from_db()
        self.assertEqual(self.product_variant.total_incoming_qty, 0)
        self.assertEqual(self.product_variant.total_available_qty, 80)

    def test_update_stock_on_po_completed_no_incoming(self):
        """Test that COMPLETED status does nothing when incoming_qty is already 0."""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.DELIVERED,
        )

        pvw = ProductVariantWarehouseFactory(
            product_variant=self.product_variant,
            warehouse=self.warehouse,
            company=self.company,
            incoming_qty=0,
            physical_qty=100,
        )
        self.product_variant.total_incoming_qty = 0
        self.product_variant.total_available_qty = 100
        self.product_variant.save()

        data = [
            {
                "product_variant_id": str(self.product_variant.id),
                "ordered_qty": 100,
                "received_qty": 100,
                "updated_qty": 100,
            }
        ]

        self.service.update_stock_on_po(
            po=po,
            new_status=PurchaseOrder.POStatus.COMPLETED,
            data=data,
        )

        pvw.refresh_from_db()
        self.assertEqual(pvw.incoming_qty, 0)
        self.assertEqual(pvw.physical_qty, 100)

        self.product_variant.refresh_from_db()
        self.assertEqual(self.product_variant.total_incoming_qty, 0)
        self.assertEqual(self.product_variant.total_available_qty, 100)


class InventoryServiceCOGSUpdateTest(TestCase):
    """Test cases for InventoryService.update_cogs_on_po method"""

    def setUp(self):
        self.company = CompanyFactory()
        self.warehouse = WarehouseFactory(company=self.company)
        self.category = CategoryFactory(company=self.company)
        self.product = ProductFactory(category=self.category, company=self.company)
        self.product_variant = ProductVariantFactory(product=self.product)
        self.service = InventoryService()

    def test_update_cogs_on_po_delivered_first_time(self):
        """Test COGS is created when PO status changes to DELIVERED (first time)."""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.SHIPPED,
            exchange_rate=2200,
        )

        initial_cogs_count = ProductCogs.objects.count()

        data = [
            {
                "product_variant_id": str(self.product_variant.id),
                "ordered_qty": 100,
                "received_qty": 100,
                "updated_qty": 0,
                "unit_price_foreign": Decimal("10"),
            }
        ]

        self.service.update_cogs_on_po(
            po=po,
            new_status=PurchaseOrder.POStatus.DELIVERED,
            data=data,
        )

        cogs = ProductCogs.objects.filter(
            product_variant=self.product_variant,
            warehouse=self.warehouse,
            reference_number=po.purchase_order_number,
        )
        self.assertEqual(cogs.count(), initial_cogs_count + 1)

        cogs_record = cogs.first()
        self.assertIsNotNone(cogs_record)
        self.assertEqual(cogs_record.price_rmb, Decimal("10.0000"))
        self.assertEqual(cogs_record.exchange_rate, 2200)
        self.assertEqual(cogs_record.cogs_amount, 22000)
        self.assertEqual(cogs_record.original_qty, 100)
        self.assertEqual(cogs_record.remaining_qty, 100)

    def test_update_cogs_on_po_fractional_exchange_rate_rounds_not_truncates(self):
        """MONEY-8 regression: a fractional PO exchange rate must survive into the FIFO
        layer rounded to 3dp, not truncated toward zero by int(). Before the fix,
        2200.678 collapsed to 2200 and corrupted the derived unit cost."""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.SHIPPED,
            exchange_rate=Decimal("2200.678"),
        )

        data = [
            {
                "product_variant_id": str(self.product_variant.id),
                "ordered_qty": 100,
                "received_qty": 100,
                "updated_qty": 0,
                "unit_price_foreign": Decimal("10"),
            }
        ]

        self.service.update_cogs_on_po(
            po=po,
            new_status=PurchaseOrder.POStatus.DELIVERED,
            data=data,
        )

        cogs_record = ProductCogs.objects.filter(
            product_variant=self.product_variant,
            warehouse=self.warehouse,
            reference_number=po.purchase_order_number,
        ).first()

        self.assertIsNotNone(cogs_record)
        self.assertEqual(cogs_record.exchange_rate, Decimal("2200.678"))
        # unit_price_idr = 10 * 2200.678 = 22006.78, floored to whole rupiah = 22006.
        # Truncating the rate to 2200 first would have produced 22000 instead.
        self.assertEqual(cogs_record.cogs_amount, 22006)

    def test_update_cogs_on_po_item_level_exchange_rate_override_is_decimal_safe(self):
        """An item-level exchange_rate override must be coerced decimal-safe and rounded
        half-up to 3dp too — not assumed int, so the field-type fix doesn't just move
        the truncation bug to a different input path."""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.SHIPPED,
            exchange_rate=Decimal("2200.000"),
        )

        data = [
            {
                "product_variant_id": str(self.product_variant.id),
                "ordered_qty": 100,
                "received_qty": 100,
                "updated_qty": 0,
                "unit_price_foreign": Decimal("10"),
                "exchange_rate": "2200.6789",
            }
        ]

        self.service.update_cogs_on_po(
            po=po,
            new_status=PurchaseOrder.POStatus.DELIVERED,
            data=data,
        )

        cogs_record = ProductCogs.objects.filter(
            product_variant=self.product_variant,
            warehouse=self.warehouse,
            reference_number=po.purchase_order_number,
        ).first()

        self.assertIsNotNone(cogs_record)
        # 2200.6789 rounds half-up to 3dp -> 2200.679, not truncated to 2200.
        self.assertEqual(cogs_record.exchange_rate, Decimal("2200.679"))
        self.assertEqual(cogs_record.cogs_amount, 22006)

    def test_update_cogs_on_po_with_discount(self):
        """Test COGS uses discounted_unit_price_foreign when provided."""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.SHIPPED,
            exchange_rate=2200,
        )

        data = [
            {
                "product_variant_id": str(self.product_variant.id),
                "ordered_qty": 100,
                "received_qty": 100,
                "updated_qty": 0,
                "unit_price_foreign": Decimal("10"),
                "discounted_unit_price_foreign": Decimal("8"),
            }
        ]

        self.service.update_cogs_on_po(
            po=po,
            new_status=PurchaseOrder.POStatus.DELIVERED,
            data=data,
        )

        cogs = ProductCogs.objects.filter(
            product_variant=self.product_variant,
            warehouse=self.warehouse,
            reference_number=po.purchase_order_number,
        ).first()

        self.assertIsNotNone(cogs)
        self.assertEqual(cogs.price_rmb, Decimal("8.0000"))
        self.assertEqual(cogs.cogs_amount, 17600)

    def test_update_cogs_on_po_partial_delivery(self):
        """Test COGS is created with partial received_qty."""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.SHIPPED,
            exchange_rate=2200,
        )

        data = [
            {
                "product_variant_id": str(self.product_variant.id),
                "ordered_qty": 100,
                "received_qty": 50,
                "updated_qty": 0,
                "unit_price_foreign": Decimal("10"),
            }
        ]

        self.service.update_cogs_on_po(
            po=po,
            new_status=PurchaseOrder.POStatus.DELIVERED,
            data=data,
        )

        cogs = ProductCogs.objects.filter(
            product_variant=self.product_variant,
            warehouse=self.warehouse,
            reference_number=po.purchase_order_number,
        ).first()

        self.assertIsNotNone(cogs)
        self.assertEqual(cogs.original_qty, 50)
        self.assertEqual(cogs.remaining_qty, 50)
        self.assertEqual(cogs.cogs_amount, 22000)

    def test_update_cogs_on_po_subsequent_delivery(self):
        """Test COGS is updated when received_qty increases on subsequent delivery."""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.SHIPPED,
            exchange_rate=2200,
        )

        ProductCogsFactory(
            company=self.company,
            product_variant=self.product_variant,
            warehouse=self.warehouse,
            reference_number=po.purchase_order_number,
            price_rmb=Decimal("10.0000"),
            exchange_rate=2200,
            cogs_amount=22000,
            original_qty=50,
            remaining_qty=50,
        )

        data = [
            {
                "product_variant_id": str(self.product_variant.id),
                "ordered_qty": 100,
                "received_qty": 80,
                "updated_qty": 50,
                "unit_price_foreign": Decimal("10"),
            }
        ]

        self.service.update_cogs_on_po(
            po=po,
            new_status=PurchaseOrder.POStatus.DELIVERED,
            data=data,
        )

        cogs = ProductCogs.objects.filter(
            product_variant=self.product_variant,
            warehouse=self.warehouse,
            reference_number=po.purchase_order_number,
        ).first()

        self.assertIsNotNone(cogs)
        self.assertEqual(cogs.original_qty, 80)
        self.assertEqual(cogs.remaining_qty, 80)

    def test_update_cogs_on_po_ordered_status_no_op(self):
        """Test that COGS is not affected when PO status is ORDERED."""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.DRAFT,
        )

        initial_cogs_count = ProductCogs.objects.count()

        data = [
            {
                "product_variant_id": str(self.product_variant.id),
                "ordered_qty": 100,
                "received_qty": 0,
                "updated_qty": 0,
            }
        ]

        self.service.update_cogs_on_po(
            po=po,
            new_status=PurchaseOrder.POStatus.ORDERED,
            data=data,
        )

        self.assertEqual(ProductCogs.objects.count(), initial_cogs_count)

    def test_update_cogs_on_po_with_empty_data(self):
        """Test that empty data does nothing."""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.SHIPPED,
        )

        self.service.update_cogs_on_po(
            po=po,
            new_status=PurchaseOrder.POStatus.DELIVERED,
            data=[],
        )

        self.assertEqual(ProductCogs.objects.count(), 0)

    def test_update_cogs_on_po_with_allocated_shipping_and_delivery_fees_single_item(self):
        """Test COGS includes allocated shipping, delivery, and commission fees for single item."""
        product = self.product_variant.product
        product.length = 10
        product.width = 10
        product.height = 10
        product.save()

        shipping_fee_per_cbm = 100000
        cbm = Decimal("0.01")
        shipping_fee = int(shipping_fee_per_cbm * cbm)

        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.SHIPPED,
            exchange_rate=2200,
            shipping_fee_per_cbm=shipping_fee_per_cbm,
            cbm=cbm,
            shipping_fee=shipping_fee,
            delivery_fee=Decimal("100.5"),
            commission_fee=22000,
            total_item_amount=220000,
        )

        data = [
            {
                "product_variant_id": str(self.product_variant.id),
                "ordered_qty": 10,
                "received_qty": 10,
                "updated_qty": 0,
                "unit_price_foreign": Decimal("10"),
                "discounted_total_price_base": 220000,
            }
        ]

        self.service.update_cogs_on_po(
            po=po,
            new_status=PurchaseOrder.POStatus.DELIVERED,
            data=data,
        )

        cogs = ProductCogs.objects.filter(
            product_variant=self.product_variant,
            warehouse=self.warehouse,
            reference_number=po.purchase_order_number,
        ).first()

        self.assertIsNotNone(cogs)
        self.assertEqual(cogs.original_qty, 10)
        self.assertEqual(cogs.remaining_qty, 10)

        unit_price_idr = 10 * 2200
        # Single item: all fees allocated to this item
        allocated_shipping = shipping_fee  # 1000
        allocated_delivery = int(Decimal("100.5") * 2200)  # 221100
        allocated_commission = 22000
        shipping_per_unit = allocated_shipping / 10
        delivery_per_unit = allocated_delivery / 10
        commission_per_unit = allocated_commission / 10
        expected_cogs = int(
            unit_price_idr + shipping_per_unit + delivery_per_unit + commission_per_unit
        )

        self.assertEqual(cogs.cogs_amount, expected_cogs)
        self.assertEqual(cogs.allocated_shipping_fee, allocated_shipping)
        self.assertEqual(cogs.allocated_delivery_fee, allocated_delivery)
        self.assertEqual(cogs.allocated_commission_fee, allocated_commission)

    def test_update_cogs_on_po_with_allocated_fees_multiple_items_same_volume(self):
        """Test COGS with multiple items sharing shipping fees equally when same LxWxH."""
        product1 = self.product_variant.product
        product1.length = 10
        product1.width = 10
        product1.height = 10
        product1.save()

        product2 = ProductFactory(
            category=self.category, company=self.company, length=10, width=10, height=10
        )
        product_variant2 = ProductVariantFactory(product=product2)
        ProductVariantWarehouseFactory(
            product_variant=product_variant2, warehouse=self.warehouse, company=self.company
        )

        shipping_fee_per_cbm = 100000
        cbm = Decimal("0.01")
        shipping_fee = int(shipping_fee_per_cbm * cbm)

        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.SHIPPED,
            exchange_rate=2200,
            shipping_fee_per_cbm=shipping_fee_per_cbm,
            cbm=cbm,
            shipping_fee=shipping_fee,
            delivery_fee=Decimal("0"),
        )

        data = [
            {
                "product_variant_id": str(self.product_variant.id),
                "ordered_qty": 10,
                "received_qty": 10,
                "updated_qty": 0,
                "unit_price_foreign": Decimal("10"),
            },
            {
                "product_variant_id": str(product_variant2.id),
                "ordered_qty": 10,
                "received_qty": 10,
                "updated_qty": 0,
                "unit_price_foreign": Decimal("20"),
            },
        ]

        self.service.update_cogs_on_po(
            po=po,
            new_status=PurchaseOrder.POStatus.DELIVERED,
            data=data,
        )

        cogs1 = ProductCogs.objects.filter(
            product_variant=self.product_variant,
            warehouse=self.warehouse,
            reference_number=po.purchase_order_number,
        ).first()
        cogs2 = ProductCogs.objects.filter(
            product_variant=product_variant2,
            warehouse=self.warehouse,
            reference_number=po.purchase_order_number,
        ).first()

        self.assertIsNotNone(cogs1)
        self.assertIsNotNone(cogs2)

        volume1 = Decimal("10") * Decimal("10") * Decimal("10") / Decimal("1000000") * Decimal("10")
        volume2 = Decimal("10") * Decimal("10") * Decimal("10") / Decimal("1000000") * Decimal("10")
        total_volume = volume1 + volume2

        allocated_shipping1 = int(shipping_fee * volume1 / total_volume)
        allocated_shipping2 = int(shipping_fee * volume2 / total_volume)

        expected_cogs1 = 10 * 2200 + allocated_shipping1 / 10
        expected_cogs2 = 20 * 2200 + allocated_shipping2 / 10

        self.assertEqual(cogs1.cogs_amount, expected_cogs1)
        self.assertEqual(cogs2.cogs_amount, expected_cogs2)
        self.assertEqual(cogs1.allocated_shipping_fee, allocated_shipping1)
        self.assertEqual(cogs2.allocated_shipping_fee, allocated_shipping2)

    def test_update_cogs_on_po_with_allocated_fees_multiple_items_different_volume(self):
        """Test COGS with multiple items where each takes portion of shipping based on volume."""
        product1 = self.product_variant.product
        product1.length = 10
        product1.width = 10
        product1.height = 10
        product1.save()

        product2 = ProductFactory(
            category=self.category, company=self.company, length=20, width=20, height=20
        )
        product_variant2 = ProductVariantFactory(product=product2)
        ProductVariantWarehouseFactory(
            product_variant=product_variant2, warehouse=self.warehouse, company=self.company
        )

        shipping_fee_per_cbm = 100000
        cbm = Decimal("0.09")
        shipping_fee = int(shipping_fee_per_cbm * cbm)

        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.SHIPPED,
            exchange_rate=2200,
            shipping_fee_per_cbm=shipping_fee_per_cbm,
            cbm=cbm,
            shipping_fee=shipping_fee,
            delivery_fee=Decimal("0"),
        )

        data = [
            {
                "product_variant_id": str(self.product_variant.id),
                "ordered_qty": 10,
                "received_qty": 10,
                "updated_qty": 0,
                "unit_price_foreign": Decimal("10"),
            },
            {
                "product_variant_id": str(product_variant2.id),
                "ordered_qty": 10,
                "received_qty": 10,
                "updated_qty": 0,
                "unit_price_foreign": Decimal("20"),
            },
        ]

        self.service.update_cogs_on_po(
            po=po,
            new_status=PurchaseOrder.POStatus.DELIVERED,
            data=data,
        )

        cogs1 = ProductCogs.objects.filter(
            product_variant=self.product_variant,
            warehouse=self.warehouse,
            reference_number=po.purchase_order_number,
        ).first()
        cogs2 = ProductCogs.objects.filter(
            product_variant=product_variant2,
            warehouse=self.warehouse,
            reference_number=po.purchase_order_number,
        ).first()

        self.assertIsNotNone(cogs1)
        self.assertIsNotNone(cogs2)

        volume1 = Decimal("10") * Decimal("10") * Decimal("10") / Decimal("1000000") * Decimal("10")
        volume2 = Decimal("20") * Decimal("20") * Decimal("20") / Decimal("1000000") * Decimal("10")
        total_volume = volume1 + volume2

        allocated_shipping1 = int(shipping_fee * volume1 / total_volume)
        allocated_shipping2 = int(shipping_fee * volume2 / total_volume)

        expected_cogs1 = 10 * 2200 + allocated_shipping1 / 10
        expected_cogs2 = 20 * 2200 + allocated_shipping2 / 10

        self.assertEqual(cogs1.cogs_amount, expected_cogs1)
        self.assertEqual(cogs2.cogs_amount, expected_cogs2)
        self.assertEqual(cogs1.allocated_shipping_fee, allocated_shipping1)
        self.assertEqual(cogs2.allocated_shipping_fee, allocated_shipping2)

    def test_update_cogs_on_po_received_qty_decreases(self):
        """Test COGS is updated when received_qty decreases."""
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.SHIPPED,
            exchange_rate=2200,
        )

        ProductCogsFactory(
            company=self.company,
            product_variant=self.product_variant,
            warehouse=self.warehouse,
            reference_number=po.purchase_order_number,
            price_rmb=Decimal("10.0000"),
            exchange_rate=2200,
            cogs_amount=22000,
            original_qty=50,
            remaining_qty=50,
        )

        data = [
            {
                "product_variant_id": str(self.product_variant.id),
                "ordered_qty": 100,
                "received_qty": 30,
                "updated_qty": 50,
                "unit_price_foreign": Decimal("10"),
            }
        ]

        self.service.update_cogs_on_po(
            po=po,
            new_status=PurchaseOrder.POStatus.DELIVERED,
            data=data,
        )

        cogs = ProductCogs.objects.filter(
            product_variant=self.product_variant,
            warehouse=self.warehouse,
            reference_number=po.purchase_order_number,
        ).first()

        self.assertIsNotNone(cogs)
        self.assertEqual(cogs.original_qty, 30)
        self.assertEqual(cogs.remaining_qty, 30)

    def test_cogs_cbm_proportional_shipping(self):
        """2 items with different CBM: shipping allocated proportionally by CBM, delivery+commission by value."""
        product1 = self.product_variant.product
        product1.length = 10
        product1.width = 10
        product1.height = 10
        product1.save()

        product2 = ProductFactory(
            category=self.category, company=self.company, length=20, width=20, height=20
        )
        product_variant2 = ProductVariantFactory(product=product2)
        ProductVariantWarehouseFactory(
            product_variant=product_variant2, warehouse=self.warehouse, company=self.company
        )

        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.SHIPPED,
            exchange_rate=2200,
            shipping_fee=10000,
            shipping_fee_per_cbm=100000,
            cbm=Decimal("0.09"),
            delivery_fee=Decimal("50"),
            commission_fee=22000,
            total_item_amount=660000,
        )

        data = [
            {
                "product_variant_id": str(self.product_variant.id),
                "ordered_qty": 10,
                "received_qty": 10,
                "updated_qty": 0,
                "unit_price_foreign": Decimal("10"),
                "discounted_total_price_base": 220000,
            },
            {
                "product_variant_id": str(product_variant2.id),
                "ordered_qty": 10,
                "received_qty": 10,
                "updated_qty": 0,
                "unit_price_foreign": Decimal("20"),
                "discounted_total_price_base": 440000,
            },
        ]

        self.service.update_cogs_on_po(
            po=po,
            new_status=PurchaseOrder.POStatus.DELIVERED,
            data=data,
        )

        cogs1 = ProductCogs.objects.filter(
            product_variant=self.product_variant,
            warehouse=self.warehouse,
            reference_number=po.purchase_order_number,
        ).first()
        cogs2 = ProductCogs.objects.filter(
            product_variant=product_variant2,
            warehouse=self.warehouse,
            reference_number=po.purchase_order_number,
        ).first()

        self.assertIsNotNone(cogs1)
        self.assertIsNotNone(cogs2)

        # CBM: item1 = 10*10*10/1e6 * 10 = 0.01, item2 = 20*20*20/1e6 * 10 = 0.08, total = 0.09
        cbm1 = Decimal("10") * Decimal("10") * Decimal("10") / Decimal("1000000") * Decimal("10")
        cbm2 = Decimal("20") * Decimal("20") * Decimal("20") / Decimal("1000000") * Decimal("10")
        total_cbm = cbm1 + cbm2

        # Shipping: CBM-proportional
        expected_shipping1 = int(round(10000 * cbm1 / total_cbm))
        expected_shipping2 = int(round(10000 * cbm2 / total_cbm))
        self.assertEqual(cogs1.allocated_shipping_fee, expected_shipping1)
        self.assertEqual(cogs2.allocated_shipping_fee, expected_shipping2)

        # Delivery: value-proportional
        total_delivery_idr = Decimal("50") * 2200
        value_ratio1 = Decimal("220000") / Decimal("660000")
        value_ratio2 = Decimal("440000") / Decimal("660000")
        expected_delivery1 = int(round(total_delivery_idr * value_ratio1))
        expected_delivery2 = int(round(total_delivery_idr * value_ratio2))
        self.assertEqual(cogs1.allocated_delivery_fee, expected_delivery1)
        self.assertEqual(cogs2.allocated_delivery_fee, expected_delivery2)

        # Commission: value-proportional
        expected_commission1 = int(round(Decimal("22000") * value_ratio1))
        expected_commission2 = int(round(Decimal("22000") * value_ratio2))
        self.assertEqual(cogs1.allocated_commission_fee, expected_commission1)
        self.assertEqual(cogs2.allocated_commission_fee, expected_commission2)

    def test_cogs_value_proportional_delivery_and_commission(self):
        """2 items with different values: delivery_fee and commission allocated proportionally by item value."""
        product1 = self.product_variant.product
        product1.length = 10
        product1.width = 10
        product1.height = 10
        product1.save()

        product2 = ProductFactory(
            category=self.category, company=self.company, length=10, width=10, height=10
        )
        product_variant2 = ProductVariantFactory(product=product2)
        ProductVariantWarehouseFactory(
            product_variant=product_variant2, warehouse=self.warehouse, company=self.company
        )

        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.SHIPPED,
            exchange_rate=2200,
            shipping_fee=5000,
            shipping_fee_per_cbm=100000,
            cbm=Decimal("0.02"),
            delivery_fee=Decimal("100"),
            commission_fee=33000,
            total_item_amount=660000,
        )

        data = [
            {
                "product_variant_id": str(self.product_variant.id),
                "ordered_qty": 10,
                "received_qty": 10,
                "updated_qty": 0,
                "unit_price_foreign": Decimal("10"),
                "discounted_total_price_base": 220000,
            },
            {
                "product_variant_id": str(product_variant2.id),
                "ordered_qty": 10,
                "received_qty": 10,
                "updated_qty": 0,
                "unit_price_foreign": Decimal("20"),
                "discounted_total_price_base": 440000,
            },
        ]

        self.service.update_cogs_on_po(
            po=po,
            new_status=PurchaseOrder.POStatus.DELIVERED,
            data=data,
        )

        cogs1 = ProductCogs.objects.filter(
            product_variant=self.product_variant,
            warehouse=self.warehouse,
            reference_number=po.purchase_order_number,
        ).first()
        cogs2 = ProductCogs.objects.filter(
            product_variant=product_variant2,
            warehouse=self.warehouse,
            reference_number=po.purchase_order_number,
        ).first()

        self.assertIsNotNone(cogs1)
        self.assertIsNotNone(cogs2)

        # Same CBM, so shipping is split equally
        self.assertEqual(cogs1.allocated_shipping_fee, 2500)
        self.assertEqual(cogs2.allocated_shipping_fee, 2500)

        # Delivery fee: value-proportional
        total_delivery_idr = Decimal("100") * 2200
        expected_delivery1 = int(round(total_delivery_idr * Decimal("220000") / Decimal("660000")))
        expected_delivery2 = int(round(total_delivery_idr * Decimal("440000") / Decimal("660000")))
        self.assertEqual(cogs1.allocated_delivery_fee, expected_delivery1)
        self.assertEqual(cogs2.allocated_delivery_fee, expected_delivery2)

        # Commission: value-proportional
        expected_commission1 = int(round(Decimal("33000") * Decimal("220000") / Decimal("660000")))
        expected_commission2 = int(round(Decimal("33000") * Decimal("440000") / Decimal("660000")))
        self.assertEqual(cogs1.allocated_commission_fee, expected_commission1)
        self.assertEqual(cogs2.allocated_commission_fee, expected_commission2)

    def test_cogs_amount_includes_commission(self):
        """cogs_amount = unit_price_idr + shipping_per_unit + delivery_per_unit + commission_per_unit."""
        product = self.product_variant.product
        product.length = 10
        product.width = 10
        product.height = 10
        product.save()

        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.SHIPPED,
            exchange_rate=2200,
            shipping_fee=1000,
            shipping_fee_per_cbm=100000,
            cbm=Decimal("0.01"),
            delivery_fee=Decimal("50"),
            commission_fee=22000,
            total_item_amount=220000,
        )

        data = [
            {
                "product_variant_id": str(self.product_variant.id),
                "ordered_qty": 10,
                "received_qty": 10,
                "updated_qty": 0,
                "unit_price_foreign": Decimal("10"),
                "discounted_total_price_base": 220000,
            },
        ]

        self.service.update_cogs_on_po(
            po=po,
            new_status=PurchaseOrder.POStatus.DELIVERED,
            data=data,
        )

        cogs = ProductCogs.objects.filter(
            product_variant=self.product_variant,
            warehouse=self.warehouse,
            reference_number=po.purchase_order_number,
        ).first()

        self.assertIsNotNone(cogs)

        unit_price_idr = Decimal("10") * 2200  # 22000
        shipping_per_unit = Decimal(str(cogs.allocated_shipping_fee)) / Decimal("10")
        delivery_per_unit = Decimal(str(cogs.allocated_delivery_fee)) / Decimal("10")
        commission_per_unit = Decimal(str(cogs.allocated_commission_fee)) / Decimal("10")
        expected_cogs = int(
            unit_price_idr + shipping_per_unit + delivery_per_unit + commission_per_unit
        )

        self.assertEqual(cogs.cogs_amount, expected_cogs)

    def test_cogs_shipping_allocation_sums_exactly_across_three_equal_volume_items(self):
        """3 equal-volume items: allocated shipping fees sum to the total, not 1 rupiah short."""
        product1 = self.product_variant.product
        product1.length = 10
        product1.width = 10
        product1.height = 10
        product1.save()

        product2 = ProductFactory(
            category=self.category, company=self.company, length=10, width=10, height=10
        )
        product_variant2 = ProductVariantFactory(product=product2)
        ProductVariantWarehouseFactory(
            product_variant=product_variant2, warehouse=self.warehouse, company=self.company
        )

        product3 = ProductFactory(
            category=self.category, company=self.company, length=10, width=10, height=10
        )
        product_variant3 = ProductVariantFactory(product=product3)
        ProductVariantWarehouseFactory(
            product_variant=product_variant3, warehouse=self.warehouse, company=self.company
        )

        # Naive per-item rounding (int(round(1_000_000 * 1/3))) gives 333,333 x 3 = 999,999.
        shipping_fee = 1_000_000

        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.SHIPPED,
            exchange_rate=2200,
            shipping_fee=shipping_fee,
            shipping_fee_per_cbm=100000,
            cbm=Decimal("0.03"),
            delivery_fee=Decimal("0"),
        )

        data = [
            {
                "product_variant_id": str(self.product_variant.id),
                "ordered_qty": 10,
                "received_qty": 10,
                "updated_qty": 0,
                "unit_price_foreign": Decimal("10"),
            },
            {
                "product_variant_id": str(product_variant2.id),
                "ordered_qty": 10,
                "received_qty": 10,
                "updated_qty": 0,
                "unit_price_foreign": Decimal("10"),
            },
            {
                "product_variant_id": str(product_variant3.id),
                "ordered_qty": 10,
                "received_qty": 10,
                "updated_qty": 0,
                "unit_price_foreign": Decimal("10"),
            },
        ]

        self.service.update_cogs_on_po(
            po=po,
            new_status=PurchaseOrder.POStatus.DELIVERED,
            data=data,
        )

        allocated_total = sum(
            ProductCogs.objects.filter(
                warehouse=self.warehouse,
                reference_number=po.purchase_order_number,
            ).values_list("allocated_shipping_fee", flat=True)
        )

        self.assertEqual(allocated_total, shipping_fee)


class AllocateLargestRemainderTest(TestCase):
    """Test cases for the shared allocate_largest_remainder fee-allocation helper."""

    def test_splits_total_across_three_equal_weights_without_losing_a_unit(self):
        """Rp 1,000,000 across 3 equal-weight items sums to exactly 1,000,000, not 999,999."""
        weights = [Decimal("1"), Decimal("1"), Decimal("1")]

        shares = allocate_largest_remainder(1_000_000, weights)

        self.assertEqual(shares, [333334, 333333, 333333])
        self.assertEqual(sum(shares), 1_000_000)

    def test_naive_rounding_loses_a_unit_but_helper_preserves_the_total(self):
        """Naive int(round(...)) per item loses 1 rupiah; helper does not."""
        weights = [Decimal("1"), Decimal("1"), Decimal("1")]
        total = 1_000_000
        weight_sum = sum(weights)

        naive_shares = [int(round(total * weight / weight_sum)) for weight in weights]
        helper_shares = allocate_largest_remainder(total, weights)

        self.assertEqual(sum(naive_shares), 999_999)
        self.assertEqual(sum(helper_shares), total)

    def test_splits_total_across_seven_equal_weights_sums_exactly(self):
        """A total that does not divide evenly across 7 equal weights still sums to the total."""
        weights = [Decimal("1")] * 7

        shares = allocate_largest_remainder(100, weights)

        self.assertEqual(len(shares), 7)
        self.assertEqual(sum(shares), 100)

    def test_allocates_proportionally_to_unequal_weights(self):
        """Shares stay proportional to unequal weights while still summing to the total exactly."""
        weights = [Decimal("1"), Decimal("2"), Decimal("7")]

        shares = allocate_largest_remainder(100, weights)

        self.assertEqual(shares, [10, 20, 70])
        self.assertEqual(sum(shares), 100)

    def test_returns_zero_shares_when_weights_sum_to_zero(self):
        """A zero-weight denominator keeps every share at zero instead of dividing by zero."""
        weights = [Decimal("0"), Decimal("0")]

        shares = allocate_largest_remainder(500, weights)

        self.assertEqual(shares, [0, 0])

    def test_returns_empty_list_for_no_weights(self):
        """No weights to allocate across returns no shares."""
        shares = allocate_largest_remainder(500, [])

        self.assertEqual(shares, [])


class EdgeCaseInventoryTests(TestCase):
    """Tests for edge case fixes in inventory."""

    def setUp(self):
        from django.core.exceptions import ValidationError

        self.ValidationError = ValidationError
        self.company = CompanyFactory()
        self.warehouse = WarehouseFactory(company=self.company)
        self.category = CategoryFactory(company=self.company)
        self.product = ProductFactory(category=self.category, company=self.company)
        self.product_variant = ProductVariantFactory(product=self.product)
        self.service = InventoryService()

    # Fix 2: Negative stock guard
    def test_outbound_insufficient_stock_raises_error(self):
        from apps.inventory.models import StockMovement

        ProductVariantWarehouseFactory(
            product_variant=self.product_variant,
            warehouse=self.warehouse,
            company=self.company,
            physical_qty=5,
        )
        self.product_variant.total_available_qty = 5
        self.product_variant.save()

        with self.assertRaises(self.ValidationError) as ctx:
            self.service.record_single_stock_movement(
                variant_id=self.product_variant.id,
                warehouse_id=self.warehouse.id,
                qty=10,
                movement_type=StockMovement.MovementType.OUTBOUND,
            )
        self.assertIn("Insufficient stock", str(ctx.exception))

    def test_outbound_sufficient_stock_succeeds(self):
        from apps.inventory.models import StockMovement

        pvw = ProductVariantWarehouseFactory(
            product_variant=self.product_variant,
            warehouse=self.warehouse,
            company=self.company,
            physical_qty=10,
        )
        self.product_variant.total_available_qty = 10
        self.product_variant.save()

        self.service.record_single_stock_movement(
            variant_id=self.product_variant.id,
            warehouse_id=self.warehouse.id,
            qty=5,
            movement_type=StockMovement.MovementType.OUTBOUND,
        )
        pvw.refresh_from_db()
        self.assertEqual(pvw.physical_qty, 5)

    # Fix 9: COGS remaining_qty cannot go negative
    def test_cogs_remaining_qty_negative_raises_error(self):
        po = PurchaseOrderFactory(
            warehouse=self.warehouse,
            company=self.company,
            status=PurchaseOrder.POStatus.SHIPPED,
            exchange_rate=2200,
        )
        # Create COGS with remaining_qty=5 (some already sold)
        ProductCogsFactory(
            company=self.company,
            product_variant=self.product_variant,
            warehouse=self.warehouse,
            reference_number=po.purchase_order_number,
            price_rmb=Decimal("10.0000"),
            exchange_rate=2200,
            cogs_amount=22000,
            original_qty=50,
            remaining_qty=5,
        )

        # Try to decrease received_qty by 10 (but only 5 remaining)
        data = [
            {
                "product_variant_id": str(self.product_variant.id),
                "ordered_qty": 100,
                "received_qty": 40,
                "updated_qty": 50,
                "unit_price_foreign": Decimal("10"),
            }
        ]

        with self.assertRaises(self.ValidationError) as ctx:
            self.service.update_cogs_on_po(
                po=po,
                new_status=PurchaseOrder.POStatus.DELIVERED,
                data=data,
            )
        self.assertIn("already sold", str(ctx.exception))


class AdjustStockBatchServiceTest(TestCase):
    """Tests for InventoryService.adjust_stock_batch"""

    def setUp(self):
        self.company = CompanyFactory()
        self.warehouse = WarehouseFactory(company=self.company)
        self.product_variant = ProductVariantFactory(company=self.company)
        self.pvw = ProductVariantWarehouseFactory(
            product_variant=self.product_variant,
            warehouse=self.warehouse,
            company=self.company,
            physical_qty=10,
        )
        self.service = InventoryService()

    def test_add_increases_stock(self):
        result = self.service.adjust_stock_batch(
            [
                {
                    "variant_id": str(self.product_variant.id),
                    "warehouse_id": str(self.warehouse.id),
                    "type": "add",
                    "qty": 5,
                }
            ]
        )
        self.pvw.refresh_from_db()
        self.assertEqual(self.pvw.physical_qty, 15)
        self.assertEqual(result["results"][0]["old_qty"], 10)
        self.assertEqual(result["results"][0]["new_qty"], 15)

    def test_min_decreases_stock(self):
        result = self.service.adjust_stock_batch(
            [
                {
                    "variant_id": str(self.product_variant.id),
                    "warehouse_id": str(self.warehouse.id),
                    "type": "min",
                    "qty": 3,
                }
            ]
        )
        self.pvw.refresh_from_db()
        self.assertEqual(self.pvw.physical_qty, 7)
        self.assertEqual(result["results"][0]["old_qty"], 10)
        self.assertEqual(result["results"][0]["new_qty"], 7)

    def test_set_sets_absolute_qty(self):
        result = self.service.adjust_stock_batch(
            [
                {
                    "variant_id": str(self.product_variant.id),
                    "warehouse_id": str(self.warehouse.id),
                    "type": "set",
                    "qty": 20,
                }
            ]
        )
        self.pvw.refresh_from_db()
        self.assertEqual(self.pvw.physical_qty, 20)
        self.assertEqual(result["results"][0]["old_qty"], 10)
        self.assertEqual(result["results"][0]["new_qty"], 20)

    def test_set_to_zero(self):
        result = self.service.adjust_stock_batch(
            [
                {
                    "variant_id": str(self.product_variant.id),
                    "warehouse_id": str(self.warehouse.id),
                    "type": "set",
                    "qty": 0,
                }
            ]
        )
        self.pvw.refresh_from_db()
        self.assertEqual(self.pvw.physical_qty, 0)
        self.assertEqual(result["results"][0]["new_qty"], 0)

    def test_min_insufficient_stock_returns_error(self):
        result = self.service.adjust_stock_batch(
            [
                {
                    "variant_id": str(self.product_variant.id),
                    "warehouse_id": str(self.warehouse.id),
                    "type": "min",
                    "qty": 15,
                }
            ]
        )
        self.assertEqual(len(result["errors"]), 1)
        self.pvw.refresh_from_db()
        self.assertEqual(self.pvw.physical_qty, 10)

    def test_set_negative_rejected(self):
        result = self.service.adjust_stock_batch(
            [
                {
                    "variant_id": str(self.product_variant.id),
                    "warehouse_id": str(self.warehouse.id),
                    "type": "set",
                    "qty": -1,
                }
            ]
        )
        self.assertEqual(len(result["errors"]), 1)

    def test_warehouse_ulid_id_no_crash(self):
        result = self.service.adjust_stock_batch(
            [
                {
                    "variant_id": str(self.product_variant.id),
                    "warehouse_id": str(self.warehouse.id),
                    "type": "add",
                    "qty": 1,
                }
            ]
        )
        self.pvw.refresh_from_db()
        self.assertEqual(self.pvw.physical_qty, 11)
        self.assertEqual(len(result["results"]), 1)

    def test_stock_movement_created_on_add(self):
        self.service.adjust_stock_batch(
            [
                {
                    "variant_id": str(self.product_variant.id),
                    "warehouse_id": str(self.warehouse.id),
                    "type": "add",
                    "qty": 5,
                }
            ]
        )
        movement = StockMovement.objects.last()
        self.assertIsNotNone(movement)
        self.assertEqual(movement.movement_type, StockMovement.MovementType.ADJUSTMENT)
        self.assertEqual(movement.quantity, 5)

    def test_stock_movement_created_on_min(self):
        self.service.adjust_stock_batch(
            [
                {
                    "variant_id": str(self.product_variant.id),
                    "warehouse_id": str(self.warehouse.id),
                    "type": "min",
                    "qty": 3,
                }
            ]
        )
        movement = StockMovement.objects.last()
        self.assertIsNotNone(movement)
        self.assertEqual(movement.movement_type, StockMovement.MovementType.ADJUSTMENT)
        self.assertEqual(movement.quantity, -3)
