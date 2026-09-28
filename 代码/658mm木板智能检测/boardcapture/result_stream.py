"""Durable result journal and acknowledged UTF-8 JSON-lines TCP service."""
from contextlib import closing
from datetime import datetime
import hmac,json,re,select,socket,socketserver,sqlite3,threading,time,uuid
from pathlib import Path

def now():return datetime.now().astimezone().isoformat(timespec='milliseconds')

class Journal:
    def __init__(self,path):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
        with closing(self.connect()) as db,db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT);
                CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT,event_key TEXT UNIQUE,payload TEXT);
                CREATE TABLE IF NOT EXISTS clients(id TEXT PRIMARY KEY,ack INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,request TEXT,status TEXT NOT NULL);''')
            db.execute('INSERT OR IGNORE INTO meta VALUES (?,?)',('device_id',uuid.uuid4().hex))
            self.device_id=db.execute('SELECT value FROM meta WHERE key=?',('device_id',)).fetchone()[0]
    def connect(self):return sqlite3.connect(self.path,timeout=5)
    def publish(self,event_key,payload):
        with closing(self.connect()) as db,db:
            event=dict(payload,schema_version=1,device_id=self.device_id,event_id=uuid.uuid4().hex,emitted_at=now())
            db.execute('INSERT OR IGNORE INTO events(event_key,payload) VALUES (?,?)',
                       (event_key,json.dumps(event,ensure_ascii=False,allow_nan=False)))
            row=db.execute('SELECT seq,payload FROM events WHERE event_key=?',(event_key,)).fetchone()
        return dict(json.loads(row[1]),seq=row[0])
    def latest(self):
        with closing(self.connect()) as db:return db.execute('SELECT COALESCE(MAX(seq),0) FROM events').fetchone()[0]
    def after(self,seq,limit=1):
        with closing(self.connect()) as db:
            rows=db.execute('SELECT seq,payload FROM events WHERE seq>? ORDER BY seq LIMIT ?',(seq,limit)).fetchall()
        return [dict(json.loads(p),seq=s) for s,p in rows]
    def cursor(self,client):
        with closing(self.connect()) as db:
            row=db.execute('SELECT ack FROM clients WHERE id=?',(client,)).fetchone()
        return row[0] if row else 0
    def ack(self,client,seq):
        with closing(self.connect()) as db,db:
            db.execute('INSERT INTO clients VALUES (?,?) ON CONFLICT(id) DO UPDATE SET ack=MAX(ack,excluded.ack)',(client,seq))
    def add_job(self,request):
        with closing(self.connect()) as db,db:
            db.execute('INSERT OR IGNORE INTO jobs VALUES (?,?,?)',(request['board_id'],json.dumps(request,ensure_ascii=False),'queued'))
    def jobs(self,status):
        with closing(self.connect()) as db:
            rows=db.execute('SELECT request FROM jobs WHERE status=? ORDER BY rowid',(status,)).fetchall()
        return [json.loads(row[0]) for row in rows]
    def mark(self,job,status):
        with closing(self.connect()) as db,db:db.execute('UPDATE jobs SET status=? WHERE id=?',(status,job))
    def hold_interrupted(self):
        with closing(self.connect()) as db,db:db.execute("UPDATE jobs SET status='held' WHERE status IN ('queued','running')")
    def resume(self):
        with closing(self.connect()) as db,db:
            for job,raw in db.execute("SELECT id,request FROM jobs WHERE status='held'").fetchall():
                req=json.loads(raw);req['resumed']=True
                db.execute("UPDATE jobs SET status='queued',request=? WHERE id=?",(json.dumps(req,ensure_ascii=False),job))
    def counts(self):
        with closing(self.connect()) as db:return dict(db.execute('SELECT status,COUNT(*) FROM jobs GROUP BY status').fetchall())

class JsonSocket:
    def __init__(self,sock):self.sock=sock;self.buffer=b''
    def send(self,obj):self.sock.sendall((json.dumps(obj,ensure_ascii=False,allow_nan=False)+'\n').encode('utf-8'))
    def receive(self):
        # Timeout is retryable; preserve fragmented frames between recv calls.
        while b'\n' not in self.buffer:
            chunk=self.sock.recv(4096)
            if not chunk:raise ConnectionError('peer closed')
            self.buffer+=chunk
            if len(self.buffer)>65536:raise ValueError('message too large')
        line,self.buffer=self.buffer.split(b'\n',1)
        value=json.loads(line.decode('utf-8'))
        if not isinstance(value,dict):raise ValueError('message must be an object')
        return value

class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        server=self.server;io=JsonSocket(self.request);self.request.settimeout(2)
        try:
            request=io.receive();client=request.get('client_id','')
            if request.get('type')!='subscribe' or not isinstance(client,str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,64}',client):
                raise ValueError('first message: subscribe with ASCII client_id')
            if not hmac.compare_digest(str(request.get('token','')).encode('utf-8'),server.token.encode('utf-8')):raise ValueError('invalid token')
            seq=request.get('after_seq',server.journal.cursor(client))
            if type(seq)!=int or seq<0 or seq>server.journal.latest():raise ValueError('invalid after_seq')
            # A named consumer has one active connection, avoiding cursor races.
            with server.clients_lock:
                if client in server.clients:raise ValueError('client_id already connected')
                server.clients.add(client)
            try:
                io.send({'type':'hello','schema_version':1,'device_id':server.journal.device_id,
                         'after_seq':seq,'latest_seq':server.journal.latest(),'delivery':'at_least_once'})
                last_heartbeat=time.monotonic();self.request.settimeout(.5)
                while not server.stopped.is_set():
                    rows=server.journal.after(seq)
                    if not rows:
                        if select.select([self.request],[],[],0)[0]:
                            # Detect disconnects while idle so client_id can reconnect promptly.
                            io.receive();raise ValueError('unexpected message while no event is pending')
                        if time.monotonic()-last_heartbeat>5:
                            io.send({'type':'heartbeat','latest_seq':server.journal.latest()});last_heartbeat=time.monotonic()
                        server.stopped.wait(.1);continue
                    event=rows[0];io.send(event);deadline=time.monotonic()+30
                    while not server.stopped.is_set():
                        try:ack=io.receive()
                        except socket.timeout:
                            if time.monotonic()>deadline:raise TimeoutError('ACK timeout; reconnect to replay')
                            continue
                        if ack.get('type')!='ack' or type(ack.get('seq'))!=int or ack['seq']!=event['seq']:
                            raise ValueError('ACK must match the delivered event seq')
                        server.journal.ack(client,event['seq']);seq=event['seq'];break
            finally:
                with server.clients_lock:server.clients.discard(client)
        except (OSError,ValueError,ConnectionError,TimeoutError) as error:
            try:io.send({'type':'error','message':str(error)})
            except OSError:pass

class TcpServer(socketserver.ThreadingTCPServer):
    allow_reuse_address=False;daemon_threads=True;block_on_close=False
    def __init__(self,journal,host,port,token=''):
        self.journal=journal;self.token=token;self.stopped=threading.Event()
        self.clients=set();self.clients_lock=threading.Lock()
        self.slots=threading.BoundedSemaphore(8)
        super().__init__((host,port),Handler)
        self.thread=threading.Thread(target=self.serve_forever,kwargs={'poll_interval':.1},daemon=True,name='ResultTCP')
        self.thread.start()
    def verify_request(self,request,client_address):return self.slots.acquire(blocking=False)
    def process_request_thread(self,request,client_address):
        try:super().process_request_thread(request,client_address)
        finally:self.slots.release()
    def stop(self):
        self.stopped.set();self.shutdown();self.server_close();self.thread.join(2)
