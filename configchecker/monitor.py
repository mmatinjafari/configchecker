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
        
        if count < 5:
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
    console = Console()
    stats_map = {c.raw_link: RollingStats(c) for c in configs}
    sem = asyncio.Semaphore(concurrency)
    
    running = True

    async def pinger(config: ProxyConfig):
        stat = stats_map[config.raw_link]
        while running:
            # Dead Config Check (10 minutes silence)
            if time.time() - stat.last_success_time > 600:
                # If dead for 10 mins, sleep for 10 mins (600s) to save bandwidth
                # Essentially stopping active checks but keeping it alive for later retry
                await asyncio.sleep(600)
                # After waking up, we do ONE check to see if it's back.
            
            async with sem:
                is_up, lat, _ = await ProxyChecker.check_tcp_connect(config, timeout=2.0, bind_addr=bind_addr)
            stat.add(is_up, lat)
            await asyncio.sleep(1 + (id(config) % 50) / 100.0) 

    pinger_tasks = [asyncio.create_task(pinger(c)) for c in configs]
    
    # Tracking for "Sticky Best Config"
    monitor_start_time = time.time()
    recommended_config: ProxyConfig = None

    def generate_dashboard():
        snapshots = []
        for stat in stats_map.values():
            score, loss, lat, jitter, count = stat.get_score()
            snapshots.append((score, loss, lat, jitter, stat.config))

        snapshots.sort(key=lambda x: x[0])

        # --- Network Health Logic ---
        top_5_stats = [s for s in snapshots[:5] if s[1] < 100 and s[0] < 900000]
        
        network_status = "CALCULATING..."
        style = "white on black"
        
        if top_5_stats:
            avg_jitter = statistics.mean(s[3] for s in top_5_stats)
            avg_loss = statistics.mean(s[1] for s in top_5_stats)
            
            if avg_loss < 2 and avg_jitter < 50:
                network_status = "EXCELLENT NETWORK"
                style = "black on green"
            elif avg_loss < 10 and avg_jitter < 200:
                network_status = "MODERATE NETWORK (Some Jitter)"
                style = "black on yellow"
            else:
                network_status = "POOR NETWORK / ISP ISSUES"
                style = "black on red"
                
            details = f"Top 5 Avg: Loss={avg_loss:.1f}% | Jitter={avg_jitter:.1f}ms"
        else:
            details = "Waiting for data..."

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
        
        # --- Sticky Best Config Footer ---
        nonlocal recommended_config
        elapsed = time.time() - monitor_start_time
        footer_content = None
        footer_style = "blue"
        
        if elapsed < 60:
            footer_content = Text(f"⏳ Analyzing stability... Best config will appear in {60 - int(elapsed)}s", style="dim white")
        else:
            # Determine Rank of current recommended
            current_rank = 999
            if recommended_config:
                # Find current rank of recommended config
                for idx, snap in enumerate(snapshots):
                    if snap[4].raw_link == recommended_config.raw_link:
                        current_rank = idx + 1
                        break
            
            # Switch Logic: If no recommendation OR current recommendation fell below rank 10
            if recommended_config is None or current_rank > 10:
                # Pick new #1 (must be somewhat decent, e.g. loss < 100)
                if snapshots and snapshots[0][1] < 100:
                    recommended_config = snapshots[0][4]
                    current_rank = 1
            
            if recommended_config:
                 rec_text = Text(
                     f"🌟 RECOMMENDED: [ {recommended_config.protocol.upper()} ] {recommended_config.remarks} (Rank #{current_rank})", 
                     style="bold white on green", justify="center"
                 )
                 # Add raw link for copying, allow folding
                 link_text = Text(recommended_config.raw_link, style="dim cyan on black", justify="center")
                 
                 footer_content = Group(rec_text, Text(""), link_text) # Spacer in between
            else:
                 footer_content = Text("No stable configs found yet...", style="red")

        footer = Panel(
            Align.center(footer_content),
            title="🏆 Best Stable Config",
            border_style="green"
        )

        # --- Table Logic ---
        table = Table(expand=True, border_style="dim")
        table.add_column("Rank", justify="right", style="cyan", no_wrap=True, width=4)
        table.add_column("Score", justify="right", style="magenta", width=8)
        table.add_column("Loss %", justify="right", style="red", width=8)
        table.add_column("Latency", justify="right", style="green", width=10)
        table.add_column("Jitter", justify="right", style="yellow", width=10)
        table.add_column("Protocol", style="blue", width=8)
        # Fix Remarks width to prevent UI jitter
        table.add_column("Remarks", style="white", width=50, no_wrap=True, overflow="ellipsis")

        count = 0
        for i, (score, loss, lat, jitter, config) in enumerate(snapshots[:30], 1): 
             if loss < 100 or i < 15:
                proto_style = "blue"
                if config.protocol == "vmess": proto_style = "magenta"
                elif config.protocol == "vless": proto_style = "cyan"
                elif config.protocol == "ss": proto_style = "green"
                elif config.protocol == "trojan": proto_style = "yellow"

                # Mark active config
                remark = config.remarks
                # Highlight recommended if visible
                if recommended_config and config.raw_link == recommended_config.raw_link:
                     remark = f"[bold green]>> {remark} <<[/]"

                table.add_row(
                    f"#{i}",
                    f"{score:.1f}",
                    f"{loss:.1f}%",
                    f"{lat:.0f}ms",
                    f"{jitter:.0f}ms",
                    f"[{proto_style}]{config.protocol}[/]",
                    remark
                )
                count += 1
                if count >= 30: break 
        
        return Group(header_panel, table, footer)

    # Main Loop
    try:
        with Live(generate_dashboard(), refresh_per_second=2, console=console, screen=True) as live:
            while True:
                live.update(generate_dashboard())
                await asyncio.sleep(1) 
            
    except asyncio.CancelledError:
        pass
    except KeyboardInterrupt:
        pass
    finally:
        running = False
        for t in pinger_tasks:
            t.cancel()
        await asyncio.gather(*pinger_tasks, return_exceptions=True)
