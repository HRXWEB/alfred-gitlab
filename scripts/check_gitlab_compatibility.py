#!/usr/bin/env python3
"""Read-only upstream source audit; does not certify a live GitLab/JiHu instance."""
import json
from concurrent.futures import ThreadPoolExecutor
from urllib.request import urlopen

VERSIONS = ('14.6.1', '15.11.13', '16.11.10', '17.11.7', '18.3.0', '19.3.3')
CHECKS = {
    'lib/api/projects.rb': ('membership', 'simple', 'get feature_category:', 'Entities::BasicProjectDetails'),
    'lib/api/entities/project_identity.rb': ('name_with_namespace', 'path_with_namespace'),
    'lib/api/entities/basic_project_details.rb': ('web_url', 'ssh_url_to_repo', 'http_url_to_repo'),
    'lib/api/users.rb': ('resource :user do', 'present current_user'),
    'config/routes/dashboard.rb': ('merge_requests', 'issues', 'snippets', 'todos', 'projects', 'starred'),
    'config/routes/profile.rb': ('preferences',),
    'config/routes/repository.rb': ('resources :network',),
}


def audit(version):
    sources = []
    for path, markers in CHECKS.items():
        url = f'https://raw.githubusercontent.com/gitlabhq/gitlabhq/v{version}/{path}'
        try:
            with urlopen(url, timeout=20) as response:
                source = response.read().decode('utf-8')
            missing = [marker for marker in markers if marker not in source]
            sources.append({'url': url, 'checked_markers': markers, 'missing_markers': missing, 'ok': not missing})
        except Exception as error:
            sources.append({'url': url, 'ok': False, 'error': type(error).__name__})
    return {'version': version, 'ok': all(s['ok'] for s in sources), 'sources': sources}


if __name__ == '__main__':
    with ThreadPoolExecutor(max_workers=6) as executor:
        results = list(executor.map(audit, VERSIONS))
    print(json.dumps({
        'scope': 'Upstream representative-tag source markers only; no server integration tests; JH not certified.',
        'versions': results,
    }, indent=2))
    raise SystemExit(0 if all(r['ok'] for r in results) else 1)
