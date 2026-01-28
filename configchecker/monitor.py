import asyncio
import time
import statistics
from collections import deque
from typing import List, Dict, Deque
from rich.live import Live
from rich.table import Table
from rich.layout import Layout
from rich.console import Console
from .models import ProxyConfig
from .checker import ProxyChecker

class RollingStats:
    def __init__(self, config: ProxyConfig, maxlen=100):
        self.config = config
        self.history = deque(maxlen=maxlen) # Stores (is_up, latency)
        self.last_jitter = 0.0
        self.smoothed_score = None
        self.last_success_time = time.time() # Start assuming alive to give it a chance
    
    def add(self, is_up, latency):
        self.history.append((is_up, latency))
        if is_up:
            self.last_success_time = time.time()
        
    def get_metrics(self):
        if not self.history:
            return 0.0, 0.0, 0.0, 0 # loss, lat, jitter, count
            
        total = len(self.history)
        failures = sum(1 for up, _ in self.history if not up)
        loss = (failures / total) * 100
        
        valid_latencies = [lat for up, lat in self.history if up]
        
        # Check for DEAD status (10 mins without success)
        # Note: We handle throttling in the pinger loop, but here we can return a flag if needed.
        # For metrics display, we treat it as 100% loss essentially.
        
        if len(valid_latencies) < 2:
            return loss, 0.0, 0.0, total
            
        avg_lat = statistics.mean(valid_latencies)
        
        # RFC-style Jitter: Mean deviation (average of absolute differences between consecutive latencies)
        # This is more robust for network jitter than standard deviation
        diffs = [abs(valid_latencies[i] - valid_latencies[i-1]) for i in range(1, len(valid_latencies))]
        jitter = statistics.mean(diffs) if diffs else 0.0
        
        return loss, avg_lat, jitter, total
        
    def get_score(self):
        loss, lat, jitter, count = self.get_metrics()
        
        # Mark as effectively dead for sorting if long inactivity
        time_since_success = time.time() - self.last_success_time
        if time_since_success > 600:
             loss = 100.0 # Force 100% loss view for sorting
             return 9999999 + time_since_success, loss, lat, jitter, count # Push to bottom
        
        # Raw score
        raw_score = (loss * 10000) + (jitter * 5) + (lat * 0.5)
        
        if count < 2:
            return 999999 + raw_score, loss, lat, jitter, count
            
        # Exponential Smoothing for Rank Stability
        # New Score = 0.1 * Raw + 0.9 * Old
        if self.smoothed_score is None:
             self.smoothed_score = raw_score
        else:
             self.smoothed_score = (0.05 * raw_score) + (0.95 * self.smoothed_score)
             
        return self.smoothed_score, loss, lat, jitter, count

from rich.layout import Layout
from rich.panel import Panel
from rich.text import Text
from rich.align import Align
from rich.console import Group, Console
from rich.style import Style

