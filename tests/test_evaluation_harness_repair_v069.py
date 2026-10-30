import json,pytest
from alienape.evaluation_v069 import assess_completion

def proposal(code,path='hello.py'):
    return json.dumps({'payload':'ok','status':'ok','proposed_files':[{'path':path,'content':code}]})

@pytest.mark.parametrize('text,protocol,source,syntax,extracted',[
 ('Hier ist Code:\n```python\nprint("Hello")\n```',False,'SINGLE_FENCE','PASS',True),
 ('',False,'NONE','UNKNOWN',False),
 (proposal('print("Hello")'),True,'FILE_PROPOSAL','PASS',True),
 (proposal('def broken('),True,'FILE_PROPOSAL','FAIL',True),
 ('```python\nprint(1)\n```\n```python\nprint(2)\n```',False,'AMBIGUOUS','UNKNOWN',False),
 ('Oops\n```python\nprint(1)\n```',False,'SINGLE_FENCE','PASS',True),
 ('Nur Text ohne Code.',False,'NONE','UNKNOWN',False),
 ('```python\n\n```',False,'SINGLE_FENCE','UNKNOWN',False),
 ('```python\nprint(1)',False,'AMBIGUOUS','UNKNOWN',False),
 (proposal('print(1)','other.py'),True,'NONE','UNKNOWN',False),
])
def test_python_extraction(text,protocol,source,syntax,extracted):
    r=assess_completion(text,'hello.py')
    assert (r['protocol_valid'],r['extraction_source'],r['syntax_status'],r['code_extracted'])==(protocol,source,syntax,extracted)

def test_html_is_structural_not_syntax():
    r=assess_completion(proposal('<button>OK</button>','index.html'),'index.html')
    assert r['syntax_status']=='UNKNOWN' and r['plausibility_status']=='PLAUSIBLE' and r['extraction_source']=='FILE_PROPOSAL'

def test_model_tool_call_never_makes_proposal_valid():
    r=assess_completion(proposal('print(1)'),'hello.py',tool_calls=True)
    assert not r['protocol_valid'] and r['extraction_source']=='NONE' and r['syntax_status']=='UNKNOWN'
