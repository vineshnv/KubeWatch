import json
import pytest
from app.engine import Session, CommandError, parse_result

def test_invalid_operation_rejected():
    with pytest.raises(CommandError):
        Session('local').run('rm-everything')

def test_parse_nodes():
    payload={'items':[{'metadata':{'name':'node-1'},'status':{'conditions':[{'type':'Ready','status':'True'}], 'nodeInfo':{'kubeletVersion':'v1.30'}}}]}
    parsed=parse_result('nodes',{'ok':True,'output':json.dumps(payload),'error':''})
    assert parsed['rows'][0]['name']=='node-1'
    assert parsed['rows'][0]['status']=='True'

def test_parse_pod_restarts():
    payload={'items':[{'metadata':{'name':'p','namespace':'tools'},'status':{'phase':'Running','containerStatuses':[{'restartCount':2}]}}]}
    assert parse_result('pods',{'ok':True,'output':json.dumps(payload),'error':''})['rows'][0]['restarts']==2

def test_parse_bad_json():
    assert not parse_result('pods',{'ok':True,'output':'not-json','error':''})['ok']

def test_validate_kubeconfig():
    with pytest.raises(CommandError): Session('local').attach_kubeconfig(b'not kube config')

def test_local_run():
    assert 'output' in Session('local').run('overview')