async def start_monitor(configs: List[ProxyConfig], concurrency: int = 100, bind_addr: str = None):
    from .verifier import XrayVerifier # Lazy import to avoid circular dependency if any
    
    console = Console()
    stats_map = {c.raw_link: RollingStats(c) for c in configs}
    sem = asyncio.Semaphore(concurrency)
    
    running = True

    async def pinger(config: ProxyConfig):
        stat = stats_map[config.raw_link]
        try:
            while running:
                # Dead Config Check (10 minutes silence)
                if time.time() - stat.last_success_time > 600:
                    await asyncio.sleep(600)
                
                try:
                    # Debug: Log start
                    with open("debug_pinger.log", "a") as f: f.write(f"checking {config.remarks}\n")
                    
                    async with sem:
                        is_up, lat, err = await ProxyChecker.check_tcp_connect(config, timeout=2.0, bind_addr=bind_addr)
                    
                    stat.add(is_up, lat)
                    
                    # Debug: Log end
                    failures = sum(1 for up, _ in stat.history if not up)
                    with open("debug_pinger.log", "a") as f: 
                        f.write(f"done {config.remarks} up={is_up} err={err} hist={len(stat.history)} fails={failures}\n")
                    
                except Exception as e:
                    # Catch pinger crash
                    with open("debug_pinger.log", "a") as f: 
                        f.write(f"CRASH {config.remarks}: {e}\n")
                    # Sleep to avoid busy loop if crash is persistent
                    await asyncio.sleep(1)

                await asyncio.sleep(0.2 + (id(config) % 20) / 100.0) # Faster cycle 
        except Exception as outer_e:
             with open("debug_pinger.log", "a") as f: 
                 f.write(f"FATAL PINGER {config.remarks}: {outer_e}\n") 

    pinger_tasks = [asyncio.create_task(pinger(c)) for c in configs]
    
    monitor_start_time = time.time()
    recommended_config: ProxyConfig = None
    verification_status = "" 

    def generate_dashboard(rec_config, verify_status):
        snapshots = []
        for stat in stats_map.values():
            score, loss, lat, jitter, count = stat.get_score()
            snapshots.append((score, loss, lat, jitter, stat.config, count))

        snapshots.sort(key=lambda x: x[0])

        # --- Network Health Logic ---
        # Warming up configs have score > 900000
        # Valid established configs have score < 900000
        
        established_stats = [s for s in snapshots if s[0] < 900000 and s[1] < 100]
        warmup_stats = [s for s in snapshots if s[0] >= 900000 and s[1] < 100 and s[5] > 0]
        
        network_status = "CRITICAL"
        style = "bold red"
        details = "Most configs are unreachable"
        
        target_stats = established_stats[:5]
        is_warmup = False
        
        if not target_stats:
            if warmup_stats:
                target_stats = warmup_stats[:5]
                is_warmup = True
            else:
                 # Truly critical, nothing is up
                 pass
        
        if target_stats:
             avg_loss = sum(s[1] for s in target_stats) / len(target_stats)
             avg_jitter = sum(s[3] for s in target_stats) / len(target_stats)
             
             if is_warmup:
                 network_status = "CALCULATING"
                 style = "bold blue"
                 # Progress Bar Logic
                 # Avg samples of top 5
                 avg_count = sum(s[5] for s in target_stats) / len(target_stats)
                 
                 # Debug: Log anomaly
                 if avg_count == 0:
                     with open("debug_pinger.log", "a") as f:
                        f.write(f"ANOMALY: avg_count=0. target_stats size={len(target_stats)}\n")
                        for idx, s in enumerate(target_stats):
                            f.write(f"  Item {idx}: count={s[5]} score={s[0]} loss={s[1]} tuple_len={len(s)}\n")
                 
                 progress = min(avg_count / 2.0, 1.0)
                 bar_len = 20
                 filled = int(progress * bar_len)
                 bar = "█" * filled + "░" * (bar_len - filled)
                 
                 # TEMPORARY DEBUG ON SCREEN
                 counts_str = ",".join(str(s[5]) for s in target_stats)
                 details = f"{bar} {int(progress*100)}% (Samps: {avg_count:.2f}/2.0) [DBG: {counts_str}]"
             else:
                 if avg_loss < 10:
                    if avg_jitter < 50:
                        network_status = "EXCELLENT"
                        style = "bold green"
                        details = "Network is stable and low jitter"
                    elif avg_jitter < 200:
                        network_status = "GOOD"
                        style = "bold green"
                        details = "Usable, slight jitter detected"
                    else:
                        network_status = "UNSTABLE"
                        style = "bold yellow"
                        details = "High jitter detected (Packet variance)"
                 elif avg_loss < 50:
                    network_status = "DEGRADED"
                    style = "bold yellow"
                    details = "Significant packet loss detected"

        header_panel = Panel(
            Align.center(
                Group(
                    Text(f" {network_status} ", style=Style(bgcolor=style.split()[-1], color="black", bold=True)),
                    Text(details, style="dim white")
                )
            ),
            title=f"📡 Network Health Monitor [{bind_addr or 'System Route'}]",
            border_style="blue"
        )
        
        # --- Footer Logic (Display Only) ---
        elapsed = time.time() - monitor_start_time
        footer_content = None
        footer_style = "blue"
        
        if elapsed < 60:
            footer_content = Text(f"⏳ Analyzing stability... Best config will appear in {60 - int(elapsed)}s", style="dim white")
        elif verify_status:
             footer_content = Text(f"{verify_status}", style="bold yellow")
        elif rec_config:
            footer_content = Group(
                Text(f"🏆 Best Stable Config: {rec_config.remarks}", style="bold cyan"),
                Text(f"Protocol: {rec_config.protocol} | Addr: {rec_config.address}", style="cyan"),
                Text(f"Raw Link (Copy):", style="dim white"),
                Text(f"{rec_config.raw_link}", style="bold white on blue")
            )
            footer_style = "green"
        else:
            footer_content = Text("No verified stable configs found yet...", style="red")

        footer_panel = Panel(
            Align.center(footer_content),
            title="🏆 Sticky Best Config (Verified)",
            border_style=footer_style
        )

        table = Table(expand=True, border_style="dim white")
        table.add_column("Rank", justify="right", width=8)
        table.add_column("Score", justify="right", width=15)
        table.add_column("Loss %", justify="right", width=15)
        table.add_column("Latency", justify="right", width=18)
        table.add_column("Jitter", justify="right", width=18)
        table.add_column("Protocol", justify="left", width=15)
        table.add_column("Remarks", justify="left", ratio=1, no_wrap=True, overflow="ellipsis") 

        count = 0
        for i, (score, loss, lat, jitter, config, count) in enumerate(snapshots, 1): 
             # Visualization Logic: Show top 25, but stop if score gets too bad unless it's top 10
             if i > 25: break
             
             row_style = ""
             if config == rec_config:
                row_style = "bold green"
             elif loss >= 100:
                row_style = "dim red"
            
             loss_str = f"{loss:.1f}%"
             if loss == 100 and score > 9000000:
                 loss_str = "DEAD" 
            
             table.add_row(
                f"#{i}", 
                f"{score:.1f}", 
                loss_str, 
                f"{lat:.0f}ms", 
                f"{jitter:.0f}ms", 
                config.protocol, 
                config.remarks,
                style=row_style
             )
             count += 1
        
        return Group(header_panel, table, footer_panel)

    try:
        # Initial Render
        with Live(generate_dashboard(None, ""), refresh_per_second=4, screen=True, auto_refresh=False) as live:
            while running:
                elapsed = time.time() - monitor_start_time
                
                # --- Verification Logic (Runs every loop but throttled by flow) ---
                if elapsed >= 60:
                    current_snapshots = []
                    for stat in stats_map.values():
                        current_snapshots.append((*stat.get_score(), stat.config))
                    current_snapshots.sort(key=lambda x: x[0])
                    
                    # Check if we need to switch
                    keep_current = False
                    if recommended_config:
                        rank = next((i for i, s in enumerate(current_snapshots) if s[5] == recommended_config), -1)
                        if rank != -1 and rank <= 10:
                            keep_current = True
                    
                    if not keep_current:
                        recommended_config = None 
                        verification_status = ""
                        
                        # Find Top 3 Alive candidates
                        candidates = [s[5] for s in current_snapshots if s[1] < 100][:3]
                        
                        found_new = False
                        for cand in candidates:
                            # Update UI to show we are verifying
                            verification_status = f"🔍 Verifying: {cand.protocol.upper()} {cand.remarks[:20]}..."
                            live.update(generate_dashboard(recommended_config, verification_status))
                            
                            # Verify
                            is_valid = await XrayVerifier.verify_config(cand)
                            if is_valid:
                                recommended_config = cand
                                verification_status = ""
                                found_new = True
                                break
                            else:
                                # Failed verification, try next
                                pass
                        
                        if not found_new and not recommended_config:
                             verification_status = "Top configs failed verification."

                # Update UI
                live.update(generate_dashboard(recommended_config, verification_status))
                await asyncio.sleep(0.5) # Refresh rate limit logic
                
    except asyncio.CancelledError:
        pass
    except KeyboardInterrupt:
        pass
    finally:
        running = False
        for t in pinger_tasks:
            t.cancel()
        await asyncio.gather(*pinger_tasks, return_exceptions=True)
