"""T04 — the extracted error-code table.

The table is generated from the Go sources by ``scripts/extract_error_codes.py``.
These tests pin its shape, pin a few entries byte-for-byte, and guard against the
generated file drifting away from the Go sources.
"""

from pathlib import Path

from calton.core import error_codes
from calton.core.error_codes import ERROR_CODES

REPO_ROOT = Path(__file__).resolve().parents[3]
SERVER_ROOT = REPO_ROOT / "server"
GENERATED = SERVER_ROOT / "src" / "calton" / "core" / "error_codes.py"

# Code values upstream reuses for two different errors each. Keying the table by
# code value (or by constant name) would silently drop one error of each pair,
# which is why the key is "<source>.<GoErrorType>".
KNOWN_COLLIDING_VALUES = {1022, 1025, 1026, 6002}


def test_keys_are_source_qualified_go_error_types() -> None:
    for key, spec in ERROR_CODES.items():
        assert key == f"{spec.source}.{spec.error}"
        assert spec.source in {"models", "user"}


def test_distinct_code_values_reveal_the_upstream_collisions() -> None:
    values = [spec.code for spec in ERROR_CODES.values()]
    assert len(set(values)) == 128

    colliding = {v for v in values if values.count(v) > 1}
    assert colliding == KNOWN_COLLIDING_VALUES


def test_collisions_are_exactly_these_pairs() -> None:
    by_value: dict[int, set[str]] = {}
    for key, spec in ERROR_CODES.items():
        by_value.setdefault(spec.code, set()).add(key)

    assert by_value[1022] == {
        "user.ErrOpenIDCustomScopeMalformed",
        "user.ErrUsernameMustNotContainSpaces",
    }
    assert by_value[1025] == {"user.ErrTOTPPasscodeUsed", "user.ErrInvalidTimezone"}
    assert by_value[1026] == {"user.ErrAccountLocked", "user.ErrUsernameReserved"}
    # pkg/models/error.go declares ErrCodeOIDCTeamDoesNotExist = 6008 but never
    # returns it; ErrExternalTeamDoesNotExist returns 6002 instead. Copied as-is.
    assert by_value[6002] == {"models.ErrTeamDoesNotExist", "models.ErrExternalTeamDoesNotExist"}
    assert 6008 not in by_value


def test_the_same_type_name_in_both_files_stays_distinct() -> None:
    assert ERROR_CODES["models.ErrInvalidTimezone"].code == 2003
    assert ERROR_CODES["user.ErrInvalidTimezone"].code == 1025


def test_spot_check_4016_invalid_task_field() -> None:
    spec = ERROR_CODES["models.ErrInvalidTaskField"]
    assert spec.const == "ErrCodeInvalidTaskField"
    assert spec.code == 4016
    assert spec.http_status == 400
    assert spec.message == "The task field '{task_field}' is invalid."
    assert spec.template_fields == ("task_field",)
    assert spec.i18n_params == ()


def test_spot_check_3001_project_does_not_exist() -> None:
    spec = ERROR_CODES["models.ErrProjectDoesNotExist"]
    assert spec.const == "ErrCodeProjectDoesNotExist"
    assert spec.code == 3001
    assert spec.http_status == 404
    assert spec.message == "This project does not exist."
    assert spec.template_fields == ()
    assert spec.i18n_params == ()


def test_spot_check_2003_invalid_timezone_carries_i18n_params() -> None:
    spec = ERROR_CODES["models.ErrInvalidTimezone"]
    assert spec.const == "ErrCodeInvalidTimezone"
    assert spec.code == 2003
    assert spec.http_status == 400
    assert spec.message == "The timezone '{name}' is invalid"
    assert spec.template_fields == ("name",)
    # i18n_params maps the client-facing placeholder onto the template field that
    # supplies its value, so both render from one set of arguments.
    assert spec.i18n_params == (("timezone", "name"),)


def test_every_spec_has_a_plausible_http_status() -> None:
    for key, spec in ERROR_CODES.items():
        assert 400 <= spec.http_status <= 599, key


def test_message_templates_are_formattable_and_free_of_go_verbs() -> None:
    for key, spec in ERROR_CODES.items():
        spec.message.format(**{field: "x" for field in spec.template_fields})
        assert "%s" not in spec.message, key
        assert "%d" not in spec.message, key
        assert "%v" not in spec.message, key


def test_i18n_params_always_reference_a_real_template_field() -> None:
    for key, spec in ERROR_CODES.items():
        for _, field in spec.i18n_params:
            assert field in spec.template_fields, key


def test_lookup_by_value_rejects_the_colliding_codes() -> None:
    assert error_codes.spec_for_value(3001).name == "models.ErrProjectDoesNotExist"
    for value in KNOWN_COLLIDING_VALUES:
        try:
            error_codes.spec_for_value(value)
        except error_codes.AmbiguousErrorCodeError:
            pass
        else:
            raise AssertionError(f"code {value} is ambiguous and must not resolve")


def test_lookup_by_value_rejects_an_unknown_code() -> None:
    try:
        error_codes.spec_for_value(999999)
    except LookupError:
        pass
    else:
        raise AssertionError("unknown code must not resolve")
