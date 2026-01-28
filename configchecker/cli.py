import asyncio
import argparse
import sys
import os
from tqdm.asyncio import tqdm
from .parser import ConfigParser
from .checker import ProxyChecker
from .monitor import start_monitor
from .utils import get_local_ip

import resource

async def async_main():
    # Increase File Descriptor Limit
    try:
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        resource.setrlimit(resource.RLIMIT_NOFILE, (hard, hard))
        print(f"System File Limit: {soft} -> {hard}")
    except Exception as e:
        print(f"Warning: Could not increase file limit: {e}")

    default_config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "configs.txt")
    parser = argparse.ArgumentParser(description="V2Ray/Proxy Stability Checker")
    parser.add_argument("--file", type=str, default=default_config_path, help="Path to config file")
    parser.add_argument("--mode", type=str, default="quick", choices=["quick", "stable", "realtime"], help="Mode: quick, stable (duration), or realtime (live monitor)")
    parser.add_argument("--duration", type=int, default=30, help="Duration for stability check in seconds")
    parser.add_argument("--concurrency", type=int, default=200, help="Number of concurrent checks")
    parser.add_argument("--bind-ip", type=str, default=None, help="Local IP to bind to (bypass VPN). Default: Auto-detect")
    args = parser.parse_args()

    print(f"Reading configs from {args.file}...")
    try:
        configs = ConfigParser.parse_file(args.file)
    except Exception as e:
        print(f"Error reading file: {e}")
        return

    print(f"Loaded {len(configs)} configurations.")

    # Determine bind_addr once for modes that need it
    bind_addr = None
    if args.bind_ip:
        bind_addr = args.bind_ip
    elif args.mode in ["stable", "realtime"]: # Only auto-detect if needed by mode
        detected_ip = get_local_ip()
        print(f"Auto-detected Local IP: {detected_ip}. Binding to this to bypass VPN loops.")
        bind_addr = detected_ip

    if args.mode == "quick":
        # ... existing quick check code ...
        print(f"Starting QUICK checks with concurrency={args.concurrency}...")
        results = await asyncio.gather(*(ProxyChecker.check_tcp_connect(c) for c in configs)) # Simplified gather for speed match
        # Re-implement using semaphore logic effectively or reuse previous logic
        # For simplicity reusing check_all logic but adapting here slightly for context
        # Ideally we refactor main logic entirely.
        
        # Let's actually use the helper from checker but it needs to be updated or we just run it inline
        # Re-importing proper check_all isn't in the simplified snippet above, so let's stick to inline sem
        sem = asyncio.Semaphore(args.concurrency)
        async def check_wrapper(c):
            async with sem:
                return (c, *await ProxyChecker.check_tcp_connect(c))
        
        tasks = [check_wrapper(c) for c in configs]
        results = await asyncio.gather(*tasks)
        
        results.sort(key=lambda x: (not x[1], x[2]))
        
        print(f"\n--- Quick Scan Results ---")
        print(f"{'STATUS':<8} | {'LATENCY':<10} | {'PROTOCOL':<8} | {'REMARKS'}")
        print("-" * 80)
        up = 0
        for config, is_up, latency, err in results:
            if is_up:
                up += 1
                print(f"{'UP':<8} | {latency:.0f}ms      | {config.protocol:<8} | {config.remarks[:40]}")
        print("-" * 80)
        print(f"Total: {len(configs)}, UP: {up}, DOWN: {len(configs)-up}")

    elif args.mode == "stable":
        print(f"Starting STABILITY checks for {args.duration}s with concurrency={args.concurrency}...")
        # We need to manually construct the tasks to wrap them with tqdm
        
        sem = asyncio.Semaphore(args.concurrency)
        async def check_wrapper(config):
            async with sem:
                return await ProxyChecker.check_stability(config, duration=args.duration, bind_addr=bind_addr)

        tasks = [check_wrapper(c) for c in configs]
        
        # Start all tasks
        future_tasks = asyncio.gather(*tasks)
        
        # Show time-based progress bar since all tasks run in parallel for the same duration
        for _ in tqdm(range(args.duration), desc="Stability Test Progress", unit="s"):
            await asyncio.sleep(1)
            
        results = await future_tasks
        
        # Sort by Packet Loss (asc), then Jitter (asc), then Latency (asc)
        results.sort(key=lambda x: (x.packet_loss, x.jitter, x.avg_latency))

        print(f"\n--- Stability Results ({args.duration}s) ---")
        print(f"{'LOSS %':<8} | {'AVG LAT':<10} | {'JITTER':<10} | {'PROTOCOL':<8} | {'REMARKS'}")
        print("-" * 120)
        
        # Keep track of unique remarks to avoid duplicates in top 5
        seen_remarks = set()
        top_5 = []

        for res in results:
            # Filter mostly dead ones? or show all. Let's show ones with at least some success or low loss
            if res.packet_loss < 100:
                remark_clean = res.config.remarks[:50]
                print(f"{res.packet_loss:<8.1f} | {res.avg_latency:<10.1f} | {res.jitter:<10.1f} | {res.config.protocol:<8} | {remark_clean}")
                
                if remark_clean not in seen_remarks and len(top_5) < 5 and res.packet_loss < 10:
                    seen_remarks.add(remark_clean)
                    top_5.append(res)
        
        print("-" * 120)
        
        if top_5:
            print("\n🏆 TOP 5 STABLE CONFIGS 🏆")
            print("=" * 120)
            for i, res in enumerate(top_5, 1):
                print(f"{i}. {res.config.remarks}")
                print(f"   Shape: Loss={res.packet_loss:.1f}%, Latency={res.avg_latency:.1f}ms, Jitter={res.jitter:.1f}ms")
                print(f"   Link: {res.config.raw_link}")
                print("-" * 120)
        else:
             print("\nNo stable configs found (< 10% packet loss).")

        print("Done.")

    elif args.mode == "realtime":
        print(f"Starting Real-Time Monitor on {bind_addr} with concurrency={args.concurrency}...")
        try:
            await start_monitor(configs, concurrency=args.concurrency, bind_addr=bind_addr)
        except KeyboardInterrupt:
            print("\nExiting monitor...")

def main():
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        print("\nAborted.")

if __name__ == "__main__":
    main()
