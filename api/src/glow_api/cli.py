"""Admin CLI for glow-api.

Usage:
    python -m glow_api.cli users list
    python -m glow_api.cli users create EMAIL [--schools A,B] [--admin]
    python -m glow_api.cli users update EMAIL [--schools ...] [--active/--inactive] [--admin/--no-admin]
    python -m glow_api.cli users delete EMAIL
    python -m glow_api.cli schools list
    python -m glow_api.cli schools create NAME
    python -m glow_api.cli schools sync
    python -m glow_api.cli db init
    python -m glow_api.cli api-keys create --name NAME [--expires-in-days DAYS]
    python -m glow_api.cli api-keys list
    python -m glow_api.cli api-keys revoke KEY_ID
"""

import hashlib
import json
import sys
from datetime import UTC, datetime

import click
import yaml
from pydantic import ValidationError
from sqlalchemy import select

from glow_api import audit
from glow_api.api_keys import issue_api_key, key_status
from glow_api.database import (
    SessionLocal,
    create_school,
    create_user,
    delete_user,
    get_api_key_by_id,
    get_school_by_name,
    get_user_by_email,
    get_user_by_username,
    list_api_keys,
    list_schools,
    list_users,
    revoke_api_key,
    run_migrations,
    seed_demo,
    sync_schools,
    update_user,
)
from glow_api.metadata_models import User
from glow_api.models import validate_email
from glow_api.entry import format_validation_error
from glow_api.settings import Settings, settings
from glow_api.suppression import RULES_PATH
from glow_api.vintages import (
    VintageStorageNotConfigured,
    cut_vintage,
    get_vintages_dir,
    list_vintages,
)


@click.group()
def cli() -> None:
    """GLOW API admin CLI."""


# ---------------------------------------------------------------------------
# db commands
# ---------------------------------------------------------------------------


@cli.group()
def db() -> None:
    """Database management commands."""


@db.command("init")
def db_init() -> None:
    """Initialise the database by applying all migrations."""
    run_migrations()
    click.echo("Database initialised.")


# ---------------------------------------------------------------------------
# users commands
# ---------------------------------------------------------------------------


@cli.group()
def users() -> None:
    """User management commands."""


@users.command("list")
def users_list() -> None:
    """List all users."""
    with SessionLocal() as db:
        all_users = list_users(db)
        # Eagerly load user data before session closes
        user_data = []
        for user in all_users:
            user_data.append(
                {
                    "id": user.id,
                    "username": user.username,
                    "is_active": user.is_active,
                    "is_admin": user.is_admin,
                    "school_names": [s.name for s in user.schools],
                }
            )

    if not user_data:
        click.echo("No users found.")
        return

    for user in user_data:
        active_flag = "active" if user["is_active"] else "inactive"
        admin_flag = ", admin" if user["is_admin"] else ""
        click.echo(
            f"  [{user['id']}] {user['username']} ({active_flag}{admin_flag}) schools={json.dumps(user['school_names'])}"
        )


@users.command("create")
@click.argument("email")
@click.option(
    "--schools",
    default="",
    help='Comma-separated school names, e.g. "Focus School Academy,Neighbouring School"',
)
@click.option(
    "--admin", "is_admin", is_flag=True, default=False, help="Grant admin privileges."
)
def users_create(email: str, schools: str, is_admin: bool) -> None:
    """Create a user by EMAIL; they link to their identity on first login."""
    try:
        email = validate_email(email)
    except ValueError:
        click.echo(f"'{email}' is not a valid email address.", err=True)
        sys.exit(1)

    with SessionLocal() as db:
        # Parse school names and get IDs
        school_ids = []
        if schools:
            school_names = [s.strip() for s in schools.split(",")]
            for school_name in school_names:
                school = get_school_by_name(db, school_name)
                if school is None:
                    click.echo(
                        f"School '{school_name}' not found. Use 'schools list' to see available schools.",
                        err=True,
                    )
                    sys.exit(1)
                school_ids.append(school.id)

        if get_user_by_email(db, email) or get_user_by_username(db, email):
            click.echo(f"User '{email}' already exists.", err=True)
            sys.exit(1)

        user = create_user(
            db, username=email, email=email, school_ids=school_ids, is_admin=is_admin
        )
        # Eagerly load school names before session closes
        school_names = [s.name for s in user.schools]
        user_id = user.id
        user_username = user.username
        user_is_admin = user.is_admin

    admin_flag = " [ADMIN]" if user_is_admin else ""
    click.echo(
        f"User '{user_username}' created (id={user_id}){admin_flag}. Schools: {school_names}"
    )


