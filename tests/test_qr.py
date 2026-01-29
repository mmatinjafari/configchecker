
import unittest
import sys
import os

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

try:
    from configchecker.monitor import generate_qr_ascii
except ImportError:
    # Need to mock imports if dependencies are missing, but for this env we assume dev deps exist
    pass

class TestQRGeneration(unittest.TestCase):
    def test_qr_width_selection(self):
        """
        Regression Test: Ensure that generate_qr_ascii succeeds when space permits.
        Now it always returns "success" status for valid QR.
        """
        
        # This string usually generates a Version 2 or 3 QR code
        data = "https://example.com" 
        
        # Test case where it fits comfortably
        text, width, status = generate_qr_ascii(data, console_width=60)
        
        self.assertEqual(status, "success", "Should return success when space allows")
        # Visual check logic: half blocks
        self.assertTrue("▀" in text or "▄" in text or "█" in text)

    def test_qr_fallback(self):
        """Test fallback error on very small screens"""
        data = "https://example.com"
        
        # Very narrow screen (e.g. 10 chars) - definitely too small for any QR
        text, width, status = generate_qr_ascii(data, console_width=10)
        
        self.assertEqual(status, "error", "Should return error when space is too tight")
        self.assertIsNone(text, "Text should be None on error")

if __name__ == "__main__":
    unittest.main()
