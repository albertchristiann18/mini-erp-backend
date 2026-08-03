#!/usr/bin/env bash
set -euo pipefail

if [ "${1:-}" = "k8s" ]; then
    echo "Handing off to K8s entrypoint..."
    exec /app/entrypoint-k8s.sh
else
    echo "Starting application with CMD: $@"
fi

echo "Applying migrations..."
uv run manage.py migrate --noinput

echo "Creating superuser if missing..."
uv run manage.py shell -c "
import os
from django.contrib.auth import get_user_model

u = os.getenv('DJANGO_SUPERUSER_USERNAME')
e = os.getenv('DJANGO_SUPERUSER_EMAIL')
p = os.getenv('DJANGO_SUPERUSER_PASSWORD')

if u and e and p:
    User = get_user_model()
    if not User.objects.filter(username=u).exists():
        User.objects.create_superuser(username=u, email=e, password=p)
        print(f'Superuser {u!r} created.')
        
        from core.models import Company, UserProfile
        company, _ = Company.objects.get_or_create(name='Test Company', defaults={'is_active': True})
        UserProfile.objects.create(user=User.objects.get(username=u), company=company, role='admin')
        print(f'superuser profile created.')
    else:
        print(f'Superuser {u!r} already exists.')
else:
    print('DJANGO_SUPERUSER_* not fully set; skipping superuser creation.')
"

echo "creating dummy data"
uv run manage.py shell -c "
from apps.inventory.models import Category, Warehouse
from apps.marketplace.models import Marketplace
from core.models import Company
from django.utils import timezone

# 1. Ensure Company exists
company, _ = Company.objects.get_or_create(
    name='Test Company', 
    defaults={'is_active': True}
)

# 2. Create Categories
categories = [
    {'name': 'Setelan', 'code': 'SEG'},
    {'name': 'Dress', 'code': 'DRS'},
    {'name': 'Pants', 'code': 'PNG'}
]
for cat in categories:
    obj, created = Category.objects.get_or_create(
        category_code=cat['code'],
        defaults={'name': cat['name'], 'company': company}
    )
    if created: print(f'Category created: {obj.category_code}')

# 3. Create Marketplaces
marketplaces = [
    {'name': 'Shopee', 'url': 'https://shopee.co.id', 'status': 'Connected'},
    {'name': 'Tokopedia & TikTok Shop', 'url': '', 'status': 'Pending'}
]

for mp in marketplaces:
    obj, created = Marketplace.objects.get_or_create(
        name=mp['name'],
        defaults={
            'url': mp['url'],
            'status': mp['status'],
            'is_active': True,
            'connected_time': timezone.now() if mp['status'] == 'Connected' else None
        }
    )
    if created: 
        print(f'Marketplace created: {obj.name} (ID: {obj.id})')
    else:
        print(f'Marketplace existing: {obj.name} (ID: {obj.id})')

# 4. Create Warehouses (NEW)
warehouses = [
    {
        'name': 'Master Warehouse', 
        'address': 'Jl. Kemanggisan No. 10, Jakarta Barat',
        'is_marketplace_visible': True
    },
    {
        'name': 'Gudang Operasional', 
        'address': 'Jl. Rungkut Industri No. 5, Surabaya',
        'is_marketplace_visible': False
    }
]

for wh in warehouses:
    obj, created = Warehouse.objects.get_or_create(
        company=company,
        name=wh['name'],
        defaults={
            'address': wh['address'],
            'is_marketplace_visible': wh['is_marketplace_visible'],
            'is_active': True
        }
    )
    if created: 
        print(f'Warehouse created: {obj.name} (ID: {obj.id})')
    else:
        print(f'Warehouse existing: {obj.name} (ID: {obj.id})')
"