"""Authentication. The refresh token lives only in an httpOnly, SameSite=Strict cookie scoped to the auth path, so
scripts in the page can never read it; the short-lived access token is returned in the body and kept in memory.
Cookie-authenticated endpoints additionally require the ``X-Requested-With`` header, which forces a CORS preflight for
cross-site requests (defence in depth against CSRF)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, Header, Request, Response
from sqlalchemy import select

from flowforge_api.deps import StateDep, UserDep, auth_rate_limit
from flowforge_api.middleware import client_ip
from flowforge_api.schemas.auth import (
    ChangePasswordIn,
    LoginIn,
    MembershipOut,
    MeOut,
    ProjectOut,
    RegisterIn,
    TokenOut,
    UpdateMeIn,
    UserOut,
)
from flowforge_api.schemas.common import OkOut, error_responses
from flowforge_core.config import Settings
from flowforge_core.db.models import User
from flowforge_core.errors import AuthenticationFailed, PermissionDenied, ValidationFailed
from flowforge_core.observability import metrics
from flowforge_core.services import identity
from flowforge_core.services.identity import IssuedSession

router = APIRouter(prefix="/auth", tags=["auth"])

REFRESH_COOKIE = "ff_refresh"
COOKIE_PATH = "/api/v1/auth"


def _set_refresh_cookie(response: Response, issued: IssuedSession, settings: Settings) -> None:
    response.set_cookie(
        REFRESH_COOKIE,
        issued.refresh_token,
        max_age=settings.refresh_token_ttl_seconds,
        path=COOKIE_PATH,
        domain=settings.cookie_domain,
        secure=settings.cookie_secure,
        httponly=True,
        samesite=settings.cookie_samesite,
    )


def _clear_refresh_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        REFRESH_COOKIE,
        path=COOKIE_PATH,
        domain=settings.cookie_domain,
        secure=settings.cookie_secure,
        httponly=True,
        samesite=settings.cookie_samesite,
    )


def _require_csrf_header(x_requested_with: str | None) -> None:
    if (x_requested_with or "").lower() != "flowforge":
        raise PermissionDenied("missing X-Requested-With: flowforge header")


def _token_out(issued: IssuedSession, user: User) -> TokenOut:
    return TokenOut(
        access_token=issued.access_token,
        expires_at=issued.access_expires_at,
        user=UserOut.model_validate(user),
    )


@router.post(
    "/register",
    status_code=201,
    response_model=TokenOut,
    dependencies=[Depends(auth_rate_limit)],
    responses=error_responses(403, 409, 422, 429),
    summary="Create an account, an organization and a default project",
)
async def register(
    body: RegisterIn, request: Request, response: Response, state: StateDep
) -> TokenOut:
    if not state.settings.allow_registration:
        raise PermissionDenied("registration is disabled on this deployment")
    async with state.db.transaction() as uow:
        reg = await identity.register_user(
            uow,
            email=body.email,
            password=body.password,
            full_name=body.full_name,
            organization_name=body.organization_name,
            settings=state.settings,
        )
        issued = await identity.issue_session(
            uow.session,
            user=reg.user,
            settings=state.settings,
            user_agent=request.headers.get("user-agent"),
            ip=client_ip(request.scope, state.settings.trusted_proxy_count),
        )
    _set_refresh_cookie(response, issued, state.settings)
    return _token_out(issued, reg.user)


@router.post(
    "/login",
    response_model=TokenOut,
    dependencies=[Depends(auth_rate_limit)],
    responses=error_responses(401, 422, 429),
)
async def login(body: LoginIn, request: Request, response: Response, state: StateDep) -> TokenOut:
    async with state.db.transaction() as uow:
        outcome = await identity.authenticate(
            uow, email=body.email, password=body.password, settings=state.settings
        )
        issued = None
        if outcome.user is not None:
            issued = await identity.issue_session(
                uow.session,
                user=outcome.user,
                settings=state.settings,
                user_agent=request.headers.get("user-agent"),
                ip=client_ip(request.scope, state.settings.trusted_proxy_count),
            )
    # Raised after the commit so the failed-attempt counter / lockout is persisted.
    if outcome.user is None or issued is None:
        metrics.AUTH_FAILURES.labels("password").inc()
        raise AuthenticationFailed(
            "invalid email or password, or the account is temporarily locked after repeated failures"
        )
    _set_refresh_cookie(response, issued, state.settings)
    return _token_out(issued, outcome.user)


@router.post(
    "/refresh",
    response_model=TokenOut,
    dependencies=[Depends(auth_rate_limit)],
    responses=error_responses(401, 403, 429),
    summary="Rotate the refresh token (cookie) and issue a new access token",
)
async def refresh(
    request: Request,
    response: Response,
    state: StateDep,
    ff_refresh: Annotated[str | None, Cookie()] = None,
    x_requested_with: Annotated[str | None, Header()] = None,
) -> TokenOut:
    _require_csrf_header(x_requested_with)
    if not ff_refresh:
        raise AuthenticationFailed("no refresh session")
    async with state.db.transaction() as uow:
        outcome = await identity.rotate_refresh_token(
            uow,
            raw_token=ff_refresh,
            settings=state.settings,
            user_agent=request.headers.get("user-agent"),
            ip=client_ip(request.scope, state.settings.trusted_proxy_count),
        )
    if outcome.issued is None or outcome.user is None:
        _clear_refresh_cookie(response, state.settings)
        metrics.AUTH_FAILURES.labels("refresh_reuse" if outcome.reuse_detected else "refresh").inc()
        raise AuthenticationFailed("session expired; sign in again")
    _set_refresh_cookie(response, outcome.issued, state.settings)
    return _token_out(outcome.issued, outcome.user)


@router.post("/logout", response_model=OkOut, summary="Revoke the current refresh-token family")
async def logout(
    response: Response,
    state: StateDep,
    ff_refresh: Annotated[str | None, Cookie()] = None,
    x_requested_with: Annotated[str | None, Header()] = None,
) -> OkOut:
    _require_csrf_header(x_requested_with)
    if ff_refresh:
        async with state.db.transaction() as uow:
            await identity.revoke_session(uow, raw_token=ff_refresh)
    _clear_refresh_cookie(response, state.settings)
    return OkOut()


async def _me(state: StateDep, user: User) -> MeOut:
    async with state.db.session() as session:
        accesses = await identity.user_projects(session, user.id)
    memberships: dict[str, MembershipOut] = {}
    for access in accesses:
        key = str(access.organization.id)
        if key not in memberships:
            memberships[key] = MembershipOut(
                organization_id=access.organization.id,
                organization_name=access.organization.name,
                organization_slug=access.organization.slug,
                role=access.role.value,
                projects=[],
            )
        memberships[key].projects.append(ProjectOut.model_validate(access.project))
    return MeOut(user=UserOut.model_validate(user), memberships=list(memberships.values()))


@router.get("/me", response_model=MeOut, responses=error_responses(401))
async def me(state: StateDep, user: UserDep) -> MeOut:
    return await _me(state, user)


@router.patch("/me", response_model=MeOut, responses=error_responses(401, 422))
async def update_me(body: UpdateMeIn, state: StateDep, user: UserDep) -> MeOut:
    async with state.db.transaction() as uow:
        row = await uow.session.scalar(select(User).where(User.id == user.id).with_for_update())
        if row is None:
            raise AuthenticationFailed("account not found")
        if body.full_name is not None:
            row.full_name = body.full_name
        if body.preferences is not None:
            # Only the fields sent are changed; an explicit null clears that preference.
            patch = body.preferences.model_dump(exclude_unset=True, mode="json")
            default_project_id = body.preferences.default_project_id
            if default_project_id is not None:
                await identity.project_access(uow.session, user.id, default_project_id)
            merged = {**row.preferences, **patch}
            row.preferences = {key: value for key, value in merged.items() if value is not None}
        await uow.session.flush()
        await uow.session.refresh(row)
    return await _me(state, row)


@router.post("/change-password", response_model=OkOut, responses=error_responses(401, 422))
async def change_password(
    body: ChangePasswordIn, response: Response, state: StateDep, user: UserDep
) -> OkOut:
    async with state.db.transaction() as uow:
        row = await uow.session.scalar(select(User).where(User.id == user.id).with_for_update())
        if row is None:
            raise ValidationFailed("account not found")
        await identity.change_password(
            uow, user=row, current_password=body.current_password, new_password=body.new_password
        )
    _clear_refresh_cookie(response, state.settings)
    return OkOut()
