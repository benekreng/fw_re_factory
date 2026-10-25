from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from host_usage import HostUsage


class HostCase(unittest.TestCase):
    def test_cpu_uses_counter_deltas_without_double_counting_guest_time(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);reader=HostUsage(root)
            (root/'stat').write_text('cpu 100 20 30 400 50 10 5 0 80 10\n')
            (root/'meminfo').write_text('MemTotal: 1000 kB\nMemAvailable: 250 kB\n')
            with patch('host_usage.time.monotonic',return_value=0):first=reader.sample()
            self.assertIsNone(first['cpuPercent'])
            (root/'stat').write_text('cpu 150 20 50 450 70 10 5 0 100 10\n')
            with patch('host_usage.time.monotonic',return_value=15):second=reader.sample()
            self.assertEqual(second['cpuPercent'],50)
            self.assertEqual(second['memoryUsedBytes'],750*1024);self.assertEqual(second['memoryPercent'],75)

    def test_missing_host_data_remains_unknown(self):
        with tempfile.TemporaryDirectory() as temp:
            result=HostUsage(temp).sample()
            self.assertIsNone(result['cpuPercent']);self.assertIsNone(result['memoryPercent'])
            self.assertEqual(len(result['errors']),2)

    def test_ram_available_cache_is_not_treated_as_used(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);reader=HostUsage(root)
            (root/'stat').write_text('cpu 10 0 5 100 0 0 0 0\n')
            (root/'meminfo').write_text('MemTotal: 1000 kB\nMemFree: 100 kB\nBuffers: 50 kB\nCached: 200 kB\nSReclaimable: 50 kB\nShmem: 20 kB\n')
            self.assertEqual(reader.sample()['memoryUsedBytes'],620*1024)


if __name__=='__main__':unittest.main()
