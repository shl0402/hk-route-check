"""Persistent, cancellable R5 worker. Matrix seeds never certify a schedule."""
import atexit
import hashlib
import json
import queue
import subprocess
import sys
import threading
from pathlib import Path

POLICY = 'r5py-1.1.7-p90-10min-fallback60-draws20-parallel2-v5'


def fingerprint(root, modes):
    files=[root/'data/generated/hk-transit-EXPERIMENTAL.gtfs.zip',root/'data/raw/2026-09-17/osm/hong-kong-latest.osm.pbf']
    return hashlib.sha256(json.dumps([POLICY,sorted(modes),[(str(p),p.stat().st_size,p.stat().st_mtime_ns) for p in files]]).encode()).hexdigest()


class R5Matrix:
    def __init__(self, root, folder):
        self.root=Path(root);self.folder=Path(folder)/'r5';self.folder.mkdir(parents=True,exist_ok=True)
        self.process=None;self.messages=None;self.log=None
        atexit.register(self.close)

    def close(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:self.process.kill();self.process.wait()
        self.process=None
        if self.log:self.log.close();self.log=None

    def start(self):
        if self.process and self.process.poll() is None:return
        self.close()
        self.log=(self.folder/'worker.log').open('a')
        self.process=subprocess.Popen([sys.executable,str(Path(__file__).with_name('r5_worker.py')),str(self.folder)],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=self.log,text=True,bufsize=1)
        self.messages=queue.Queue();process=self.process;messages=self.messages;log=self.log
        def read():
            for line in process.stdout:
                if line.startswith('R5JSON '):
                    try:messages.put(json.loads(line[7:]))
                    except ValueError:pass
                else:
                    try:log.write(line);log.flush()
                    except ValueError:pass
            messages.put({'kind':'error','message':'R5 worker stopped. Check data/optimization/r5/worker.log.'})
        threading.Thread(target=read,daemon=True).start()

    def __call__(self,spec,emit,cancelled):
        from optimizer import Cancelled
        version=fingerprint(self.root,spec['modes'])
        request=dict(root=str(self.root),version=version,modes=spec['modes'],
            departure=spec['epoch'].isoformat(),points=[dict(id=str(i),lat=p['lat'],lon=p['lon']) for i,p in enumerate(spec['points']) if p['lat'] is not None])
        policy=POLICY
        if spec.get('planningMode')=='large':
            request['planningMode']='large'
            request['matrixPolicy']='10min-isolated-origin-retry-v1'
            policy=POLICY+'-large-isolated-origin-retry-v1'
        key=hashlib.sha256(json.dumps(request,sort_keys=True).encode()).hexdigest()
        path=self.folder/(key+'.json')
        if cancelled():raise Cancelled()
        use_cache=spec.get('useTravelCache',True)
        if use_cache and path.exists():
            data=json.loads(path.read_text())
            emit('matrix','Reusing the R5 travel-time matrix.',done=len(data['rows']),total=len(data['rows']))
            return data['rows'],dict(r5CacheHit=True,r5Policy=policy)
        emit('matrix','Preparing R5. First use builds a reusable network; later runs load its cache.')
        self.start();self.process.stdin.write(json.dumps(request)+'\n');self.process.stdin.flush()
        while True:
            if cancelled():self.close();raise Cancelled()
            try:message=self.messages.get(timeout=.2)
            except queue.Empty:continue
            if message['kind']=='error':
                self.close();raise RuntimeError(message['message'])
            if message['kind']=='progress':emit('matrix',message['message'],**message.get('extra',{}))
            if message['kind']=='result':
                if use_cache:
                    temporary=path.with_suffix('.tmp');temporary.write_text(json.dumps(message));temporary.replace(path)
                return message['rows'],dict(r5CacheHit=False,r5Policy=policy,r5BuildSeconds=message['buildSeconds'],r5ComputeSeconds=message['computeSeconds'])
