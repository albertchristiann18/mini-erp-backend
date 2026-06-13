# 🧾 mini-ERP — Inventory & FIFO COGS Backend

A simple **backend project** that tracks stock and calculates **COGS using FIFO**.  
Built with **Django**, **PostgreSQL**, and **uv** (fast Python environment manager).  
Designed for clean setup and easy onboarding for new contributors.

---

## 🧰 Prerequisites

Make sure these are installed before you start:

| Tool | Purpose | Link |
|------|----------|------|
| **Git** | Clone the repository | [git-scm.com](https://git-scm.com/) |
| **Docker Desktop** | Container runtime & Database container | [docker.com](https://www.docker.com/products/docker-desktop) |
| **uv** | Python dependency & environment manager | [uv (Astral)](https://github.com/astral-sh/uv) |
| **VS Code** *(recommended)* | Editor with Python/Django extensions | [code.visualstudio.com](https://code.visualstudio.com/) |
| **Minikube** | Local Kubernetes environment | [minikube.sigs.k8s.io](https://minikube.sigs.k8s.io/docs/start/) |
| **kubectl** | Command line tool for Kubernetes | [kubernetes.io/docs/reference/kubectl/](https://kubernetes.io/docs/reference/kubectl/) |
---

## ⚙️ Setup (Development)

### 1️⃣ Create your `.env` file

Copy the example file:

```bash
cp .env.example .env
```

Your `.env` file should look similar to:

```
# Django
DJANGO_SETTINGS_MODULE=core.settings
SECRET_KEY=dev-change-me
DEBUG=true
ALLOWED_HOSTS=127.0.0.1,localhost

# Database (LOCAL Django → Docker Postgres)
DB_NAME=mini_erp
DB_USER=postgres
DB_PASSWORD=postgres
DB_HOST=localhost
DB_PORT=5433

# Initial superuser (auto-created by migrate service)
DJANGO_SUPERUSER_USERNAME=admin
DJANGO_SUPERUSER_EMAIL=admin@example.com
DJANGO_SUPERUSER_PASSWORD=changeme123
```

---

### 2️⃣ Start PostgreSQL via Docker

```bash
docker compose up -d
```

This will:

* Start the **Postgres** container
* Run the **migrate** service
* Apply all Django migrations
* Automatically create a superuser
  (based on values inside `.env`)

You do **NOT** need to run migrations manually.

You can confirm everything ran:

```bash
docker compose logs migrate
```

---

### 3️⃣ Install Python dependencies with uv

```bash
uv sync
```

This will:

* Create a `.venv/`
* Install all project dependencies
* Make the environment ready

---

### 4️⃣ Run the backend (Django) locally

```bash
uv run manage.py runserver
```

Backend URL:
👉 [http://127.0.0.1:8000/](http://127.0.0.1:8000/)

Admin panel:
👉 [http://127.0.0.1:8000/admin/](http://127.0.0.1:8000/admin/)
(**login using the auto-created superuser from `.env`**)

---

## 📋 Database Migrations

Migrations are automatically applied when starting PostgreSQL via Docker. However, if you need to run migrations manually:

### Run All Pending Migrations for All Apps

Create migrations for all changed models across all apps:

```bash
uv run manage.py makemigrations
```

Apply all pending migrations:

```bash
uv run manage.py migrate
```

### Check Migration Status

```bash
uv run manage.py showmigrations
```

### Create a New Migration for a Specific App

After modifying models in a specific app, create migrations only for that app:

```bash
uv run manage.py makemigrations <app_name>
```

For example, to create migrations for the inventory app:

```bash
uv run manage.py makemigrations inventory
```

### Create an Empty Migration File

Sometimes you need to create an empty migration file to add custom SQL or data migrations. Use the `--empty` flag with an optional `--name` to specify a descriptive name:

```bash
uv run manage.py makemigrations <app_name> --empty --name <migration_name>
```

**Example: Create an empty migration for adding a database trigger in the inventory app**

```bash
uv run manage.py makemigrations inventory --empty --name add_sku_sequence_and_triggers
```

This will create a new migration file like `apps/inventory/migrations/0002_add_sku_sequence_and_triggers.py` with empty `operations` list where you can add custom SQL:

```python
from django.db import migrations

class Migration(migrations.Migration):
    dependencies = [
        ('inventory', '0001_initial'),
    ]

    operations = [
        migrations.RunSQL(
            sql="CREATE SEQUENCE product_sku_seq START WITH 1;",
            reverse_sql="DROP SEQUENCE product_sku_seq;"
        ),
        # Add more operations here
    ]
```


---

This section details how to deploy the application into a local Kubernetes cluster (Minikube) using pre-built manifests and the included deployment script.

### 1️⃣ Ensure Minikube is Running

Before proceeding, make sure Minikube is started:

```bash
minikube start
```

### 2️⃣ Grant Execute Permission to the Deployment Script

The `local_deploy.sh` script handles building the Docker image, setting the Minikube context, creating the Kubernetes Secret, and deploying all services.

```bash
chmod +x local_deploy.sh
```

### 3️⃣ Run the Kubernetes Deployment

This single script command performs the entire build and deployment process:

```bash
./local_deploy.sh
```

**What this script does:**

1.  Sets your local Docker client to build inside the Minikube VM's image registry.
2.  Builds the optimized multi-stage Docker image (`mini-erp-uv:v1`).
3.  Creates a Kubernetes Secret (`mini-erp-db-secret`) from your `.env` file.
4.  Deploys the PostgreSQL database (via `k8s/postgres.yaml`).
5.  Deploys the Django application (via `k8s/django-app.yaml`).
      * **Crucially:** The application container runs the `entrypoint-k8s.sh` script, which automatically **waits for the database**, **runs migrations**, **creates the superuser**, and **starts the Gunicorn server**.

### 4️⃣ Access the Application

Once the script finishes, it will provide the final access URL. If you need to retrieve it manually:

```bash
minikube service django-app-service --url
```

-----


### Run Unit Tests

There are two ways to run unit tests:

#### 1. Locally (using local database)
```bash
uv run pytest
```

#### 2. Using Docker (isolated environment)
```bash
docker compose -f docker-compose.test.yml run --rm test uv run pytest --reuse-db
```

### standard for datetime format
ISO 8601
2026-03-03T10:49:31Z

## User Roles

Every user has a `UserProfile` that assigns them a **role** within their company. The role controls what parts of the system they can access.

| Role | Key | Description |
|---|---|---|
| **Admin** | `admin` | Full access — manage products, orders, stock, finance, and users |
| **Customer Service** | `cs` | Create and manage sales orders; view products and stock |
| **Warehouse** | `warehouse` | Receive purchase orders, record stock movements, view inventory |
| **Finance** | `finance` | View and record payments on payables and receivables |
| **Viewer** | `viewer` | Read-only access across all modules; cannot create or edit anything |

### Creating a user

Use the Makefile target to create a new user with an isolated company and a randomly generated password:

```bash
make create-user username=albert
# With a specific role (default is admin):
make create-user username=albert role=viewer
```

The command prints the generated password once — save it before closing the terminal.

### Available roles

```
admin     — full access
cs        — sales orders
warehouse — stock & purchasing
finance   — payables & receivables
viewer    — read-only
```

---

## remove .venv
this for handling when we got error when we want to do `uv sync`

```bash
rm -Recurse -Force .venv
```