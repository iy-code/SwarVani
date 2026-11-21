import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import HTTPException
from app import whatsapp


class SendTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_credentials(self):
        with patch.object(whatsapp, 'settings', SimpleNamespace(whatsapp_sending_ready=False)):
            with self.assertRaises(HTTPException) as error:
                await whatsapp.send_test_followup('SV-TEST1234')
            self.assertEqual(error.exception.status_code, 503)

    async def test_buttons_roundtrip_and_rejection(self):
        config = SimpleNamespace(whatsapp_sending_ready=True, whatsapp_test_recipient='919999999999',
                                 whatsapp_api_version='v24.0', whatsapp_phone_number_id='123',
                                 whatsapp_access_token='secret')
        client = AsyncMock()
        client.post.return_value = httpx.Response(200, json={'messages': [{'id': 'wamid.test'}]})
        with patch.object(whatsapp, 'settings', config), patch.object(whatsapp.httpx, 'AsyncClient') as factory:
            factory.return_value.__aenter__.return_value = client
            self.assertEqual(await whatsapp.send_test_followup('SV-TEST1234'), 'wamid.test')
            payload = client.post.call_args.kwargs['json']
            self.assertEqual(payload['to'], config.whatsapp_test_recipient)
            for button in payload['interactive']['action']['buttons']:
                parsed = whatsapp.parse_reply({'interactive': {'button_reply': button['reply']}})
                self.assertEqual(parsed[0], 'SV-TEST1234')
            client.post.return_value = httpx.Response(400, json={'error': {'message': 'secret'}})
            with self.assertRaises(HTTPException) as error:
                await whatsapp.send_test_followup('SV-TEST1234')
            self.assertEqual(error.exception.status_code, 502)
            self.assertNotIn('secret', error.exception.detail)
