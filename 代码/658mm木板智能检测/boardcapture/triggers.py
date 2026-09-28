"""Direct photoelectric sequence, independent of PLC M020 ladder logic."""


class PhotoelectricTrigger:
    def __init__(self, debounce_ms=20):
        self.debounce = debounce_ms / 1000
        self.reset()

    def reset(self):
        self.seen_low = False
        self.rear_seen = False
        self.deadline = None
        self.last_front = False
        self.stable = None
        self.candidate = None
        self.changed_at = 0.0

    def update(self, status, now, capturing, post_ms):
        # Emergency is never debounced or inferred from another PLC bit.
        if not status['emergency_ok']:
            self.reset()
            return 'emergency'
        pair = (bool(status['front_sensor']), bool(status['rear_sensor']))
        if pair != self.candidate:
            self.candidate = pair
            self.changed_at = now
        if now - self.changed_at >= self.debounce:
            self.stable = pair
        if self.stable is None:
            return None
        front, rear = self.stable
        action = None
        if capturing:
            if rear:
                self.rear_seen = True
                self.deadline = None
            # Only a rear ON followed by rear OFF can finish a board. A front
            # OFF edge alone must never cut off a long board or a short board
            # travelling in the gap between the two photoelectric sensors.
            elif self.rear_seen:
                if self.deadline is None:
                    self.deadline = now + post_ms / 1000
                # Raw rear ON also cancels completion during debounce.
                if pair[1]:
                    self.deadline = None
                elif now >= self.deadline:
                    action = 'finish'
                    self.deadline = None
                    self.rear_seen = False
                    self.seen_low = False
        elif not front and not rear:
            self.seen_low = True
        elif self.seen_low and front and not self.last_front:
            self.rear_seen = rear
            self.deadline = None
            action = 'start'
        self.last_front = front
        return action

    def hint(self, capturing):
        if self.stable is None or (not capturing and not self.seen_low):
            return '等待X1、X2均无遮挡，再接受新板（防止从半块板开始）'
        if not capturing:
            return '等待前光电X1：无遮挡 → 有板'
        if not self.rear_seen:
            return '采集中：等待板材到达后光电X2；X1恢复不会提前结束'
        if self.deadline is not None:
            return '板尾已离开X2，正在尾部续采'
        return '采集中：X2已检测到板材，等待板尾离开X2'