def _find_user(db, email: str):
    """Look up by normalised email, falling back to legacy non-email usernames."""
    return get_user_by_email(db, email) or get_user_by_username(db, email)


@users.command("update")
@click.argument("email")
@click.option(
    "--schools",
    default=None,
    help='Comma-separated school names to replace existing schools, e.g. "Focus School Academy,Neighbouring School"',
)
@click.option(
    "--active/--inactive",
    default=None,
    help="Set user active or inactive.",
)
@click.option(
    "--admin/--no-admin",
    "is_admin",
    default=None,
    help="Grant or revoke admin privileges.",
)
def users_update(
    email: str,
    schools: str | None,
    active: bool | None,
    is_admin: bool | None,
) -> None:
    """Update a user's schools, active status, or admin flag."""
    if schools is None and active is None and is_admin is None:
        click.echo(
            "Nothing to update. Provide --schools, --active/--inactive or --admin/--no-admin."
        )
        return

    school_ids: list[int] | None = None
    if schools is not None:
        school_ids = []
        with SessionLocal() as db:
            school_names = [s.strip() for s in schools.split(",") if s.strip()]
            for school_name in school_names:
                school = get_school_by_name(db, school_name)
                if school is None:
                    click.echo(
                        f"School '{school_name}' not found. Use 'schools list' to see available schools.",
                        err=True,
                    )
                    sys.exit(1)
                school_ids.append(school.id)

    with SessionLocal() as db:
        user = _find_user(db, email)
        if user is None:
            click.echo(f"User '{email}' not found.", err=True)
            sys.exit(1)
        update_user(
            db, user, school_ids=school_ids, is_active=active, is_admin=is_admin
        )

    click.echo(f"User '{email}' updated.")


@users.command("delete")
@click.argument("email")
@click.confirmation_option(prompt="Are you sure you want to delete this user?")
def users_delete(email: str) -> None:
    """Delete a user."""
    with SessionLocal() as db:
        user = _find_user(db, email)
        if user is None:
            click.echo(f"User '{email}' not found.", err=True)
            sys.exit(1)
        delete_user(db, user)

    click.echo(f"User '{email}' deleted.")


# ---------------------------------------------------------------------------
# schools commands
# ---------------------------------------------------------------------------


@cli.group()
def schools() -> None:
    """School management commands."""


@schools.command("list")
def schools_list() -> None:
    """List all schools."""
    with SessionLocal() as db:
        all_schools = list_schools(db)
    if not all_schools:
        click.echo("No schools found.")
        return
    for school in all_schools:
        click.echo(
            f"  [{school.id}] {school.name} (size={school.size}, category={school.category})"
        )


@schools.command("create")
@click.argument("name")
@click.option("--size", default=None, help="School size (e.g., small, medium, large)")
@click.option(
    "--category", default=None, help="School category (e.g., comprehensive, academy)"
)
@click.option(
    "--odk-school-id",
    default=None,
    help="Raw ODK 'school' submission value to link this school to (leave unset "
    "for a school not yet connected to ODK data).",
)
def schools_create(
    name: str, size: str | None, category: str | None, odk_school_id: str | None
) -> None:
    """Create a new school."""
    with SessionLocal() as db:
        existing = get_school_by_name(db, name)
        if existing is not None:
            click.echo(f"School '{name}' already exists.", err=True)
            sys.exit(1)
        school = create_school(
            db, name=name, size=size, category=category, odk_school_id=odk_school_id
        )

    click.echo(f"School '{school.name}' created (id={school.id}).")


