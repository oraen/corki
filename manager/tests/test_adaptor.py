"""Gateway management with isolated config and deterministic process control."""
import importlib.util
from pathlib import Path
import json
import pytest
from corki.config import CorkiSettings
from corki.core.runtime import _create_model, _model_capabilities

spec=importlib.util.spec_from_file_location('adaptor_manager',Path(__file__).parents[1]/'server.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)

@pytest.fixture
def store(tmp_path,monkeypatch):
 home=tmp_path/'home';home.mkdir()
 (home/'settings.json').write_text('// retain comment\n'+json.dumps({'default':{'provider':'existing','model':'fixture'},'providers':{'existing':{'api_key':'fixture-key','base_url':'https://api.example.invalid/v1','models':{'fixture':{'context_window':262144}}}},'unrelated':{'flag':True}}))
 store=module.Store(home,tmp_path)
 store.commands=[];store.checked_providers=[];store.service={'running':False,'port':17001};store.fail_start=None;store.auto_port=17001
 def control(action,*,port=None,install=False,provider=None):
  store.commands.append((action,port))
  if action=='check':store.checked_providers.append(provider)
  if action=='check':return {'ready':True,**({'models':{'auto':{'context_window':200000},'GLM-5.3':{'context_window':180000}}} if provider=='qoder' else {})}
  if action=='stop':store.service['running']=False
  if action=='start-auto':
   if not store.service['running']:
    if store.fail_start==store.auto_port:raise ValueError('fixture startup failure')
    store.service.update(running=True,port=store.auto_port)
  if action=='start':
   if store.fail_start==port:raise ValueError('fixture startup failure')
   store.service.update(running=True,port=port)
  return {**store.service,'managed':True}
 monkeypatch.setattr(store.adaptor,'control',control)
 return store

def change(store,**values):
 return store.mutate({'kind':'adaptor','revision':module.revision(store.config_text()),**values})

def enable_provider(store):
 change(store,action='power',enabled=True)
 return change(store,action='provider',provider='openai',enabled=True)

def test_enable_creates_real_profile_and_keeps_existing_default(store):
 result=enable_provider(store)
 config=module.document.loads(store.config_text());profile=config['providers']['adaptor-openai']
 assert profile['base_url']=='http://127.0.0.1:17001/openai'
 assert profile['api_mode']=='responses' and profile['api_key']==module.ADAPTOR_LOCAL_KEY
 assert profile['enabled'] is True and profile['models']
 assert config['default']=={'provider':'existing','model':'fixture'}
 assert config['unrelated']=={'flag':True} and '// retain comment' in store.config_text()
 assert any(action=='check' for action,_ in store.commands)
 assert result['gateway']['providers'][0]['enabled'] and result['gateway']['running']
 settings=CorkiSettings.for_directory(store.workspace,config_file=store.config,provider='adaptor-openai')
 assert settings.provider_enabled and settings.api_base.endswith('/openai')

def test_disabled_profile_is_kept_and_model_creation_is_blocked(store):
 enable_provider(store)
 models=module.document.loads(store.config_text())['providers']['adaptor-openai']['models']
 change(store,action='provider',enabled=False)
 config=module.document.loads(store.config_text());assert config['providers']['adaptor-openai']['models']==models
 assert config['providers']['adaptor-openai']['enabled'] is False
 assert store.service['running']
 settings=CorkiSettings.for_directory(store.workspace,config_file=store.config,provider='adaptor-openai')
 assert not any(owner=='adaptor-openai' for _,owner in settings.model_choices())
 with pytest.raises(ValueError,match='关闭'):_create_model(settings,_model_capabilities(settings))

def test_model_management_cannot_toggle_system_provider(store):
 enable_provider(store)
 original=store.config_text();commands=list(store.commands)
 for enabled in (False,True):
  with pytest.raises(ValueError,match='主页-工具箱-套餐转发'):
   store.mutate({'kind':'providers','action':'toggle','id':'adaptor-openai','values':{'enabled':enabled},'revision':module.revision(store.config_text())})
 assert store.config_text()==original and store.commands==commands
 assert store.service['running']
 change(store,action='provider',enabled=False)
 assert store.service['running']


def test_auto_port_updates_managed_and_legacy_without_losing_models(store):
 config=module.document.loads(store.config_text());config['providers']['existing']['base_url']='http://127.0.0.1:17001'
 store.config.write_text(module.document.dumps(config));store.auto_port=18231
 enable_provider(store)
 config=module.document.loads(store.config_text())
 saved=config['providers']['adaptor-openai']['models']
 for name in ('existing','adaptor-openai'):
  assert config['providers'][name]['base_url']=='http://127.0.0.1:18231/openai'
  assert config['providers'][name]['api_key']==module.ADAPTOR_LOCAL_KEY
 assert store.adaptor.port()==18231
 store.commands.clear();enable_provider(store)
 assert ('stop',None) not in store.commands
 assert module.document.loads(store.config_text())['providers']['adaptor-openai']['models']==saved

@pytest.mark.parametrize('port',[0,65536,17002,True,1.5,'18001'])
def test_invalid_port_does_not_change_service_or_config(store,port):
 original=store.config_text()
 with pytest.raises(ValueError):change(store,action='port',port=port)
 assert original==store.config_text() and not store.commands

def test_failed_auto_start_keeps_configuration(store):
 original=store.config_text();store.auto_port=18232;store.fail_start=18232
 with pytest.raises(ValueError):change(store,action='power',enabled=True)
 assert store.config_text()==original and store.adaptor.port()==17001
 assert not store.service['running']

@pytest.mark.parametrize('name',['adaptor-openai','adaptor-claude',' Adaptor-other '])
def test_reserved_provider_creation_is_rejected(store,name):
 with pytest.raises(ValueError,match="供应商名称不能以\'adaptor-\'开头"):
  store.mutate({'kind':'providers','action':'save','id':name,'values':{'id':name,'base_url':'https://api.example.invalid','api_mode':'responses'},'revision':module.revision(store.config_text())})
 config=module.document.loads(store.config_text());config['providers']['adaptor-claude']=config['providers']['existing']
 with pytest.raises(ValueError,match="供应商名称不能以\'adaptor-\'开头"):store.validate_config(module.document.dumps(config))

def test_invalid_credentials_leave_configuration_and_runtime_untouched(store,monkeypatch):
 before=store.config_text()
 def control(*args,**kwargs):raise ValueError('未登录 Codex')
 monkeypatch.setattr(store.adaptor,'control',control)
 with pytest.raises(ValueError,match='Codex'):enable_provider(store)
 assert before==store.config_text()

def test_closed_regular_provider_has_real_enabled_setting(store):
 store.mutate({'kind':'providers','action':'toggle','id':'existing','values':{'enabled':False},'revision':module.revision(store.config_text())})
 settings=CorkiSettings.for_directory(store.workspace,config_file=store.config)
 assert settings.provider_enabled is False

@pytest.mark.parametrize('version',[1,2])
def test_independent_json_control_protocol_is_versioned(store,monkeypatch,version):
 from types import SimpleNamespace
 calls=[]
 monkeypatch.setattr(store.adaptor,'control',module.GatewayManagement.control.__get__(store.adaptor))
 monkeypatch.setattr(store.adaptor,'interpreter',lambda **kwargs:'/fixture/gateway-python')
 def run(command,**kwargs):
  calls.append(command)
  return SimpleNamespace(returncode=0,stdout=json.dumps({'protocol_version':version,'running':False,'port':17001}))
 monkeypatch.setattr(module.subprocess,'run',run)
 if version==1:
  assert store.adaptor.control('status')['running'] is False
 else:
  with pytest.raises(ValueError,match='版本'):store.adaptor.control('status')
 assert calls[0][:4]==['/fixture/gateway-python','-m','corki_openai_gateway.control','status']
 assert '--home' in calls[0] and 'fixture-token' not in str(calls[0])


def test_managed_connection_settings_are_read_only(store):
 enable_provider(store)
 original=store.config_text()
 profile=module.document.loads(original)['providers']['adaptor-openai']
 with pytest.raises(ValueError,match='适配模型不允许编辑'):
  store.mutate({'kind':'providers','action':'save','id':'adaptor-openai',
   'revision':module.revision(original),'values':{**profile,'id':'adaptor-openai'}})
 assert store.config_text()==original


def test_provider_enable_requires_running_gateway_without_mutation(store):
 before=store.config_text()
 with pytest.raises(ValueError,match='需要先开启适配网关'):
  change(store,action='provider',enabled=True)
 assert store.config_text()==before and not store.service['running']
 assert all(command=='status' for command,_ in store.commands)


def test_provider_switches_never_control_gateway_or_other_providers(store):
 enable_provider(store)
 config=module.document.loads(store.config_text())
 config['providers']['other']=dict(config['providers']['existing'])
 store.config.write_text(module.document.dumps(config))
 before=config['providers']['other'].copy()
 for enabled in (False,True):
  store.commands.clear()
  change(store,action='provider',enabled=enabled)
  assert not any(action in ('start','start-auto','stop') for action,_ in store.commands)
  assert store.service['running']
  assert module.document.loads(store.config_text())['providers']['other']==before
 # An already-enabled supplier can still be turned off after an external stop.
 store.service['running']=False
 change(store,action='provider',enabled=False)
 assert module.document.loads(store.config_text())['providers']['adaptor-openai']['enabled'] is False
 assert not store.service['running']


def test_gateway_power_does_not_enable_supplier(store):
 change(store,action='power',enabled=True)
 assert store.service['running']
 assert 'adaptor-openai' not in module.document.loads(store.config_text())['providers']
 enable_provider(store)
 change(store,action='power',enabled=False)
 assert not store.service['running']
 change(store,action='power',enabled=True)
 assert store.service['running']
 assert not module.document.loads(store.config_text())['providers']['adaptor-openai']['enabled']


def test_qoder_creates_independent_chat_provider_and_checks_its_login(store):
 change(store,action='power',enabled=True)
 change(store,action='provider',provider='qoder',enabled=True)
 profile=module.document.loads(store.config_text())['providers']['adaptor-qoder']
 assert profile['api_mode']=='chat_completions'
 assert profile['base_url']=='http://127.0.0.1:17001/qoder'
 assert list(profile['models'])==['auto','GLM-5.3']
 assert profile['api_key']==module.ADAPTOR_LOCAL_KEY
 assert store.checked_providers[-1]=='qoder'
 assert 'adaptor-openai' not in module.document.loads(store.config_text())['providers']
 change(store,action='provider',provider='qoder',enabled=False)
 assert store.service['running']
 assert not module.document.loads(store.config_text())['providers']['adaptor-qoder']['enabled']
 assert profile['models']==module.document.loads(store.config_text())['providers']['adaptor-qoder']['models']


def test_qoder_login_failure_leaves_config_and_running_gateway_unchanged(store,monkeypatch):
 change(store,action='power',enabled=True)
 original=store.config_text()
 control=store.adaptor.control
 def reject(action,**kwargs):
  if action=='check' and kwargs.get('provider')=='qoder':
   raise ValueError('开启失败，没有检测到对应登录信息')
  return control(action,**kwargs)
 monkeypatch.setattr(store.adaptor,'control',reject)
 with pytest.raises(ValueError,match='登录信息'):
  change(store,action='provider',provider='qoder',enabled=True)
 assert store.config_text()==original and store.service['running']


def test_qoder_catalog_update_adds_models_without_replacing_custom_settings(store):
 change(store,action='power',enabled=True)
 config=module.document.loads(store.config_text())
 config['providers']['adaptor-qoder']={'api_key':'fixture-local-key','base_url':'http://127.0.0.1:17001/qoder','api_mode':'chat_completions','enabled':False,'models':{'auto':{'context_window':123456,'description':'user settings'},'private-alias':{'context_window':131072}}}
 store.config.write_text(module.document.dumps(config))
 change(store,action='provider',provider='qoder',enabled=True)
 models=module.document.loads(store.config_text())['providers']['adaptor-qoder']['models']
 assert models['auto']=={'context_window':123456,'description':'user settings'}
 assert models['private-alias']=={'context_window':131072}
 assert models['GLM-5.3']=={'context_window':180000}
