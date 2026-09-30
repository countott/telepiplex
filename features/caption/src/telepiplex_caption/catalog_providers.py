"""Official finished-subtitle catalogs and opt-in local historical archives.

No repository checkout, media download, registration or remote script execution.
Catalog names allocate candidates; the shared archive/content gates remain final.
"""
from __future__ import annotations

import csv
import hashlib
import html
import io
import json
import os
import re
import stat
import threading
import time
import unicodedata
from pathlib import Path, PurePosixPath
from urllib.parse import quote, unquote, urlencode, urlsplit

from .matching import episode_coordinates, match_candidate, _title_converter
from .models import MediaQuery, SubtitleCandidate
from .providers import ProviderError, ProviderResult, SubtitleProvider, _json, _plain

_EXTENSIONS = {'.ass', '.ssa', '.srt', '.zip', '.7z', '.rar'}
_BAD = re.compile(r'(?i)(?:^|[ /_.\[\]-])(?:fonts?|typeface|effects?|source|templates?|commentary|ncop\d*|nced\d*|op\d*|ed\d*|screen|insert\d*|staff)(?:$|[ /_.\]\d-])')
_GITHUB_HOSTS = ('api.github.com', 'github.com', 'raw.githubusercontent.com', 'objects.githubusercontent.com', 'release-assets.githubusercontent.com')


def _normalized(value: str) -> str:
    # Retain kana: removing it makes distinct Japanese titles collide.
    value = unicodedata.normalize('NFKC', value).casefold()
    try:
        value = _title_converter().convert(value)
    except ImportError:
        pass
    return ''.join(c for c in value if c.isalnum())


def _work_name(value: str) -> str:
    value = PurePosixPath(value).stem
    value = re.sub(r'^\s*\[[^\]]+\]\s*', '', value)
    value = re.split(r'(?<=[ ._(])(?:19|20)\d{2}(?=[ ._)]|$)', value, maxsplit=1)[0]
    value = re.split(r'(?i)\s*\[(?:\d{1,3}|movie|bd|web|subtitles)|(?:[ ._-]S\d{1,2}(?:E\d{1,3})?)(?!\d)|\s+-\s+\d{1,3}(?:\D|$)|\bEP?\d{1,3}\b', value)[0]
    value = re.sub(r'(?i)(?:[ ._-](?:bd(?:rip)?|web(?:rip)?|subtitles?|zho|jpch|jpsc|jptc|chs|cht|chi|sc|tc|jpn|mono|ass|srt|ssa))+$', '', value)
    return value.strip(' ._-')


def _matches(query: MediaQuery, aliases: list[str]) -> bool:
    wanted = {_normalized(x) for x in (query.title, query.original_title, *query.aliases) if x}
    return any(_normalized(x) in wanted for x in aliases if x)


def _supported(path: str) -> bool:
    return PurePosixPath(path).suffix.lower() in _EXTENSIONS and not _BAD.search(path)


def _mapping(value) -> dict:
    if not isinstance(value, dict) or isinstance(value.get('season'), bool):
        return {}
    try:
        result = {'season': int(value['season']), 'offset': int(value.get('offset', 0))}
        for key in ('first', 'last'):
            if value.get(key) is not None:
                result[key] = int(value[key])
        if not 0 <= result['season'] <= 99 or abs(result['offset']) > 1000:
            return {}
        return result
    except (ValueError, TypeError, KeyError):
        return {}


