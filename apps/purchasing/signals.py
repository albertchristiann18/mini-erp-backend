from django.db.models.signals import pre_delete
from django.dispatch import receiver

from apps.inventory.models import ProductVariant
from apps.purchasing.models import SourcingPoolItem


@receiver(pre_delete, sender=ProductVariant)
def clear_sourcing_pool_variant_code_on_variant_delete(
    sender: type[ProductVariant], instance: ProductVariant, **kwargs: object
) -> None:
    # Must use pre_delete (not post_delete): after SET_NULL runs, variant_id is already
    # NULL so we can no longer identify which items to clear. Here variant_id is still set.
    SourcingPoolItem.objects.filter(variant_id=instance.pk).update(variant_code=None)
