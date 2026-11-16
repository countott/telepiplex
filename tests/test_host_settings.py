import asyncio

import pytest
import yaml

from tests.test_bot_runtime_startup import load_bot_module
from tests.test_config_semantics_migration import contracts


def manager_with_settings(tmp_path, monkeypatch, extra=None):
    path = tmp_path / 'config.yaml'
    cfg = {'config_version': 3, 'plugins': {'root': str(tmp_path/'plugins'),
           'runtime_root': str(tmp_path/'runtime')}, **(extra or {})}
    path.write_text(yaml.safe_dump(cfg))
    manager = load_bot_module().build_plugin_manager(cfg, tmp_path/'db', config_path=path)
    from app.runtime import config_settings
    monkeypatch.setattr(config_settings, 'load_descriptor', lambda: {'features': {
        key: {'defaults': defaults, 'schema': schema, 'version': '1', 'config_schema_version': 1}
        for key, (defaults, schema) in contracts().items()}})
    return manager


def test_uninstalled_basic_draft_compares_owned_leaf_only(tmp_path, monkeypatch):
    manager = manager_with_settings(tmp_path, monkeypatch)
    from app.runtime.config_settings import setting_rows
    row = setting_rows(manager, 'sync_plex')[1]
    assert row.status == '默认' and row.sensitive
    central = manager.store.central_config
    central.commit(central.prepare_document_patch({'features': {'download': {'access_token': 'new'}}}))
    outcome = asyncio.run(manager.save_setting(row, 'replacement'))
    assert not outcome.details['affected_plugins']
    assert central.read()['features'] == {'download': {'access_token': 'new'}, 'sync': {'plex': {'token': 'replacement'}}}
    assert manager.store.active('sync') is None
    with pytest.raises(Exception) as conflict:
        asyncio.run(manager.save_setting(row, 'older'))
    assert conflict.value.code == 'config_conflict'


def test_github_reset_removes_only_selected_override(tmp_path, monkeypatch):
    manager = manager_with_settings(tmp_path, monkeypatch, {'features': {'caption': {
        'providers': {'nekomoe': {'github_token': ''}, 'mingy': {}},
        'provider_defaults': {'github': {'github_token': 'shared'}}}}})
    from app.runtime.config_settings import setting_rows
    rows = setting_rows(manager, 'caption_github')
    row = next(r for r in rows if r.field == 'providers.nekomoe.github_token')
    assert row.status == '匿名' and 'shared' not in repr(row)
    asyncio.run(manager.save_setting(row, reset=True))
    assert manager.store.central_config.read()['features']['caption']['providers'] == {'mingy': {}}
    assert next(r for r in setting_rows(manager,'caption_github') if r.field == row.field).status == '继承共享设置'


def test_entity_preview_includes_manual_rows_and_detects_new_reference(tmp_path, monkeypatch):
    from app.runtime.directory_settings import directory_rows, prepare_directory
    manager = manager_with_settings(tmp_path, monkeypatch, {'shared': {'directories': {'one': {'name':'一','path':'/1'}},
        'directory_roles': {'manual_downloads':[{'directory_id':'one'}]}}})
    store = manager.store.central_config
    draft = next(row for row in directory_rows(store.read()) if row.field == 'entity:one')
    assert 'manual_downloads[0]' in draft.display
    change = prepare_directory(store.read(), draft, {'name':'一','path':'/2'})
    store.commit(store.prepare_document_patch({'shared': {'directory_roles': {'manual_downloads': [
        {'directory_id':'one'}, {'directory_id':'one','label':'二'}]}}}))
    with pytest.raises(Exception) as conflict:
        store.commit(change)
    assert conflict.value.code == 'config_conflict'
    with pytest.raises(Exception):
        prepare_directory(store.read(), draft, None)


