import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock,Mock,patch
from consumer import TaskConsumer

class ConsumerApiKeyTests(unittest.IsolatedAsyncioTestCase):
    def consumer(self,key):
        c=TaskConsumer.__new__(TaskConsumer)
        c.config=SimpleNamespace(api_key=key)
        c.api_base_url='http://platform';c.api_username='account';c.api_password='password';c._api_token=None
        return c
    async def request(self,key,status,payload):
        c=self.consumer(key)
        client=AsyncMock();client.post.return_value=Mock(status_code=status,json=lambda:payload,text='denied')
        with patch('consumer.httpx.AsyncClient') as factory:
            factory.return_value.__aenter__.return_value=client
            token=await c._get_api_token()
        return c,client,token
    async def test_key_exchange_never_uses_password_login(self):
        c,client,token=await self.request('key-value',200,{'status':'success','data':{'token':'jwt'}})
        self.assertEqual(token,'jwt')
        client.post.assert_awaited_once_with('http://platform/api/api-keys/exchange-token/',headers={'X-API-Key':'key-value'})
        self.assertEqual(await c._get_api_token(),'jwt')
    async def test_invalid_key_does_not_fallback_to_account(self):
        c,client,token=await self.request('bad',401,{})
        self.assertIsNone(token);self.assertEqual(client.post.await_count,1)
    async def test_password_login_remains_supported(self):
        c,client,token=await self.request('',200,{'access':'legacy-jwt'})
        self.assertEqual(token,'legacy-jwt')
        client.post.assert_awaited_once_with('http://platform/api/token/',json={'username':'account','password':'password'})
