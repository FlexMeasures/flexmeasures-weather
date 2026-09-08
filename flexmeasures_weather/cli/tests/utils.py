from typing import List
from datetime import datetime, timedelta
from flexmeasures.utils.time_utils import as_server_time, get_timezone

from ...weather_providers import HourlyForecast


def cli_params_from_dict(d) -> List[str]:
    cli_params = []
    for k, v in d.items():
        cli_params.append(f"--{k}")
        cli_params.append(v)
    return cli_params


def mock_api_response(api_key, location):
    """A `call_api`-shaped fake, with normalized `HourlyForecast` field names. Provider is
    no longer relevant here: both OWM and WeatherAPI populate the same fields post-refactor
    (see `weather_providers/`)."""
    mock_date = datetime.now()
    mock_date_tz_aware = as_server_time(
        datetime.fromtimestamp(mock_date.timestamp(), tz=get_timezone())
    ).replace(second=0, microsecond=0)

    return mock_date_tz_aware, [
        HourlyForecast(
            time=as_server_time(
                datetime.fromtimestamp(mock_date.timestamp(), tz=get_timezone())
            ),
            temperature_c=40,
            wind_speed_mps=100,
        ),
        HourlyForecast(
            time=as_server_time(
                datetime.fromtimestamp(
                    (mock_date + timedelta(hours=1)).timestamp(), tz=get_timezone()
                )
            ),
            temperature_c=42,
            wind_speed_mps=90,
        ),
    ]
