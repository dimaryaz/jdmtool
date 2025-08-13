import requests


FLY_HOST = "https://fly.garmin.com"


def list_aircraft(access_token: str) -> list:
    resp = requests.get(
        f"{FLY_HOST}/fly-garmin/api/aircraft/",
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
