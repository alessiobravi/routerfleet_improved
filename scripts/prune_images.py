#!/usr/bin/env python3
"""Delete the image versions on ghcr.io that no release needs.

Kept:
  - every version tagged `latest` or with a release number (MAJOR.MINOR.PATCH)
    at or above RELEASE_FLOOR in routerfleet/versioning.py;
  - every manifest such a version references. A multi-platform image is an
    index whose per-platform manifests are versions of their own, untagged -
    deleting them would break the image they belong to.
Deleted: everything else - leftovers of earlier builds, old tag schemes,
withdrawn releases.

It refuses to delete anything when the release just published is not among
the versions, or when a referenced manifest cannot be read: a partial view of
what is in use is no basis for deleting.

    GH_TOKEN=... scripts/prune_images.py IMAGE RELEASE [--dry-run]

Standard library only. The GitHub API needs a token with packages: write
(the workflow's GITHUB_TOKEN); the registry is read anonymously, the images
being public.
"""
import json
import os
import re
import sys
import urllib.request

RELEASE_TAG = re.compile(r'^(latest|\d+\.\d+\.\d+)$')
INDEX_TYPES = ('application/vnd.oci.image.index.v1+json',
               'application/vnd.docker.distribution.manifest.list.v2+json')
ACCEPT = ', '.join(INDEX_TYPES + ('application/vnd.oci.image.manifest.v1+json',
                                  'application/vnd.docker.distribution.manifest.v2+json'))


def _versioning():
    import importlib.util
    import pathlib
    spec = importlib.util.spec_from_file_location(
        'versioning', pathlib.Path(__file__).resolve().parent.parent / 'routerfleet' / 'versioning.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def is_kept_tag(tag, retired=None):
    """latest, or a release at or above the floor."""
    retired = retired or _versioning().is_retired
    return bool(RELEASE_TAG.match(tag)) and not retired(tag)


def plan(versions, children_of, retired=None):
    """Which versions to delete.

    versions: [{'id': int, 'name': 'sha256:...', 'tags': [...]}, ...]
    children_of: callable(tag) -> digests the manifest under that tag references
    Returns (kept, delete), each a list of versions.
    """
    retired = retired or _versioning().is_retired
    kept = [v for v in versions if any(is_kept_tag(t, retired) for t in v['tags'])]
    protected = set()
    for version in kept:
        tag = next(t for t in version['tags'] if is_kept_tag(t, retired))
        protected.update(children_of(tag))
    delete = [v for v in versions if v not in kept and v['name'] not in protected]
    return kept, delete


def _request(url, token=None, method='GET', accept=None):
    request = urllib.request.Request(url, method=method)
    if token:
        request.add_header('Authorization', f'Bearer {token}')
    if accept:
        request.add_header('Accept', accept)
    request.add_header('User-Agent', 'routerfleet-image-prune')
    with urllib.request.urlopen(request, timeout=30) as response:
        body = response.read()
        return json.loads(body) if body else None


def list_versions(owner, image, token):
    versions, page = [], 1
    while True:
        batch = _request(f'https://api.github.com/users/{owner}/packages/container/{image}/versions'
                         f'?per_page=100&page={page}', token,
                         accept='application/vnd.github+json')
        if not batch:
            return versions
        versions += [{'id': v['id'], 'name': v['name'],
                      'tags': v['metadata']['container']['tags']} for v in batch]
        page += 1


def registry_children(owner, image):
    token = _request(f'https://ghcr.io/token?scope=repository:{owner}/{image}:pull')['token']

    def children_of(tag):
        manifest = _request(f'https://ghcr.io/v2/{owner}/{image}/manifests/{tag}', token, accept=ACCEPT)
        if manifest.get('mediaType') in INDEX_TYPES or 'manifests' in manifest:
            return [m['digest'] for m in manifest['manifests']]
        return []
    return children_of


def main(argv):
    args = [a for a in argv[1:] if not a.startswith('--')]
    if len(args) != 2:
        sys.exit(__doc__)
    image, release = args
    dry_run = '--dry-run' in argv
    owner = os.environ.get('GITHUB_REPOSITORY_OWNER', 'alessiobravi')
    token = os.environ.get('GH_TOKEN')
    if not token:
        sys.exit('GH_TOKEN is not set')

    versions = list_versions(owner, image, token)
    if not any(release in v['tags'] for v in versions):
        sys.exit(f'{image}:{release} is not among the versions - nothing deleted')
    kept, delete = plan(versions, registry_children(owner, image))

    print(f'{image}: {len(versions)} versions, keeping {len(kept)} tagged '
          f'({", ".join(sorted(t for v in kept for t in v["tags"]))}) and the manifests they reference')
    for version in delete:
        tags = ','.join(version['tags']) or 'untagged'
        if dry_run:
            print(f'would delete {version["id"]} {version["name"]} ({tags})')
            continue
        _request(f'https://api.github.com/users/{owner}/packages/container/{image}/versions/{version["id"]}',
                 token, method='DELETE', accept='application/vnd.github+json')
        print(f'deleted {version["id"]} {version["name"]} ({tags})')
    print(f'{image}: {len(delete)} {"would be " if dry_run else ""}deleted')


if __name__ == '__main__':
    main(sys.argv)