def mapped_episode(candidate: SubtitleCandidate, filename: str) -> tuple[int, int] | None:
    """Map an absolute episode only when its catalog/user supplied a season.

    Explicit S/E must agree with this mapping. Decimal episodes, ranges, specials
    and unmatched files stay unresolved, never silently become an aired episode.
    """
    mapping = _mapping(candidate.metadata.get('episode_mapping'))
    if not mapping:
        return None
    name = PurePosixPath(filename.replace('\\', '/')).name
    if _BAD.search(filename) or re.search(r'(?i)(?:^|[ ._\[-])(?:sp|ova|oad|special)\d*(?:[ ._\]-]|$)|\b\d{1,3}\.\d\b', name):
        return None
    season, episodes = episode_coordinates(filename)
    if season is not None and season != mapping['season']:
        return None
    if len(episodes) > 1:
        return None
    if episodes:
        number = next(iter(episodes))
        # Explicit aired S/E already uses aired numbering, never offset twice.
        if season is not None:
            low = mapping.get('first', 1) + mapping['offset']
            high = mapping.get('last', 999) + mapping['offset']
            return (season, number) if max(1, low) <= number <= high else None
    else:
        found = re.findall(r'(?:^|[ ._-])(?:\[(\d{1,3})\]|(\d{1,3})(?=[ ._-](?:SC|TC|CHS|CHT|CHI|JPCH|JPSC|JPTC|ass|ssa|srt)(?:[ ._-]|$)))', name, re.I)
        numbers = {int(a or b) for a, b in found}
        numbers.update(int(value) for value in re.findall(r'\s-\s(\d{1,3})(?=[ ._\[]|$)', name))
        if len(numbers) != 1:
            return None
        number = numbers.pop()
    if number < mapping.get('first', 1) or number > mapping.get('last', 999):
        return None
    episode = number + mapping['offset']
    return (mapping['season'], episode) if 1 <= episode <= 999 else None


class _CachedCatalog(SubtitleProvider):
    hosts = _GITHUB_HOSTS
    repository = ''
    branch = 'main'
    project_url = ''
    attribution = ''

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._cache = {}
        self._lock = threading.RLock()

    def _cached(self, url: str, *, as_json=True, limit=8 * 1024 * 1024):
        now = time.monotonic()
        with self._lock:
            cached = self._cache.get(url)
            if cached and cached[0] > now:
                return cached[1]
            headers = {'Accept': 'application/vnd.github+json'} if urlsplit(url).hostname == 'api.github.com' else {}
            if headers and self.config.get('github_token'):
                headers['Authorization'] = 'Bearer ' + str(self.config['github_token'])
            response = self._request(url, headers=headers, max_bytes=limit)
            value = _json(response) if as_json else response.body.decode('utf-8-sig')
            if len(self._cache) >= 128:
                self._cache.clear()
            self._cache[url] = (now + max(30, min(int(self.config.get('cache_ttl_seconds', 3600)), 86400)), value)
            return value

    def _api(self, suffix: str):
        return self._cached(f'https://api.github.com/repos/{self.repository}/{suffix}')

    def _tree(self):
        data = self._api(f'git/trees/{self.branch}?recursive=1')
        if not isinstance(data, dict) or not isinstance(data.get('tree'), list):
            raise ProviderError('invalid_response', '字幕仓库目录格式已改变')
        if data.get('truncated'):
            raise ProviderError('catalog_incomplete', '字幕仓库目录被截断，不能把局部结果当作完整索引')
        return [x for x in data['tree'] if isinstance(x, dict) and x.get('type') == 'blob']

    def _aliases(self, key: str):
        configured = self.config.get('title_aliases', {})
        value = configured.get(key, []) if isinstance(configured, dict) else []
        return [str(x) for x in value if isinstance(x, str)] if isinstance(value, list) else []

    def _mapping_for(self, *keys):
        configured = self.config.get('episode_mappings', {})
        if isinstance(configured, dict):
            for key in keys:
                result = _mapping(configured.get(key))
                if result:
                    return result
        return {}

    def _candidate(self, query, url, filename, aliases, *, directory='', season=None, mapping=None, media_type='', release=''):
        mapping = self._mapping_for(filename, directory) or mapping or {}
        data = {'filename': filename, 'document_aliases': list(dict.fromkeys(aliases)),
                'source_attribution': self.attribution, 'catalog_repository': self.repository,
                'catalog_directory': directory, 'catalog_evidence': 'official_finished_catalog',
                'requires_episode_mapping': query.media_type in {'series', 'tv'}}
        if mapping:
            data['episode_mapping'] = mapping
        return SubtitleCandidate(self.name, hashlib.sha256(url.encode()).hexdigest()[:24], query.title,
            download_url=url, detail_url=f'https://github.com/{self.repository}',
            release_name=release or filename, media_type=media_type,
            season=mapping.get('season', season), subtitle_format='ass' if filename.lower().endswith(('.ass', '.ssa', '.7z', '.zip')) else 'srt',
            language='cht' if re.search(r'(?i)(?:^|[._ ])(?:tc|cht)(?:[._ ]|$)', filename) else 'chi', metadata=data)

    def _project_aliases(self, query):
        """Read public rendered rows, with the site's own text filter.

        Never call write/GraphQL endpoints or infer missing paginated rows. The
        recursive file tree remains authoritative for downloadable paths.
        """
        if not self.project_url:
            return {}
        url = self.project_url + '?' + urlencode({'filterQuery': query.title})
        page = self._cached(url, as_json=False, limit=3 * 1024 * 1024)
        match = re.search(r'<script[^>]*id="memex-paginated-items-data"[^>]*>(.*?)</script>', page, re.S)
        if not match:
            return {}
        data = json.loads(match[1])
        nodes = list(data.get('nodes', []))
        for group in data.get('groupedItems', []):
            nodes.extend(group.get('nodes', []))
        result = {}
        for node in nodes:
            titles, paths = [], []
            for cell in node.get('memexProjectColumnValues', []):
                value = cell.get('value') or {}
                raw = value.get('raw') or (value.get('title') or {}).get('raw') or ''
                if not isinstance(raw, str):
                    continue
                if raw.startswith('https://github.com/' + self.repository + '/tree/'):
                    tail = unquote(urlsplit(raw).path).split('/tree/', 1)[1]
                    _, _, folder = tail.partition('/')
                    paths.append(folder.strip('/'))
                elif raw and not raw.startswith('http') and not raw.isnumeric():
                    titles.extend(x.strip() for x in re.split(r'\s+/\s+', raw))
            if _matches(query, titles):
                for path in paths:
                    result[path] = titles
        return result


