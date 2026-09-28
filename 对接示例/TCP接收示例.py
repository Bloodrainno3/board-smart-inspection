"""Python 3 standard-library client: durable receipt, ACK, reconnect and replay.
This example stores detection data only; it sends no machine movement commands.
"""
import argparse,json,os,socket,sqlite3,time
from pathlib import Path

def main():
    p=argparse.ArgumentParser();p.add_argument('--host',default='127.0.0.1');p.add_argument('--port',type=int,default=3110)
    p.add_argument('--client-id',default='downstream-demo');p.add_argument('--database',default='接收结果.sqlite')
    p.add_argument('--token',default=os.environ.get('BOARD_RESULT_TOKEN',''));a=p.parse_args()
    database=Path(a.database);database.parent.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(database)
    db.execute('CREATE TABLE IF NOT EXISTS received(event_id TEXT PRIMARY KEY,device_id TEXT,seq INTEGER,board_id TEXT,type TEXT,payload TEXT)');db.commit()
    while True:
        try:
            with socket.create_connection((a.host,a.port),timeout=10) as sock:
                sock.settimeout(15)
                def send(value):sock.sendall((json.dumps(value,ensure_ascii=False)+'\n').encode('utf-8'))
                # No after_seq: use the server's durable ACK for this client_id.
                send({'type':'subscribe','client_id':a.client_id,'token':a.token})
                with sock.makefile('rb') as stream:
                    for line in stream:
                        if len(line)>8*1024*1024:raise ValueError('unexpected oversized event')
                        event=json.loads(line)
                        if event['type']=='error':raise RuntimeError(event['message'])
                        if event['type'] in ('hello','heartbeat'):continue
                        # A replay has the same event_id. Commit before ACK.
                        with db:
                            inserted=db.execute('INSERT OR IGNORE INTO received VALUES (?,?,?,?,?,?)',
                                (event['event_id'],event['device_id'],event['seq'],event['board_id'],event['type'],json.dumps(event,ensure_ascii=False))).rowcount
                        if inserted:
                            print(event['seq'],event['type'],'板图',event.get('board_number'),
                                  '模拟' if event.get('simulation') else '现场',flush=True)
                            if event['type']=='board.completed':
                                print('整板结果已存入数据库；待复核=',event['requires_review'],event['review_reasons'],flush=True)
                                # Your downstream software can read the stored event.
                                # Apply physical board tracking, calibration and timing before any actuation.
                        send({'type':'ack','seq':event['seq']})
        except KeyboardInterrupt:break
        except Exception as error:
            print('连接中断，3秒后重试：',error,flush=True);time.sleep(3)
    db.close()

if __name__=='__main__':main()
