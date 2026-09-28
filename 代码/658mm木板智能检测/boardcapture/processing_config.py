"""Acquisition/analysis choices and read-only result service settings."""
from pathlib import Path
import ipaddress

def defaults(root):
    return {'auto_grade':False,'material':'hongxiang','grading_threads':2,'min_confidence':.8,
            'results_directory':str(Path(root)/'online_results'),
            'tcp_enabled':False,'tcp_host':'127.0.0.1',
            'tcp_port':6580 if '658' in Path(root).name else 3110,'tcp_token':''}

def validate(value,original_directory):
    for k in ['auto_grade','tcp_enabled']:
        if not isinstance(value[k],bool):raise ValueError(k+'必须为开关值')
    if value['material'] not in ['hongxiang','ouxiang','semu','shanhetao']:raise ValueError('木皮种类无效')
    if type(value['grading_threads']) is not int or not 1<=value['grading_threads']<=4:raise ValueError('分级线程数应为1～4')
    if not 0<=value['min_confidence']<=1:raise ValueError('分级复核阈值应在0～1之间')
    if type(value['tcp_port']) is not int or not 1024<=value['tcp_port']<=65535:raise ValueError('TCP端口必须在1024～65535之间')
    host=ipaddress.ip_address(value['tcp_host'])
    if host.version!=4:raise ValueError('当前接口使用IPv4地址')
    if not host.is_loopback and len(value['tcp_token'])<16:raise ValueError('局域网监听请设置至少16位的接口口令')
    if not str(value['results_directory']).strip():raise ValueError('在线结果目录不能为空')
    original=Path(original_directory).resolve();output=Path(value['results_directory']).resolve()
    if output==original or original in output.parents:raise ValueError('在线结果目录必须在原图目录之外')
