import io
import unittest
from unittest import mock

from hasc.console import make_output_safe


class TestMakeOutputSafe(unittest.TestCase):
    def test_vietnamese_prints_to_a_cp1252_stream_after_the_call(self):
        out, err = (io.TextIOWrapper(io.BytesIO(), encoding="cp1252", newline="\n") for _ in range(2))
        with self.assertRaises(UnicodeEncodeError):
            print("kết", file=io.TextIOWrapper(io.BytesIO(), encoding="cp1252", newline="\n"), flush=True)  # the control
        with mock.patch("sys.stdout", out), mock.patch("sys.stderr", err):
            make_output_safe()
            print("không kết nối", flush=True)
            print("lỗi", file=err, flush=True)
        self.assertEqual(out.buffer.getvalue(), b"kh\xf4ng k\\u1ebft n\\u1ed1i\n")
        self.assertEqual(err.buffer.getvalue(), b"l\\u1ed7i\n")

    def test_a_stream_that_cannot_be_reconfigured_is_left_alone(self):
        make_output_safe([io.StringIO(), object()])  # StringIO and a bare object have no reconfigure

    def test_a_closed_stream_is_left_alone(self):
        closed = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", newline="\n")
        closed.close()
        make_output_safe([closed])


if __name__ == "__main__":
    unittest.main()
