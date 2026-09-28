"""Nonblocking low/high/low command pulse with ladder feedback verification."""

START='conveyor_start_command'
STOP='conveyor_stop_command'


class CommandPulse:
    def __init__(self,write,key,control,now):
        self.write,self.key=write,key
        self.pulse_s=control['command_pulse_ms']/1000
        self.timeout_s=control['response_timeout_s']
        self.phase='low'
        self.deadline=now+.05  # ensure the ladder sees a low before a fresh edge
        self.write(key,False)

    def tick(self,status,now):
        if self.phase=='low' and now>=self.deadline:
            if self.key==START:
                if not status['emergency_ok']:raise RuntimeError('X000开关许可为0，PLC不会允许M10置位')
                if status['conveyor_stop_command']:raise RuntimeError('M400停止确认仍为1，不能发送M300')
                if any(status[k] for k in ('front_sensor','rear_sensor','capture_active')):
                    raise RuntimeError('启动确认前请移开板材，等待X1/X2和M20均为0')
            self.phase='failed'  # no automatic repeat if the ON acknowledgement is lost
            self.write(self.key,True)
            self.phase='high';self.deadline=now+self.pulse_s
        elif self.phase=='high' and now>=self.deadline:
            self.phase='failed'
            self.write(self.key,False)
            self.phase='feedback';self.deadline=now+self.timeout_s
        if self.phase=='feedback':
            expected=self.key==START
            if status['conveyor_running']==expected:
                self.phase='done';return True
            if now>=self.deadline:
                raise RuntimeError(f'命令脉冲已清零，但M010未变为{int(expected)}；请核对X000许可、M300/M400映射及PLC程序')
        return False

    def cancel(self):
        self.phase='failed'
        self.write(self.key,False)
        self.phase='cancelled'