def test_feature_selector_cannot_combine_write_or_cross_owner(tmp_path,monkeypatch):
    from app.handlers.plugin_handler import handle_feature_result
    from tests.test_shared_config_handler import SharedConfigHandlerTest
    from types import SimpleNamespace
    update,context,_ = SharedConfigHandlerTest().request()
    manager = manager_with_settings(tmp_path,monkeypatch)
    context.application.bot_data['telepiplex_plugin_manager'] = manager
    route = SimpleNamespace(plugin_id='sync',client=object())
    context.application.bot_data['telepiplex_plugin_router'] = SimpleNamespace(plugin_route=lambda _:route)
    monkeypatch.setattr('app.handlers.plugin_handler.init.check_user',lambda _:True)
    for extra in ({'config_navigation':'rename_ai'}, {'config_navigation':'sync_plex','config_patch':{'plex':{'token':'evil'}}}):
        asyncio.run(handle_feature_result(update,context,route,{'session':{'state':'close'},**extra}))
        assert context.user_data == {} and 'features' not in manager.store.central_config.read()
    asyncio.run(handle_feature_result(update,context,route,{'config_navigation':'sync_plex','session':{'state':'close'}}))
    assert context.user_data['host_shared_config'][(10,1)]['target']=='sync_plex'
    assert 'Plex' in update.callback_query.edit_message_text.await_args.args[0]
    asyncio.run(handle_feature_result(update,context,route,{'config_patch':{'plex':{'token':'stale'}}}))
    assert '旧配置会话已失效' in update.callback_query.edit_message_text.await_args.args[0]
    assert 'features' not in manager.store.central_config.read()
    context.application.bot_data['telepiplex_plugin_router']=SimpleNamespace(plugin_route=lambda _:None)
    asyncio.run(handle_feature_result(update,context,route,{'config_navigation':'sync_fanart','session':{'state':'close'}}))
    assert context.user_data['host_shared_config'][(10,1)]['target']=='sync_plex'


def test_handoff_entry_without_conversation_saves_once_without_echo(tmp_path,monkeypatch):
    from app.handlers.config_handler import register_feature_config_handlers
    from app.handlers.settings_editor import open_section
    from app.handlers.shared_config_handler import shared_config_callback,shared_config_message
    from tests.test_shared_config_handler import SharedConfigHandlerTest
    from unittest.mock import Mock
    from telegram import Chat, User, Message, CallbackQuery, Update
    from datetime import datetime,timezone
    manager = manager_with_settings(tmp_path,monkeypatch)
    update,ctx,_ = SharedConfigHandlerTest().request()
    ctx.application.bot_data['telepiplex_plugin_manager']=manager
    monkeypatch.setattr('app.handlers.shared_config_handler.init.check_user',lambda _:True)
    asyncio.run(open_section(update,ctx,manager,'sync_plex'))
    data=update.callback_query.edit_message_text.await_args.kwargs['reply_markup'].inline_keyboard[1][0].callback_data
    app=Mock()
    register_feature_config_handlers(app)
    conv=app.add_handler.call_args.args[0]
    user=User(1,'Test',False)
    message=Message(7,datetime.now(timezone.utc),Chat(10,'private'),from_user=user)
    event=Update(17,callback_query=CallbackQuery('7',user,'t',message=message,data=data))
    from unittest.mock import AsyncMock
    from telegram import Bot
    bot=Mock(spec=Bot)
    bot.answer_callback_query=AsyncMock()
    bot.edit_message_text=AsyncMock()
    bot.send_message=AsyncMock()
    event.callback_query.set_bot(bot)
    message.set_bot(bot)
    ctx.matches=[]
    asyncio.run(conv.handle_update(event,app,conv.check_update(event),ctx))
    reply=Message(8,message.date,message.chat,from_user=user,text='sensitive-test-input')
    reply.set_bot(bot)
    incoming=Update(18,message=reply)
    checked=conv.check_update(incoming)
    assert checked and checked[2].callback is shared_config_message
    asyncio.run(conv.handle_update(incoming,app,checked,ctx))
    update.callback_query.data=bot.send_message.await_args.kwargs['reply_markup'].inline_keyboard[0][0].callback_data
    asyncio.run(shared_config_callback(update,ctx))
    asyncio.run(shared_config_callback(update,ctx))
    assert manager.store.central_config.read()['features']['sync']['plex']['token']=='sensitive-test-input'
    assert 'sensitive-test-input' not in str(update.callback_query.edit_message_text.await_args_list)


def test_repair_retained_v2_archive_and_retry(tmp_path,monkeypatch):
    from app.runtime.config_settings import setting_rows
    monkeypatch.setattr('app.runtime.config_semantics_migration.load_builtin_contracts',contracts)
    manager=manager_with_settings(tmp_path,monkeypatch,{'config_version':2,'features':{'caption':{
        'providers':{'local_archive':{'roots':['relative']}}}}})
    assert manager.migration_status()['blockers']
    row=next(r for r in setting_rows(manager,'repair_caption') if r.field.endswith('.roots'))
    asyncio.run(manager.save_setting(row,['/archive']))
    assert manager.store.central_config.read()['config_version']==2
    assert not asyncio.run(manager.retry_semantic_migration())['blockers']
    assert manager.store.central_config.read()['config_version']==3


