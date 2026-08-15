from copy import deepcopy

from telepiplex_plugin_sdk.media_metadata import (
    validate_media_metadata,
    validate_media_metadata_detailed,
)


def _valid_contract() -> dict:
    return {
        "schema_version": 1,
        "metadata_id": "pressure-movie",
        "confirmed": True,
        "identity": {
            "chinese_title": "布达佩斯大饭店",
            "english_title": "The Grand Budapest Hotel",
            "year": "2014",
            "content_kind": "movie",
            "external_ids": {"tmdb": "120467"},
        },
        "relation": {"target_series": None, "source": "confirmed"},
        "placement": {
            "category_kind": "live_action_movie",
            "library_type": "movie",
            "mapping_kind": "standalone",
            "season_number": None,
            "episode_number": None,
        },
        "evidence": {},
        "warnings": [],
        "items": [],
    }


def _mutate_invalid(value: dict, kind: int) -> None:
    if kind == 0:
        value["schema_version"] = 99
    elif kind == 1:
        value["metadata_id"] = ""
    elif kind == 2:
        value["confirmed"] = False
    elif kind == 3:
        value["identity"] = []
    elif kind == 4:
        value["identity"]["content_kind"] = "unknown"
    elif kind == 5:
        value["placement"]["category_kind"] = "animated_series"
    elif kind == 6:
        value["placement"]["mapping_kind"] = "invented"
    elif kind == 7:
        value["warnings"] = "none"
    elif kind == 8:
        value["items"] = [{
            "season_number": 1,
            "episode_number": 0,
        }]
    else:
        value["placement"]["season_number"] = 1


def test_media_metadata_diagnostics_pressure_10_000_contracts():
    baseline = _valid_contract()
    for index in range(10_000):
        value = deepcopy(baseline)
        if index % 11 == 0:
            validated, issue = validate_media_metadata_detailed(
                value,
                require_confirmed=True,
            )
            assert validated == baseline
            assert validated is not value
            assert issue is None
            continue

        _mutate_invalid(value, index % 10)
        validated, issue = validate_media_metadata_detailed(
            value,
            require_confirmed=True,
        )

        assert validated is None
        assert issue is not None
        assert issue["path"].startswith("$")
        assert issue["reason_code"]
        assert issue["detail"]
        assert validate_media_metadata(
            value,
            require_confirmed=True,
        ) is None
