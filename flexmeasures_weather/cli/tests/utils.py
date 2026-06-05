from typing import List
from datetime import datetime, timedelta
from flask import current_app
from flexmeasures.utils.time_utils import as_server_time, get_timezone


def cli_params_from_dict(d) -> List[str]:
    cli_params = []
    for k, v in d.items():
        cli_params.append(f"--{k}")
        cli_params.append(v)
    return cli_params


def mock_api_response(api_key, location):
    mock_date = datetime.now()
    mock_date_tz_aware = as_server_time(
        datetime.fromtimestamp(mock_date.timestamp(), tz=get_timezone())
    ).replace(second=0, microsecond=0)

    provider = str(current_app.config.get("WEATHER_PROVIDER", ""))
    date_key = "dt"
    temp_key = "temp"
    wind_speed_key = "wind_speed"
    if provider == "WAPI":
        date_key = "time_epoch"
        temp_key = "temp_c"
        wind_speed_key = "wind_kph"

    return mock_date_tz_aware, [
        {date_key: mock_date.timestamp(), temp_key: 40, wind_speed_key: 100},
        {
            date_key: (mock_date + timedelta(hours=1)).timestamp(),
            temp_key: 42,
            wind_speed_key: 90,
        },
    ]
