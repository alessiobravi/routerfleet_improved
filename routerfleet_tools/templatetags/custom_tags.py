from django import template
from django.conf import settings
from routerfleet_tools.models import WebadminSettings

register = template.Library()


def version_string(number):
    """8001 -> '0.8.1': the stored integer spelled as the release it stands for."""
    from routerfleet.versioning import from_number
    return from_number(number)


@register.simple_tag
def tag_webadmin_version():
    webadmin_settings, settings_created = WebadminSettings.objects.get_or_create(name='webadmin_settings')
    if webadmin_settings.current_version != settings.ROUTERFLEET_VERSION:
        webadmin_settings.current_version = settings.ROUTERFLEET_VERSION
        webadmin_settings.save()
    if webadmin_settings.current_version > webadmin_settings.latest_version:
        webadmin_settings.latest_version = webadmin_settings.current_version
        webadmin_settings.save()
    if webadmin_settings.current_version == webadmin_settings.latest_version:
        webadmin_settings.update_available = False
        webadmin_settings.save()
    return {
        'current_version': settings.ROUTERFLEET_RELEASE,
        'latest_version': version_string(webadmin_settings.latest_version),
        'update_available': webadmin_settings.update_available,
        'changelog_url': getattr(settings, 'UPDATE_CHANGELOG_URL', ''),
    }


@register.simple_tag
def tag_project_repository_url():
    """Where the interface points for the project itself."""
    return getattr(settings, 'PROJECT_REPOSITORY_URL', '')


@register.simple_tag
def tag_project_support_url():
    """Where the interface points for questions and device requests."""
    return getattr(settings, 'PROJECT_SUPPORT_URL', '')


@register.simple_tag
def tag_update_state():
    """The top bar's update icon: green on the latest release, yellow when a
    newer one exists, grey when the check is off or has not run yet."""
    from dashboard.views import update_check_state
    state = update_check_state()
    release = settings.ROUTERFLEET_RELEASE
    if not state['enabled']:
        return {'color': 'text-secondary', 'title': f'RouterFleet Improved {release} - update check off', 'href': '/updates/', 'external': False}
    # The icon opens What's new in the app: what the newer releases bring
    if state['available']:
        return {'color': 'text-warning', 'title': f"Update available: RouterFleet Improved {state['latest']} (running {release}) - what's new",
                'href': '/updates/', 'external': False}
    if state['checked']:
        return {'color': 'text-success', 'title': f'RouterFleet Improved {release} - the latest release',
                'href': '/updates/', 'external': False}
    return {'color': 'text-secondary', 'title': f'RouterFleet Improved {release} - updates not checked yet',
            'href': '/updates/', 'external': False}


@register.filter
def startswith(value, prefix):
    """The menu marks the section a page belongs to by the START of its address:
    /fleet_commander/router/execute/ is Fleet Commander, not Router Manager."""
    return str(value or '').startswith(str(prefix))


@register.simple_tag
def page_messages(messages, form=None):
    """The messages queued for the page and the errors of a form that was not
    saved, as plain data for notify.js (template_messages.html)."""
    queued = [[message.tags or 'info', str(message)] for message in (messages or [])]
    errors = []
    if form is not None and getattr(form, 'errors', None):
        for field_errors in form.errors.values():
            errors.extend(str(error) for error in field_errors)
    return {'messages': queued, 'errors': errors}


# One icon per meaning, everywhere a state is shown: a green tick when all is
# well; the warning sign in yellow, orange or red as it gets more serious.
STATE_ICONS = {
    'ok': 'fa-circle-check',
    'warn': 'fa-triangle-exclamation',      # yellow: worth a look
    'high': 'fa-triangle-exclamation',      # orange: needs attention soon
    'bad': 'fa-triangle-exclamation',       # red: act now
    'info': 'fa-circle-info',
    'run': 'fa-arrows-rotate',
    'wait': 'fa-hourglass-half',
    'purple': 'fa-ban',
}


@register.filter
def state_icon(state, label=''):
    """The icon of a state ('ok', 'warn', 'high', 'bad', 'info', 'run',
    'wait'). With a label the icon stands alone and says it (tooltip, screen
    readers); without one it sits beside text that says it."""
    from django.utils.html import format_html
    state = state if state in STATE_ICONS else 'info'
    if label:
        return format_html('<i class="fas {} rf-state rf-state-{}" role="img" aria-label="{}" title="{}"></i>',
                           STATE_ICONS[state], state, label, label)
    return format_html('<i class="fas {} rf-state rf-state-{}" aria-hidden="true"></i>', STATE_ICONS[state], state)


# ---------------------------------------------------------------------------
# Forms in the settings layout (grouped rows), from the crispy layout they
# already have: fields side by side in a row stay so, checkboxes are switches,
# buttons and links go to the foot.
# ---------------------------------------------------------------------------

