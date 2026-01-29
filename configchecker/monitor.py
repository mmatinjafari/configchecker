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
import io

def generate_qr_ascii(data: str, console_width: int = None) -> tuple:
    """
    Generate QR code for terminal with correct aspect ratio.
    Uses double-width characters (██ and  ) to make QR square.
    Returns: (qr_text, qr_width) or (error_message, 0) on failure
    """
    try:
        import segno
        qr = segno.make(data, error='L', boost_error=False)
        
        # Get the matrix (list of lists of booleans)
        matrix = qr.matrix
        
        # Add 2-module quiet zone (border)
        border = 2
        height = len(matrix)
        width = len(matrix[0]) if matrix else 0
        
        lines = []
        
        # Top border (2 rows of spaces)
        border_line = "  " * (width + border * 2)
        for _ in range(border):
            lines.append(border_line)
        
        # QR content with side borders
        for row in matrix:
            line = "  " * border  # Left border
            for cell in row:
                # Two characters per module for correct aspect ratio
                line += "██" if cell else "  "
            line += "  " * border  # Right border
            lines.append(line)
        
        # Bottom border
        for _ in range(border):
            lines.append(border_line)
        
        qr_text = "\n".join(lines)
        qr_width = (width + border * 2) * 2  # Each module = 2 chars
        
        # Check if terminal is wide enough
        if console_width and qr_width > console_width - 10:
            return ("Terminal too narrow for QR", 0)
        
        return (qr_text, qr_width)
    except Exception as e:
        return (f"(QR error: {e})", 0)

