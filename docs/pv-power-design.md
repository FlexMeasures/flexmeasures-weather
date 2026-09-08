# Design notes: predicted PV power output

This plugin already derives irradiance itself: it reads cloud cover from OpenWeatherMap or
WeatherAPI and converts it to global horizontal irradiance (GHI) via pvlib clear-sky, in
`flexmeasures_weather/utils/radiating.py`. What was missing is the step from GHI to
*expected PV power for a specific array*.

`PVWattsForecaster` (`flexmeasures_weather/pv/forecaster.py`) implements that step. It is a
FlexMeasures `Forecaster`, so it needs no new entry point of its own.

## Physical (pvlib) versus trained regressor

FlexMeasures 1.0 ships a full forecasting framework whose default model is LightGBM
(`darts.models.LightGBMModel`, one model per forecast horizon). The two approaches differ
in what they need and what they can know:

| | pvlib physical model | FlexMeasures LightGBM regressor |
| --- | --- | --- |
| Required input | tilt, azimuth, kWp, losses + an irradiance forecast | the sensor's own metered history + regressors |
| History needed | none | the UI button needs >= 2 days; the docs recommend >= 2 years |
| Needs metering at all | no | yes |
| Learns site quirks (shading, soiling, snow, inverter clipping, real orientation error, degradation) | no - has to be hand-tuned into `pv_losses` | yes, automatically |
| Interpretability | full, closed form | low |
| Probabilistic output | no | yes (`probabilistic=True`) |
| Accuracy once history exists | lower | higher |

The two approaches serve three complementary roles, in order of value:

1. **Cold start.** A newly commissioned array has no history, so LightGBM can't run at
   all, but the scheduler still needs a forecast. The physical model provides one from
   day one.
2. **A potential feature for the regressor.** FlexMeasures' docs note "the most important
   factor is often the features provided to the model". Feeding an expected-PV-watts
   series in as a `future-regressors` entry could hand LightGBM a physics prior - array
   geometry, angle of incidence, the GHI-to-plane-of-array nonlinearity - instead of
   leaving it to learn all that from raw GHI. Unverified: needs testing against a real
   regressor to confirm it helps.
3. **Reference and fault detection.** Physical estimate minus metered output is a residual
   that flags soiling, snow, shading or inverter faults.

### Using the physical estimate as a feature for the regressor

Forecast the physical estimate onto its own sensor, then use that sensor as a future
regressor when forecasting the metered PV sensor:

```bash
# 1. the physical estimate, onto a dedicated sensor (say ID 43)
flexmeasures add forecasts --sensor 43 --forecaster PVWattsForecaster

# 2. the trained forecast for the metered sensor (say ID 42), using it
flexmeasures add forecasts --sensor 42 --future-regressors 43
```

Sensor 43 must be filled far enough ahead to cover the prediction window, so schedule step
1 before step 2 (both are ordinary cron `automations`).

## Specifying PV specs

Tilt, azimuth, DC capacity and losses are physical properties of the installation, not of
a forecasting run, so they are stored as attributes on the PV `GenericAsset`:

| attribute | meaning |
| --- | --- |
| `pv_tilt` | tilt from horizontal in degrees; 0 is flat, 90 is vertical |
| `pv_azimuth` | direction faced, in degrees clockwise from north; 180 is south |
| `pv_capacity_in_kw` | DC nameplate capacity in kW (kWp) |
| `pv_losses` | aggregate DC loss fraction; defaults to the PVWatts 0.14 |
| `pv_mount` | mounting, one of `open_rack`/`close_mount`/`insulated_back`; defaults to `close_mount` |

FlexMeasures has no built-in notion of tilt or azimuth, so this plugin defines them. They
are prefixed with `pv_` because an asset's attributes are one flat namespace shared with
whatever FlexMeasures itself may add later. The asset also carries the latitude and
longitude pvlib needs, so one lookup returns the whole site.
`flexmeasures_weather/pv/specs.py` defines that contract once, used by the CLI, the forecaster and these docs.

No new sensor type was added, and nothing was added to `sensor_specs.mapping`: every entry
there is a property of a weather-station *location*, shared across accounts, whereas PV
power is per-installation.