def _layout_blocks(layout):
    """The crispy layout as blocks: rows of field names (fields share a row as
    their column widths allow), section headings, buttons and links."""
    import re
    from django.utils.html import strip_tags
    from crispy_forms.layout import HTML, Submit, Button, Field
    blocks = []

    def is_cell(node):
        if isinstance(node, (str, Field)):
            return True
        children = getattr(node, 'fields', None)
        return bool(children) and not isinstance(node, (HTML, Submit, Button)) \
            and all(isinstance(child, (str, Field)) for child in children)

    def names(node):
        if isinstance(node, str):
            return [node]
        found = []
        for child in node.fields:
            found += [child] if isinstance(child, str) else [f for f in child.fields if isinstance(f, str)]
        return found

    def width(node):
        match = re.search(r'col-(?:md-|lg-)?(\d+)', getattr(node, 'css_class', '') or '')
        return int(match.group(1)) if match else 12

    def html_block(html):
        if re.search(r'<h\d', html):
            heading = re.search(r'<h\d[^>]*>(.*?)</h\d>', html, re.S)
            text = strip_tags(html[heading.end():] if heading else '').strip()
            blocks.append({'kind': 'section', 'title': strip_tags(heading.group(1)).strip() if heading else '',
                           'text': text})
        elif strip_tags(html).strip() or '<' in html:
            blocks.append({'kind': 'html', 'html': html})

    def walk(node):
        if isinstance(node, (Submit, Button)):
            blocks.append({'kind': 'button', 'value': node.value, 'name': node.name,
                           'css': getattr(node, 'field_classes', 'btn btn-primary'),
                           'submit': isinstance(node, Submit)})
        elif isinstance(node, HTML):
            html_block(node.html)
        elif is_cell(node):
            blocks.append({'kind': 'fields', 'names': names(node), 'cols': width(node)})
        else:
            row, used, grid = [], 0, 12
            for child in getattr(node, 'fields', []):
                if is_cell(child):
                    w = width(child)
                    if row and used + w > 12:
                        blocks.append({'kind': 'fields', 'names': row, 'cols': grid})
                        row, used = [], 0
                    row, used, grid = row + names(child), used + w, w
                else:
                    if row:
                        blocks.append({'kind': 'fields', 'names': row, 'cols': grid})
                        row, used = [], 0
                    walk(child)
            if row:
                blocks.append({'kind': 'fields', 'names': row, 'cols': grid})

    walk(layout)
    return blocks


def _widget_class(field):
    from django import forms
    widget = field.field.widget
    if isinstance(widget, forms.CheckboxInput):
        return 'form-check-input'
    if isinstance(widget, (forms.Select, forms.SelectMultiple)):
        return 'form-select'
    return 'form-control'


@register.inclusion_tag('includes/settings_form.html', takes_context=True)
def settings_form(context, form):
    """The form in grouped rows. Its crispy layout decides the order and the
    pairs; without one, every field in order."""
    from django import forms as django_forms
    from django.template import Context, Template
    helper = getattr(form, 'helper', None)
    layout = getattr(helper, 'layout', None)
    blocks = _layout_blocks(layout) if layout is not None else \
        [{'kind': 'fields', 'names': [name]} for name in form.fields]
    groups, foot, shown = [], [], set()
    current = []
    section = {'title': '', 'text': ''}
    for block in blocks:
        if block['kind'] == 'section':
            if current:
                groups.append({**section, 'rows': current})
            current, section = [], {'title': block['title'], 'text': block['text']}
            continue
        if block['kind'] == 'fields':
            row = []
            for name in block['names']:
                if name not in form.fields or name in shown:
                    continue
                shown.add(name)
                bound = form[name]
                if bound.is_hidden:
                    continue
                css = ' '.join(filter(None, [_widget_class(bound), bound.field.widget.attrs.get('class', '')]))
                if bound.errors and css != 'form-check-input':
                    css += ' is-invalid'
                row.append({'field': bound, 'widget': bound.as_widget(attrs={'class': css}),
                            'checkbox': isinstance(bound.field.widget, django_forms.CheckboxInput)})
            explained = [item for item in row if item['checkbox'] and item['field'].help_text]
            if len(row) > 1 and explained:
                current.extend([item] for item in row)
            elif row:
                if len(row) == 1 and block.get('cols', 12) < 12:
                    row[0]['cols'] = block['cols']
                current.append(row)
        elif block['kind'] == 'html':
            # Rendered with an empty context: a layout's HTML() is static text
            # (values are filled in, escaped, in Python before it gets here), so
            # nothing a template could reach - an object's name - ever runs here
            html = Template(block['html']).render(Context({})).strip()
            if html:
                foot.append({'kind': 'html', 'html': html})
        else:
            foot.append(block)
    # Fields the layout does not mention are shown too, at the end
    for name in [name for name in form.fields if name not in shown and not form[name].is_hidden]:
        bound = form[name]
        current.append([{'field': bound, 'widget': bound.as_widget(attrs={'class': _widget_class(bound)}),
                         'checkbox': isinstance(bound.field.widget, django_forms.CheckboxInput)}])
    if current:
        groups.append({**section, 'rows': current})
    if not any(item['kind'] == 'button' for item in foot):
        foot.insert(0, {'kind': 'button', 'value': 'Save', 'name': 'submit', 'css': 'btn btn-primary', 'submit': True})
    # Two titled groups or more: each is a section, closed until opened on purpose
    sections = sum(1 for group in groups if group.get('title')) >= 2
    return {'form': form, 'groups': groups, 'foot': foot, 'csrf_token': context.get('csrf_token'), 'sections': sections,
            'action': getattr(helper, 'form_action', '') or '',
            'form_tag': getattr(helper, 'form_tag', True) if helper else True,
            'multipart': form.is_multipart()}
