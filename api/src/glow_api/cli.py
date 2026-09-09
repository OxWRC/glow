"""Admin CLI for glow-api.

Usage:
    python -m glow_api.cli users list
    python -m glow_api.cli users create USERNAME
    python -m glow_api.cli users update USERNAME
    python -m glow_api.cli users delete USERNAME
    python -m glow_api.cli schools list
    python -m glow_api.cli schools create NAME
    python -m glow_api.cli schools sync
    python -m glow_api.cli db init
"""

import json
import sys

import click
from sqlalchemy import select

from glow_api.auth import get_password_hash
from glow_api.database import (
    SessionLocal,
    create_user,
    delete_user,
    get_user_by_username,
    list_users,
    run_migrations,
    update_user,
    create_school,
    get_school_by_name,
    list_schools,
    extract_schools_from_dataframe,
    grant_admins_all_schools,
    set_geographical_neighbors,
    set_statistical_neighbors,
)
from glow_api.metadata_models import User


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
@click.argument("username")
@click.option("--password", prompt=True, hide_input=True, confirmation_prompt=True)
@click.option(
    "--schools",
    default="",
    help='Comma-separated school names, e.g. "Focus School Academy,Neighbouring School"',
)
@click.option(
    "--admin", "is_admin", is_flag=True, default=False, help="Grant admin privileges."
)
def users_create(username: str, password: str, schools: str, is_admin: bool) -> None:
    """Create a new user."""
    hashed = get_password_hash(password)

    with SessionLocal() as db:
        existing = get_user_by_username(db, username)
        if existing is not None:
            click.echo(f"User '{username}' already exists.", err=True)
            sys.exit(1)

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

        user = create_user(
            db,
            username=username,
            hashed_password=hashed,
            school_ids=school_ids,
            is_admin=is_admin,
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


@users.command("update")
@click.argument("username")
@click.option(
    "--password", default=None, help="New password (will prompt if not provided)."
)
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
def users_update(
    username: str,
    password: str | None,
    schools: str | None,
    active: bool | None,
) -> None:
    """Update a user's password, schools, or active status."""
    if password is None and schools is None and active is None:
        click.echo(
            "Nothing to update. Provide --password, --schools, or --active/--inactive."
        )
        return

    hashed: str | None = None
    if password is not None:
        hashed = get_password_hash(password)

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
        user = get_user_by_username(db, username)
        if user is None:
            click.echo(f"User '{username}' not found.", err=True)
            sys.exit(1)
        update_user(
            db, user, hashed_password=hashed, school_ids=school_ids, is_active=active
        )

    click.echo(f"User '{username}' updated.")


@users.command("delete")
@click.argument("username")
@click.confirmation_option(prompt="Are you sure you want to delete this user?")
def users_delete(username: str) -> None:
    """Delete a user."""
    with SessionLocal() as db:
        user = get_user_by_username(db, username)
        if user is None:
            click.echo(f"User '{username}' not found.", err=True)
            sys.exit(1)
        delete_user(db, user)

    click.echo(f"User '{username}' deleted.")


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
    from glow_api.data import get_datastore
    import random

    click.echo("Starting school synchronization...")

    # Step 1: Load data and extract schools
    click.echo("\n1. Extracting schools from loaded data...")
    datastore = get_datastore()
    data_snapshot = datastore.to_frozen()
    df = data_snapshot.df

    # If datastore is empty, load it now
    if df.empty:
        click.echo("   Data not yet loaded, loading now...")
        datastore.startup()
        data_snapshot = datastore.to_frozen()
        df = data_snapshot.df

    if df.empty:
        click.echo("Error: No data loaded. Cannot extract schools.", err=True)
        sys.exit(1)

    with SessionLocal() as db:
        try:
            schools = extract_schools_from_dataframe(db, df)
            click.echo(f"   Found {len(schools)} unique schools in data")
            for school in schools:
                click.echo(f"     - {school.name}")
        except ValueError as e:
            click.echo(f"Error: {e}", err=True)
            sys.exit(1)

    # Step 2: Create neighbor relationships
    click.echo("\n2. Creating neighbor relationships...")
    click.echo(f"   Ensuring each school has at least {min_geographical} geographical")
    click.echo(f"   and {min_statistical} statistical neighbors")

    with SessionLocal() as db:
        schools = list_schools(db)

        if len(schools) < 2:
            click.echo(
                "   Warning: Need at least 2 schools to create neighbor relationships."
            )
        else:
            for school in schools:
                # Get all other schools (potential neighbors)
                potential_neighbors = [s for s in schools if s.id != school.id]

                if len(potential_neighbors) == 0:
                    click.echo(f"   {school.name}: No other schools available")
                    continue

                # Determine how many neighbors to assign
                num_geo = min(min_geographical, len(potential_neighbors))
                num_stat = min(min_statistical, len(potential_neighbors))

                # Check current neighbor counts
                current_geo_count = len(school.geographical_neighbors)
                current_stat_count = len(school.statistical_neighbors)

                geo_neighbors_to_add = []
                stat_neighbors_to_add = []

                # Add geographical neighbors if needed
                if current_geo_count < num_geo:
                    current_geo_ids = {n.id for n in school.geographical_neighbors}
                    available = [
                        s for s in potential_neighbors if s.id not in current_geo_ids
                    ]
                    needed = num_geo - current_geo_count
                    if needed > 0 and available:
                        new_neighbors = random.sample(
                            available, min(needed, len(available))
                        )
                        geo_neighbors_to_add = [n.id for n in new_neighbors]

                # Add statistical neighbors if needed
                if current_stat_count < num_stat:
                    current_stat_ids = {n.id for n in school.statistical_neighbors}
                    available = [
                        s for s in potential_neighbors if s.id not in current_stat_ids
                    ]
                    needed = num_stat - current_stat_count
                    if needed > 0 and available:
                        new_neighbors = random.sample(
                            available, min(needed, len(available))
                        )
                        stat_neighbors_to_add = [n.id for n in new_neighbors]

                # Update geographical neighbors
                if geo_neighbors_to_add:
                    all_geo_ids = [
                        n.id for n in school.geographical_neighbors
                    ] + geo_neighbors_to_add
                    set_geographical_neighbors(db, school, all_geo_ids)
                    click.echo(
                        f"   {school.name}: Added {len(geo_neighbors_to_add)} geographical neighbors"
                    )
                elif current_geo_count >= num_geo:
                    click.echo(
                        f"   {school.name}: Already has {current_geo_count} geographical neighbors"
                    )

                # Update statistical neighbors
                if stat_neighbors_to_add:
                    all_stat_ids = [
                        n.id for n in school.statistical_neighbors
                    ] + stat_neighbors_to_add
                    set_statistical_neighbors(db, school, all_stat_ids)
                    click.echo(
                        f"   {school.name}: Added {len(stat_neighbors_to_add)} statistical neighbors"
                    )
                elif current_stat_count >= num_stat:
                    click.echo(
                        f"   {school.name}: Already has {current_stat_count} statistical neighbors"
                    )

    # Step 3: Set up user->school mappings (admin accesses all)
    click.echo("\n3. Granting admin users access to all schools...")
    with SessionLocal() as db:
        updated_count = grant_admins_all_schools(db)
        click.echo(f"   Updated {updated_count} admin user(s)")

        if create_users:
            click.echo("   Creating users for schools...")
            # Re-fetch: `schools` was bound to the Step 2 session, which has
            # since closed - using it here raises DetachedInstanceError.
            for school in list_schools(db):
                username = "".join(c for c in school.name if c.isupper())
                user = db.execute(
                    select(User).where(User.username == username)
                ).scalar_one_or_none()
                if user is None:
                    create_user(
                        db=db,
                        username=username,
                        hashed_password=get_password_hash(username),
                        is_active=True,
                        is_admin=False,
                        school_ids=[school.id],
                    )
                    click.echo(f"      {school.name} -> {username}:{username}")

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


if __name__ == "__main__":
    cli()
