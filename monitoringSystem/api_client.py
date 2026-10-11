"""
The only module that talks to the CompostIQ API.

Calls are made from the server, inside Dash callbacks - the browser never
sees the API or the token. The signed-in user's token is read from the Flask
session and sent as a Bearer header.

Three things can go wrong, and callers handle each differently:
  NotAuthenticated  the token is missing, expired or no longer valid; the
                    session has already been cleared
  ApiUnavailable    the API could not be reached, or it failed (5xx)
  ApiError          the API refused the request (409, 422, a wrong password...)
"""
import requests
from flask import session

from settings import API_URL

# (connect, read) in seconds - a slow API must not hang a gunicorn worker
TIMEOUT = (3.05, 10)


class NotAuthenticated(Exception):
    pass


class ApiUnavailable(Exception):
    pass


class ApiError(Exception):
    def __init__(self, status, detail):
        super().__init__(f"{status}: {detail}")
        self.status = status
        self.detail = detail

    @property
    def fields(self):
        """The request fields a 422 complained about, e.g. {"email"}."""
        if not isinstance(self.detail, list):
            return set()
        return {str(error["loc"][-1]) for error in self.detail if error.get("loc")}


def _request(method, path, json=None, signed_in=True):
    headers = {}
    if signed_in:
        token = session.get("token")
        if not token:
            raise NotAuthenticated()
        headers["Authorization"] = f"Bearer {token}"

    try:
        response = requests.request(method, f"{API_URL}{path}", json=json, headers=headers, timeout=TIMEOUT)
    except requests.RequestException as error:
        raise ApiUnavailable(str(error)) from error

    if response.status_code >= 500:
        raise ApiUnavailable(f"{response.status_code} from {path}")
    # The API marks a rejected token with a WWW-Authenticate header. A wrong
    # password is also a 401 but has no header, and must not end the session.
    if response.status_code == 401 and signed_in and "bearer" in response.headers.get("WWW-Authenticate", "").lower():
        session.clear()
        raise NotAuthenticated()
    if response.status_code >= 400:
        try:
            detail = response.json().get("detail")
        except ValueError:
            detail = response.text
        raise ApiError(response.status_code, detail)

    return response.json() if response.content else None


# -------------------------------------------------------------------- auth ---

def register(email, password, display_name=None):
    body = {"email": email, "password": password, "display_name": display_name}
    return _request("POST", "/auth/register", json=body, signed_in=False)


def login(email, password):
    return _request("POST", "/auth/login", json={"email": email, "password": password}, signed_in=False)


def get_me():
    return _request("GET", "/auth/me")


def update_me(display_name):
    return _request("PATCH", "/auth/me", json={"display_name": display_name})


def change_password(current_password, new_password):
    body = {"current_password": current_password, "new_password": new_password}
    return _request("POST", "/auth/change-password", json=body)


def delete_me(password):
    return _request("DELETE", "/auth/me", json={"password": password})


# ----------------------------------------------------------------- pairing ---
# The device redeems the code itself (POST /pairing/redeem); the dashboard
# issues it, watches for it being used, then sets the device up.

def create_pairing_code():
    return _request("POST", "/pairing/codes")


def pairing_status(code):
    return _request("GET", f"/pairing/codes/{code}")


# ----------------------------------------------------------- devices, bins ---

def list_devices():
    return _request("GET", "/devices")


def register_device(name, bin_id):
    """Register a device without pairing: {device, api_key}. The key comes back
    this once - show it to the user and never keep it."""
    return _request("POST", "/devices", json={"name": name, "bin_id": bin_id})


def get_device(device_id):
    return _request("GET", f"/devices/{device_id}")


def new_device_key(device_id):
    """A registered device's new key, {device, api_key}; its old key stops
    working at once. Shown to the user once, never kept."""
    return _request("POST", f"/devices/{device_id}/key")


def set_up_device(device_id, name, bin_id):
    return _request("POST", f"/devices/{device_id}/setup", json={"name": name, "bin_id": bin_id})


def unpair_device(device_id):
    return _request("DELETE", f"/devices/{device_id}")


def list_bins():
    return _request("GET", "/bins")


def create_bin(name, country_code, location=""):
    body = {"name": name, "location": location or "", "country_code": country_code}
    return _request("POST", "/bins", json=body)


def get_bin(bin_id):
    return _request("GET", f"/bins/{bin_id}")


def update_bin(bin_id, name=None, location=None, country_code=None):
    body = {"name": name, "location": location, "country_code": country_code}
    return _request("PATCH", f"/bins/{bin_id}", json={key: value for key, value in body.items() if value is not None})


def delete_bin(bin_id):
    return _request("DELETE", f"/bins/{bin_id}")


def get_bin_records(bin_id, hours=24):
    return _request("GET", f"/bins/{bin_id}/records?hours={hours}")


def get_bin_history(bin_id, days=30):
    return _request("GET", f"/bins/{bin_id}/history?days={days}")


def update_device(device_id, name=None, bin_id=None):
    body = {"name": name, "bin_id": bin_id}
    return _request("PATCH", f"/devices/{device_id}", json={key: value for key, value in body.items() if value is not None})


def get_device_records(device_id, hours=24):
    return _request("GET", f"/devices/{device_id}/records?hours={hours}")


# ------------------------------------------------------------------ alerts ---

def list_alerts(bin_id=None, include_resolved=False):
    path = f"/alerts?include_resolved={str(include_resolved).lower()}"
    if bin_id:
        path += f"&bin_id={bin_id}"
    return _request("GET", path)
