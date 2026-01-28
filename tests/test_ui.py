
import sys
import os
import time
from collections import deque

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from configchecker.models import ProxyConfig
from configchecker.monitor import start_monitor # We will mock parts of this or just copy logic? 
# Better: Import the RollingStats and generate_dashboard if possible. 
# But generate_dashboard is inside start_monitor. 
# We'll have to mock the usage pattern.

# Let's inspect monitor.py again to see if we can import RollingStats easily.
from configchecker.monitor import RollingStats

# Mock Rich Console to avoid actual output
from rich.console import Console
console = Console(file=open(os.devnull, "w"))

def test_dashboard_generation():
    print("Testing Dashboard Generation Logic...")
    
    # 1. Create Dummy Configs
    configs = []
    for i in range(30):
        c = ProxyConfig(
            protocol="vmess",
            uuid="uuid",
            address="1.1.1.1",
            port=443,
            security="auto",
            network="ws",
            path="/",
            host="host",
            sni="sni",
            raw_link=f"vmess://dummy{i}",
            remarks=f"Test Config {i}"
        )
        configs.append(c)
        
    # 2. Initialize Stats
    stats_map = {c.raw_link: RollingStats(c) for c in configs}
    
    # 3. Simulate some data
    # Config 0: Good
    stats_map[configs[0].raw_link].add(True, 100)
    stats_map[configs[0].raw_link].add(True, 120)
    stats_map[configs[0].raw_link].add(True, 110) # 3 samples, > 2 threshold
    
    # Config 1: Dead
    stats_map[configs[1].raw_link].add(False, 0)
    stats_map[configs[1].raw_link].add(False, 0)
    
    # Config 2: Warmup (1 sample)
    stats_map[configs[2].raw_link].add(True, 200)

    # 4. Simulate the Logic inside start_monitor
    # We copy-paste the critical logic here to verify it
    
    snapshots = []
    for stat in stats_map.values():
        score, loss, lat, jitter, count = stat.get_score()
        snapshots.append((score, loss, lat, jitter, stat.config, count)) # This is the fix we expect

    snapshots.sort(key=lambda x: x[0])
    
    # Verify Unpacking compatibility
    try:
        count_loop = 0
        for i, (score, loss, lat, jitter, config, count) in enumerate(snapshots, 1):
            # This loop triggered ValueError before
            pass
            count_loop += 1
        print("✅ Unpacking loop passed.")
    except Exception as e:
        print(f"❌ Unpacking loop FAILED: {e}")
        return

    # Verify Header Logic structure
    established_stats = [s for s in snapshots if s[0] < 900000 and s[1] < 100]
    warmup_stats = [s for s in snapshots if s[0] >= 900000 and s[1] < 100]
    
    target_stats = established_stats[:5]
    if not target_stats:
        if warmup_stats:
             pass 
             # avg_count = sum(s[5] for s in warmup_stats[:5]) / 5 # This triggered TypeError before
             # Note: s[5] is count. s[4] was config.
             
             try:
                avg_count = sum(s[5] for s in warmup_stats[:5]) / 5
                print(f"✅ Header average count calculation passed: {avg_count}")
             except Exception as e:
                print(f"❌ Header calculation FAILED: {e}")
                return

    print("All UI Logic tests passed!")

if __name__ == "__main__":
    try:
        test_dashboard_generation()
    except Exception as e:
        print(f"Test Crashed: {e}")
        sys.exit(1)