def generate_fullscreen_qr(data: str, console) -> None:
    """Display QR code fullscreen with dark background for better scanning."""
    try:
        import segno
        qr = segno.make(data, error='L', boost_error=False)
        
        # Get terminal size
        term_width = console.width
        term_height = console.height
        
        # Generate QR
        output = io.StringIO()
        qr.terminal(out=output, compact=True, border=2)
        qr_text = output.getvalue()
        
        qr_lines = qr_text.strip().split('\n')
        qr_height = len(qr_lines)
        qr_width = len(qr_lines[0]) if qr_lines else 0
        
        # Calculate padding for centering
        top_padding = max(0, (term_height - qr_height - 6) // 2)
        left_padding = max(0, (term_width - qr_width) // 2)
        
        # Clear screen and show fullscreen QR
        console.clear()
        
        # Top padding
        for _ in range(top_padding):
            console.print("")
        
        # Title
        console.print(Align.center(Text("📱 SCAN THIS QR CODE", style="bold yellow on black")))
        console.print(Align.center(Text("Press any key to return...", style="dim white on black")))
        console.print("")
        
        # QR code centered with white on black for contrast
        for line in qr_lines:
            padded_line = " " * left_padding + line
            console.print(Text(padded_line, style="white on black"))
        
        console.print("")
        console.print(Align.center(Text("━" * 40, style="dim")))
        
    except Exception as e:
        console.print(f"[red]QR Error: {e}[/red]")


async def start_monitor(configs: List[ProxyConfig], concurrency: int = 50, bind_addr: str = None):
    from .verifier import XrayVerifier # Lazy import to avoid circular dependency if any
    from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
    
    console = Console()
    
    # ═══════════════════════════════════════════════════════════════════════
    # PHASE 1: Real Delay Verification (Tests all configs with actual proxy)
    # ═══════════════════════════════════════════════════════════════════════
    console.print("\n[bold cyan]═══ PHASE 1: Testing Real Delay for All Configs ═══[/bold cyan]")
    console.print("[dim]This verifies configs actually work through proxy...[/dim]\n")
    
    # Check if Xray needs to be downloaded (show message to user)
    import os
    xray_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bin", "xray")
    if not os.path.exists(xray_path):
        console.print("[yellow]⬇️  Downloading Xray core (first time only)...[/yellow]")
    
    verified_configs = []
    real_delays = {}  # config.raw_link -> latency
    
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TextColumn("[cyan]{task.fields[status]}[/cyan]"),
        console=console
    ) as progress:
        task = progress.add_task("Verifying configs...", total=len(configs), status="Starting...")
        
        def update_progress(completed, total, name, valid, latency):
            status = f"✓ {latency:.0f}ms" if valid else "✗ Failed"
            progress.update(task, completed=completed, status=f"{name[:20]}... {status}")
        
        results = await XrayVerifier.verify_all_configs(
            configs, 
            concurrency=10,  # Higher concurrency for faster testing
            progress_callback=update_progress
        )
        
        # Handle Xray unavailable or results
        if results is None:
            # Xray not available - tell user how to install
            console.print("\n[bold yellow]⚠ Xray not available - for installing and real delay, run:[/bold yellow]")
            console.print("[bold white]   curl -sSL https://raw.githubusercontent.com/mmatinjafari/configchecker/master/install-xray.sh | bash[/bold white]\n")
            console.print("[dim]Continuing with TCP-only monitoring...[/dim]")
            verified_configs = configs
            real_delays = {}
        elif results:
            for config, latency in results:
                verified_configs.append(config)
                real_delays[config.raw_link] = latency
            console.print(f"\n[bold green]✓ Phase 1 Complete: {len(verified_configs)}/{len(configs)} configs verified[/bold green]")
        else:
            console.print(f"\n[bold green]✓ Phase 1 Complete: 0/{len(configs)} configs verified[/bold green]")
    
    if not verified_configs:
        console.print("[bold red]No working configs found! Exiting.[/bold red]")
        return
    
    # Show top 5 by real delay (only if we have real delays)
    if real_delays:
        console.print("\n[bold]Top 5 by Real Delay:[/bold]")
        for i, cfg in enumerate(verified_configs[:5], 1):
            lat = real_delays.get(cfg.raw_link, 0)
            console.print(f"  {i}. {cfg.remarks[:40]} - [cyan]{lat:.0f}ms[/cyan]")
    
    console.print("\n[bold cyan]═══ PHASE 2: Stability Monitoring ═══[/bold cyan]\n")
    await asyncio.sleep(2)  # Brief pause before Phase 2
    
    # ═══════════════════════════════════════════════════════════════════════
    # PHASE 2: Stability Monitoring (Only for verified configs)
    # ═══════════════════════════════════════════════════════════════════════
    
    stats_map = {c.raw_link: RollingStats(c) for c in verified_configs}
    # Pre-populate with real delay data
    for cfg in verified_configs:
        stats_map[cfg.raw_link].add(True, real_delays.get(cfg.raw_link, 100))
    
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
                await asyncio.sleep(1)

                # Adaptive interval: fast warmup, then slow down to prevent port exhaustion
                sample_count = len(stat.history)
                if sample_count < 5:
                    interval = 0.5  # Fast warmup to show data quickly
                else:
                    interval = 2.0 + (id(config) % 100) / 100.0  # Slow steady-state
                await asyncio.sleep(interval) 
        except Exception as outer_e:
             with open("debug_pinger.log", "a") as f: 
                 f.write(f"FATAL PINGER {config.remarks}: {outer_e}\n") 

    pinger_tasks = [asyncio.create_task(pinger(c)) for c in verified_configs]
    
    monitor_start_time = time.time()
    recommended_config: ProxyConfig = None
    verification_status = ""
    failed_verifications = set()  # Track configs that failed verification (by raw_link)
    last_verification_time = 0  # Cooldown between verification attempts 

    def generate_dashboard(rec_config, verify_status):
        snapshots = []
        
        # DEBUG: Log what we're seeing in stats
        debug_samples = []
        for stat in stats_map.values():
            score, loss, lat, jitter, count = stat.get_score()
            snapshots.append((score, loss, lat, jitter, stat.config, count))
            if len(debug_samples) < 5:  # Log first 5 for debug
                debug_samples.append(f"{stat.config.remarks[:20]}: hist={len(stat.history)} score={score:.1f} count={count}")
        
        # Write debug
        with open("debug_dashboard.log", "a") as f:
            f.write(f"DASHBOARD: total_stats={len(snapshots)} samples={debug_samples}\n")

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
            # Config info only (QR will be in separate panel)
            footer_content = Group(
                Text(f"🏆 {rec_config.remarks[:60]}", style="bold cyan"),
                Text(f"{rec_config.protocol.upper()} → {rec_config.address}:{rec_config.port}", style="cyan"),
                Text(""),
                Text(rec_config.raw_link, style="dim white", overflow="fold"),
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
        # RESPONSIVE: Calculate rows based on terminal height
        term_height = console.height if console.height else 40
        
        # Reserve space for: header(3) + footer(8) + QR(~20 if shown)
        # Small screens (<35 rows): hide QR, show more table
        # Large screens (>=35 rows): show QR, fewer table rows
        show_qr = term_height >= 35 and rec_config
        
        if show_qr:
            # Leave room for QR (~20 lines)
            max_rows = max(5, term_height - 33)
        else:
            # No QR, use more space for table
            max_rows = max(5, term_height - 15)
        
        max_rows = min(max_rows, 25)  # Cap at 25 rows
        
        for i, (score, loss, lat, jitter, config, count) in enumerate(snapshots, 1): 
             if i > max_rows: break
             
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
        
        # Create QR panel only if screen is big enough
        qr_panel = None
        if show_qr:
            # Calculate available width for QR (approx 1/3 of screen)
            available_width = console.width // 3 if console.width else 40
            qr_text, qr_width = generate_qr_ascii(rec_config.raw_link, available_width)
            
            if qr_width > 0:
                # QR fits - create panel with no_wrap to prevent line breaking
                qr_panel = Panel(
                    Align.center(Text(qr_text, style="white on black", no_wrap=True, overflow="ignore")),
                    title="📱 Scan",
                    border_style="yellow",
                    padding=(0, 1)
                )
            else:
                # QR too wide - show compact message
                qr_panel = Panel(
                    Text("QR: Widen terminal\nor copy link", style="dim yellow", justify="center"),
                    title="📱",
                    border_style="dim yellow",
                    padding=(0, 0)
                )
        
        # Layout: header, table, then footer+QR side by side
        if qr_panel:
            from rich.table import Table as RichTable
            # Create side-by-side layout: footer on left, QR on right
            bottom_layout = RichTable.grid(expand=True)
            bottom_layout.add_column("footer", ratio=2)
            bottom_layout.add_column("qr", ratio=1)
            bottom_layout.add_row(footer_panel, Align.center(qr_panel, vertical="middle"))
            return Group(header_panel, table, bottom_layout)
        else:
            return Group(header_panel, table, footer_panel)

    try:
        # Initial Render
        with Live(generate_dashboard(None, ""), refresh_per_second=4, screen=True, auto_refresh=True) as live:
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
                    
                    # Only run verification if we don't have a working config AND cooldown has passed
                    current_time = time.time()
                    if not keep_current and (current_time - last_verification_time) > 10:
                        last_verification_time = current_time
                        recommended_config = None 
                        verification_status = ""
                        
                        # Find Top 10 Alive candidates that haven't failed verification
                        candidates = [s[5] for s in current_snapshots 
                                     if s[1] < 100 and s[5].raw_link not in failed_verifications][:10]
                        
                        if not candidates:
                            verification_status = "All top configs failed verification. Retrying in 5 min..."
                            # Clear failed set after 5 minutes to retry
                            if len(failed_verifications) > 0:
                                failed_verifications.clear()
                        else:
                            found_new = False
                            for cand in candidates:
                                # Update UI to show we are verifying
                                verification_status = f"🔍 Verifying: {cand.protocol.upper()} {cand.remarks[:20]}..."
                                live.update(generate_dashboard(recommended_config, verification_status))
                                
                                # Verify
                                is_valid, _ = await XrayVerifier.verify_config(cand)
                                if is_valid:
                                    recommended_config = cand
                                    verification_status = ""
                                    found_new = True
                                    break
                                else:
                                    # Mark as failed so we don't retry immediately
                                    failed_verifications.add(cand.raw_link)
                            
                            if not found_new and not recommended_config:
                                 verification_status = f"Top {len(candidates)} configs failed. Trying others..."

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