class MingYProvider(_CachedCatalog):
    name = 'mingy'
    repository = 'MingYSub/SubsArchive'
    attribution = 'MingYSub · https://github.com/MingYSub/SubsArchive · CC BY-NC-SA 4.0'

    def _search(self, query):
        candidates = []
        for page in range(1, max(1, min(int(self.config.get('max_release_pages', 3)), 10)) + 1):
            releases = self._api(f'releases?per_page=30&page={page}')
            if not isinstance(releases, list):
                raise ProviderError('invalid_response', '字幕成品发布目录格式已改变')
            for release in releases:
                if release.get('draft') or release.get('prerelease'):
                    continue
                assets = {a.get('browser_download_url'): a for a in release.get('assets', []) if isinstance(a, dict)}
                for row in str(release.get('body') or '').splitlines():
                    if not row.lstrip().startswith('|'):
                        continue
                    cells = row.strip().strip('|').split('|')
                    aliases = [_plain(x) for x in re.split(r'<br\s*/?>', cells[0], flags=re.I)]
                    urls = re.findall(r'\]\((https://github\.com/[^)]+)\)', row)
                    for url in urls:
                        asset = assets.get(url)
                        if not asset or not _supported(str(asset.get('name', ''))):
                            continue
                        filename = asset['name']
                        aliases += self._aliases(filename)
                        if not _matches(query, aliases):
                            continue
                        # The table describes this release's title. It does not
                        # establish a TMDB aired season for absolute numbering.
                        movie = str(release.get('tag_name', '')).casefold() == 'movie'
                        if (query.media_type in {'series', 'tv'}) == movie:
                            continue
                        item = self._candidate(query, url, filename, aliases, media_type='movie' if movie else 'series')
                        candidates.append(item)
            if len(releases) < 30:
                break
        return candidates


