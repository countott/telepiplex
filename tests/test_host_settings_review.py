import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from telegram import Bot, CallbackQuery, Chat, Message, MessageEntity, Update, User
from telegram.ext import MessageHandler, filters

from tests.test_config_semantics_migration import contracts
from tests.test_host_settings import manager_with_settings


def panel(manager,monkeypatch):
    from tests.test_shared_config_handler import SharedConfigHandlerTest
    update,ctx,_=SharedConfigHandlerTest().request()
    ctx.application.bot_data['telepiplex_plugin_manager']=manager
    monkeypatch.setattr('app.handlers.shared_config_handler.init.check_user',lambda _:True)
    return update,ctx


@pytest.mark.parametrize('command',['search_config','auth'])
def test_declared_feature_command_switches_real_conversation(tmp_path,monkeypatch,command):
    from app.handlers.config_handler import register_feature_config_handlers
    from app.handlers.plugin_handler import dynamic_command_gateway,dynamic_message_gateway,ROUTER_KEY
    from app.handlers.settings_editor import open_section
    manager=manager_with_settings(tmp_path,monkeypatch)
    update,ctx=panel(manager,monkeypatch)
    feature='search' if command=='search_config' else 'download'
    route=SimpleNamespace(plugin_id=feature,client=AsyncMock(),manifest=SimpleNamespace(callbacks=(feature,)))
    route.client.request.side_effect=[{'actions':[],'session':{'state':'open'}},{'actions':[],'session':{'state':'close'}}]
    ctx.application.bot_data[ROUTER_KEY]=SimpleNamespace(command_route=lambda name:route if name==command else None,
                                                          plugin_route=lambda _:route,direct_message_route=lambda _:None)
    app=Mock()
    app.bot_data=ctx.application.bot_data
    register_feature_config_handlers(app)
    conv=app.add_handler.call_args.args[0]
    handlers=[conv,MessageHandler(filters.COMMAND,dynamic_command_gateway),MessageHandler(filters.TEXT & ~filters.COMMAND,dynamic_message_gateway)]
    bot=Mock(spec=Bot,username='test_bot')
    bot.answer_callback_query=AsyncMock()
    bot.edit_message_text=AsyncMock()
    bot.send_message=AsyncMock()
    user=User(1,'Owner',False)
    def event(text=None,data=None):
        message=Message(1,datetime.now(timezone.utc),Chat(10,'private'),from_user=user,text=text,
                        entities=[MessageEntity('bot_command',0,len(text))] if text and text.startswith('/') else [])
        message.set_bot(bot)
        if data:
            query=CallbackQuery('1',user,'test',message=message,data=data)
            query.set_bot(bot)
            return Update(1,callback_query=query)
        return Update(1,message=message)
    def dispatch(e):
        for handler in handlers:
            checked=handler.check_update(e)
            if checked:
                asyncio.run(handler.handle_update(e,app,checked,ctx))
                return
        raise AssertionError('update unhandled')
    ctx.matches=[]
    asyncio.run(open_section(update,ctx,manager,'sync_plex'))
    dispatch(event(data=update.callback_query.edit_message_text.await_args.kwargs['reply_markup'].inline_keyboard[1][0].callback_data))
    dispatch(event(text='/Movies'))
    old=bot.send_message.await_args.kwargs['reply_markup'].inline_keyboard[0][0].callback_data
    dispatch(event(text='/'+command))
    assert 'host_shared_config' not in ctx.user_data
    assert route.client.request.await_args.args[0]=='command.dispatch'
    dispatch(event(data=old))
    dispatch(event(text='feature reply'))
    assert route.client.request.await_args.args[0]=='message.dispatch'
    assert 'features' not in manager.store.central_config.read()


