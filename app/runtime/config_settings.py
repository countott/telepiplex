"""Finite Host settings, backed by the active package or shipped package metadata."""
from copy import deepcopy
from dataclasses import dataclass, field as hidden
import json
from pathlib import Path

from jsonschema import Draft202012Validator

from app.runtime.central_config import CentralConfigError, _ABSENT, _get, _set, _equal, _mapped
from app.runtime.config_projection import GITHUB, ProjectionContract, invalid, project_feature_config
from app.runtime.config_role_edits import canonical_path, make_change, check_timeout_edit


LABELS = {'directories': '115 目录与用途', 'shared_tmdb': '共享 TMDB 凭据',
          'caption_policy': '字幕开关', 'caption_github': '字幕 GitHub 默认与来源',
          'caption_credentials': '字幕来源凭据', 'search_prowlarr': 'Prowlarr 连接',
          'search_tvdb': 'TVDB 连接', 'search_anilist': 'AniList 连接',
          'search_tmdb_endpoint': 'Search TMDB 接口', 'sync_plex': 'Plex 连接',
          'sync_fanart': 'Fanart 连接', 'rename_ai': 'AI 整理', 'download_connection': '115 服务连接',
          'runtime': 'Host 插件目录（重启后生效）'}
RECOVERY = {
    'repair_categories': ('category_folder',),
    'repair_caption': ('output_path','providers.local_archive.roots','providers.local_archive.mapping_file'),
    'repair_download': ('save_directories',),
    'repair_rename': ('unorganized_path','caption_enabled','category_folder'),
    'repair_sync': ('tmdb.api_key','category_folder'),
    'repair_search': ('metadata.tmdb.api_key','category_folder'),
}
NAVIGATION = {
    'search': {'shared_tmdb','directories','search_prowlarr','search_tvdb','search_anilist','search_tmdb_endpoint'},
    'sync': {'shared_tmdb','directories','sync_plex','sync_fanart'},
    'rename': {'directories','rename_ai','caption_policy'},
    'download': {'directories','download_connection'},
    'caption': {'directories','caption_policy','caption_github','caption_credentials'},
}
FIELDS = {
    'search_prowlarr': ('search.prowlarr.base_url','search.prowlarr.api_key'),
    'search_tvdb': ('metadata.tvdb.base_url','metadata.tvdb.api_key','metadata.tvdb.subscriber_pin','metadata.tvdb.timeout'),
    'search_anilist': ('metadata.anilist.endpoint','metadata.anilist.timeout'),
    'search_tmdb_endpoint': ('metadata.tmdb.base_url','metadata.tmdb.timeout'),
    'sync_plex': ('plex.base_url','plex.token'), 'sync_fanart': ('fanart.api_key',),
    'rename_ai': ('ai.enable','ai.api_url','ai.api_key','ai.model'),
    'caption_policy': ('enabled','automatic'),
    'caption_github': tuple(group+'.'+key for group in ('provider_defaults.github', *('providers.'+p for p in GITHUB))
                            for key in ('github_token','cache_ttl_seconds')),
    'caption_credentials': tuple('providers.'+p+'.'+k for p, keys in {
        'assrt':['token'], 'opensubtitles':['api_key','username','password','token'],
        'subdl':['api_key'], 'subsource':['api_key']}.items() for k in keys),
    'download_connection': ('base_url','passport_url','auth_mode','app_id'),
    'runtime': ('plugins.root','plugins.runtime_root'), **RECOVERY,
}


def load_descriptor():
    try:
        return json.loads(Path(__file__).with_name('config_settings.generated.json').read_text())
    except (OSError, ValueError):
        return {}


def contract_for(manager, plugin):
    if plugin != 'host':
        active = manager.store.active(plugin)
        if active is not None:
            return manager.store.config_defaults(active), manager.store.config_schema(active)
    descriptor = load_descriptor()
    try:
        row = descriptor['host'] if plugin == 'host' else descriptor['features'][plugin]
        if plugin != 'host' and not (row['version'] and row['config_schema_version']):
            raise KeyError
        return row['defaults'], row['schema']
    except (TypeError, KeyError):
        invalid('config_settings', 'Bundled configuration metadata unavailable')