class _TreeCatalog(_CachedCatalog):
    def _search(self, query):
        tree = self._tree()
        groups = {}
        for entry in tree:
            path = str(entry.get('path', ''))
            if '/' in path:
                groups.setdefault(path.split('/')[0], []).append(entry)
        matched = {}
        for directory, entries in groups.items():
            aliases = [directory, *self._aliases(directory)]
            aliases += [_work_name(e['path'].rsplit('/', 1)[-1]) for e in entries if _supported(e['path'])]
            if _matches(query, aliases):
                matched[directory] = list(dict.fromkeys(aliases))
        if not matched:
            matched = self._project_aliases(query)
        candidates = []
        for directory, aliases in matched.items():
            entries = groups.get(directory, [])
            if not entries:
                continue
            readmes = [e['path'] for e in entries if e['path'].lower() == directory.lower() + '/readme.md']
            readme = self._cached(self._raw(readmes[0]), as_json=False, limit=512 * 1024) if readmes else ''
            for heading in re.findall(r'^#\s+(.+)$', readme, re.M):
                if not re.search(r'字幕|字体|font|subtitle', heading, re.I):
                    aliases.append(_plain(heading))
            candidates.extend(self._finished(query, directory, entries, readme, aliases))
        return candidates

    def _raw(self, path):
        return f'https://raw.githubusercontent.com/{self.repository}/{self.branch}/{quote(path, safe="/")}'


class NekomoeProvider(_TreeCatalog):
    name = 'nekomoe'
    repository = 'Nekomoekissaten-SUB/Nekomoekissaten-Storage'
    branch = 'master'
    project_url = 'https://github.com/orgs/Nekomoekissaten-SUB/projects/1'
    attribution = '喵萌奶茶屋 · https://github.com/Nekomoekissaten-SUB/Nekomoekissaten-Subs'

    def _finished(self, query, directory, entries, readme, aliases):
        result, current_season = [], None
        for line in readme.splitlines():
            heading = re.match(r'^#{1,4}\s+(?:Season\s+|S)(\d{1,2})\b', line, re.I)
            if heading:
                current_season = int(heading[1])
            for url in re.findall(r'https://github\.com/[^)\s]+/releases/download/subtitle_pkg/[^)\s]+', line):
                if not url.startswith('https://github.com/' + self.repository + '/'):
                    continue
                filename = unquote(urlsplit(url).path.rsplit('/', 1)[-1])
                if not _supported(filename):
                    continue
                mapping = {'season': current_season, 'offset': 0} if current_season is not None else {}
                bounds = re.search(r'\[(\d{1,3})-(\d{1,3})\b', line)
                if mapping and bounds:
                    mapping.update(first=int(bounds[1]), last=int(bounds[2]))
                result.append(self._candidate(query, url, filename, aliases, directory=directory, mapping=mapping))
        if result:
            return result
        # Official policy leaves only small 1–4-file finished works unpackaged.
        direct = [e for e in entries if _supported(e['path']) and PurePosixPath(e['path']).suffix.lower() in {'.ass', '.ssa', '.srt'}]
        if len(direct) <= 4:
            for item in direct:
                result.append(self._candidate(query, self._raw(item['path']), PurePosixPath(item['path']).name, aliases, directory=directory))
        return result


class HaruhanaProvider(_TreeCatalog):
    name = 'haruhana'
    repository = 'HaruhanaSub/Haruhana-Storage'
    project_url = 'https://github.com/users/HaruhanaSub/projects/2'
    attribution = '拨雪寻春 · https://github.com/HaruhanaSub/Haruhana-Storage'

    def _finished(self, query, directory, entries, readme, aliases):
        # This group's official README identifies repository ASS and packaged
        # large ASS as its finished distribution. Fonts/source roles are excluded.
        return [self._candidate(query, self._raw(e['path']), PurePosixPath(e['path']).name, aliases, directory=directory)
                for e in entries if _supported(e['path'])]


