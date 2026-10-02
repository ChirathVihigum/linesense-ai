# LineSense AI — Local Run Guide (Windows & PowerShell)

This guide provides step-by-step instructions for running the **LineSense AI** decision-support system locally on Windows using PowerShell.

---

## Architecture Overview

LineSense AI requires four core processes running concurrently:
1. **Development OIDC Identity Provider (`dev_oidc`)**: Port `8090`
2. **FastAPI Backend (`api`)**: Port `8000`
3. **Durable Worker (`jobs`)**: Background orchestrator for AI agents and tasks
4. **Vite / React Frontend (`web`)**: Port `5173`

---

## Prerequisites

Ensure the following tools are installed:
- **Python 3.12+** (managed via `uv`)
- **uv** (Python package & environment manager)
- **Node.js** (v20+ or v25+) & **npm**
- **PostgreSQL Database** (configured in `.env` — Supabase or local PostgreSQL instance)

---

## 1. One-Time Setup

Run the following commands in **PowerShell** from the root repository directory (`linesense`):

### 1.1 Backend Dependencies
Install and synchronize Python virtual environment packages:
```powershell
cd services/backend
uv sync
cd ../..
```

### 1.2 Database Migrations
Apply Alembic migrations to set up the database schema:
```powershell
cd services/backend
uv run alembic upgrade head
cd ../..
```

### 1.3 Seed Demo Data & SOP Documents
Populate the database with synthetic factories, production lines, demo orders (`PO-DEMO-001`), and document vector embeddings:
```powershell
cd services/backend
uv run python -m app.seed --with-documents
cd ../..
```

### 1.4 Frontend Dependencies
Install JavaScript dependencies for the web UI:
```powershell
cd apps/web
npm install
cd ../..
```

---

## 2. Running the Application

Open **4 separate PowerShell terminal tabs or windows** in the project root folder.

### Terminal 1: Development Identity Provider (OIDC)
Authenticates logins during local development:
```powershell
cd services/backend
uv run python -m devtools.dev_oidc
```
- **Service URL**: `http://127.0.0.1:8090`

---

### Terminal 2: Backend API (FastAPI)
Hosts the REST API endpoints and business logic:
```powershell
cd services/backend
uv run uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000 --reload
```
- **API URL**: `http://127.0.0.1:8000`
- **Swagger Docs**: `http://127.0.0.1:8000/docs`

---

### Terminal 3: Durable Background Worker
Executes async jobs, agent analyses (planning, RM, IE, and quality agents), and document indexing.
> **Note:** Without this worker running, order analyses will remain in the `QUEUED` state indefinitely.

```powershell
cd services/backend
uv run python -m app.jobs
```

---

### Terminal 4: Frontend Development Server (React / Vite)
Launches the web user interface:
```powershell
cd apps/web
npm run dev
```
- **Web App URL**: `http://localhost:5173`

---

## 3. Accessing the App & Demo Credentials

1. Open your browser and go to: **[http://localhost:5173](http://localhost:5173)**
2. Click **Sign in**.
3. Log in with any of the seeded demo users:

| Role | Email | Password |
|---|---|---|
| **Supervisor (KTN)** | `supervisor@demo.test` | `demo-password` |
| **Planner (KTN)** | `planner@demo.test` | `demo-password` |
| **IE Engineer (KTN)** | `ie@demo.test` | `demo-password` |
| **Quality Manager (KTN)** | `quality@demo.test` | `demo-password` |
| **Storekeeper (KTN)** | `storekeeper@demo.test` | `demo-password` |
| **Org Admin (All plants)** | `admin@demo.test` | `demo-password` |
| **Viewer (KTN)** | `viewer@demo.test` | `demo-password` |

*(All demo identities use the default password `demo-password` defined in `.env`)*

---

## 4. Verification & Health Checks

To verify that the backend and database connection are operational:

- **Liveness check**:
  ```powershell
  curl http://127.0.0.1:8000/api/health/live
  ```
  *Expected response:* `{"status":"ok"}`

- **Readiness check (DB & pgvector verification)**:
  ```powershell
  curl http://127.0.0.1:8000/api/health/ready
  ```
  *Expected response:* `{"status":"ready", ...}`
