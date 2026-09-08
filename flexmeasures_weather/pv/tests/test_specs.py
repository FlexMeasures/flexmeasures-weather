"""Tests for the PV array attribute contract.

No database and no Flask, so the module under test is loaded by path and these run with
``pytest --noconftest``. See the note at the top of ``test_solar_power.py``.
"""

import importlib.util
from pathlib import Path

import pytest


def _load_module_by_path(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


specs = _load_module_by_path("specs", Path(__file__).parent.parent / "specs.py")
read_pv_specs = specs.read_pv_specs
DEFAULT_LOSSES = specs.DEFAULT_LOSSES


class FakeAsset:
    """Stands in for a GenericAsset: all `read_pv_specs` needs is these three members."""

    def __init__(self, attributes: dict, name: str = "Test PV array", id: int = 7):
        self.attributes = attributes
        self.name = name
        self.id = id

    def get_attribute(self, attribute, default=None):
        return self.attributes.get(attribute, default)


COMPLETE = {
    "pv_tilt": 30,
    "pv_azimuth": 180,
    "pv_capacity_in_kw": 5.4,
    "pv_losses": 0.1,
}


def test_complete_specs_are_read_as_estimate_pv_power_keyword_arguments():
    specs = read_pv_specs(FakeAsset(COMPLETE))

    assert specs == {
        "tilt": 30.0,
        "azimuth": 180.0,
        "capacity": 5.4,
        "losses": 0.1,
        "mount": "close_mount",
    }


def test_losses_default_to_the_pvwatts_default():
    attributes = {key: value for key, value in COMPLETE.items() if key != "pv_losses"}

    assert read_pv_specs(FakeAsset(attributes))["losses"] == DEFAULT_LOSSES


def test_mount_defaults_to_close_mount():
    attributes = {key: value for key, value in COMPLETE.items() if key != "pv_mount"}

    assert read_pv_specs(FakeAsset(attributes))["mount"] == specs.DEFAULT_MOUNT


def test_an_unknown_mount_is_rejected():
    attributes = {**COMPLETE, "pv_mount": "on_a_pole_in_a_lake"}

    with pytest.raises(ValueError, match="pv_mount"):
        read_pv_specs(FakeAsset(attributes))


@pytest.mark.parametrize("missing", ["pv_tilt", "pv_azimuth", "pv_capacity_in_kw"])
def test_a_missing_attribute_is_named_along_with_how_to_set_it(missing):
    attributes = {key: value for key, value in COMPLETE.items() if key != missing}

    with pytest.raises(ValueError) as error:
        read_pv_specs(FakeAsset(attributes))

    message = str(error.value)
    assert missing in message
    assert "Missing data for required field" in message
    # the message should tell the user what to run, for this asset
    assert "flexmeasures weather register-pv-array --asset-id 7" in message
    assert "Test PV array" in message


def test_all_missing_attributes_are_reported_at_once():
    with pytest.raises(ValueError) as error:
        read_pv_specs(FakeAsset({}))

    message = str(error.value)
    assert all(
        attribute in message
        for attribute in ("pv_tilt", "pv_azimuth", "pv_capacity_in_kw")
    )


@pytest.mark.parametrize(
    "attribute, value",
    [
        ("pv_tilt", -1),
        ("pv_tilt", 91),
        ("pv_azimuth", -0.5),
        ("pv_azimuth", 361),
        ("pv_capacity_in_kw", -1),
        ("pv_losses", -0.1),
        ("pv_losses", 1.0),  # a totally lossy array is not a useful spec
        ("pv_losses", 1.5),
    ],
)
def test_out_of_range_values_are_rejected(attribute, value):
    with pytest.raises(ValueError, match=attribute):
        read_pv_specs(FakeAsset({**COMPLETE, attribute: value}))


@pytest.mark.parametrize(
    "attribute, value",
    [
        ("pv_tilt", 0),
        ("pv_tilt", 90),
        ("pv_azimuth", 0),
        ("pv_azimuth", 360),
        ("pv_capacity_in_kw", 0),
        ("pv_losses", 0),
        ("pv_losses", 0.99),
    ],
)
def test_boundary_values_are_accepted(attribute, value):
    read_pv_specs(FakeAsset({**COMPLETE, attribute: value}))


def test_a_non_numeric_attribute_is_rejected():
    with pytest.raises(ValueError, match="pv_tilt"):
        read_pv_specs(FakeAsset({**COMPLETE, "pv_tilt": "steep"}))


def test_overrides_take_precedence_over_the_asset_attributes():
    specs = read_pv_specs(FakeAsset(COMPLETE), overrides={"tilt": 15, "losses": 0.2})

    assert specs["tilt"] == 15.0
    assert specs["losses"] == 0.2
    assert specs["azimuth"] == 180.0


def test_overrides_can_complete_a_partial_asset():
    attributes = {key: value for key, value in COMPLETE.items() if key != "pv_tilt"}

    assert read_pv_specs(FakeAsset(attributes), overrides={"tilt": 25})["tilt"] == 25.0


def test_overrides_are_validated_too():
    with pytest.raises(ValueError, match="pv_tilt"):
        read_pv_specs(FakeAsset(COMPLETE), overrides={"tilt": 120})


def test_none_valued_overrides_are_ignored():
    """Click passes None for options the user did not give."""
    specs = read_pv_specs(FakeAsset(COMPLETE), overrides={"tilt": None, "losses": None})

    assert specs["tilt"] == 30.0
    assert specs["losses"] == 0.1


def test_unknown_overrides_are_rejected():
    with pytest.raises(ValueError, match="orientation"):
        read_pv_specs(FakeAsset(COMPLETE), overrides={"orientation": 180})
