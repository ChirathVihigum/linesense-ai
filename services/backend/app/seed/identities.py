"""Fixed demo identity vocabulary (backend-contracts.md section 9).

Pure data only — no I/O, no database access. Shared by the seed generator
(``app.seed.generator``), the integration test helper
(``tests/helpers/auth.py``), and the dev OIDC provider's user list
(``devtools/dev_oidc/users.json``, kept in sync by
``tests/unit/test_dev_oidc.py::test_users_json_matches_demo_identities``).
"""

from __future__ import annotations

DemoIdentity = tuple[str, str, str, str, str | None]
"""``(email, subject, display_name, role, factory_code | None)``."""

DEMO_ORG_NAME = "Demo Apparel Group"
DEMO_ORG_SLUG = "demo-apparel"

DEMO_FACTORIES: tuple[tuple[str, str], ...] = (
    ("KTN", "Katunayake Plant"),
    ("BYG", "Biyagama Plant"),
)

DEMO_IDENTITIES: tuple[DemoIdentity, ...] = (
    ("admin@demo.test", "dev|admin", "Org Admin (All plants)", "org_admin", None),
    ("supervisor@demo.test", "dev|supervisor", "Supervisor (KTN)", "supervisor", "KTN"),
    ("supervisor.b@demo.test", "dev|supervisor.b", "Supervisor B (KTN)", "supervisor", "KTN"),
    ("planner@demo.test", "dev|planner", "Planner (KTN)", "planner", "KTN"),
    ("storekeeper@demo.test", "dev|storekeeper", "Storekeeper (KTN)", "storekeeper", "KTN"),
    ("ie@demo.test", "dev|ie", "IE Engineer (KTN)", "ie_engineer", "KTN"),
    ("quality@demo.test", "dev|quality", "Quality Manager (KTN)", "quality_manager", "KTN"),
    (
        "quality.b@demo.test",
        "dev|quality.b",
        "Quality Manager B (KTN)",
        "quality_manager",
        "KTN",
    ),
    ("viewer@demo.test", "dev|viewer", "Viewer (KTN)", "viewer", "KTN"),
    ("byg.planner@demo.test", "dev|byg.planner", "Planner (BYG)", "planner", "BYG"),
)

__all__ = [
    "DEMO_FACTORIES",
    "DEMO_IDENTITIES",
    "DEMO_ORG_NAME",
    "DEMO_ORG_SLUG",
    "DemoIdentity",
]
