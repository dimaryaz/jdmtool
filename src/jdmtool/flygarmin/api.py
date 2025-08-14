import requests


API_PREFIX = "https://fly.garmin.com/fly-garmin/api"


def list_aircraft(access_token: str) -> list:
    resp = requests.get(
        f"{API_PREFIX}/aircraft/",
        params={
            "withAvdbs": "true",
            "withJeppImported": "true",
            "withSharedAircraft": "true",
        },
        headers={
            "Authorization": f"Bearer {access_token}",
        },
        timeout=5,
    )
    resp.raise_for_status()
    return resp.json()


def list_series(series_id: int) -> dict:
    resp = requests.get(
        f"{API_PREFIX}/avdb-series/{series_id}/",
        timeout=5,
    )
    resp.raise_for_status()
    return resp.json()


def list_files(series_id: int, issue_name: str) -> dict:
    resp = requests.get(
        f"{API_PREFIX}/avdb-series/{series_id}/{issue_name}/files/",
        timeout=5,
    )
    resp.raise_for_status()
    return resp.json()