@schools.command("sync")
@click.option(
    "--min-geographical",
    default=2,
    help="Minimum number of geographical neighbors per school (default: 2)",
)
@click.option(
    "--min-statistical",
    default=2,
    help="Minimum number of statistical neighbors per school (default: 2)",
)
@click.option(
    "--create-users/--no-create-users",
    default=False,
    help="Create a per-school login user for each synced school (default: off).",
)
def schools_sync(
    min_geographical: int, min_statistical: int, create_users: bool = False
) -> None:
    """Extract schools from loaded data, create neighbor relationships, and grant admin access.

    This command:
    1. Extracts all unique schools from the loaded CSV/Parquet data
    2. Creates school records in the metadata database (skips existing)
    3. Creates neighbor relationships (geographical and statistical)
    4. Grants all admin users access to all schools
    5. Creates new users for each school (using the capitalized letters of the school as username and password)
    """
    df = _load_df_or_exit()

    click.echo("Starting school synchronization...")
    with SessionLocal() as db:
        try:
            schools = sync_schools(db, df, min_geographical, min_statistical)
        except ValueError as e:
            click.echo(f"Error: {e}", err=True)
            sys.exit(1)
        click.echo(f"Found {len(schools)} unique schools in data")
        for school in schools:
            click.echo(f"  - {school.name}")

        if create_users:
            click.echo("   Creating users for schools...")
            for school in list_schools(db):
                username = "".join(c for c in school.name if c.isupper())
                user = db.execute(
                    select(User).where(User.username == username)
                ).scalar_one_or_none()
                if user is None:
                    create_user(
                        db=db,
                        username=username,
                        is_active=True,
                        is_admin=False,
                        school_ids=[school.id],
                    )
                    click.echo(f"      {school.name} -> {username}")

    # Step 4: Verification
    click.echo("\n4. Verification:")
    with SessionLocal() as db:
        schools = list_schools(db)
        for school in schools:
            geo_count = len(school.geographical_neighbors)
            stat_count = len(school.statistical_neighbors)
            overlap = len(
                set(n.id for n in school.geographical_neighbors)
                & set(n.id for n in school.statistical_neighbors)
            )
            click.echo(
                f"   {school.name}: {geo_count} geographical, {stat_count} statistical ({overlap} overlap)"
            )

            if geo_count < min_geographical and len(schools) > 1:
                click.echo(
                    f"     WARNING: Only {geo_count} geographical neighbors (minimum {min_geographical})"
                )
            if stat_count < min_statistical and len(schools) > 1:
                click.echo(
                    f"     WARNING: Only {stat_count} statistical neighbors (minimum {min_statistical})"
                )

    click.echo("\nSchool synchronization completed successfully!")


def _load_df_or_exit():
    """Return the slim counts frame (enough for school names), from the cache
    or a fresh poll; exit 1 if there's no data."""
    from glow_api import counts_store

    df = counts_store.get_counts_store().ensure_loaded()
    if df.empty:
        click.echo("Error: No data loaded. Cannot extract schools.", err=True)
        sys.exit(1)
    return df


@cli.group()
def demo() -> None:
    """Demo-mode commands."""


@demo.command("reset")
def demo_reset() -> None:
    """Wipe users and schools, re-seed from loaded data, recreate admin."""
    if not settings.DEMO_MODE:
        click.echo(
            "GLOW_DEMO_MODE is off - refusing to wipe users and schools.", err=True
        )
        sys.exit(1)
    df = _load_df_or_exit()
    with SessionLocal() as db:
        seed_demo(db, df)
    click.echo("Demo data reset.")


# ---------------------------------------------------------------------------
# api-keys commands
# ---------------------------------------------------------------------------


def _audit_cli(action: str, **fields) -> None:
    # CLI actions bypass the HTTP request-logging middleware, so write the
    # audit line directly.
    audit.write_audit_line(
        {
            "actor": "cli",
            "action": action,
            "ts": datetime.now(UTC).isoformat(),
            **fields,
        }
    )


@cli.group("api-keys")
def api_keys() -> None:
    """Pseudonymous data export API keys."""


@api_keys.command("create")
@click.option("--name", required=True, help="Who/what the key is for.")
@click.option(
    "--expires-in-days",
    type=click.IntRange(1, 365),
    default=None,
    help=f"Lifetime in days (default {settings.API_KEY_EXPIRE_DAYS}).",
)
def api_keys_create(name: str, expires_in_days: int | None) -> None:
    """Issue a key. The raw key is printed once and never again."""
    with SessionLocal() as db:
        record, raw_key = issue_api_key(db, name, expires_in_days, None)
        key_id, expires_at = record.id, record.expires_at
    _audit_cli("api_key_created", api_key_id=key_id, name=name)
    click.echo(f"Created API key {key_id} '{name}', expires {expires_at:%Y-%m-%d} UTC.")
    click.echo("Copy it now - it will not be shown again:")
    click.echo(raw_key)


