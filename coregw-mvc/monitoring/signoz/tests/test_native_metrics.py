import importlib.util
import os
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('native_metrics', Path(__file__).resolve().parents[1] / 'native_metrics.py')
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)


class NativeMetricsTest(unittest.TestCase):
    def test_identifies_webflux_even_with_both_modules_in_classpath(self):
        self.assertEqual(native.identify_service('/jdk/bin/java -cp /coregw-mvc/classes:/coregw-webflux/classes vn.coregw.GatewayWebFluxApplication'), 'coregw-webflux')
        self.assertEqual(native.identify_service('/jdk/bin/java -cp /coregw-mvc/classes vn.coregw.GatewayApplication'), 'coregw-mvc')
        self.assertIsNone(native.identify_service('/jdk/bin/java OtherApplication'))
        self.assertIsNone(native.identify_service('/jdk/bin/java vn.coregw.GatewayWebFluxApplication', 'coregw-mvc'))

    def test_discovery_replaces_service_and_clears_stopped_target(self):
        import tempfile
        import json
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            native.write_discovery(path, 'coregw-mvc', 8080)
            native.write_discovery(path, 'coregw-webflux', 8080)
            self.assertEqual(json.loads((path / 'app-targets.json').read_text())[0]['labels']['application'], 'coregw-webflux')
            native.write_discovery(path, None, 8080)
            self.assertEqual(json.loads((path / 'app-targets.json').read_text()), [])

    def test_disabled_nmt_is_unavailable(self):
        self.assertIsNone(native.parse_nmt('123:\nNative memory tracking is not enabled'))

    def test_nmt_units_and_category_are_distinct(self):
        sample = '''Total: reserved=999KB, committed=888KB
- Thread (reserved=100KB, committed=30KB)
    (thread #10)
    (stack: reserved=96KB, committed=24KB, peak=24KB)
'''
        self.assertEqual(native.parse_nmt(sample), dict(thread_reserved=102400,
            thread_committed=30720, stack_reserved=98304, stack_committed=24576))

    def test_darwin_abi_matches_process(self):
        info = native.task_info(os.getpid())
        self.assertEqual(native.ctypes.sizeof(native.TaskInfo), 96)
        self.assertGreater(info.resident_size, 0)
        self.assertGreater(info.threadnum, 0)
        self.assertGreaterEqual(info.csw & 0xffffffff, 0)


if __name__ == '__main__':
    unittest.main()
