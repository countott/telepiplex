"""Telegram screens for the finite Host settings and directory editors."""
import json
import secrets
import time
from jsonschema import Draft202012Validator
from telegram.ext import ConversationHandler
from app.handlers.shared_config_handler import CONFIG_SHARED, _owner, _session, _show, central_config_saved_feedback, host_draft, set_host_draft, clear_host_draft
from app.runtime.config_settings import LABELS, RECOVERY, setting_rows
from app.runtime.directory_settings import directory_rows, ROLES

HINT = {
    'new': '{"id":"movies","name":"电影","path":"/电影"}',
    'manual_downloads':'[{"directory_id":"movies","label":"电影"}]，顺序即菜单顺序；省略 label 继承目录名称。',
    'unorganized_inventory':'{"directory_id":"extra"}；null 停用额外根。',
    'standalone_subtitles':'{"directory_id":"subs"}',
    'media_categories':'四分类对象：每项包含 directory_id、plex_library_id，可省略 label 继承名称。',
    'media_category_order':'四个分类 kind 的有序 JSON 数组。',
}

FIELD_LABELS = {
    'enable':'启用','enabled':'允许使用字幕服务','automatic':'下载后自动补字幕',
    'api_key':'API Key','token':'Token','subscriber_pin':'订阅 PIN','github_token':'GitHub Token',
    'cache_ttl_seconds':'缓存时间（秒）','base_url':'服务地址','passport_url':'授权服务地址',
    'api_url':'API 地址','endpoint':'接口地址','timeout':'请求超时（秒）','app_id':'应用 ID',
    'auth_mode':'授权方式','model':'模型','username':'用户名','password':'密码',
    'root':'插件安装目录','runtime_root':'运行时目录',
}


def _label(row):
    parts=row.field.split('.')
    label=FIELD_LABELS.get(parts[-1],parts[-1])
    if parts[0]=='providers':
        return parts[1]+' · '+label
    if parts[0]=='provider_defaults':
        return '共享 · '+label
    return label


def _new(update,context,target):
    from app.handlers.plugin_handler import retire_feature_settings
    retire_feature_settings(update,context)
    session = {'owner':_owner(update),'expires':time.monotonic()+1800,'target':target,
               'nonce':secrets.token_hex(5),'stage':'rows'}
    set_host_draft(update, context, session)
    return session


def _button(session,text,action):
    return (text,'e:'+session['nonce']+':'+action)


async def _directory_panel(update, session, text, buttons, *, fresh=False):
    # At most 1,800 Unicode code points (3,600 UTF-16 units) per body,
    # leaving room for both page counts. Keep the exact text inspectable.
    pages = [text[i:i+1800] for i in range(0,len(text),1800)] or ['']
    choices = [[(label[:64], action.split(':',2)[2]) for label,action in row] for row in buttons]
    session['directory_panel'] = dict(pages=pages,choices=choices,text_index=0,choice_index=0)
    return await _render_directory_page(update,session,fresh=fresh)


