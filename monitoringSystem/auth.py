"""
Sign-in state for the dashboard.

The token and the user's details live only in the signed, HttpOnly Flask
session cookie - never in a dcc.Store or anywhere browser JavaScript can
read. This is for the user's experience; the API enforces the real rules.
"""
from datetime import datetime, timezone

from flask import session

import api_client
from api_client import NotAuthenticated


def _start_session(token):
    session.clear()
    session.permanent = True  # gives the cookie its 8-hour lifetime
    session["token"] = token["access_token"]
    session["expires_at"] = token["expires_at"]
    session["user"] = token["user"]
    return token["user"]


def register(email, password, display_name=None):
    """Creates the account and signs the user in straight away."""
    return _start_session(api_client.register(email, password, display_name))


def login(email, password):
    return _start_session(api_client.login(email, password))


def logout():
    session.clear()


def has_session():
    """True if the browser holds a sign-in, even one that has run out."""
    return "token" in session


def is_signed_in():
    """True while the session holds a token that has not reached its expiry.

    A local check only. The API has the final say on every call, e.g. after
    the account is deleted somewhere else.
    """
    if not has_session():
        return False
    try:
        expires_at = datetime.fromisoformat(session.get("expires_at", ""))
    except ValueError:
        return False
    return expires_at > datetime.now(timezone.utc)


def current_user(refresh=False):
    """The signed-in user as {id, email, display_name}, or None.

    With refresh=True the API is asked, which also proves the token still
    works: it raises NotAuthenticated (session cleared) or ApiUnavailable.
    """
    if not refresh:
        return session.get("user") if is_signed_in() else None
    if not is_signed_in():
        logout()
        raise NotAuthenticated()
    return remember_user(api_client.get_me())


def remember_user(user):
    """Keeps the session's copy of the user in step after a profile change."""
    session["user"] = user
    return user


def update_display_name(display_name):
    return remember_user(api_client.update_me(display_name))


def change_password(current_password, new_password):
    api_client.change_password(current_password, new_password)


def delete_account(password):
    """Deletes the account and everything it owns, then ends the session."""
    api_client.delete_me(password)
    logout()
