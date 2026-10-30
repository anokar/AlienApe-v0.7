import httpx,pytest
from alienape.worker import user_error

@pytest.mark.parametrize('locale,expected',[('de','Zeitlimit'),('en','timed out'),('fr','délai')])
def test_server_timeout_is_localized(locale,expected):
    assert expected in user_error(httpx.TimeoutException('internal'),locale)

@pytest.mark.parametrize('locale',['de','en','fr'])
def test_http_code_is_preserved_without_raw_details(locale):
    request=httpx.Request('GET','http://localhost/');response=httpx.Response(503,request=request)
    result=user_error(httpx.HTTPStatusError('FAKE_PRIVATE_DETAIL',request=request,response=response),locale)
    assert '503' in result and 'FAKE_PRIVATE_DETAIL' not in result and 'SERVER_HTTP_ERROR' not in result
