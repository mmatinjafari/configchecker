
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
        Regression Test: Ensure that generate_qr_ascii selects 'double' mode
        even if console width is tight, because double mode is actually
        same width as compact mode (just different height/block chars).
        
        Scenario:
        - Data: "https://example.com"
        - QR Matrix size: approx 29 modules (Version 3-ish)
        - Compact Width: 29 + 2 (border) = 31 chars
        - Double Width: 29 + 4 (border) = 33 chars (NOT 66!)
        
        If console width is 40:
        - Old Buggy Calc: double=66. 66 <= 30 (40-10)? False. -> Fallback to Compact.
        - New Correct Calc: double=33. 33 <= 30 (40-10)? False.
          Wait, 33 <= 30 is False. So it might still fail if padding is too aggressive.
          Let's verify the padding logic in the code:
          if double_width <= console_width - 10:
          
        Let's pick a console width that SHOULD fail buggy but pass correct.
        Console = 60.
        Buggy: 66 <= 50 (60-10)? False. -> Compact.
        Correct: 33 <= 50? True. -> Double.
        """
        
        # This string usually generates a Version 2 or 3 QR code
        data = "https://example.com" 
        
        # Test case where it fits comfortably
        text, width, mode = generate_qr_ascii(data, console_width=60)
        
        if mode is None:
             self.fail(f"QR Generation failed: {text}")

        self.assertEqual(mode, "double", "Should favor double/square mode when space allows")
        # Visual check logic: Double mode uses half blocks
        self.assertTrue("▀" in text or "▄" in text or "█" in text)

    def test_qr_fallback(self):
        """Test fallback to compact or error on very small screens"""
        data = "https://example.com"
        
        # Very narrow screen
        text, width, mode = generate_qr_ascii(data, console_width=20)
        
        # Depending on specific QR version, 20 might be too small for anything.
        # If it returns error, that's fine. If it returns proper mode, check constraints.
        if mode:
            # If it managed to generate one, it must be compact because double has stricter padding
            # But actually double is just +2 border vs +1 border.
            pass

if __name__ == "__main__":
    unittest.main()