def test_directory_role_limits_and_inheritance(tmp_path,monkeypatch):
    from app.runtime.directory_settings import prepare_directory,directory_rows
    manager=manager_with_settings(tmp_path,monkeypatch,{'shared':{'directories':{
        'root':{'name':'根','path':'/'},'space':{'name':'空格','path':'/尾部 '}}}})
    store=manager.store.central_config
    for role,key,ok in [('standalone_subtitles','root',True),('manual_downloads','root',False),
                        ('unorganized_inventory','space',False),('manual_downloads','space',True)]:
        draft=next(r for r in directory_rows(store.read()) if r.field==role)
        value={'directory_id':key}
        if role=='manual_downloads':
            value=[value]
        if ok:
            asyncio.run(manager.save_directory(draft,value))
        else:
            with pytest.raises(Exception):
                asyncio.run(manager.save_directory(draft,value))
    assert store.effective('download',{})['save_directories']==[{'name':'空格','path':'尾部 '}]


def test_closed_section_without_metadata_fails_without_write(tmp_path,monkeypatch):
    from app.runtime import config_settings
    manager=manager_with_settings(tmp_path,monkeypatch)
    original=manager.store.central_config.path.read_bytes()
    monkeypatch.setattr(config_settings,'load_descriptor',lambda:{})
    with pytest.raises(Exception,match='metadata unavailable'):
        config_settings.setting_rows(manager,'sync_plex')
    assert manager.store.central_config.path.read_bytes()==original


def test_reset_last_automatic_value_still_reloads_rename_consumer(tmp_path,monkeypatch):
    from app.runtime.config_settings import setting_rows,prepare_setting
    manager=manager_with_settings(tmp_path,monkeypatch,{'features':{'caption':{'automatic':False}}})
    row=next(r for r in setting_rows(manager,'caption_policy') if r.field=='automatic')
    change=prepare_setting(manager,row,None,True)
    assert set(change.affected_plugins)=={'caption','rename'}


@pytest.mark.parametrize('version,marked,maximum',[(2,True,60),(3,False,60),(3,True,300)])
def test_metadata_timeout_prompt_matches_save_bounds(tmp_path,monkeypatch,version,marked,maximum):
    from app.runtime import config_settings as settings
    document={'config_version':version}
    if version==2:
        document['features']={'caption':{'output_path':'../blocked'}}
    manager=manager_with_settings(tmp_path,monkeypatch,document)
    descriptor=settings.load_descriptor()
    if not marked:
        descriptor['features']['search']['schema'].pop('x-telepiplex-metadata-timeout-contract',None)
    monkeypatch.setattr(settings,'load_descriptor',lambda:descriptor)
    row=next(r for r in settings.setting_rows(manager,'search_tvdb') if r.field.endswith('.timeout'))
    assert row.schema['maximum']==maximum
    with pytest.raises(Exception):
        asyncio.run(manager.save_setting(row,maximum+1))
    asyncio.run(manager.save_setting(row,maximum))
    assert manager.store.central_config.read()['features']['search']['metadata']['tvdb']['timeout']==maximum


def test_entity_delete_scope_and_raw_path_preservation(tmp_path,monkeypatch):
    from app.runtime.directory_settings import directory_rows
    manager=manager_with_settings(tmp_path,monkeypatch,{'shared':{'directories':{
        'root':{'name':'字幕','path':'/','vendor':{'enabled':True}}},
        'directory_roles':{'standalone_subtitles':{'directory_id':'root'}}}})
    store=manager.store.central_config
    row=next(r for r in directory_rows(store.read()) if r.field=='entity:root')
    with pytest.raises(Exception) as exc:
        asyncio.run(manager.save_directory(row,None))
    assert exc.value.details['config_error_paths']==['shared.directory_roles.standalone_subtitles']
    asyncio.run(manager.save_directory(row,{'name':'字幕','path':'字幕/ 归档 '}))
    assert store.read()['shared']['directories']['root']=={'name':'字幕','path':'/字幕/ 归档 ','vendor':{'enabled':True}}