**Known trade-off.** Because the specs live on the asset rather than in the forecaster
`config`, editing the tilt does *not* spawn a new `DataSource` the way a config change
would. Past forecasts therefore lose the link to the geometry that produced them. The
asset audit log partly covers this. The per-run `tilt` / `azimuth` / `capacity` / `losses` /
`mount` config overrides *do* spawn a new source, which is why what-if runs should use
those rather than editing the asset.

## Modelling decisions

### The PVWatts loss stage is deliberately disabled

`ModelChain.with_pvwatts` defaults to `losses_model="pvwatts"`, which applies its own
default system derate (measured: `results.losses == 0.8592`, i.e. 14.08%). Since we also
fold the caller's `losses` into the DC nameplate rating, both would apply - net about
`0.86 * 0.86 = 0.74`. Forecasts would come out ~14% low, and the `losses` knob would be
mislabelled: `losses=0` would still lose 14%, `losses=0.14` would lose 26%.

Measured for a clear 21 June, 5 kWp, south-facing, 30 degrees tilt, Utrecht (52.09, 5.12):

| configuration | peak | % of nameplate | daily kWh |
| --- | --- | --- | --- |
| both loss stages applied | 3097 W | 61.9 % | 26.3 |
| `losses_model="no_loss"`, `losses=0.14` | 3601 W | 72.0 % | 30.6 |
| `losses_model="no_loss"`, `losses=0` | 4187 W | 83.7 % | 35.6 |

70-75% of nameplate at clear-sky solar noon and ~6 kWh/kWp for a clear Dutch midsummer day
are the physically right figures; 62% is not. So `losses_model="no_loss"` is passed, and
the caller's single aggregate `losses` is the only system derate.

The alternative - dropping the fold into `pdc0` and passing `losses_parameters` to
`PVSystem` - would need all nine PVWatts loss sub-components, far more than an installation
owner knows.

`flexmeasures_weather/pv/tests/test_solar_power.py::test_losses_are_applied_exactly_once`
is the regression test for this.

### Using Erbs decomposition to derive DNI/DHI from GHI

A tilted plane sees direct and diffuse light differently, so a transposition model needs
both components, but weather providers give us GHI only. The Erbs model estimates the split
from GHI and the solar zenith angle alone. This was the main modelling risk, so it was
measured: at solar noon the Erbs split of clear-sky GHI gives DNI 764.7 / DHI 173.0 against
pvlib's true clear-sky DNI 804.6 / DHI 137.9 - but *after transposition* the plane-of-array
irradiance is **974.6 vs 978.0 W/m², a 0.35% difference**. Estimating the split from GHI
costs essentially nothing, so no provider is asked for DNI/DHI.

### Other choices

- **PVWatts rather than a module-level model** (SAPM, CEC), because PVWatts needs only
  specs an installation owner actually knows.
- **`close_mount` cell temperature parameters by default, `pv_mount` to override.**
  Roof-mounted arrays run hotter than ground/pole-mounted ones. Measured (5 kWp,
  south-facing, 30 degrees tilt, Utrecht, clear 21 June, 28°C air): `close_mount`
  (roof-racked, some airflow) is ~5.8% lower daily energy than `open_rack`, `insulated_back`
  (flush/BIPV, no airflow) is ~8.5% lower - worth exposing, so `pv_mount` is an optional asset
  attribute / forecaster override (`open_rack`/`close_mount`/`insulated_back`, default
  `close_mount`), since the difference is bigger than "a few percent" at peak-production
  hours on a hot day. `close_mount` is the default because most arrays these specs describe
  are residential roof-mounted, not ground/pole-mounted; pvlib itself has no default here
  (`PVSystem`'s `temperature_model_parameters` defaults to `None`, and
  `open_rack_glass_glass` appears only as an example value in `ModelChain.with_pvwatts`'s
  docstring, not an enforced default).
- **No hard capacity clipping.** The inverter's DC input limit is set equal to the derated
  DC rating, which rolls output off near the rating via the PVWatts inverter efficiency
  curve. A firm cap is better expressed with the framework's `upper` post-processing.
- **Vectorized.** Unlike `radiating.py:compute_irradiance`, which builds a `Location` per
  timestamp, `solar_power.py` calls pvlib once for the whole forecast horizon.
- **`--as-job` raises.** pvlib runs in milliseconds, so queueing buys nothing, and the CLI
  reads an `as_job` return as a differently shaped result (`pipeline_returns["n_jobs"]`).
  Raising beats silently ignoring the flag.

