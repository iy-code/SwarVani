"""Offline API and voice regressions: python -m unittest scripts.test_regressions -v."""
import asyncio
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from app import db, main, pipeline
from app.config import settings
from app.gnani import parse_stt_message
from app.pipeline import VisitSession
from app import extract, danger_signs
from app.normalization import normalize_numbers, latin_name
from app.schemas import FIELD_TIMEZONE, parse_date_hint

NOTE = 'Sunita Devi ka ANC checkup kiya, weight 52 kilo, agle mahine 15 ko aana hai.'
HINDI_NOTE = (
    'मैं आज साहिल के घर जा रही हूं आज उसके चेकअप करना है उसको आज हल्का सा फीवर '
    'पाया गया है उसका आज वजन 15 किलो है अभी के लिए दवाई दे दी है कल वापस आऊंगी देखने'
)


class SessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_worker_sets_fever_age_without_guessing_from_name(self):
        with patch.object(extract, 'settings', replace(settings, demo_mode=True)):
            session = VisitSession()
            events = await session.handle({'type': 'utterance', 'text': 'Demo Sita ke ghar gayi. Halka bhukhaar hai.'})
            self.assertEqual(events[0]['signs'][0]['ageGroup'], 'unknown')
            for group, level in (
                ('two_months_or_older', 'check'), ('under_two_months', 'urgent'), ('unknown', 'check'),
            ):
                events = await session.handle({'type': 'fever_age', 'ageGroup': group})
                self.assertEqual(events[0]['signs'][0]['ageGroup'], group)
                self.assertEqual(events[0]['signs'][0]['level'], level)
            await session.handle({'type': 'correct', 'text': 'दौरा पड़ा।'})
            events = await session.handle({'type': 'fever_age', 'ageGroup': 'two_months_or_older'})
            self.assertTrue(events[0]['message'].startswith('Urgent referral'))

    async def test_fever_check_can_escalate_with_age_or_urgent_sign(self):
        with patch.object(extract, 'settings', replace(settings, demo_mode=True)):
            session = VisitSession()
            first = await session.handle({'type': 'utterance', 'text': 'Sahil ke ghar gayi. Halka bhukhaar hai.'})
            self.assertEqual(first[0]['signs'][0]['level'], 'check')
            self.assertIn('Measure temperature', first[0]['message'])
            self.assertTrue(any(line.startswith('Check-up needed') for line in session.record.followUps))
            later = await session.handle({'type': 'correct', 'text': 'Baby is 10 days old.'})
            self.assertEqual(later[0]['signs'][0]['level'], 'urgent')
            self.assertTrue(any(line.startswith('Urgent referral flag') for line in session.record.followUps))

            session = VisitSession()
            mixed = await session.handle({'type': 'utterance', 'text': 'बुखार है और दौरा पड़ा।'})
            self.assertEqual({item['key']: item['level'] for item in mixed[0]['signs']},
                             {'convulsions': 'urgent', 'fever': 'check'})
            self.assertTrue(mixed[0]['message'].startswith('Urgent referral'))

    async def test_requested_voice_fields_and_corrections(self):
        with patch.object(extract, 'settings', replace(settings, demo_mode=True)):
            session = VisitSession()
            await session.handle({'type': 'utterance', 'text': 'अम्मार'})
            self.assertEqual(session.record.beneficiaryName, 'Ammar')
            await session.handle({'type': 'correct', 'text': 'fifty kilo, beneficiary id one two three four'})
            self.assertEqual(session.record.weightKg, 50)
            self.assertEqual(session.record.beneficiaryId, '1234')
            self.assertEqual(session.record.beneficiaryName, 'Ammar')
            self.assertIn('अम्मार', session.record.transcript)
            events = await session.handle({'type': 'correct', 'text': 'बच्चा दूध नहीं पी रहा है'})
            self.assertEqual(events[0]['type'], 'danger')
            self.assertIn('not_feeding', {s['key'] for s in session.record.dangerSigns})

    async def test_confirmation_requires_bare_agreement(self):
        session = VisitSession()
        for text in ('yes', 'haan', 'okay!', 'sahi hai'):
            self.assertTrue(session._is_confirmation(text), text)
        for text in ('not correct', 'no yes', 'yes weight 53', 'haan nahi'):
            self.assertFalse(session._is_confirmation(text), text)

    async def test_saved_record_is_immutable(self):
        with patch.object(extract, 'settings', replace(settings, demo_mode=True)):
            session = VisitSession()
            self.assertEqual((await session.handle({'type': 'confirm'}))[0]['type'], 'error')
            await session.handle({'type': 'utterance', 'text': NOTE})
            await session.handle({'type': 'confirm'})
            original = session.record.as_dict()
            for message in ({'type': 'correct', 'text': 'weight 60 kilo'}, {'type': 'confirm'}):
                self.assertEqual((await session.handle(message))[0]['type'], 'error')
            self.assertEqual(original, session.record.as_dict())

    async def test_transcript_parser(self):
        for frame in ('null', '[]', '42', 'invalid', b'audio'):
            self.assertIsNone(await parse_stt_message(frame))
        self.assertEqual(await parse_stt_message('{"transcript":"hello","is_final":false}'),
                         {'text': 'hello', 'final': False, 'done': False})


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        config = replace(settings, demo_mode=True, db_path=Path(self.tmp.name) / 'test.db', db_key='')
        self.patches = [patch.object(module, 'settings', config) for module in (main, db, pipeline, extract)]
        self.patches.append(patch.object(db, '_fernet', None))
        for item in self.patches:
            item.start()
        self.client = TestClient(main.app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        for item in reversed(self.patches):
            item.stop()
        self.tmp.cleanup()

    def receive_until(self, ws, kind):
        for _ in range(12):
            event = ws.receive_json()
            if event['type'] == kind:
                return event
        self.fail(f'No {kind} event')

    def test_http_websocket_save_and_purge(self):
        for path in ('health', 'records', 'followups', 'danger-signs'):
            self.assertEqual(self.client.get('/api/' + path).status_code, 200)
        with self.client.websocket_connect('/ws/session') as ws:
            self.assertEqual(ws.receive_json()['type'], 'hello')
            for raw in ('{', 'null', '[]', '{"type":4}'):
                ws.send_text(raw)
                self.assertEqual(ws.receive_json()['type'], 'error')
            ws.send_json({'type': 'start'})
            self.receive_until(ws, 'agent')
            ws.send_json({'type': 'utterance', 'text': NOTE})
            record = self.receive_until(ws, 'record')['record']
            self.assertEqual(record['weightKg'], 52)
            self.receive_until(ws, 'agent')
            ws.send_json({'type': 'confirm'})
            self.receive_until(ws, 'saved')
            saved = self.client.get('/api/records').json()['records']
            self.assertEqual(len(saved), 1)
            self.assertNotIn('transcript', saved[0])
            self.assertTrue(self.client.get('/api/followups').json()['followUps'])
        self.assertEqual(self.client.post('/api/purge-transcripts').json()['purged'], 1)
        self.assertEqual(self.client.post('/api/purge-transcripts').json()['purged'], 0)

    def test_storage_failure_never_reports_saved(self):
        with self.client.websocket_connect('/ws/session') as ws:
            ws.receive_json()
            ws.send_json({'type': 'utterance', 'text': NOTE})
            self.receive_until(ws, 'agent')
            with patch.object(db, 'save_record', side_effect=OSError('test disk failure')):
                ws.send_json({'type': 'confirm'})
                self.assertEqual(ws.receive_json()['type'], 'error')
            ws.send_json({'type': 'confirm'})
            self.assertEqual(ws.receive_json()['type'], 'saved')

    def test_danger_alert_before_extraction_finishes(self):
        async def slow_extract(text):
            await asyncio.sleep(0.05)
            return {}, 'heuristic'
        with patch.object(pipeline, 'extract_with_llm', slow_extract):
            with self.client.websocket_connect('/ws/session') as ws:
                ws.receive_json()
                ws.send_json({'type': 'utterance', 'text': 'not feeding'})
                event = ws.receive_json()
                self.assertEqual(event['type'], 'danger')
                self.assertEqual(event['signs'][0]['key'], 'not_feeding')


    def test_audio_stream_produces_transcript_and_record(self):
        class SpeechStream:
            def __init__(self):
                self.frames = asyncio.Queue()
                self.audio = []

            def __aiter__(self):
                return self

            async def __anext__(self):
                frame = await self.frames.get()
                if frame is None:
                    raise StopAsyncIteration
                return frame

            async def send(self, frame):
                if isinstance(frame, bytes):
                    self.audio.append(frame)
                else:
                    import json
                    await self.frames.put(json.dumps({'transcript': HINDI_NOTE, 'is_final': True}))

            async def close(self):
                await self.frames.put(None)

        stream = SpeechStream()
        live = replace(settings, demo_mode=False, gnani_api_key='test-only')
        with patch.object(main, 'settings', live), patch.object(main, 'open_stt', AsyncMock(return_value=stream)):
            with self.client.websocket_connect('/ws/session') as ws:
                ws.receive_json()
                ws.send_json({'type': 'audio_start'})
                ws.send_bytes(b'\x00\x00' * 512)
                ws.send_json({'type': 'audio_stop'})
                transcript = self.receive_until(ws, 'transcript')
                self.assertEqual(transcript['text'], HINDI_NOTE)
                self.assertTrue(transcript['final'])
                event = self.receive_until(ws, 'record')
                self.assertEqual(event['missing'], [])
                self.assertEqual(event['record']['beneficiaryName'], 'Sahil')
                self.assertEqual(event['record']['weightKg'], 15)
                expected_date = datetime.now(FIELD_TIMEZONE).date() + timedelta(days=1)
                self.assertEqual(event['record']['nextVisit'], expected_date.isoformat())
                self.assertEqual(event['record']['transcript'], HINDI_NOTE)
                self.assertEqual(len(stream.audio), 1)


class ExtractionTests(unittest.TestCase):
    def test_spoken_measurements(self):
        for text, expected in [('fifty kilo', 50), ('weight fifty-two kilograms', 52),
                               ('one hundred and twenty kilos', 120), ('two point eight kilo', 2.8),
                               ('पचास किलो', 50), ('वजन बावन किलो', 52), ('वजन ५० किलो', 50),
                               ('दो दशमलव आठ किलो', 2.8), ('120 kg', 120)]:
            with self.subTest(text=text):
                self.assertEqual(extract.extract_heuristic(text).get('weightKg'), expected)
        self.assertEqual(extract.extract_heuristic('BP one twenty by eighty')['bp'], '120/80')
        self.assertEqual(extract.extract_heuristic('BP one seventy five by eighty')['bp'], '175/80')
        self.assertEqual(extract.extract_heuristic('Hb twelve point five')['hb'], 12.5)
        self.assertNotIn('weightKg', extract.extract_heuristic('350 kg'))

    def test_names_and_identifiers(self):
        for text in ['नाम अम्मार है, वजन fifty kilo, beneficiary id 1234',
                     'name Ammar, fifty kilo, beneficiary ID one two three four']:
            with self.subTest(text=text):
                data = extract.extract_heuristic(text)
                self.assertEqual(data['beneficiaryName'], 'Ammar')
                self.assertEqual(data['beneficiaryId'], '1234')
                self.assertEqual(data['weightKg'], 50)
        self.assertEqual(extract.extract_heuristic('beneficiary id 001234')['beneficiaryId'], '001234')
        self.assertEqual(extract.extract_heuristic('लाभार्थी आईडी १२३४')['beneficiaryId'], '1234')
        self.assertEqual(extract.extract_heuristic('beneficiary ID वन टू थ्री फोर फाइव')['beneficiaryId'], '12345')
        self.assertNotIn('beneficiaryName', extract.extract_heuristic('beneficiary id 1234'))
        self.assertEqual(extract.extract_heuristic('सुनीता देवी का वजन पचास किलो')['beneficiaryName'], 'Sunita Devi')
        screenshot_note = (
            'beneficiary आयुष झा, beneficiary ID वन टू थ्री फोर, visit type Today '
            'weight fifty kg, BP one fifty five, vaccination not done yet. '
            'Supplements, no. Next visit tomorrow.'
        )
        screenshot_data = extract.extract_heuristic(screenshot_note)
        self.assertEqual(screenshot_data['beneficiaryName'], 'Ayush Jha')
        self.assertEqual(screenshot_data['beneficiaryId'], '1234')
        self.assertEqual(screenshot_data['weightKg'], 50)
        self.assertNotIn('beneficiaryName', extract.extract_heuristic('beneficiary ID 1234'))
        self.assertEqual(extract.extract_heuristic('लाभार्थी आयुष झा')['beneficiaryName'], 'Ayush Jha')
        self.assertEqual(normalize_numbers('next visit 2026-12-15'), 'next visit 2026-12-15')
        today = datetime.now(FIELD_TIMEZONE).date()
        self.assertEqual(extract.extract_heuristic('next visit tomorrow')['nextVisit'], (today + timedelta(days=1)).isoformat())

    def test_hindi_visit_context_uses_name_and_future_return(self):
        tomorrow = datetime.now(FIELD_TIMEZONE).date() + timedelta(days=1)
        for text, name in (
            (HINDI_NOTE, 'Sahil'),
            ('मैं आज साहिल कुमार के घर गई। वजन पंद्रह किलो है। कल वापस आऊँगी।', 'Sahil Kumar'),
            ('Main aaj Sahil ke ghar gayi, 15 kilo, kal wapas aaungi.', 'Sahil'),
        ):
            with self.subTest(text=text):
                self.assertEqual(extract.extract_heuristic(text), {
                    'beneficiaryName': name, 'weightKg': 15, 'nextVisit': tomorrow.isoformat(),
                })
        for past_note in (
            'कल साहिल के घर गई थी। वजन 15 किलो है।',
            'मैं कल वापस आई थी।',
            'कल वापस आऊंगी नहीं।',
            'next visit BP 120/80',
            'next visit 15 kilo',
        ):
            with self.subTest(text=past_note):
                self.assertNotIn('nextVisit', extract.extract_heuristic(past_note))
        self.assertNotIn('beneficiaryName', extract.extract_heuristic('उसके घर गई और उसका वजन 15 किलो है।'))
        self.assertEqual(extract.extract_heuristic('नाम साहिल कुमार है, अगली मुलाक़ात कल'), {
            'beneficiaryName': 'Sahil Kumar', 'nextVisit': tomorrow.isoformat(),
        })
        self.assertEqual(parse_date_hint('कल', today=date(2026, 12, 31)), '2027-01-01')
        self.assertEqual(parse_date_hint('अगले महीने की 15 तारीख़', today=date(2026, 12, 31)), '2027-01-15')
        self.assertEqual(parse_date_hint('15 दिसंबर', today=date(2026, 11, 22)), '2026-12-15')

    def test_safety_phrase_catalog_and_negation(self):
        for sign in danger_signs.DANGER_SIGNS:
            for phrase in sign.phrases:
                with self.subTest(phrase=phrase):
                    self.assertIn(sign.key, {m.key for m in danger_signs.scan(phrase)})
        for text in ['no fever', 'bukhar nahi hai', 'बुखार नहीं है', 'no convulsions', 'not bleeding']:
            with self.subTest(text=text):
                self.assertEqual(danger_signs.scan(text), [])
        self.assertEqual({m.key for m in danger_signs.scan('no fever but fast breathing')}, {'fast_breathing'})
        self.assertEqual({m.key for m in danger_signs.scan('bukhar nahi hai lekin bukhar hai')}, {'fever'})
        for phrase in ('bhukhaar', 'बुख़ार', 'हल्का सा फीवर', 'bleeding', 'fast breathing'):
            with self.subTest(phrase=phrase):
                self.assertEqual([m.level for m in danger_signs.scan(phrase)], ['check'])
        for phrase in ('बेहोश', 'दौरा पड़ा', 'heavy bleeding', 'severe chest indrawing', 'not feeding'):
            with self.subTest(phrase=phrase):
                self.assertEqual([m.level for m in danger_signs.scan(phrase)], ['urgent'])



if __name__ == '__main__':
    unittest.main()
