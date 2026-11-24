from __future__ import annotations

import re

from django.db.models import Q
from ninja.errors import HttpError

from apps.retrieval.schemas import MetadataFilter

FIELD_PATH_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*(\.[a-zA-Z_][a-zA-Z0-9_]*)*$")


def build_metadata_q(filters: list[MetadataFilter]) -> Q:
    """Safely translate structured MetadataFilter entries into parameterized Django Q objects."""
    combined = Q()
    for f in filters:
        if not FIELD_PATH_RE.match(f.field):
            raise HttpError(422, f"Invalid metadata filter field: '{f.field}'")

        lookup_base = "metadata__" + f.field.replace(".", "__")

        if f.operator == "eq":
            combined &= Q(**{lookup_base: f.value})
        elif f.operator == "ne":
            combined &= ~Q(**{lookup_base: f.value})
        elif f.operator == "in":
            if not isinstance(f.value, list):
                raise HttpError(422, f"Operator 'in' requires a list value for field '{f.field}'.")
            combined &= Q(**{f"{lookup_base}__in": f.value})
        elif f.operator in ("gt", "gte", "lt", "lte", "contains"):
            combined &= Q(**{f"{lookup_base}__{f.operator}": f.value})
        else:
            raise HttpError(422, f"Unsupported filter operator: '{f.operator}'")

    return combined
