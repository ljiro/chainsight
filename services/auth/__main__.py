"""Manage users from the command line (first owner, password resets).

    python -m services.auth create-user --email you@example.com --role owner
    python -m services.auth set-password --email you@example.com

The password is read from CHAINSIGHT_PASSWORD or prompted for (never passed as an argument,
so it does not end up in shell history or process listings).
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import sys

import sqlalchemy as sa

from core.logging_config import configure_logging
from core.settings import get_settings
from core.timeutils import utcnow
from database.enums import MemberRole
from database.session import get_async_engine, get_sessionmaker
from services.auth import crypto
from services.auth.service import AuthError, create_user, revoke_user_sessions


def _password(email: str) -> str:
    pw = os.environ.get("CHAINSIGHT_PASSWORD")
    if pw is None:
        pw = getpass.getpass("Password: ")
        if getpass.getpass("Repeat password: ") != pw:
            sys.exit("Passwords do not match")
    problems = crypto.password_problems(pw, email)
    if problems:
        sys.exit("Password " + "; ".join(problems))
    return pw


async def _create(email: str, role: MemberRole, org_slug: str, name: str | None) -> None:
    password = _password(email)
    async with get_sessionmaker()() as session, session.begin():
        org = (
            await session.execute(
                sa.text("SELECT id FROM organizations WHERE slug = :s"), {"s": org_slug}
            )
        ).scalar_one_or_none()
        if org is None:
            org = (
                await session.execute(
                    sa.text("INSERT INTO organizations (name, slug) VALUES (:n, :s) RETURNING id"),
                    {"n": org_slug, "s": org_slug},
                )
            ).scalar_one()
        await create_user(session, email=email, password=password, organization_id=org,
                          role=role, full_name=name)  # fmt: skip
        await session.execute(
            sa.text(
                "INSERT INTO audit_logs (organization_id, action, entity_type, changes) "
                "VALUES (:o, 'member.added_cli', 'user', CAST(:c AS jsonb))"
            ),
            {"o": org, "c": json.dumps({"email": email, "role": role.value})},
        )
    print(f"Created {role.value} {email} in organization '{org_slug}'.")


async def _set_password(email: str) -> None:
    password = _password(email)
    async with get_sessionmaker()() as session, session.begin():
        uid = (
            await session.execute(
                sa.text("UPDATE users SET password_hash = :h, updated_at = now() "
                        "WHERE lower(email) = lower(:e) RETURNING id"),
                {"h": crypto.hash_password(password), "e": email},
            )
        ).scalar_one_or_none()  # fmt: skip
        if uid is None:
            sys.exit(f"No user with email {email}")
        await revoke_user_sessions(session, uid, utcnow(), except_session=None)
    print(f"Password set for {email}; existing sessions were signed out.")


def main(argv: list[str] | None = None) -> None:
    configure_logging("WARNING", get_settings().log_format)
    parser = argparse.ArgumentParser(prog="python -m services.auth")
    sub = parser.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create-user", help="Create a user and add them to an organization")
    c.add_argument("--email", required=True)
    c.add_argument("--role", choices=[r.value for r in MemberRole], default="owner")
    c.add_argument("--org", default="local", help="Organization slug (created if missing)")
    c.add_argument("--name")
    p = sub.add_parser("set-password", help="Reset a user's password")
    p.add_argument("--email", required=True)
    args = parser.parse_args(argv)

    async def run() -> None:
        try:
            if args.cmd == "create-user":
                await _create(args.email, MemberRole(args.role), args.org, args.name)
            else:
                await _set_password(args.email)
        except AuthError as exc:
            sys.exit(exc.message)
        finally:
            await get_async_engine().dispose()

    asyncio.run(run())


if __name__ == "__main__":
    main()
