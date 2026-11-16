"""Host editor for fixed directory roles and named 115 resources."""
from copy import deepcopy
from dataclasses import dataclass, field, replace
import json

from app.runtime.central_config import _get, _set, _equal, _KEY, _ABSENT, CentralConfigError
from app.runtime.config_projection import ROLE_CONSUMERS, cloud, invalid, role_value
from app.runtime.config_role_edits import make_change

ROLES = (*ROLE_CONSUMERS, 'media_category_order')
ROLE_PATH = ('shared','directory_roles')
ENTITIES = ('shared','directories')
ROLE_LABEL = dict(media_categories='四分类绑定', manual_downloads='手动下载菜单（按顺序）',
                  unorganized_inventory='额外盘点根', standalone_subtitles='独立字幕导出',
                  media_category_order='分类顺序')


@dataclass(frozen=True)
class Directory:
    field: str
    display: str
    current: str
    guards: dict = field(repr=False)


def refs(roles,key):
    hits = []
    for role in ROLE_CONSUMERS:
        rows = roles.get(role)
        for label, item in (rows.items() if role=='media_categories' and isinstance(rows,dict)
                            else enumerate(rows) if role=='manual_downloads' and isinstance(rows,list)
                            else [('',rows)]):
            if isinstance(item,dict) and item.get('directory_id')==key:
                hits.append(role+(f'[{label}]' if label!='' else ''))
    return hits


def directory_rows(document):
    if document.get('config_version') != 3:
        return []
    shared = document.get('shared',{})
    roles, entities = shared.get('directory_roles',{}),shared.get('directories',{})
    def row(name,label,path,guards):
        value = _get(document,path)
        if name.startswith('entity:') and isinstance(value,dict):
            value = {k:value[k] for k in ('name','path') if k in value}
        return Directory(name,label,'未显式设置' if value is _ABSENT else json.dumps(value,ensure_ascii=False),
                         {p:_get(document,p) for p in (*guards,path,('config_version',))})
    rows = [row('entity:'+key, f'{key} · '+('、'.join(refs(roles,key)) or '未引用'), (*ENTITIES,key), [ROLE_PATH])
            for key in entities if _KEY.fullmatch(key)]
    rows.append(row('new','新增目录（稳定 ID）',ENTITIES,[]))
    for role in ROLES:
        rows.append(row(role,ROLE_LABEL[role],(*ROLE_PATH,role),[ENTITIES]))
    return rows


def prepare_directory(before,draft,value,*,reset=False):
    after = deepcopy(before)
    area = draft.field
    guards = dict(draft.guards)
    watched = ()
    if area == 'new':
        previous = guards.pop(ENTITIES)
        key = value.get('id') if isinstance(value, dict) else None
        if isinstance(key, str):
            guards[(*ENTITIES,key)] = _get(previous, (key,))
    elif area.startswith('entity:'):
        key = area.split(':',1)[1]
        previous = guards.pop(ROLE_PATH)
        watched = (key,)
        if refs(previous if isinstance(previous,dict) else {},key) != refs(before.get('shared',{}).get('directory_roles',{}),key):
            raise CentralConfigError('config_conflict','Directory references changed')
    elif area in ROLES:
        previous = guards.pop(ENTITIES)
        entries = (value.values() if area=='media_categories' and isinstance(value,dict)
                   else value if isinstance(value,list) else [value])
        for entry in entries:
            key = entry.get('directory_id') if isinstance(entry,dict) else None
            if isinstance(key,str):
                guards[(*ENTITIES,key)] = _get(previous, (key,))
    if before.get('config_version') != 3 or any(not _equal(_get(before,p),v) for p,v in guards.items()):
        raise CentralConfigError('config_conflict','Directory settings changed')
    if area.startswith('entity:') or area=='new':
        if area=='new':
            if not isinstance(value,dict) or set(value)!={'id','name','path'}:
                invalid('shared.directories')
            value = dict(value)
            key = value.pop('id')
            if not isinstance(key,str) or not _KEY.fullmatch(key) or _get(before,(*ENTITIES,key)) is not _ABSENT:
                invalid('shared.directories')
        else:
            key = area.split(':',1)[1]
            if not _KEY.fullmatch(key) or _get(before,(*ENTITIES,key)) is _ABSENT:
                invalid('shared.directories')
        if value is None:
            hits = refs(before.get('shared',{}).get('directory_roles',{}),key)
            if hits:
                invalid('shared.directory_roles.'+hits[0])
            _set(after,(*ENTITIES,key),_ABSENT)
        else:
            if not isinstance(value,dict) or set(value)!={'name','path'} or not isinstance(value['name'],str) or not value['name']:
                invalid('shared.directories.'+key)
            original=_get(before,(*ENTITIES,key))
            entity=dict(original) if isinstance(original,dict) else {}
            entity.update(name=value['name'],path=cloud(value['path'],'shared.directories.'+key+'.path',relative=True,root=True))
            _set(after,(*ENTITIES,key),entity)
    elif area in ROLES and reset:
        if _get(before,(*ROLE_PATH,area)) is not _ABSENT:
            _set(after,(*ROLE_PATH,area),_ABSENT)
    elif area in ROLES:
        rows = (value.values() if area=='media_categories' and isinstance(value,dict)
                else value if area=='manual_downloads' and isinstance(value,list) else [value])
        if area != 'media_category_order':
            for binding in rows:
                if area=='unorganized_inventory' and binding is None:
                    continue
                allowed = {'directory_id'} | ({'label','plex_library_id'} if area=='media_categories' else
                                              {'label'} if area=='manual_downloads' else set())
                if not isinstance(binding,dict) or set(binding)-allowed or 'directory_id' not in binding:
                    invalid('shared.directory_roles.'+area)
        _set(after,(*ROLE_PATH,area),value)
    else:
        invalid('shared.directory_roles')
    change = replace(make_change(before,after,guards), directory_reference_ids=watched)
    if area=='manual_downloads' or area.startswith('entity:'):
        menu=role_value(after['shared'],'manual_downloads',[])
        names,paths=set(),set()
        for i,row in enumerate(menu):
            name=row['name']
            if (not name.strip() or '\n' in name or '\r' in name or name.strip() in names or row['path'] in paths):
                invalid('shared.directory_roles.manual_downloads['+str(i)+']')
            names.add(name.strip())
            paths.add(row['path'])
    return change