def leaf_schema(schema, path):
    def resolve(node):
        seen = set()
        while isinstance(node, dict) and '$ref' in node:
            ref = node['$ref']
            if not isinstance(ref, str) or not ref.startswith('#/') or ref in seen:
                invalid('config_settings', 'Invalid bundled schema reference')
            seen.add(ref)
            target = schema
            for segment in ref[2:].split('/'):
                key = segment.replace('~1', '/').replace('~0', '~')
                if not isinstance(target, dict) or key not in target:
                    invalid('config_settings', 'Unknown bundled schema reference')
                target = target[key]
            if not isinstance(target, dict):
                invalid('config_settings', 'Invalid bundled schema reference')
            node = {**target, **{key: val for key, val in node.items() if key != '$ref'}}
        return node if isinstance(node, dict) else {}

    node = resolve(schema)
    for key in path:
        child = (node.get('items') if isinstance(key, int)
                 else node.get('properties', {}).get(key, node.get('additionalProperties')))
        node = resolve(child)
    return node if node.get('type') or node.get('enum') else None


@dataclass(frozen=True)
class Setting:
    target: str
    field: str
    schema: dict
    sensitive: bool
    status: str
    display: str
    version: int
    expected: object = hidden(repr=False)
    dependencies: dict = hidden(repr=False)


def _location(plugin, path, version, repair=False):
    if repair:
        return ('features',plugin,*path)
    if plugin == 'host':
        return path
    return (canonical_path if version == 3 else _mapped)(plugin, path)


def setting_rows(manager, target, *, document=None):
    if target not in FIELDS:
        invalid('settings')
    repair = target in RECOVERY
    plugin = _source(target)
    defaults, schema = contract_for(manager, plugin)
    doc = manager.store.central_config.read() if document is None else document
    version = doc.get('config_version', 1)
    if repair and version == 3:
        invalid('config_version')
    rows = []
    for name in FIELDS[target]:
        path = tuple(name.split('.'))
        shared_github = path[:2] == ('provider_defaults','github')
        if shared_github and version != 3:
            continue
        source = ('providers', GITHUB[0], path[-1]) if shared_github else path
        spec = leaf_schema(schema, source)
        if not spec:
            continue
        if plugin == 'search' and path[:1] == ('metadata',) and path[-1] == 'timeout':
            from app.runtime.config_projection import timeout_bounds
            lo, hi = timeout_bounds(version == 3 and ProjectionContract(schema).extended_timeout)
            spec = {**spec, 'minimum': max(lo,spec.get('minimum',lo)), 'maximum': min(hi,spec.get('maximum',hi))}
        sensitive = bool(spec.get('writeOnly')) or path[-1] in {'api_key','subscriber_pin','token','password','github_token','username'}
        public = ('shared','category_folder') if target=='repair_categories' else _location(plugin,path,version,repair)
        raw = _get(doc, public)
        if repair and target!='repair_categories' and raw is _ABSENT and path in (('tmdb','api_key'),('metadata','tmdb','api_key'),('category_folder',)):
            continue
        inherited = target == 'caption_github' and not shared_github and version == 3
        status = ('匿名' if inherited and path[-1]=='github_token' and raw == '' else
                  '显式设置' if raw is not _ABSENT else '继承共享设置' if inherited else '默认')
        value = raw
        if value is _ABSENT and inherited:
            value = _get(doc, ('features','caption','provider_defaults','github',path[-1]))
        if value is _ABSENT:
            value = _get(defaults,source)
        display = ('已配置' if value not in ('',None,_ABSENT) else '未配置') if sensitive else (
            '未配置' if value is _ABSENT else str(value))
        if path[-1] in ('base_url','api_url','endpoint','passport_url') and value not in ('',_ABSENT):
            from urllib.parse import urlsplit
            try:
                parsed=urlsplit(value)
                if parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment:
                    display='已配置（地址含私有信息）'
            except (ValueError,AttributeError,TypeError):
                display='格式无效'
        if not sensitive and isinstance(value,(dict,list)):
            display='已配置（结构化值）'
            if repair and isinstance(value,list):
                if path[-1] in ('save_directories','category_folder'):
                    allowed=('kind','name','path','plex_library_id') if path[-1]=='category_folder' else ('name','path')
                    display=json.dumps([{k:r[k] for k in allowed if isinstance(r.get(k),str)}
                                        for r in value if isinstance(r,dict)],ensure_ascii=False)
                elif path[-1]=='roots' and all(isinstance(p,str) for p in value):
                    display=json.dumps(value,ensure_ascii=False)
        if (plugin=='search' and path[0]=='metadata' and path[-1]=='timeout' and version!=3
            and type(value) in (float,int) and not spec['minimum']<=value<=spec['maximum']):
            display+=f'，实际 {min(spec["maximum"],max(spec["minimum"],value))}'
        deps = {}
        if inherited:
            common = ('features','caption','provider_defaults','github',path[-1])
            deps[common] = _get(doc,common)
        rows.append(Setting(target,name,spec,sensitive,status,display,version,raw,deps))
    if not rows:
        invalid('config_settings', 'Section unavailable in this package')
    return rows