@pytest.mark.parametrize('active,retired',[(True,False),(False,True)])
def test_late_standalone_navigation_preserves_current_owner(tmp_path,monkeypatch,active,retired):
    from app.handlers.plugin_handler import handle_feature_result,ROUTER_KEY,COORDINATOR_KEY
    manager=manager_with_settings(tmp_path,monkeypatch)
    update,ctx=panel(manager,monkeypatch)
    route=SimpleNamespace(plugin_id='sync',client=object())
    ctx.application.bot_data[ROUTER_KEY]=SimpleNamespace(plugin_route=lambda _:route)
    ctx.application.bot_data[COORDINATOR_KEY]=SimpleNamespace(active=lambda *_:SimpleNamespace(plugin_id='search') if active else None)
    prior={'target':'search_prowlarr','owner':(10,1)}
    ctx.user_data['host_shared_config']={(10,1):prior}
    if retired:
        ctx.user_data['retired_config_sources']={(10,1):{'sync'}}
    asyncio.run(handle_feature_result(update,ctx,route,{'config_navigation':'sync_plex','session':{'state':'close'}}))
    assert ctx.user_data['host_shared_config'][(10,1)] is prior


def test_standalone_result_from_earlier_same_plugin_dispatch_stays_retired(tmp_path,monkeypatch):
    from app.handlers.plugin_handler import dynamic_command_gateway,ROUTER_KEY
    manager=manager_with_settings(tmp_path,monkeypatch)
    update,ctx=panel(manager,monkeypatch)
    update.effective_message.text='/sync_config'
    update.callback_query=None
    route=SimpleNamespace(plugin_id='sync',client=AsyncMock(),manifest=SimpleNamespace(callbacks=('sync',)))
    ctx.application.bot_data[ROUTER_KEY]=SimpleNamespace(command_route=lambda _:route,plugin_route=lambda _:route)
    async def scenario():
        started,release=asyncio.Event(),asyncio.Event()
        async def receive(*args,**kwargs):
            if not started.is_set():
                started.set()
                await release.wait()
                return {'session':{'state':'close'},'config_navigation':'sync_plex'}
            return {'actions':[],'session':{'state':'open'}}
        route.client.request.side_effect=receive
        old=asyncio.create_task(dynamic_command_gateway(update,ctx))
        await started.wait()
        await dynamic_command_gateway(update,ctx)
        release.set()
        await old
    asyncio.run(scenario())
    assert 'host_shared_config' not in ctx.user_data
    assert ctx.application.bot_data['telepiplex_plugin_sessions'][(10,1)]['plugin_id']=='sync'


@pytest.mark.parametrize('old',[True,False])
def test_preinstall_manual_role_fits_package(tmp_path,monkeypatch,old):
    from app.runtime import config_settings
    from app.runtime.directory_settings import directory_rows
    from app.runtime.config_projection import ProjectionContract
    from app.runtime.plugin_store import _validate
    manager=manager_with_settings(tmp_path,monkeypatch,{'shared':{'directories':{'space':{'name':'电影','path':'/ 电影 '}}}})
    desc=config_settings.load_descriptor()
    if old:
        desc['features']['download']['schema'].pop('x-telepiplex-download-directory-contract')
    monkeypatch.setattr(config_settings,'load_descriptor',lambda:desc)
    store=manager.store.central_config
    draft=next(r for r in directory_rows(store.read()) if r.field=='manual_downloads')
    if old:
        with pytest.raises(Exception):
            asyncio.run(manager.save_directory(draft,[{'directory_id':'space'}]))
        assert 'directory_roles' not in store.read()['shared']
    else:
        asyncio.run(manager.save_directory(draft,[{'directory_id':'space'}]))
        defaults,schema=contracts()['download']
        value=store.effective('download',defaults,contract=ProjectionContract(schema))
        assert _validate(schema,value)['save_directories']==[{'name':'电影','path':' 电影 '}]


def test_v2_new_manual_edit_rejects_changed_effective_path(tmp_path,monkeypatch):
    from app.runtime.config_settings import setting_rows
    manager=manager_with_settings(tmp_path,monkeypatch,{'config_version':2,'features':{'caption':{'output_path':'../bad'}}})
    row=setting_rows(manager,'repair_download')[0]
    with pytest.raises(Exception):
        asyncio.run(manager.save_setting(row,[{'name':'电影','path':'电影 '}]))