async def _render_directory_page(update, session, *, fresh=False):
    panel=session['directory_panel']
    pages,choices=panel['pages'],panel['choices']
    text_index,choice_index=panel['text_index'],panel['choice_index']
    session['nonce']=secrets.token_hex(5)
    text=pages[text_index]
    start=12*choice_index
    rows=[[_button(session,label,action) for label,action in row] for row in choices[start:start+12]]
    for prefix,label,index,total in (
        ('text','内容',text_index,len(pages)), ('choices','选项',choice_index,max(1,(len(choices)+11)//12))
    ):
        if total<=1:
            continue
        text+=f'\n{label} {index+1}/{total}'
        controls=[]
        if index>0:
            controls.append(_button(session,label+'上一页',prefix+'-'))
        if index+1<total:
            controls.append(_button(session,label+'下一页',prefix+'+'))
        rows.append(controls)
    await _show(update,text,rows+[[_button(session,'退出','cancel')]],fresh=fresh)
    return CONFIG_SHARED


def _setting_buttons(session):
    pending = session.get('repairs', {})
    rows = []
    for index, row in enumerate(session['rows']):
        suffix = '待保存' if (row.target,row.field) in pending else row.status+'（'+row.display[:54]+'）'
        rows.append([_button(session, _label(row)+' · '+suffix, 'row'+str(index))])
    if pending:
        rows.append([_button(session,'确认保存 '+str(len(pending))+' 项修复','save_repairs')])
        rows.append([_button(session,'修复其他分组','repair_groups')])
    return rows


async def _stage_repair(update, session, value, reset=False):
    row = session['row']
    pending = session.setdefault('repairs', {})
    key = (row.target,row.field)
    # Revisiting an edited field retains its original compare-and-swap baseline.
    original = pending[key][0] if key in pending else row
    pending[key] = (original, value, reset)
    session.update(stage='rows', nonce=secrets.token_hex(5))
    await _show(update, '修复已暂存。可继续修改其他字段，然后一起保存。',
                _setting_buttons(session)+[[_button(session,'取消','cancel')]])
    return CONFIG_SHARED


async def open_section(update,context,manager,target, *, fresh=False):
    s = _new(update,context,target)
    buttons = []
    try:
        if target == 'migration':
            status = manager.migration_status()
            text = '配置迁移\n当前主文件版本：'+str(manager.store.central_config.read().get('config_version',1))
            for key,label in [('blockers','阻塞字段'),('notices','迁移提示')]:
                text += '\n'+label+'：'+('、'.join(status.get(key,[])) or '无')
            buttons = [[_button(s,'只运行预检','preview'),_button(s,'重新预检并迁移','retry')], [('修复原配置','go:recovery')]]
        elif target == 'recovery':
            text='修复原配置后，返回迁移诊断重新预检。私有冲突字段可恢复默认以移除重复值。'
            buttons=[[(key.split('_')[1],'go:'+key)] for key in RECOVERY if key!='repair_categories'] if manager.store.central_config.read().get('config_version')!=3 else []
            buttons+=[[('公共凭据','go:shared_tmdb')],[('目录','go:directories')],[('迁移诊断','go:migration')]]
        elif target == 'shared_tmdb':
            text = LABELS.get(target,'迁移修复：'+target)
            buttons = [[_button(s,'编辑 API Read Access Token','tmdb')]]
        elif target == 'directories':
            doc = manager.store.central_config.read()
            rows = directory_rows(doc)
            text = LABELS[target]+'\n修改目录会影响全部引用；修改用途只重新绑定该用途。'
            if not rows:
                text += '\n主配置尚未迁移，可以编辑原配置后重试。'
                buttons = [[('原四分类与 Plex 库','go:repair_categories')],[('其他原配置','go:recovery')]]
            else:
                s['rows'] = rows
                buttons = [[_button(s,r.field[7:] if r.field.startswith('entity:') else r.display,'row'+str(i))]
                           for i,r in enumerate(rows)]
                return await _directory_panel(update,s,text,buttons,fresh=fresh)
        else:
            rows = setting_rows(manager,target)
            s['rows'] = rows
            text = LABELS.get(target,'迁移修复：'+target)
            buttons = _setting_buttons(s)
    except Exception:
        clear_host_draft(update, context)
        await _show(update,'该配置分组的契约或配置不可用；请检查安装包、内置描述或主配置。')
        return ConversationHandler.END
    await _show(update,text,buttons+[[_button(s,'退出','cancel')]],fresh=fresh)
    return CONFIG_SHARED


async def editor_action(update,context,manager,action):
    s = _session(update,context)
    bits = action.split(':',2)
    if not s or len(bits)!=3 or bits[1]!=s.get('nonce'):
        await _show(update,'旧配置按钮已失效。请使用当前配置面板。',fresh=True)
        return CONFIG_SHARED if s else ConversationHandler.END
    op = bits[2]
    if s['target']=='directories' and 'directory_panel' in s and op in ('text+','text-','choices+','choices-'):
        panel=s['directory_panel']
        axis='text_index' if op.startswith('text') else 'choice_index'
        count=len(panel['pages']) if axis=='text_index' else (len(panel['choices'])+11)//12
        index=panel[axis]+(1 if op.endswith('+') else -1)
        if 0<=index<count:
            panel[axis]=index
        return await _render_directory_page(update,s)
    if op=='cancel':
        clear_host_draft(update, context)
        await _show(update,'已退出配置。')
        return ConversationHandler.END
    if s['stage']=='rows' and s['target'] in RECOVERY and s.get('repairs'):
        if op=='repair_groups':
            s['nonce']=secrets.token_hex(5)
            await _show(update,'选择其他修复字段。',[
                [_button(s,target.split('_')[1], 'group:'+target)] for target in RECOVERY
            ]+[[_button(s,'取消','cancel')]])
            return CONFIG_SHARED
        if op.startswith('group:') and op[6:] in RECOVERY:
            target=op[6:]
            try:
                rows=setting_rows(manager,target)
            except Exception:
                await _show(update,'该分组没有可修复字段。',[[_button(s,'返回分组','repair_groups')]])
                return CONFIG_SHARED
            s.update(target=target, rows=rows, nonce=secrets.token_hex(5))
            await _show(update,'选择修复字段。',_setting_buttons(s)+[[_button(s,'取消','cancel')]])
            return CONFIG_SHARED
        if op=='save_repairs':
            s['stage']='editor_confirm'
            op='save'
    if s['stage']=='rows' and op in ('tmdb','routes'):
        from app.handlers.shared_config_handler import shared_config_callback
        s['stage']='choose'
        return await shared_config_callback(update,context,_action=op)
    if s['stage']=='rows' and op=='preview' and s['target']=='migration':
        result=manager.preview_semantic_migration()
        fields=result['blockers'] or result['notices']
        await _show(update,'预检未写入主配置。\n'+('、'.join(fields) if fields else '未发现阻塞字段。'),
                    [[_button(s,'重新预检并迁移','retry')],[('修复原配置','go:recovery')]])
        return CONFIG_SHARED
    if s['stage']=='rows' and op=='retry' and s['target']=='migration':
        s['stage']='saving'
        try:
            await manager.retry_semantic_migration()
        except Exception:
            await _show(update,'迁移未完成。请检查主配置和已安装包，再重新预检。')
            return ConversationHandler.END
        return await open_section(update,context,manager,'migration')
    if s['stage']=='rows' and op.startswith('row') and op[3:].isdigit():
        i=int(op[3:])
        if i<len(s.get('rows',[])):
            row=s['rows'][i]
            s.update(stage='edit',row=row,nonce=secrets.token_hex(5))
            buttons=[]
            if s['target']=='directories':
                if row.field.startswith('entity:'):
                    s['stage']='entity'
                    entity=json.loads(row.current)
                    text=f'{row.display}\n名称：{entity["name"]}\n115 路径：{entity["path"]}\n以上用途将一起更新。'
                    buttons=[[_button(s,'修改 115 路径','path'),_button(s,'修改目录名称','name')],[_button(s,'删除目录','delete')]]
                    return await _directory_panel(update,s,text,buttons)
                text = row.display+'\n当前：'+(row.current if row.field!='new' else '新增')+'\n请输入 JSON：'+HINT.get(row.field,'')
                if row.field in ROLES:
                    text+='\n可用目录 ID：'+('、'.join(r.field[7:] for r in s['rows'] if r.field.startswith('entity:')) or '请先新增目录')
                    buttons=[[_button(s,'恢复默认用途','reset')]]
                    if row.field in ('unorganized_inventory','standalone_subtitles'):
                        buttons=[[_button(s,r.field[7:],'bind'+str(idx))] for idx,r in enumerate(s['rows']) if r.field.startswith('entity:')]+buttons
                        if row.field=='unorganized_inventory':
                            buttons.append([_button(s,'停用额外盘点根','unbind')])
                return await _directory_panel(update,s,text,buttons)
            else:
                spec=row.schema
                fmt = {'string':'文本','integer':'整数','number':'数值','boolean':'true 或 false',
                       'array':'JSON 数组','object':'JSON 对象'}.get(spec.get('type'),' / '.join(spec.get('enum',[])))
                text=f'{_label(row)} · {row.status}：{row.display}\n请输入{fmt}'
                if 'minimum' in spec or 'maximum' in spec:
                    text+=f'，范围 {spec.get("minimum","−∞")}–{spec.get("maximum","+∞")}。'
                if row.sensitive:
                    text+='。敏感值不会回显；发送 clear 显式清空。'
                buttons=[[_button(s,'使用共享设置' if s['target']=='caption_github' and row.field.startswith('providers.') else '恢复默认','reset')]]
            await _show(update,text,buttons+[[_button(s,'退出','cancel')]])
            return CONFIG_SHARED
    if s['stage']=='entity' and op in ('path','name','delete'):
        s.pop('directory_panel',None)
        s['nonce']=secrets.token_hex(5)
        if op=='delete':
            s['value']=None
            return await _confirm(update,s)
        s.update(stage='edit',entity_field=op)
        await _show(update,'请输入新的 115 路径。' if op=='path' else '请输入新的目录名称。',[[_button(s,'退出','cancel')]])
        return CONFIG_SHARED
    if s['stage']=='edit' and s['target']=='directories' and s['row'].field in ('unorganized_inventory','standalone_subtitles'):
        if op=='unbind' and s['row'].field=='unorganized_inventory':
            s['value']=None
            return await _confirm(update,s)
        if op.startswith('bind') and op[4:].isdigit() and int(op[4:])<len(s['rows']):
            entry=s['rows'][int(op[4:])]
            if entry.field.startswith('entity:'):
                s['value']={'directory_id':entry.field[7:]}
                return await _confirm(update,s)
    if s['stage']=='edit' and op=='reset' and (s['target']!='directories' or s['row'].field in ROLES):
        if s['target'] in RECOVERY:
            return await _stage_repair(update,s,None,reset=True)
        s['reset']=True
        return await _confirm(update,s)
    if s['stage']=='editor_confirm' and op=='save':
        s['stage']='saving'
        try:
            if s['target'] in RECOVERY:
                outcome=await manager.save_repairs(tuple(s['repairs'].values()))
            elif s['target']=='directories':
                outcome=await manager.save_directory(s['row'],s.get('value'),reset=s.get('reset',False))
            else:
                outcome=await manager.save_setting(s['row'],s.get('value'),reset=s.get('reset',False))
            text=central_config_saved_feedback(outcome)
            if outcome.details.get('restart_host'):
                text+='\n重启 Host 后使用新目录。'
        except Exception as exc:
            fields=getattr(exc,'details',{}).get('config_error_paths',[])
            text=('配置在编辑期间已变化，本次修改未保存。请重新打开配置。'
                  if getattr(exc,'code','')=='config_conflict' else '保存失败；请检查配置后重试。')
            if fields:
                text+=' 字段：'+'、'.join(fields)
            if (s['target'] in RECOVERY and getattr(exc,'code','')!='config_conflict'
                    and host_draft(update, context) is s):
                s.update(stage='rows', nonce=secrets.token_hex(5))
                await _show(update, text+'\n已保留暂存的修复，可继续修改后再保存。',
                            _setting_buttons(s)+[[_button(s,'取消','cancel')]])
                return CONFIG_SHARED
        if host_draft(update, context) is s:
            clear_host_draft(update, context)
        await _show(update,text)
        return ConversationHandler.END
    await _show(update,'此步骤已失效，请使用当前配置面板。')
    return CONFIG_SHARED


async def _confirm(update,s):
    s.pop('directory_panel',None)
    s.update(stage='editor_confirm',nonce=secrets.token_hex(5))
    await _show(update,'确认将这项修改保存到主配置？', [[_button(s,'确认保存','save'),_button(s,'取消','cancel')]])
    return CONFIG_SHARED


async def editor_message(update,s):
    if s['stage']!='edit':
        await _show(update,'请选择当前面板上的操作。')
        return CONFIG_SHARED
    raw=str(update.effective_message.text or '')
    try:
        if s['target']=='directories':
            if s.get('entity_field'):
                value=json.loads(s['row'].current)
                value[s['entity_field']]=raw
            else:
                value=json.loads(raw)
        else:
            spec=s['row'].schema
            if spec.get('type') in ('boolean','integer','number','object','array'):
                value=json.loads(raw)
            else:
                value='' if raw=='clear' and s['row'].sensitive else raw
            if not Draft202012Validator(spec).is_valid(value):
                raise ValueError
    except (ValueError,TypeError):
        await _show(update,'输入格式或范围无效，请重新输入。',[[_button(s,'退出','cancel')]])
        return CONFIG_SHARED
    s['value']=value
    if s['target'] in RECOVERY:
        return await _stage_repair(update,s,value)
    return await _confirm(update,s)