def _source(target):
    if target=='runtime':
        return 'host'
    if target=='repair_categories':
        return 'search'
    return target.split('_')[1] if target in RECOVERY else target.split('_')[0]


def _apply_field(manager, before, after, draft, value, reset):
    current = next((r for r in setting_rows(manager,draft.target,document=before) if r.field==draft.field), None)
    if (current is None or current.version != draft.version or current.schema!=draft.schema
        or not _equal(current.expected,draft.expected) or not _equal(current.dependencies,draft.dependencies)):
        raise CentralConfigError('config_conflict','Setting changed')
    repair = draft.target in RECOVERY
    plugin = _source(draft.target)
    path = tuple(current.field.split('.'))
    public = ('shared','category_folder') if draft.target=='repair_categories' else _location(plugin,path,current.version,repair)
    defaults,schema = contract_for(manager, plugin)
    if not reset and not Draft202012Validator({**current.schema,'$defs':schema.get('$defs',{})}).is_valid(value):
        invalid('.'.join(public))
    if not reset and plugin=='host' and (not isinstance(value,str) or not value.startswith('/')):
        invalid('.'.join(public))
    if not reset and public[-1] in ('base_url','api_url','endpoint','passport_url') and value:
        from urllib.parse import urlsplit
        try:
            url=urlsplit(value)
            if (url.scheme not in ('https','http') or not url.hostname or url.username is not None
                or url.password is not None or any(c.isspace() for c in value)):
                raise ValueError
            _ = url.port
        except ValueError:
            invalid('.'.join(public))
    if reset:
        if current.expected is not _ABSENT:
            _set(after,public,_ABSENT)
            for i in range(len(public)-1, 1,-1):
                if _get(after,public[:i]) != {}:
                    break
                _set(after,public[:i],_ABSENT)
    else:
        _set(after,public,value)
    if plugin != 'host':
        contract = ProjectionContract(schema)
        delta = {}
        if not reset:
            _set(delta,path,value)
        check_timeout_edit(before,plugin,delta,contract,None)
        if public == ('features','download','save_directories') and current.version == 2 and not reset:
            from app.runtime.config_projection import cloud
            for item in value:
                raw = item['path']
                expected = cloud(raw, '.'.join(public), relative=True)
                legacy = cloud(raw.strip(), '.'.join(public), relative=True)
                if expected != legacy:
                    invalid('.'.join(public), 'The retained v2 consumer would change this path')
    return public, current.dependencies, plugin


def _prepare_fields(manager, entries, *, repair_only):
    before = manager.store.central_config.read()
    after = deepcopy(before)
    guards = {('config_version',)}
    consumers = set()
    seen = set()
    for draft, value, reset in entries:
        if repair_only and draft.target not in RECOVERY:
            invalid('recovery')
        public, dependencies, plugin = _apply_field(manager,before,after,draft,value,reset)
        if public in seen:
            invalid('recovery')
        seen.add(public)
        guards.update((public,*dependencies))
        if plugin != 'host':
            consumers.update(('search','rename','sync') if draft.target=='repair_categories' else (plugin,))
    if not seen:
        invalid('settings')
    from app.runtime.plugin_store import _validate
    for plugin in consumers:
        defaults,schema=contract_for(manager,plugin)
        snapshot = project_feature_config(plugin, defaults, document=after, contract=ProjectionContract(schema))
        _validate(schema, snapshot, flat_search=plugin == 'search' and after.get('config_version') == 3)
    change = make_change(before,after,guards)
    from dataclasses import replace
    affected = set(change.affected_plugins) | consumers if change.changed_paths else set()
    if change.changed_paths and before.get('config_version')==3 and ('features','caption','automatic') in seen:
        affected.add('rename')
    return replace(change, affected_plugins=tuple(sorted(affected)))


def prepare_setting(manager, draft, value, reset):
    return _prepare_fields(manager, [(draft,value,reset)], repair_only=False)


def prepare_repair(manager, entries):
    return _prepare_fields(manager, entries, repair_only=True)
