"""What the versions ahead of this installation bring, for the "What's new"
page: the project's changelog - the copy in the image for the release that
runs, the published one for newer releases (fetched only while the update
check is on, through the same outbound path, kept for an hour) - parsed into
one section per version and rendered from escaped text, so nothing fetched
can bring markup into the page."""
import html
import logging
import re
from pathlib import Path

from django.conf import settings
from django.utils.safestring import mark_safe

logger = logging.getLogger(__name__)

HEADING = re.compile(r'^## \[(\d+\.\d+\.\d+)\](?: - (\d{4}-\d{2}-\d{2}))?(?: - (.+))?\s*$', re.M)
CACHE_KEY = 'rf-release-notes'
CACHE_SECONDS = 3600
MAX_BYTES = 1024 * 1024


def local_changelog():
    try:
        return (Path(settings.BASE_DIR) / 'CHANGELOG.md').read_text(encoding='utf-8')
    except OSError:
        return ''


def published_changelog():
    """The published changelog, or '' - never fetched while the update check is
    off, and not again for an hour after a fetch (a failure included)."""
    from django.core.cache import caches
    from routerfleet_tools.views import check_enabled
    url = getattr(settings, 'UPDATE_NOTES_URL', '')
    if not url or not check_enabled():
        return ''
    cache = caches['limits']
    text = cache.get(CACHE_KEY)
    if text is None:
        from routerlib import outbound
        try:
            response = outbound.https_get(url, headers={'User-Agent': 'RouterFleet-Improved-release-notes'}, limit=MAX_BYTES)
            response.raise_for_status()
            text = response.text
        except Exception as error:
            logger.info(f'Release notes could not be read: {type(error).__name__}')
            text = ''
        cache.set(CACHE_KEY, text, CACHE_SECONDS)
    return text


def sections(text):
    """[{'version', 'number', 'date', 'label', 'body'}] of a changelog,
    newest first as written."""
    from routerfleet.versioning import to_number
    found = list(HEADING.finditer(text or ''))
    result = []
    for index, match in enumerate(found):
        end = found[index + 1].start() if index + 1 < len(found) else len(text)
        version = match.group(1)
        result.append({'version': version, 'number': to_number(version), 'date': match.group(2) or '',
                       'label': (match.group(3) or '').strip(), 'body': text[match.end():end].strip()})
    return result


def _inline(text):
    """Escaped text with **bold**, *italics* and `code` - nothing else."""
    text = html.escape(text, quote=True)
    text = re.sub(r'`([^`]+)`', r'<code>\1</code>', text)
    text = re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', text)
    text = re.sub(r'(?<![\w*])\*([^*\s][^*]*)\*(?![\w*])', r'<em>\1</em>', text)
    text = re.sub(r'\[([^\]]+)\]\([^)]*\)', r'\1', text)   # a link's words, not its address
    return text


def render(body):
    """A changelog section as HTML: ### headings, - lists (continuation lines
    indented), paragraphs. Built from escaped text only."""
    out, items, paragraph = [], [], []

    def flush_items():
        if items:
            out.append('<ul>' + ''.join(f'<li>{_inline(" ".join(item))}</li>' for item in items) + '</ul>')
            items.clear()

    def flush_paragraph():
        if paragraph:
            out.append(f'<p>{_inline(" ".join(paragraph))}</p>')
            paragraph.clear()

    for line in (body or '').splitlines():
        stripped = line.strip()
        if not stripped:
            flush_items()
            flush_paragraph()
        elif stripped.startswith('### '):
            flush_items()
            flush_paragraph()
            out.append(f'<h6 class="rf-notes-h">{_inline(stripped[4:])}</h6>')
        elif stripped.startswith('- '):
            flush_paragraph()
            items.append([stripped[2:]])
        elif items and line.startswith(' '):
            items[-1].append(stripped)
        else:
            flush_items()
            paragraph.append(stripped)
    flush_items()
    flush_paragraph()
    return mark_safe('\n'.join(out))   # nosec B308 B703 - built from escaped text above


def notes():
    """What the "What's new" page shows: the versions newer than the one that
    runs (from the published changelog), and the one that runs (from the
    image's copy). 'fetched' says whether the published one could be read."""
    current = settings.ROUTERFLEET_VERSION
    published = published_changelog()
    newer = [dict(s, html=render(s['body'])) for s in sections(published) if s['number'] > current]
    running = next((s for s in sections(local_changelog()) if s['number'] == current), None)
    if running:
        running = dict(running, html=render(running['body']))
    return {'newer': newer, 'running': running, 'fetched': bool(published)}
