"""Native receipt evidence must reject successful-looking fallbacks and corruption."""
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'App'))
from native_backend import decode_metadata, make_ini, verified_receipt
from config import DEFAULT_CONTROLS


class NativeEvidence(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(dir=ROOT / 'Test/results')
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / 'metadata.bin'

    def test_real_receipt_is_verified_but_fallback_and_wrong_job_are_not(self):
        receipt = dict(ok=1, armed=1, native_nr=1, contract=1, nr_created=1, nr_evaluated=1,
                       frames=1, verified_nr_frames=1, job_id=1, dlss_evaluations=31,
                       cancelled=0, later_failure=0, failure=0, sr_only=0)
        self.assertTrue(verified_receipt(receipt))
        for key, value in [('sr_only', 1), ('nr_created', 0), ('nr_evaluated', 0),
                           ('verified_nr_frames', 0), ('job_id', 2), ('cancelled', 1), ('failure', 1)]:
            self.assertFalse(verified_receipt(receipt | {key: value}), key)

    def test_receipt_decode_and_truncated_stream(self):
        payload = bytearray(152)
        payload[0] = 1
        payload[3:8] = bytes([1]*5)
        for offset, value in [(16,1),(32,31),(40,1),(56,1)]:
            struct.pack_into('<Q',payload,offset,value)
        self.path.write_bytes(struct.pack('<IHHI',0x3152574E,6,2,len(payload))+payload)
        self.assertTrue(verified_receipt(decode_metadata(self.path)[0]))
        self.path.write_bytes(self.path.read_bytes()[:-1])
        with self.assertRaises(ValueError):
            decode_metadata(self.path)

    def test_ini_carries_changed_controls_and_single_pass(self):
        ini = make_ini(DEFAULT_CONTROLS | dict(style=2, local_tone=0.25, local_structure=1.5, auto_mask=False))
        for value in ('NRStyle=2\n','NRLocalTone=0.25\n','NRLocalStructure=1.5\n',
                      'NRAutoMask=0\n','NRPasses=1\n','NRColorStrength=1\n'):
            self.assertIn(value, ini)


if __name__ == '__main__':
    unittest.main()