def test_two_bad_caption_fields_repaired_in_one_editor_commit(tmp_path,monkeypatch):
    from app.handlers.settings_editor import open_section
    from app.handlers.shared_config_handler import shared_config_callback,shared_config_message
    monkeypatch.setattr('app.runtime.config_semantics_migration.load_builtin_contracts',contracts)
    manager=manager_with_settings(tmp_path,monkeypatch,{'config_version':2,'features':{'caption':{
        'output_path':'../bad','providers':{'local_archive':{'roots':15}}}}})
    update,ctx=panel(manager,monkeypatch)
    asyncio.run(open_section(update,ctx,manager,'repair_caption'))
    def click(label):
        rows=update.callback_query.edit_message_text.await_args.kwargs['reply_markup'].inline_keyboard
        update.callback_query.data=next(b.callback_data for row in rows for b in row if label in b.text)
        asyncio.run(shared_config_callback(update,ctx))
    for field,value in [('output_path','/subs'),('roots','["/archive"]')]:
        click(field)
        update.effective_message.text=value
        asyncio.run(shared_config_message(update,ctx))
        if field=='output_path':
            click('确认保存')
            assert manager.store.central_config.read()['features']['caption']['output_path']=='../bad'
            assert ctx.user_data['host_shared_config'][(10,1)]['repairs']
    store=manager.store.central_config
    assert store.read()['features']['caption']['output_path']=='../bad'
    store.commit(store.prepare_document_patch({'features':{'download':{'access_token':'fresh'}}}))
    click('确认保存')
    assert store.read()['features']['caption']['providers']['local_archive']['roots']==['/archive']
    assert not asyncio.run(manager.retry_semantic_migration())['blockers']
    assert store.read()['features']['download']['access_token']=='fresh'


def test_large_directory_cards_are_paged(tmp_path,monkeypatch):
    from app.handlers.settings_editor import open_section
    from app.handlers.shared_config_handler import shared_config_callback
    entities={f'd{i:063d}':{'name':str(i),'path':'/'+str(i)} for i in range(80)}
    manager=manager_with_settings(tmp_path,monkeypatch,{'shared':{'directories':entities}})
    update,ctx=panel(manager,monkeypatch)
    asyncio.run(open_section(update,ctx,manager,'directories'))
    def screen():
        args=update.callback_query.edit_message_text.await_args
        buttons=[b for row in args.kwargs['reply_markup'].inline_keyboard for b in row]
        assert len(buttons)<=25 and len(args.args[0].encode('utf-16-le'))//2<=4096
        return buttons
    def click(button):
        update.callback_query.data=button.callback_data
        asyncio.run(shared_config_callback(update,ctx))
    seen=[]
    while True:
        buttons=screen()
        seen.extend(b.text for b in buttons)
        nxt=next((b for b in buttons if '下一页' in b.text),None)
        if nxt is None:
            break
        click(nxt)
    assert all(any(key in text for text in seen) for key in entities)
    click(next(b for b in buttons if '独立字幕导出' in b.text))
    buttons=screen()
    initial=buttons[0]
    click(next(b for b in buttons if '选项下一页' in b.text))
    current=ctx.user_data['host_shared_config'][(10,1)]['nonce']
    click(initial)
    assert ctx.user_data['host_shared_config'][(10,1)]['nonce']==current
    assert ctx.user_data['host_shared_config'][(10,1)]['stage']=='edit'
    # Every ID remains readable on the independent content pages.
    text_pages=[]
    while True:
        args=update.callback_query.edit_message_text.await_args
        text_pages.append(args.args[0])
        nxt=next((b for b in screen() if '内容下一页' in b.text),None)
        if nxt is None:
            break
        click(nxt)
    body=''.join(page.split('\n内容 ')[0] for page in text_pages)
    assert all(key in body for key in entities)


def test_multi_repair_guard_conflicts_without_partial_write(tmp_path,monkeypatch):
    from app.runtime.config_settings import setting_rows
    manager=manager_with_settings(tmp_path,monkeypatch,{'config_version':2,'features':{'caption':{
        'output_path':'../bad','providers':{'local_archive':{'roots':15}}}}})
    store=manager.store.central_config
    rows=setting_rows(manager,'repair_caption')
    entries=[(rows[0],'/subs',False),(rows[1],['/archive'],False)]
    store.commit(store.prepare_document_patch({'features':{'caption':{'output_path':'/other'}}}))
    with pytest.raises(Exception) as exc:
        asyncio.run(manager.save_repairs(entries))
    assert exc.value.code=='config_conflict'
    assert store.read()['features']['caption']=={'output_path':'/other','providers':{'local_archive':{'roots':15}}}


