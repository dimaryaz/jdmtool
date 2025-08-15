import requests


API_PREFIX = "https://fly.garmin.com/fly-garmin/api"


session = requests.Session()
session.headers['User-Agent'] = None  # type: ignore


def list_aircraft(access_token: str) -> list:
    resp = session.get(
        f"{API_PREFIX}/aircraft/",
        params={
            "withAvdbs": "true",
            "withJeppImported": "true",
            "withSharedAircraft": "true",
        },
        headers={
            "Authorization": f"Bearer {access_token}",
        },
    )
    resp.raise_for_status()
    return resp.json()


def list_series(series_id: int) -> dict:
    resp = session.get(
        f"{API_PREFIX}/avdb-series/{series_id}/",
    )
    resp.raise_for_status()
    return resp.json()


def list_files(series_id: int, issue_name: str) -> dict:
    resp = session.get(
        f"{API_PREFIX}/avdb-series/{series_id}/{issue_name}/files/",
    )
    resp.raise_for_status()
    return resp.json()


def unlock(access_token: str, series_id: int, issue_name: str, device_id: int, card_serial: int) -> dict:
    resp = session.get(
        f"{API_PREFIX}/avdb-series/{series_id}/{issue_name}/unlock/",
        params={
            "deviceIDs": device_id,
            "cardSerial": card_serial,
        },
        headers={
            "Authorization": f"Bearer {access_token}",
        },
    )
    resp.raise_for_status()
    return resp.json()
