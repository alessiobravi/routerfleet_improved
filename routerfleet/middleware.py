from django.core.exceptions import ValidationError
from django.views.defaults import page_not_found


class MalformedIdentifierMiddleware:
    """A ?uuid= that is not a UUID is a page that does not exist.

    Views look objects up with get_object_or_404(Model, uuid=...), which answers
    404 for a UUID that matches nothing - but a value that is not a UUID at all
    makes the database layer raise a ValidationError first, and that surfaced as
    a server error. Only that one error is turned into 404: any other
    ValidationError reaching this point is a real fault and keeps its 500.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_exception(self, request, exception):
        if isinstance(exception, ValidationError) and is_malformed_uuid(exception):
            return page_not_found(request, exception)
        return None


def is_malformed_uuid(error):
    """UUIDField.to_python raises code 'invalid' with the value in params."""
    return any(
        getattr(item, 'code', None) == 'invalid' and 'UUID' in str(item.message)
        for item in getattr(error, 'error_list', [error])
    )