def test_entity_impact_screen_exposes_all_references_and_complete_path(tmp_path,monkeypatch):
    from app.runtime.config_projection import KINDS
    from app.handlers.settings_editor import open_section
    from app.handlers.shared_config_handler import shared_config_callback
    long_path='/'+('🎉'*4500)
    roles={'media_categories':{kind:{'directory_id':'common','plex_library_id':''} for kind in KINDS},
           'manual_downloads':[{'directory_id':'common'}], 'standalone_subtitles':{'directory_id':'common'},
           'unorganized_inventory':{'directory_id':'common'}}
    manager=manager_with_settings(tmp_path,monkeypatch,{'shared':{
        'directories':{'common':{'name':'电影','path':long_path}},'directory_roles':roles}})
    update,ctx=panel(manager,monkeypatch)
    asyncio.run(open_section(update,ctx,manager,'directories'))
    def click(text):
        rows=update.callback_query.edit_message_text.await_args.kwargs['reply_markup'].inline_keyboard
        update.callback_query.data=next(b.callback_data for row in rows for b in row if b.text==text)
        asyncio.run(shared_config_callback(update,ctx))
    click('common')
    pages=[]
    while True:
        args=update.callback_query.edit_message_text.await_args
        assert len(args.args[0].encode('utf-16-le'))//2<=4096
        pages.append(args.args[0].split('\n内容 ')[0])
        if not any(b.text=='内容下一页' for row in args.kwargs['reply_markup'].inline_keyboard for b in row):
            break
        click('内容下一页')
    preview=''.join(pages)
    assert long_path in preview
    for ref in [*(f'media_categories[{kind}]' for kind in KINDS),'manual_downloads[0]','standalone_subtitles','unorganized_inventory']:
        assert ref in preview


@pytest.mark.parametrize('other_navigation',[False,True])
def test_same_user_other_chat_command_preserves_original_confirmation(tmp_path,monkeypatch,other_navigation):
    from app.handlers.settings_editor import open_section
    from app.handlers.shared_config_handler import shared_config_callback,shared_config_message
    from app.handlers.plugin_handler import dynamic_command_gateway,ROUTER_KEY
    manager=manager_with_settings(tmp_path,monkeypatch)
    original,ctx=panel(manager,monkeypatch)
    original.effective_chat.id=20
    asyncio.run(open_section(original,ctx,manager,'sync_plex'))
    original.callback_query.data=original.callback_query.edit_message_text.await_args.kwargs['reply_markup'].inline_keyboard[1][0].callback_data
    asyncio.run(shared_config_callback(original,ctx))
    original.effective_message.text='draft-in-chat-20'
    asyncio.run(shared_config_message(original,ctx))
    original.callback_query.data=original.callback_query.edit_message_text.await_args.kwargs['reply_markup'].inline_keyboard[0][0].callback_data
    user=User(1,'Owner',False)
    command='/sync_config' if other_navigation else '/scan'
    message=Message(8,datetime.now(timezone.utc),Chat(10,'private'),from_user=user,text=command,
                    entities=[MessageEntity('bot_command',0,len(command))])
    bot=Mock(spec=Bot)
    bot.send_message=AsyncMock()
    message.set_bot(bot)
    event=Update(17,message=message)
    route=SimpleNamespace(plugin_id='sync',client=AsyncMock(),manifest=SimpleNamespace(callbacks=('sync',)))
    response={'actions':[],'session':{'state':'close'}}
    if other_navigation:
        response['config_navigation']='sync_fanart'
    route.client.request.return_value=response
    ctx.application.bot_data[ROUTER_KEY]=SimpleNamespace(command_route=lambda _:route,plugin_route=lambda _:route)
    gateway=MessageHandler(filters.COMMAND,dynamic_command_gateway)
    asyncio.run(gateway.handle_update(event,ctx.application,gateway.check_update(event),ctx))
    if other_navigation:
        assert 'Fanart' in bot.send_message.await_args.kwargs['text']
    asyncio.run(shared_config_callback(original,ctx))
    assert manager.store.central_config.read()['features']['sync']['plex']['token']=='draft-in-chat-20'
