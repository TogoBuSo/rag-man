from __future__ import annotations

from collections.abc import Callable

from django.http import HttpRequest, HttpResponse


class SimpleCORSMiddleware:
    """Allow cross-origin API access from any client during debugging."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if request.method == "OPTIONS":
            response = HttpResponse(status=204)
        else:
            response = self.get_response(request)

        response["Access-Control-Allow-Origin"] = "*"
        response["Access-Control-Allow-Methods"] = "GET, POST, PUT, PATCH, DELETE, OPTIONS"
        response["Access-Control-Allow-Headers"] = "Authorization, X-API-Key, Content-Type, Accept"
        return response