## Using Bright Sky and Open-Meteo as a blended alternative to the cloud-cover pipeline

`PVWattsForecaster` above is one physical model, but it depends on one particular
irradiance derivation.
`BlendedPVForecaster` (`blended_forecaster.py`) an alternative, combining two model outputs, found to work better for a specific Dutch site.

Both alternative providers need **no API key and no signup**, unlike WeatherAPI
(`WEATHERAPI_KEY`), so no new secrets or config plumbing was needed to add them.

### Bright Sky: DWD's MOSMIX forecast has native irradiance

Bright Sky (<https://brightsky.dev>) is a free wrapper around DWD's MOSMIX station
forecast (nearest station picked automatically from the given lat/lon). Its `/weather`
endpoint returns a *native* hourly `solar` field - measured/forecast irradiation, not just
cloud cover - so `weather_providers/brightsky.py` feeds it straight into the existing Erbs
decomposition (`pv/utils/solar_power.py::decompose_ghi`) rather than re-deriving GHI from
cloud cover.

Two things had to be gotten right, and both are regression-tested
(`weather_providers/tests/test_brightsky.py`):

- `solar` is reported at the interval's *end* timestamp, unlike this plugin's other
  fields and its own interval-start convention - left as-is, it reads about an hour late.
  Shifted onto the correct interval before use.
- The real horizon is about 10 days (a MOSMIX station forecast), confirmed against
  `api.brightsky.dev/sources`. Before a run's `first_record`, falls back to the existing
  cloud-cover-derived clear-sky estimate.

With the shift fix, this measurably improves on the existing pipeline - see
`docs/pv-power-solcast-evaluation.md` for the numbers.

### Open-Meteo (`ecmwf_ifs025`): native GHI and DHI, no decomposition needed

Open-Meteo (<https://open-meteo.com>) is also free and key-less. Requesting
`models=ecmwf_ifs025` (ECMWF's global IFS at 0.25deg resolution) returns native hourly
GHI *and* DHI directly, so no cloud-cover decomposition and no Erbs estimate is needed -
only DNI has to be backed out, solved via pvlib's closure with correct sunrise/sunset
edge handling (a manual division was tried first and produced spikes near low sun
angles).

The real horizon is about 15 days: Open-Meteo pads its response with null past a
model's actual horizon regardless of the days requested, so null rows are dropped rather than trusting the request parameter.

`models=icon_eu` (DWD's higher-resolution regional model) was also measured, at the
same Dutch site: more accurate than the existing pipeline, but visibly spikier
hour-to-hour than `ecmwf_ifs025` - real cloud-cover noise in `icon_eu` itself, not a
decomposition artifact, since a finer-resolution regional model picks up small-scale
cloud variability a coarser global model averages away. Not judged worth adding on top
of `ecmwf_ifs025`, so only the latter is the default - see "Geographic scope" below for
where that default was and wasn't re-checked, and how to override it.

`model` is a required constructor argument of `OpenMeteoProvider` precisely so this
Dutch-site choice isn't silently assumed elsewhere - a deployment picks its own by
constructing `OpenMeteoProvider(model=...)`.

### The two providers' underlying models

The two providers are not two views of the same underlying forecast - they come from
different weather models, with different resolutions, different inputs, and different
error characteristics:

- **Open-Meteo's `ecmwf_ifs025`** is a raw, global numerical weather prediction (NWP)
  model: ECMWF's IFS, gridded at 0.25 degrees, no station-level correction. Its error at
  any one point is whatever the model's physics and that grid cell's resolution leave
  unresolved.
- **Bright Sky's data** comes from DWD's MOSMIX, which is not a raw NWP grid but a
  *statistical post-processing* product: DWD blends its own regional (`ICON-EU`) and
  global (`ICON`) model runs and then regresses the result against real station
  observations at each MOSMIX station, correcting the kind of systematic local bias a
  gridded model cannot know about (a station's specific microclimate, siting, sensor
  characteristics). In practice, then: one is effectively a **global, uncorrected NWP
  model** (ECMWF), the other is a **local, observation-corrected statistical forecast**
  (DWD MOSMIX), rather than two independent looks at the same raw model.

That difference in origin is why averaging them helps: their errors are only partially
correlated, so one's local station-level correction does not fix the other's coarse-grid under-resolution, and vice versa.

Concretely, the blend averages the two providers' *independent, final PVWatts AC power
outputs*, each provider running its own DNI/DHI split, temperature and wind through its
own PVWatts model chain, rather than being averaged upstream of any of that.

A simpler-looking alternative - average the two providers' raw GHI first, then decompose
and run PVWatts once on the average - was not implemented and never measured: GHI-level
and power-level averaging aren't guaranteed to behave identically once Erbs/PVWatts's
nonlinearities (transposition, temperature derating, the inverter efficiency curve) are
in between. So each provider's GHI runs through its own full PVWatts chain, and only the
two resulting power series get averaged.

That average is a plain, unweighted mean, skipping NaN by default - which is what makes
the blend degrade gracefully rather than fail outright: past Bright Sky's ~10-day
horizon its series goes NaN, and the mean silently falls back to Open-Meteo alone out to
~15 days.

### Geographic scope: everything above was checked at one Dutch site

Mileage may vary elsewhere - all numbers above come from one site in the Netherlands. A
spot-check at other cities (Stockholm, Athens, New York, Tokyo, a handful of European
capitals) found:

- Bright Sky's `solar` field comes back null outside a cluster of NW-European cities
  (Berlin, Paris, Brussels, London stayed populated; Warsaw, Madrid and all
  non-European cities didn't) - looks like a per-MOSMIX-station property, not something
  predictable from distance or country. When null, it silently falls back to the
  weaker cloud-cover estimate, so check `api.brightsky.dev/weather?lat=..&lon=..` for
  your target site before relying on it.
- `icon_eu` is spikier than `ecmwf_ifs025` everywhere it has coverage, by a varying
  margin, and returns HTTP 400 outside Europe entirely; `ecmwf_ifs025` also beat every
  other Open-Meteo model tried outside Europe. `OpenMeteoProvider.model` therefore has
  no code default - pick one for your own site by checking directly, rather than copying the Dutch-site value.

Which providers to blend is injected, not a config field: `BlendedPVForecaster(providers=[...])`
swaps the blend without a subclass; omitting `providers` falls back to the validated
pair. This only helps callers constructing `BlendedPVForecaster` directly - the CLI
(`--forecaster BlendedPVForecaster`) always gets the default blend, since the framework
has no way to pass extra constructor arguments.

How many days ahead to request is likewise derived per run (the furthest event the
forecast needs), not a config field - both providers get the same value, and each
already truncates to its own real horizon regardless of what's requested.

### A separate forecaster, not a mode on `PVWattsForecaster`

`BlendedPVForecaster` is a second `Forecaster` subclass, kept alongside
`PVWattsForecaster` rather than replacing it, so existing WeatherAPI/OWM users are
unaffected and switch by passing `--forecaster BlendedPVForecaster`. It shares its
array-spec contract and PVWatts modelling choices with `PVWattsForecaster`.

Structurally it differs in one way: it fetches both providers live at compute time
instead of reading a pre-collected `irradiance` sensor, since neither needs a signup.

**Known trade-off, not implemented here.** Neither provider's GHI is persisted to a
sensor of its own, unlike `PVWattsForecaster`'s `irradiance` sensor - so this path
doesn't (yet) serve the reference/fault-detection or future-regressor use cases
described elsewhere in this doc. Natural follow-up, not done because it's a bigger change than this feature needed to ship.

### What was evaluated but not built

Exposing PVWatts's full loss breakdown (soiling, shading, snow, mismatch, wiring,
connections, LID, nameplate rating, age, availability) was considered as an alternative
to the single aggregate `pv_losses` knob, and rejected for all sub-components except
age:

- **Age** grows automatically over an install's life and owners can state it
  confidently, so it should be derived each run from an install date rather than a
  static number that goes stale.
- **Snow** looked promising but PVWatts's parameter is a flat annual-average %, not a
  coverage model, so it can't consume a live depth forecast - not worth a separate
  code path for installs where snow cover is rare and short-lived.
- **Soiling** has no matching forecast input (no dust/aerosol variable), so left at
  the PVWatts default.
- The remaining six are installation-specific constants nobody measures per-site;
  left at PVWatts defaults rather than adding knobs nobody can fill in confidently.
