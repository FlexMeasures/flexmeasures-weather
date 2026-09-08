"""The attribute contract for a PV array: tilt, azimuth, DC capacity and losses, stored as
`pv_`-prefixed attributes on the PV `GenericAsset`. See docs/pv-power-design.md.
"""

from __future__ import annotations

from marshmallow import Schema, ValidationError, fields
from marshmallow.validate import Range, OneOf

# PVWatts' default aggregate system losses (soiling, shading, snow, mismatch, wiring,
# connections, light-induced degradation, nameplate rating, availability).
DEFAULT_LOSSES = 0.14

# Mount choices, mirroring `flexmeasures_weather.pv.utils.solar_power.MOUNT_TEMPERATURE_MODELS`.
# Duplicated (rather than imported) so this module stays free of pvlib/pandas and loadable
# by path in tests without a database or Flask app - see `pv/tests/test_specs.py`.
PV_MOUNT_CHOICES = ("open_rack", "close_mount", "insulated_back")
DEFAULT_MOUNT = "close_mount"

# Asset attribute name -> the keyword argument of
# `flexmeasures_weather.pv.utils.solar_power.estimate_pv_power` it feeds.
PV_ATTRIBUTES = {
    "pv_tilt": "tilt",
    "pv_azimuth": "azimuth",
    "pv_capacity_in_kw": "capacity",
    "pv_losses": "losses",
    "pv_mount": "mount",
}

REGISTER_COMMAND = "flexmeasures weather register-pv-array"


class PVSpecsSchema(Schema):
    """Validate a complete set of PV array specs, as stored in asset attributes."""

    pv_tilt = fields.Float(
        required=True,
        validate=Range(min=0, max=90),
        metadata={"description": "Tilt from horizontal in degrees. 0 is flat."},
    )
    pv_azimuth = fields.Float(
        required=True,
        validate=Range(min=0, max=360),
        metadata={
            "description": "Direction the array faces, in degrees clockwise from north."
            " 180 is south."
        },
    )
    pv_capacity_in_kw = fields.Float(
        required=True,
        validate=Range(min=0),
        metadata={"description": "DC nameplate capacity of the array in kW (kWp)."},
    )
    pv_losses = fields.Float(
        load_default=DEFAULT_LOSSES,
        validate=Range(min=0, max=1, max_inclusive=False),
        metadata={
            "description": "Fraction of DC output lost to soiling, shading, mismatch,"
            f" wiring and the like. Defaults to {DEFAULT_LOSSES}."
        },
    )
    pv_mount = fields.Str(
        load_default=DEFAULT_MOUNT,
        validate=OneOf(PV_MOUNT_CHOICES),
        metadata={
            "description": "How the array is mounted, affecting how hot the cells run:"
            " 'open_rack' (ground/pole-mounted or well-ventilated),"
            " 'close_mount' (roof-racked with limited airflow behind the panels), or"
            " 'insulated_back' (flush-mounted or building-integrated, no airflow behind"
            f" the panels). Defaults to '{DEFAULT_MOUNT}'."
        },
    )


def read_pv_specs(asset, overrides: dict | None = None) -> dict:
    """Read the PV array specs off an asset, and validate them as a set.

    Args:
        asset: A `GenericAsset` (anything with `get_attribute`, `name` and `id`).
        overrides: Optional per-run overrides, keyed by 'tilt'/'azimuth'/'capacity'/
            'losses', for what-if runs. Validated along with the rest.

    Returns:
        Dict keyed by `estimate_pv_power`'s keyword arguments.

    Raises:
        ValueError: If a required attribute is missing or out of range.
    """
    overrides = {
        key: value for key, value in (overrides or {}).items() if value is not None
    }
    by_spec_name = {
        spec_name: attribute for attribute, spec_name in PV_ATTRIBUTES.items()
    }
    unknown = set(overrides) - set(by_spec_name)
    if unknown:
        raise ValueError(
            f"Unknown PV spec override(s): {', '.join(sorted(unknown))}."
            f" Expected any of: {', '.join(sorted(by_spec_name))}."
        )

    raw = {
        attribute: asset.get_attribute(attribute)
        for attribute in PV_ATTRIBUTES
        if asset.get_attribute(attribute) is not None
    }
    raw.update(
        {by_spec_name[spec_name]: value for spec_name, value in overrides.items()}
    )

    try:
        loaded = PVSpecsSchema().load(raw)
    except ValidationError as error:
        raise ValueError(_describe(error, asset)) from error

    return {PV_ATTRIBUTES[attribute]: value for attribute, value in loaded.items()}


def _describe(error: ValidationError, asset) -> str:
    """Turn a marshmallow error into a message that says what to do about it."""
    problems = "; ".join(
        f"{attribute}: {' '.join(messages) if isinstance(messages, list) else messages}"
        for attribute, messages in sorted(error.normalized_messages().items())
    )
    return (
        f"The PV array specs on asset '{getattr(asset, 'name', asset)}'"
        f" (ID {getattr(asset, 'id', None)}) are not usable - {problems}"
        f" Set them with: {REGISTER_COMMAND} --asset-id {getattr(asset, 'id', None)}"
        " --tilt <degrees> --azimuth <degrees> --capacity-kw <kWp> [--losses <fraction>]"
        " [--mount <open_rack|close_mount|insulated_back>]"
    )
