"""Static files with the hash of their content in the name (app.3f2a9c1e4b7d.js):
a file that changes gets a new address, so browsers may keep every file for a
year and still never run an old one after an update - nginx sends them with a
long cache lifetime.

The source-map comments of the vendor files are left alone: the maps are not
shipped, and a missing map must not stop collectstatic. A name not in the
manifest (a page shown before collectstatic ran, the test runner) falls back to
the plain name instead of failing."""
from django.contrib.staticfiles.storage import ManifestStaticFilesStorage


class HashedStaticFiles(ManifestStaticFilesStorage):
    manifest_strict = False
    patterns = tuple(
        (extension, tuple(pattern for pattern in patterns
                          if 'sourceMappingURL' not in (pattern if isinstance(pattern, str) else pattern[0])))
        for extension, patterns in ManifestStaticFilesStorage.patterns)

    def stored_name(self, name):
        try:
            return super().stored_name(name)
        except ValueError:   # not collected (yet): the plain name, served without the long lifetime
            return name
