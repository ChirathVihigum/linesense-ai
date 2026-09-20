#!/usr/bin/env python3
"""Static validator for LineSense AI's deployment artefacts (Task 26).

No Docker is available in the environment this was authored in, so none of
these artefacts (Dockerfiles, Compose file, CI workflow, Keycloak realm,
Caddyfile) have ever actually been built or run. This script is the
substitute: structural and content checks against the committed files,
run with `make infra-check` (wired into the CI `backend` job).

Usage (from `services/backend`, so `uv run` picks up the backend's
virtualenv and PyYAML):

    cd services/backend && uv run python ../../scripts/validate-infra.py

Every ``check_*`` function returns a ``list[str]`` of human-readable
problem descriptions (empty means "no problems found"), so this module can
also be imported directly by tests.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent

COMPOSE_PATH = REPO_ROOT / "infra" / "compose" / "docker-compose.yml"
CADDYFILE_PATH = REPO_ROOT / "infra" / "proxy" / "Caddyfile"
REALM_PATH = REPO_ROOT / "infra" / "identity" / "keycloak" / "linesense-realm.json"
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "ci.yml"
BACKEND_DOCKERFILE_PATH = REPO_ROOT / "services" / "backend" / "Dockerfile"
WEB_DOCKERFILE_PATH = REPO_ROOT / "apps" / "web" / "Dockerfile"

REQUIRED_COMPOSE_SERVICES = {"db", "migrate", "api", "worker", "web", "keycloak"}
ONLY_PORTS_SERVICE = "web"
REQUIRED_CI_JOBS = {"backend", "contracts", "web", "security", "eval"}
# Browser E2E (Task 24) was dropped; a job named "e2e" would resurrect scope
# that was explicitly cut, so its absence is checked for, not just its
# presence elsewhere.
FORBIDDEN_CI_JOBS = {"e2e"}

# High-risk secret-shaped literals: cloud access keys, PEM private key
# headers, and known LLM-provider API-key prefixes. This deliberately does
# NOT flag ordinary dev/demo placeholder passwords (e.g. "dev-app-only",
# "demo-password") -- those are the repo's own established convention
# (.env.example) and are expected to appear, literally, in example/demo
# configuration. scripts/secret-scan.sh (Task 25) is the broader,
# whole-repository secret scanner; this check is narrower and scoped to the
# infra artefacts this script already parses.
SECRET_PATTERNS = [
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"-----BEGIN (RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    re.compile(r"sk-ant-[A-Za-z0-9-]{10,}"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"ghp_[A-Za-z0-9]{30,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
]

PINNED_ACTION_RE = re.compile(r"^[^@]+@(v?\d[\w.-]*)$")
# A full 40-character commit SHA is also an acceptable pin (the strongest
# one, in fact -- immutable regardless of tag moves) even though it need not
# start with a digit (e.g. `@a1b2c3...`), which `PINNED_ACTION_RE` requires.
SHA_PINNED_ACTION_RE = re.compile(r"^[^@]+@([0-9a-fA-F]{40})$")
FLOATING_ACTION_REFS = {"main", "master", "latest", "head"}

# `image: repo/name:${VAR:-default}` (Compose/shell-style interpolation with
# a default): the default is what actually applies whenever the variable is
# unset, which is the common case for a plain checkout with no `.env`
# override, so it is what the "pins latest" check must look at.
_VAR_DEFAULT_RE = re.compile(r"^\$\{[^}:]+:-(.*)\}$")


def _resolve_tag_default(tag: str) -> str:
    """Resolve a `${VAR:-default}` interpolated tag to its default value;
    any other tag (including a plain literal) is returned unchanged."""
    match = _VAR_DEFAULT_RE.match(tag)
    return match.group(1) if match else tag


def _image_tag(image_ref: str) -> str | None:
    """The tag portion of ``image_ref`` (after the last ``/``, everything
    after the *first* remaining ``:``), or ``None`` if it has no tag.

    Splitting on the first colon (not the last) matters once the tag itself
    is a `${VAR:-default}` interpolation: that syntax has its own internal
    colon (`${TAG:-latest}`), so `rsplit(":", 1)` picks the wrong one and
    returns a truncated, unrecognizable tag like `-latest}`.
    """
    last_segment = image_ref.rsplit("/", 1)[-1]
    if ":" not in last_segment:
        return None
    return last_segment.split(":", 1)[1]


def _rel(path: Path) -> str:
    """``path`` relative to the repo root for display, tolerating paths
    outside it (e.g. when this module is exercised against fixtures from a
    test, rather than the real committed files)."""
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _load_yaml(path: Path) -> Any:
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def check_secret_patterns(paths: list[Path]) -> list[str]:
    problems: list[str] = []
    for path in paths:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        for pattern in SECRET_PATTERNS:
            match = pattern.search(text)
            if match:
                problems.append(
                    f"{_rel(path)}: matches high-risk secret pattern "
                    f"{pattern.pattern!r} ({match.group(0)[:12]}...)"
                )
    return problems


def check_compose(compose: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    services = compose.get("services")
    if not isinstance(services, dict):
        return ["docker-compose.yml: no top-level 'services' mapping"]

    missing = REQUIRED_COMPOSE_SERVICES - services.keys()
    if missing:
        problems.append(f"docker-compose.yml: missing required service(s): {sorted(missing)}")

    for name, definition in services.items():
        if not isinstance(definition, dict):
            problems.append(f"docker-compose.yml: service '{name}' is not a mapping")
            continue

        has_ports = bool(definition.get("ports"))
        if has_ports and name != ONLY_PORTS_SERVICE:
            problems.append(
                f"docker-compose.yml: service '{name}' publishes host ports; only "
                f"'{ONLY_PORTS_SERVICE}' may (see infra brief requirement 3)"
            )
        if name == ONLY_PORTS_SERVICE and not has_ports:
            problems.append(f"docker-compose.yml: service '{name}' should publish a host port")

        image = definition.get("image")
        if isinstance(image, str):
            tag = _image_tag(image)
            if tag is None:
                problems.append(
                    f"docker-compose.yml: service '{name}' image '{image}' has no explicit tag"
                )
            elif _resolve_tag_default(tag) == "latest":
                problems.append(
                    f"docker-compose.yml: service '{name}' pins image tag 'latest' "
                    "(pin an explicit, verified version)"
                )
        elif "build" not in definition:
            problems.append(f"docker-compose.yml: service '{name}' has neither 'image' nor 'build'")

    db = services.get("db", {})
    if isinstance(db, dict) and "healthcheck" not in db:
        problems.append("docker-compose.yml: service 'db' has no healthcheck")

    keycloak = services.get("keycloak", {})
    if isinstance(keycloak, dict):
        profiles = keycloak.get("profiles") or []
        if "dev" not in profiles:
            problems.append(
                "docker-compose.yml: service 'keycloak' must be gated behind the 'dev' "
                "profile (production Keycloak is documented, not run here)"
            )
        command = keycloak.get("command") or []
        command_items = command if isinstance(command, list) else [command]
        if not any("start-dev" in str(item) for item in command_items):
            problems.append(
                "docker-compose.yml: service 'keycloak' must use 'start-dev' "
                "(only under the 'dev' profile)"
            )

    networks = compose.get("networks")
    if not isinstance(networks, dict) or not networks:
        problems.append("docker-compose.yml: no top-level 'networks' defined")

    return problems


def check_caddyfile(text: str) -> list[str]:
    problems: list[str] = []
    if "/internal" not in text:
        problems.append("Caddyfile: no reference to blocking /internal")
    elif not re.search(r"respond\s+\S*\s*404", text) and "404" not in text:
        problems.append("Caddyfile: /internal is referenced but no 404 response is configured")
    if "tls internal" not in text:
        problems.append("Caddyfile: no 'tls internal' directive (local HTTPS demo)")
    if "reverse_proxy" not in text:
        problems.append("Caddyfile: no reverse_proxy directive")
    return problems


def check_realm(realm: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    if realm.get("realm") != "linesense":
        problems.append(
            f"linesense-realm.json: realm is {realm.get('realm')!r}, expected 'linesense'"
        )

    clients = realm.get("clients")
    if not isinstance(clients, list):
        return problems + ["linesense-realm.json: no 'clients' list"]

    web_clients = [
        c for c in clients if isinstance(c, dict) and c.get("clientId") == "linesense-web"
    ]
    if not web_clients:
        problems.append("linesense-realm.json: no client with clientId 'linesense-web'")
        return problems

    client = web_clients[0]
    if client.get("publicClient") is not False:
        problems.append(
            "linesense-realm.json: 'linesense-web' must be confidential (publicClient: false)"
        )
    if client.get("standardFlowEnabled") is not True:
        problems.append("linesense-realm.json: 'linesense-web' must have standardFlowEnabled: true")
    attributes = client.get("attributes") or {}
    if attributes.get("pkce.code.challenge.method") != "S256":
        problems.append(
            "linesense-realm.json: 'linesense-web' must set "
            "attributes.pkce.code.challenge.method=S256"
        )
    redirect_uris = client.get("redirectUris") or []
    if "https://localhost/auth/callback" not in redirect_uris:
        problems.append(
            "linesense-realm.json: 'linesense-web' redirectUris must include "
            "'https://localhost/auth/callback' exactly"
        )

    users = realm.get("users") or []
    if len(users) < 10:
        problems.append(
            f"linesense-realm.json: expected at least 10 demo users (backend-contracts.md "
            f"section 9), found {len(users)}"
        )
    for user in users:
        if not isinstance(user, dict):
            continue
        if "UPDATE_PASSWORD" not in (user.get("requiredActions") or []):
            problems.append(
                f"linesense-realm.json: user {user.get('username')!r} is missing "
                "requiredActions UPDATE_PASSWORD (temporary password must force a reset)"
            )
        credentials = user.get("credentials") or []
        if not any(cred.get("temporary") is True for cred in credentials if isinstance(cred, dict)):
            problems.append(
                f"linesense-realm.json: user {user.get('username')!r} has no temporary credential"
            )

    return problems


def check_workflow(workflow: dict[Any, Any]) -> list[str]:
    problems: list[str] = []

    # PyYAML parses the bare `on:` mapping key as the boolean True.
    triggers = workflow.get("on", workflow.get(True))
    if isinstance(triggers, dict):
        trigger_names = set(triggers.keys())
    elif isinstance(triggers, list):
        trigger_names = set(triggers)
    else:
        trigger_names = set()
    if not {"push", "pull_request"} <= trigger_names:
        problems.append(
            f"ci.yml: expected 'push' and 'pull_request' triggers, found {trigger_names}"
        )

    jobs = workflow.get("jobs")
    if not isinstance(jobs, dict):
        return problems + ["ci.yml: no top-level 'jobs' mapping"]

    missing = REQUIRED_CI_JOBS - jobs.keys()
    if missing:
        problems.append(f"ci.yml: missing required job(s): {sorted(missing)}")
    present_forbidden = FORBIDDEN_CI_JOBS & jobs.keys()
    if present_forbidden:
        problems.append(
            f"ci.yml: job(s) {sorted(present_forbidden)} should not exist "
            "(browser E2E / Task 24 was dropped)"
        )

    eval_job = jobs.get("eval", {})
    if isinstance(eval_job, dict):
        eval_triggers = eval_job.get("on") or eval_job.get(True)
        # Job-level `on:` isn't a real GitHub Actions key (triggers are
        # workflow-level); a manual-only job instead guards its steps with
        # `if: github.event_name == 'workflow_dispatch'` or the whole
        # workflow trigger set is exactly workflow_dispatch for that job's
        # concerns. We check the *workflow* also declares workflow_dispatch
        # and that the eval job is conditioned on it.
        del eval_triggers  # not a meaningful GitHub Actions construct; see 'if' check below
        job_if = eval_job.get("if", "")
        if "workflow_dispatch" not in str(job_if):
            problems.append(
                "ci.yml: job 'eval' must be gated to run only on workflow_dispatch "
                "(an 'if:' condition referencing github.event_name == 'workflow_dispatch')"
            )
    if "workflow_dispatch" not in trigger_names:
        problems.append("ci.yml: 'workflow_dispatch' trigger required (for the manual 'eval' job)")

    # Every `uses:` reference across every job/step must be pinned to an
    # explicit version, not a floating ref like @main/@latest, and not
    # missing a ref entirely.
    for job_name, job in jobs.items():
        if not isinstance(job, dict):
            continue
        for step in job.get("steps") or []:
            if not isinstance(step, dict):
                continue
            uses = step.get("uses")
            if not isinstance(uses, str):
                continue
            match = PINNED_ACTION_RE.match(uses) or SHA_PINNED_ACTION_RE.match(uses)
            if not match:
                problems.append(f"ci.yml: job '{job_name}' step 'uses: {uses}' has no pinned ref")
                continue
            ref = match.group(1).lower()
            if ref in FLOATING_ACTION_REFS:
                problems.append(
                    f"ci.yml: job '{job_name}' step 'uses: {uses}' is pinned to a floating "
                    f"ref ({ref!r}); pin an explicit version tag"
                )

    return problems


def check_image_tags_not_latest(dockerfile_paths: list[Path]) -> list[str]:
    problems: list[str] = []
    from_re = re.compile(r"^FROM\s+(\S+)", re.IGNORECASE)
    for path in dockerfile_paths:
        if not path.is_file():
            problems.append(f"{_rel(path)}: file does not exist")
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            match = from_re.match(line.strip())
            if not match:
                continue
            image_ref = match.group(1)
            tag = _image_tag(image_ref)
            if tag is None:
                problems.append(f"{_rel(path)}: '{image_ref}' has no explicit tag")
            elif _resolve_tag_default(tag) == "latest":
                problems.append(f"{_rel(path)}: '{image_ref}' pins tag 'latest'")
    return problems


def run_all_checks() -> list[str]:
    problems: list[str] = []

    for required_path in (
        COMPOSE_PATH,
        CADDYFILE_PATH,
        REALM_PATH,
        WORKFLOW_PATH,
        BACKEND_DOCKERFILE_PATH,
        WEB_DOCKERFILE_PATH,
    ):
        if not required_path.is_file():
            problems.append(f"missing required file: {_rel(required_path)}")

    if problems:
        return problems

    compose = _load_yaml(COMPOSE_PATH)
    workflow = _load_yaml(WORKFLOW_PATH)
    realm = _load_json(REALM_PATH)
    caddyfile_text = CADDYFILE_PATH.read_text(encoding="utf-8")

    problems += check_compose(compose)
    problems += check_caddyfile(caddyfile_text)
    problems += check_realm(realm)
    problems += check_workflow(workflow)
    problems += check_image_tags_not_latest([BACKEND_DOCKERFILE_PATH, WEB_DOCKERFILE_PATH])
    problems += check_secret_patterns(
        [
            COMPOSE_PATH,
            CADDYFILE_PATH,
            REALM_PATH,
            WORKFLOW_PATH,
            BACKEND_DOCKERFILE_PATH,
            WEB_DOCKERFILE_PATH,
        ]
    )

    return problems


def main() -> int:
    problems = run_all_checks()
    if problems:
        print(f"validate-infra: {len(problems)} problem(s) found:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    print("validate-infra: all checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