class KitaujiProvider(_CachedCatalog):
    name = 'kitauji'
    repository = 'Kitauji-Sub/subs-shikanoko'
    attribution = '北宇治字幕组 · https://github.com/Kitauji-Sub/subs-shikanoko'

    def _search(self, query):
        aliases = ['鹿乃子乃子乃子虎视眈眈', 'しかのこのこのここしたんたん', 'Shikanoko Nokonoko Koshitantan', 'My Deer Friend Nokotan', *self._aliases('subs-shikanoko')]
        if not _matches(query, aliases) or query.media_type not in {'series', 'tv'}:
            return []
        release = self._api('releases/latest')
        if release.get('draft') or release.get('prerelease'):
            return []
        return [self._candidate(query, a['browser_download_url'], a['name'], aliases,
                    directory='subs-shikanoko', media_type='series')
                for a in release.get('assets', []) if isinstance(a, dict) and
                re.fullmatch(r'sino_subs_[A-Za-z0-9._-]+_mono\.zip', str(a.get('name', ''))) and a.get('browser_download_url')]


class LocalArchiveProvider(SubtitleProvider):
    """Index only user-configured roots, opening files by no-follow descriptors."""
    name = 'local_archive'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._index = (0, [])
        self._lock = threading.RLock()

    def search(self, query):
        if not self.config.get('roots'):
            return ProviderResult(self.name, 'not_configured', message='尚未配置本地字幕归档目录')
        try:
            return super().search(query)
        except OSError:
            return ProviderResult(self.name, 'unavailable', message='本地字幕归档目录暂时无法读取')

    def _roots(self):
        raw = self.config.get('roots', [])
        if not isinstance(raw, list):
            raise ProviderError('invalid_config', '本地字幕归档 roots 必须是目录列表')
        roots = []
        for value in raw:
            path = Path(str(value)).expanduser().absolute()
            if not path.is_dir() or path.is_symlink() or any(p.is_symlink() for p in path.parents):
                raise ProviderError('unsafe_path', '本地字幕归档目录不存在或经过符号链接')
            roots.append(path)
        return roots

    def _rows(self):
        mapping_file = self.config.get('mapping_file')
        if not mapping_file:
            return {}
        path = Path(str(mapping_file)).expanduser()
        if path.is_symlink() or path.stat().st_size > 4 * 1024 * 1024:
            raise ProviderError('unsafe_path', '本地字幕索引文件不安全或过大')
        with path.open(encoding='utf-8-sig', newline='') as stream:
            rows = list(csv.DictReader(stream, delimiter='\t'))
        if len(rows) > 20000:
            raise ProviderError('catalog_incomplete', '本地字幕映射表超过条目限制')
        result = {}
        for row in rows:
            relative = row.get('relative_path', '')
            if not relative or relative.startswith('/') or '..' in PurePosixPath(relative).parts or '\\' in relative:
                raise ProviderError('unsafe_path', '本地字幕映射表只能引用归档目录内的相对路径')
            result[(int(row.get('root_index') or 0), relative)] = row
        return result

    def _files(self):
        with self._lock:
            if self._index[0] > time.monotonic():
                return self._index[1]
            roots = self._roots()
            rows = self._rows()
            files, seen = [], 0
            maximum = max(1, min(int(self.config.get('max_files', 20000)), 100000))
            depth = max(1, min(int(self.config.get('max_depth', 12)), 30))
            for index, root in enumerate(roots):
                for directory, dirs, names in os.walk(root, followlinks=False):
                    parent = Path(directory)
                    # Account for every filesystem entry so non-subtitle trees
                    # cannot make a nominally bounded index scan unbounded.
                    seen += len(dirs) + len(names)
                    if seen > maximum:
                        raise ProviderError('catalog_incomplete', '本地字幕归档超过扫描上限，请缩小 roots 或提高 max_files')
                    dirs[:] = sorted(d for d in dirs if not (parent / d).is_symlink() and not d.startswith('.'))
                    if len(parent.relative_to(root).parts) >= depth and dirs:
                        raise ProviderError('catalog_incomplete', '本地字幕归档超过目录深度限制，请缩小 roots 或提高 max_depth')
                    for name in sorted(names):
                        path = parent / name
                        relative = path.relative_to(root).as_posix()
                        if not _supported(relative) or path.is_symlink() or not path.is_file():
                            continue
                        files.append((index, relative, rows.get((index, relative), {})))
            self._index = (time.monotonic() + max(1, min(int(self.config.get('cache_ttl_seconds', 300)), 86400)), files)
            return files

    def _search(self, query):
        result = []
        for root_index, relative, row in self._files():
            season, episodes = episode_coordinates(relative)
            if row.get('season'):
                season = int(row['season'])
            episode = int(row['episode']) if row.get('episode') else next(iter(episodes)) if len(episodes) == 1 else None
            aliases = [x.strip() for x in str(row.get('aliases', '')).split(';') if x.strip()]
            title = row.get('title') or _work_name(PurePosixPath(relative).name)
            if not row.get('title'):
                aliases.extend(_work_name(p) for p in PurePosixPath(relative).parts[:-1])
            metadata = {'filename': PurePosixPath(relative).name, 'local_root_index': root_index,
                        'local_relative_path': relative, 'document_aliases': [title, *aliases],
                        'source_attribution': '用户配置的本地字幕归档',
                        'requires_episode_mapping': query.media_type in {'series', 'tv'}}
            for key in ('tmdb_id', 'imdb_id'):
                if row.get(key):
                    metadata[key] = row[key]
            mappings = self.config.get('episode_mappings', {})
            if isinstance(mappings, dict) and (mapping := _mapping(mappings.get(relative))):
                metadata['episode_mapping'] = mapping
                season = mapping['season']
            candidate = SubtitleCandidate(self.name, f'{root_index}:' + hashlib.sha256(relative.encode()).hexdigest()[:24],
                title, release_name=relative, year=int(row['year']) if row.get('year') else None,
                media_type=row.get('media_type', ''), season=season, episode=episode,
                subtitle_format=PurePosixPath(relative).suffix.lstrip('.').lower(), metadata=metadata)
            if metadata.get('episode_mapping') and (coordinates := mapped_episode(candidate, relative)):
                from dataclasses import replace
                candidate = replace(candidate, season=coordinates[0], episode=coordinates[1])
            if not _matches(query, [title, *aliases]):
                continue
            # Canonical title is asserted only after a strict local/TSV alias
            # match; the candidate retains real year/season/release evidence.
            from dataclasses import replace
            candidate = replace(candidate, title=query.title)
            if match_candidate(query, candidate).accepted:
                result.append(candidate)
        return result

    def download(self, candidate):
        if candidate.provider != self.name:
            raise ProviderError('invalid_candidate', '字幕候选与归档来源不一致')
        roots = self._roots()
        try:
            index = int(candidate.metadata['local_root_index'])
            relative = str(candidate.metadata['local_relative_path'])
            if index < 0 or index >= len(roots) or relative.startswith('/') or '\\' in relative or '..' in PurePosixPath(relative).parts or not _supported(relative):
                raise ValueError
            expected = f'{index}:' + hashlib.sha256(relative.encode()).hexdigest()[:24]
            if candidate.candidate_id != expected:
                raise ValueError
        except (KeyError, TypeError, ValueError):
            raise ProviderError('unsafe_path', '字幕归档文件定位无效') from None
        descriptors = []
        try:
            # Every component is opened relative to a retained directory fd.
            # A symlink swap between search and download cannot escape roots.
            descriptors.append(os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW))
            for part in roots[index].parts[1:]:
                descriptors.append(os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptors[-1]))
            parts = PurePosixPath(relative).parts
            for part in parts[:-1]:
                descriptors.append(os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptors[-1]))
            fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=descriptors[-1])
            descriptors.append(fd)
            info = os.fstat(fd)
            limit = int(getattr(self.transport, 'max_bytes', 20 * 1024 * 1024))
            if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
                raise ProviderError('too_large', '本地字幕不是常规文件或超过下载大小限制')
            with os.fdopen(os.dup(fd), 'rb') as stream:
                content = stream.read(limit + 1)
            if len(content) > limit:
                raise ProviderError('too_large', '本地字幕超过下载大小限制')
            return content, parts[-1]
        except OSError:
            raise ProviderError('unsafe_path', '字幕归档文件已变化、不可读或经过符号链接') from None
        finally:
            for fd in reversed(descriptors):
                os.close(fd)
