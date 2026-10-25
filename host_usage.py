"""Factory-host CPU and RAM readings from Linux procfs; no extra dependencies."""
import os
from pathlib import Path
import threading
import time

from app import now


class HostUsage:
    def __init__(self, proc_root='/proc'):
        self.proc=Path(proc_root)
        self.lock=threading.Lock()
        self.previous=None
        self.previous_at=None
        self.cached=None

    def sample(self):
        with self.lock:
            clock=time.monotonic()
            if self.cached and clock-self.previous_at<1:return dict(self.cached)
            result={'cpuPercent':None,'cpuCount':os.cpu_count(),'memoryUsedBytes':None,
                    'memoryTotalBytes':None,'memoryPercent':None,'sampledAt':now(),'errors':[]}
            try:
                fields=(self.proc/'stat').read_text().splitlines()[0].split()
                if fields[0]!='cpu' or len(fields)<5:raise ValueError('CPU counters are missing.')
                ticks=[int(v) for v in fields[1:9]]  # Guest time is already included in user/nice.
                total=sum(ticks);idle=ticks[3]+(ticks[4] if len(ticks)>4 else 0)
                if self.previous:
                    elapsed=total-self.previous[0];rest=idle-self.previous[1]
                    if elapsed>0 and 0<=rest<=elapsed:result['cpuPercent']=round(100*(elapsed-rest)/elapsed,1)
                self.previous=(total,idle)
            except (OSError,ValueError,IndexError):
                result['errors'].append('Host CPU usage is unavailable.');self.previous=None
            try:
                memory={}
                for line in (self.proc/'meminfo').read_text().splitlines():
                    key,value=line.split(':',1);memory[key]=int(value.split()[0])*1024
                total=memory['MemTotal']
                available=memory.get('MemAvailable')
                if available is None:
                    if 'MemFree' not in memory:raise KeyError('No available-memory reading.')
                    available=sum(memory.get(k,0) for k in ('MemFree','Buffers','Cached','SReclaimable'))-memory.get('Shmem',0)
                if total<=0 or not 0<=available<=total:raise ValueError('Invalid RAM reading.')
                used=total-available
                result.update(memoryUsedBytes=used,memoryTotalBytes=total,memoryPercent=round(used*100/total,1))
            except (OSError,ValueError,KeyError,IndexError):result['errors'].append('Host RAM usage is unavailable.')
            self.previous_at=clock;self.cached=result
            return dict(result)


host_usage=HostUsage()
