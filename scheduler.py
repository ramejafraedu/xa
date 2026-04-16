"""Scheduler — APScheduler with timezone and misfire handling.

Runs the pipeline for each niche at their configured times.
Handles misfires (if the computer was sleeping during a scheduled time).

To keep it running on Windows:
  Option A: Keep terminal open (simplest)
  Option B: Windows Task Scheduler → run `python video_factory.py --schedule` at startup
  Option C: Create a .bat file and add to Startup folder
"""
from __future__ import annotations

import signal
import sys

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from loguru import logger
from rich.console import Console
from rich.table import Table

from config import NICHOS, settings

console = Console()


def _run_niche(nicho_slug: str):
    """Wrapper to run pipeline for a niche (called by scheduler)."""
    logger.info(f"⏰ Scheduled run: {nicho_slug}")
    try:
        if settings.scheduler_use_v15:
            from core.director import DirectorMode
            from core.pipeline_v15 import run_pipeline_v15
            run_pipeline_v15(nicho_slug, mode=DirectorMode.AUTO)
        else:
            from video_factory import run_pipeline
            run_pipeline(nicho_slug)
    except Exception as e:
        if settings.scheduler_use_v15:
            logger.exception(f"Scheduled V15 run failed for {nicho_slug}, trying V14 fallback: {e}")
            try:
                from video_factory import run_pipeline
                run_pipeline(nicho_slug)
                return
            except Exception as v14_error:
                logger.exception(f"Scheduled V14 fallback also failed for {nicho_slug}: {v14_error}")
        else:
            logger.exception(f"Scheduled run failed for {nicho_slug}: {e}")


def start_scheduler():
    """Start the APScheduler with all niche schedules."""
    scheduler = BlockingScheduler(
        timezone="America/Mexico_City",
        job_defaults={
            "coalesce": True,       # If multiple misfires, run only once
            "max_instances": 1,     # Don't overlap runs
            "misfire_grace_time": 3600,  # Allow up to 1h late
        },
    )

    # Print schedule table
    table = Table(title="📅 Scheduled Jobs")
    table.add_column("Nicho", style="bold cyan")
    table.add_column("Hours")
    table.add_column("Timezone")

    all_slugs = list(NICHOS.keys())
    target_slugs = settings.resolve_scheduler_nichos(all_slugs)
    target_set = set(target_slugs)

    for slug, nicho in NICHOS.items():
        if slug not in target_set:
            continue

        hours_str = ",".join(str(h) for h in nicho.horas)
        trigger = CronTrigger(hour=hours_str, timezone="America/Mexico_City")

        scheduler.add_job(
            _run_niche,
            trigger=trigger,
            args=[slug],
            id=f"video_{slug}",
            name=f"Video {slug.capitalize()}",
            replace_existing=True,
        )

        table.add_row(
            slug,
            ", ".join(f"{h:02d}:00" for h in nicho.horas),
            "America/Mexico_City",
        )

    if settings.scheduler_canary_mode:
        console.print(
            f"[yellow]⚠️ Canary mode activo. Nichos programados: {', '.join(target_slugs)}[/yellow]"
        )

    console.print(table)
    console.print("\n[green]✅ Scheduler started. Press Ctrl+C to stop.[/green]\n")

    # Graceful shutdown
    def _shutdown(signum, frame):
        console.print("\n[yellow]⏹️ Shutting down scheduler...[/yellow]")
        scheduler.shutdown(wait=False)
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        console.print("[yellow]Scheduler stopped.[/yellow]")

if __name__ == "__main__":
    start_scheduler()
