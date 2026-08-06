"""Base model implementing Go's JSON omission semantics.

Pydantic cannot drop a key from an annotation alone, so the marker classes in
``db.types`` (``OmitZero``/``OmitEmptyPtr``/``OmitEmptyCollection``) are read back here by
a wrapping serializer.
Nothing else in Calton may emit ``null`` for a timestamp — see ``db.types``.
"""

from __future__ import annotations

from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    GetJsonSchemaHandler,
    SerializerFunctionWrapHandler,
    model_serializer,
)
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import CoreSchema

from calton.db.types import ZERO_TIME, OmitEmptyCollection, OmitEmptyPtr, OmitZero


class CaltonModel(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True,
        # Read-modify-write clients hand back whole objects including read-only and
        # unknown fields; those must be ignored rather than rejected (design R4).
        extra="ignore",
        # Named ser_json_by_alias in the design doc; that spelling was renamed in
        # Pydantic 2.11.
        serialize_by_alias=True,
    )

    @classmethod
    def __get_pydantic_json_schema__(
        cls, core_schema: CoreSchema, handler: GetJsonSchemaHandler
    ) -> JsonSchemaValue:
        """Keep the OpenAPI schema from being erased by the wrap serializer below.

        Pydantic builds the *serialization*-mode JSON schema from a model serializer's
        return annotation. Ours is ``dict[str, Any]``, so every model here would document
        itself as ``{"type": "object", "additionalProperties": true}`` — no properties at
        all. FastAPI documents responses in serialization mode, so that is what clients
        and CI would see.

        The consequence is not cosmetic and not loud: the AC-2 contract diff compares
        response field names, and comparing against an empty set passes *vacuously* —
        every endpoint using a CaltonModel would be certified as matching upstream while
        declaring nothing. ``openapi-typescript`` would likewise hand the frontend ``{}``.
        It is exactly the failure convention C-1 bans ``-> dict[str, Any]`` handlers for,
        one layer lower down, where the handler does everything right and the model erases
        itself. Found by T18: the five task operations reported all 36 fields missing.

        In serialization mode we therefore generate from the inner model schema, which is
        the field-by-field one the serializer wraps. Nested models and ``$defs`` come out
        unchanged; only the "a serializer replaced this" step is skipped.
        """
        if handler.mode == "serialization":
            return handler(core_schema.get("schema", core_schema))
        return handler(core_schema)

    @model_serializer(mode="wrap")
    def _apply_go_omission(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data: dict[str, Any] = handler(self)

        for name, field in type(self).model_fields.items():
            key = field.alias or name
            if key not in data:
                continue

            value = getattr(self, name)
            omit_zero = any(isinstance(m, OmitZero) for m in field.metadata)
            omit_ptr = any(isinstance(m, OmitEmptyPtr) for m in field.metadata)
            # Go's omitempty drops an empty slice/map as well as a nil one. Tested with
            # len() rather than truthiness so a field holding 0/False/"" is never caught
            # by it — only the two markers' own field sets are affected.
            omit_collection = any(isinstance(m, OmitEmptyCollection) for m in field.metadata)

            if (
                (omit_zero and value == ZERO_TIME)
                or (omit_ptr and value is None)
                or (omit_collection and (value is None or len(value) == 0))
            ):
                del data[key]

        return data