@api_keys.command("list")
def api_keys_list() -> None:
    """List keys (never shows the secret)."""
    now = datetime.now(UTC)
    with SessionLocal() as db:
        rows = [
            (
                k.id,
                k.name,
                k.prefix,
                key_status(k, now),
                k.expires_at,
                k.last_used_at,
                k.use_count or 0,
            )
            for k in list_api_keys(db)
        ]
    if not rows:
        click.echo("No API keys.")
        return
    for key_id, name, prefix, status, expires_at, last_used_at, use_count in rows:
        last_used = f"{last_used_at:%Y-%m-%d %H:%M} UTC" if last_used_at else "never"
        click.echo(
            f"  [{key_id}] {name} ({prefix}...) {status}, "
            f"expires {expires_at:%Y-%m-%d}, last used {last_used}, uses {use_count}"
        )


@api_keys.command("revoke")
@click.argument("key_id", type=int)
def api_keys_revoke(key_id: int) -> None:
    """Revoke a key immediately."""
    with SessionLocal() as db:
        record = get_api_key_by_id(db, key_id)
        if record is None:
            click.echo(f"API key {key_id} not found.")
            sys.exit(1)
        revoke_api_key(db, record)
    _audit_cli("api_key_revoked", api_key_id=key_id)
    click.echo(f"API key {key_id} revoked.")


# ---------------------------------------------------------------------------
# vintages commands
# ---------------------------------------------------------------------------


@cli.group("vintages")
def vintages() -> None:
    """Periodic, immutable dataset archives /query and /export pin against."""


@vintages.command("cut")
def vintages_cut() -> None:
    """Cut a vintage now, outside the scheduled cadence (e.g. right after a
    school finishes a data collection push)."""
    from glow_api.data import ODKFetchError, get_datastore

    try:
        info = cut_vintage(get_datastore(), get_vintages_dir(), created_by=None)
    except ODKFetchError as exc:
        click.echo(f"Error: {exc}. No vintage cut.", err=True)
        sys.exit(1)
    except VintageStorageNotConfigured as exc:
        click.echo(f"Error: {exc}.", err=True)
        sys.exit(1)
    _audit_cli("vintage_cut", prefix=info.prefix, row_count=info.row_count)
    click.echo(f"Cut vintage {info.prefix} ({info.row_count} rows).")


@vintages.command("list")
def vintages_list() -> None:
    """List all vintages, oldest first."""
    try:
        rows = list_vintages(get_vintages_dir())
    except VintageStorageNotConfigured as exc:
        click.echo(f"Error: {exc}.", err=True)
        sys.exit(1)
    if not rows:
        click.echo("No vintages.")
        return
    for info in rows:
        creator = info.created_by_username or "scheduler"
        click.echo(
            f"  {info.prefix} created {info.created_at:%Y-%m-%d %H:%M} UTC by {creator}, "
            f"{info.row_count} rows"
        )


# ---------------------------------------------------------------------------
# config / suppression commands
# ---------------------------------------------------------------------------


@cli.group()
def config() -> None:
    """Configuration commands."""


@config.command("check")
def config_check() -> None:
    """Validate settings with the production code; exit 1 on any problem."""
    try:
        s = Settings()
        s.validate_auth_config()
        if not s.DEMO_MODE and not s.OIDC_ISSUER:
            # The API refuses to start in this state (see auth.py).
            raise RuntimeError(
                "GLOW_OIDC_ISSUER is not set and GLOW_DEMO_MODE is off. "
                "Set GLOW_OIDC_ISSUER (and GLOW_OIDC_CLIENT_ID) or enable demo mode."
            )
    except ValidationError as exc:
        for line in format_validation_error(exc):
            click.echo(line)
        sys.exit(1)
    except RuntimeError as exc:
        click.echo(str(exc))
        sys.exit(1)
    click.echo("Config OK")


def hash_suppression_file(path) -> str:
    """SHA-256 of the canonical JSON of the parsed rules (comment-insensitive)."""
    with open(path, encoding="utf-8") as f:
        rules = yaml.safe_load(f)
    blob = json.dumps(rules, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()


@cli.command("suppression-hash")
def suppression_hash() -> None:
    """Print the hash of the packaged suppression rules."""
    click.echo(hash_suppression_file(RULES_PATH))


if __name__ == "__main__":
    cli()
