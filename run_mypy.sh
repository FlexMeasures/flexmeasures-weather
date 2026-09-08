#!/bin/bash
set -e
pip install --upgrade mypy > 1.4
pip install types-pytz types-requests types-Flask types-click types-redis types-tzlocal types-python-dateutil types-setuptools
files=$(find flexmeasures_weather -name \*.py)
# Note: --follow-imports=skip is deliberately not used here. It makes mypy treat
# imported modules as untyped stubs, which loses the pydantic plugin's synthesized
# signatures for HourlyForecast (base.py) and produces false "unexpected keyword
# argument" errors at every call site.
mypy --ignore-missing-imports $files 
