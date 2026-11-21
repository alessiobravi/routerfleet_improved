"""Actions that change something only answer a POST.

A GET can be triggered by any page the user visits - an image tag pointing at
/router/manage/?uuid=...&action=delete is enough - while a POST goes through
Django's CSRF check, which only a page served by this installation can pass.
The confirmation word a user types is a guard against a slip, not against a
forged request: it is the same word for everyone.
"""


def posted_action(request, name):
    """True only for a POST asking for this action; its CSRF token was checked
    by the middleware before the view ran."""
    return request.method == 'POST' and request.POST.get('action') == name


def posted_confirmation(request):
    """The confirmation typed into the dialog, from a POST only."""
    return request.POST.get('confirmation') if request.method == 'POST' else None
