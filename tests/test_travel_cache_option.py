import io
import json
import queue
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'route_checker'))
import optimizer as opt
from r5_matrix import R5Matrix
from test_optimizer import Router,request

class CacheOptionTests(unittest.TestCase):
    def test_boolean_validation(self):
        self.assertTrue(opt.validate(request(),Router)['useTravelCache'])
        self.assertFalse(opt.validate(dict(request(),useTravelCache=False),Router)['useTravelCache'])
        with self.assertRaises(ValueError):opt.validate(dict(request(),useTravelCache='false'),Router)

    def test_otp_bypass_recalculates_without_overwriting_existing_cache(self):
        for fast in (False,True):
            with self.subTest(fast=fast),tempfile.TemporaryDirectory() as folder:
                c=opt.TravelCache.__new__(opt.TravelCache);c.path=Path(folder)/'pairs.sqlite3';c.version='test'
                with sqlite3.connect(c.path) as db:db.execute('CREATE TABLE pairs (key TEXT PRIMARY KEY,value TEXT NOT NULL)')
                plan=Mock(side_effect=[{'itineraries':[{'end':60}]},{'itineraries':[{'end':120}]},{'itineraries':[{'end':180}]}])
                c.router=SimpleNamespace(plan=plan,timestamp=float)
                route=c.route_fast if fast else c.route
                args=({'lat':22.3,'lon':114.1},{'lat':22.4,'lon':114.2},datetime.fromtimestamp(0,Router.HK),['mtr'])
                original,hit=route(*args);self.assertFalse(hit)
                self.assertEqual(route(*args),(original,True))
                self.assertEqual(route(*args,use_cache=False)[0]['seconds'],120)
                self.assertEqual(route(*args,use_cache=False)[0]['seconds'],180)
                self.assertEqual(route(*args),(original,True))
                self.assertEqual(plan.call_count,3)
                self.assertEqual(plan.call_args.kwargs['for_optimization'],'fast' if fast else True)

    def test_r5_bypass_ignores_and_preserves_saved_matrix(self):
        with tempfile.TemporaryDirectory() as folder,patch('r5_matrix.fingerprint',return_value='test'):
            c=R5Matrix.__new__(R5Matrix);c.root=Path(folder);c.folder=Path(folder)
            calls=[]
            def start():
                calls.append(1);c.process=SimpleNamespace(stdin=io.StringIO());c.messages=queue.Queue()
                c.messages.put(dict(kind='result',rows=[dict(seconds=len(calls)*60)],buildSeconds=0,computeSeconds=1))
            c.start=start
            spec=dict(modes=['mtr'],epoch=datetime(2026,9,24,tzinfo=Router.HK),points=[dict(lat=22.3,lon=114.1)])
            emit=lambda *a,**kw:None
            original,_=c(spec,emit,lambda:False)
            self.assertTrue(c(spec,emit,lambda:False)[1]['r5CacheHit'])
            before={p.name:p.read_bytes() for p in Path(folder).glob('*.json')}
            fresh,metrics=c(dict(spec,useTravelCache=False),emit,lambda:False)
            self.assertNotEqual(original,fresh);self.assertFalse(metrics['r5CacheHit'])
            self.assertEqual(before,{p.name:p.read_bytes() for p in Path(folder).glob('*.json')})
            self.assertEqual(len(calls),2)
            larger,meta=c(dict(spec,planningMode='large'),emit,lambda:False)
            self.assertFalse(meta['r5CacheHit'])
            self.assertIn('large-isolated-origin',meta['r5Policy'])
            self.assertEqual(c(dict(spec,planningMode='large'),emit,lambda:False)[0],larger)
            self.assertEqual(c(spec,emit,lambda:False)[0],original)

if __name__=='__main__':unittest.main()
