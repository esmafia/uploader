"""Command-line interface.

Two modes:

  1. Flag-based (legacy, unchanged): `python cli.py login -n foo`,
     `python cli.py upload -u foo -v clip.mp4 -t "hi"`, etc.
     All existing scripts and CI invocations keep working.

  2. Interactive REPL (new): `python cli.py` with no subcommand drops into a
     prompt_toolkit-based shell with history, tab-completion, and commands
     matching the flag-based subcommands.

Scheduling (`-sc` / `--schedule`) now enqueues a row in the shared SQLite DB
instead of relying on TikTok's server-side schedule_time (which proved
unreliable). If the scheduler service isn't reachable the user gets a clear
pointer. Immediate uploads (no `-sc`) still call tiktok.upload_video directly
and work without Docker, exactly like today.
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
from datetime import datetime, timedelta, timezone

from tiktok_uploader import Video, tiktok
from tiktok_uploader.basics import eprint
from tiktok_uploader.Config import Config


# ---------- argparse surface (shared by flag-mode and REPL) -----------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="TikTokAutoUpload CLI, scheduled and immediate uploads",
        prog="cli.py",
    )
    sub = parser.add_subparsers(dest="subcommand")

    lp = sub.add_parser("login", help="Log in and save session cookies")
    lp.add_argument("-n", "--name", required=True)

    up = sub.add_parser("upload", help="Upload a video to TikTok")
    up.add_argument("-u", "--users", required=True)
    up.add_argument("-v", "--video")
    up.add_argument("-yt", "--youtube")
    up.add_argument("-t", "--title", required=True)
    up.add_argument(
        "-sc", "--schedule", type=int, default=0,
        help="Schedule upload this many seconds in the future (queued in our scheduler, not TikTok's)",
    )
    up.add_argument("-ct", "--comment", type=int, default=1, choices=[0, 1])
    up.add_argument("-d", "--duet", type=int, default=0, choices=[0, 1])
    up.add_argument("-st", "--stitch", type=int, default=0, choices=[0, 1])
    up.add_argument("-vi", "--visibility", type=int, default=0)
    up.add_argument("-bo", "--brandorganic", type=int, default=0)
    up.add_argument("-bc", "--brandcontent", type=int, default=0)
    up.add_argument("-ai", "--ailabel", type=int, default=0)
    up.add_argument("-p", "--proxy", default="")

    sp = sub.add_parser("show", help="List users and videos")
    sp.add_argument("-u", "--users", action="store_true")
    sp.add_argument("-v", "--videos", action="store_true")

    schp = sub.add_parser("schedule", help="Manage scheduled uploads (queue for our scheduler)")
    schsub = schp.add_subparsers(dest="schedule_action")
    schsub.add_parser("list", help="List all scheduled uploads")
    sch_cancel = schsub.add_parser("cancel", help="Cancel a pending upload by id")
    sch_cancel.add_argument("id", type=int)

    return parser


# ---------- command handlers ------------------------------------------------


def _enqueue_scheduled_upload(args) -> None:
    """Insert a row in scheduled_uploads. Imported lazily so CLI users who
    don't care about scheduling don't pay the fastapi/sqlmodel import cost."""
    try:
        from api.db import init_db
        from api.models import Account, ScheduledUpload
        from sqlmodel import Session, select
    except ImportError:
        eprint(
            "Scheduling requires the API package + sqlmodel installed. "
            "Run `pip install -r requirements.txt` (or use `docker compose up scheduler`)."
        )
        sys.exit(1)

    from api.db import engine

    init_db()
    scheduled_for = datetime.now(timezone.utc) + timedelta(seconds=args.schedule)

    options = {
        "allow_comment": args.comment,
        "allow_duet": args.duet,
        "allow_stitch": args.stitch,
        "visibility_type": args.visibility,
        "brand_organic_type": args.brandorganic,
        "branded_content_type": args.brandcontent,
        "ai_label": args.ailabel,
        "proxy": args.proxy,
    }

    source_type = "youtube" if args.youtube else "local"
    source_ref = args.youtube or os.path.abspath(
        os.path.join(os.getcwd(), Config.get().videos_dir, args.video)
    )

    with Session(engine) as s:
        acct = s.exec(select(Account).where(Account.username == args.users)).first()
        if not acct:
            # Auto-register if the cookie file exists — same "import-from-disk"
            # semantics as the web UI.
            from api.services import cookie_store
            if not cookie_store.exists(args.users):
                eprint(f"No cookie file found for '{args.users}'. Run `cli.py login -n {args.users}` first.")
                sys.exit(1)
            acct = Account(
                username=args.users,
                cookie_path=cookie_store.cookie_file_path(args.users),
                has_valid_session=cookie_store.has_valid_session(args.users),
            )
            s.add(acct)
            s.commit()
            s.refresh(acct)

        row = ScheduledUpload(
            account_id=acct.id,
            source_type=source_type,
            source_ref=source_ref,
            title=args.title,
            options_json=json.dumps(options),
            scheduled_for=scheduled_for,
            status="pending",
        )
        s.add(row)
        s.commit()
        s.refresh(row)
        print(f"[+] Queued upload #{row.id} for {scheduled_for.isoformat()} "
              f"(scheduler service will pick it up).")


def do_login(args) -> None:
    tiktok.login(args.name)


def do_upload(args) -> None:
    if args.video is None and args.youtube is None:
        eprint("No source provided. Use -v or -yt to provide video source.")
        sys.exit(1)
    if args.video and args.youtube:
        eprint("Both -v and -yt flags cannot be used together.")
        sys.exit(1)

    # Scheduled → enqueue, don't call upload_video now.
    if args.schedule and args.schedule > 0:
        _enqueue_scheduled_upload(args)
        return

    if args.youtube:
        video_obj = Video(args.youtube, args.title)
        video_obj.is_valid_file_format()
        args.video = video_obj.source_ref
    else:
        expected = os.path.join(os.getcwd(), Config.get().videos_dir, args.video)
        if not os.path.exists(expected) and not os.path.isabs(args.video):
            print("[-] Video does not exist")
            print("Video Names Available: ")
            video_dir = os.path.join(os.getcwd(), Config.get().videos_dir)
            for name in os.listdir(video_dir):
                print(f"[-] {name}")
            sys.exit(1)

    # schedule_time=0 — we bypass TikTok's server-side scheduling entirely.
    tiktok.upload_video(
        args.users, args.video, args.title, 0,
        args.comment, args.duet, args.stitch, args.visibility,
        args.brandorganic, args.brandcontent, args.ailabel, args.proxy,
    )


def do_show(args) -> None:
    if args.users:
        print("User Names logged in: ")
        cookie_dir = os.path.join(os.getcwd(), Config.get().cookies_dir)
        if os.path.isdir(cookie_dir):
            for name in os.listdir(cookie_dir):
                if name.startswith("tiktok_session-"):
                    print(f'[-] {name.split("tiktok_session-")[1].rsplit(".cookie", 1)[0]}')
    if args.videos:
        print("Video Names: ")
        video_dir = os.path.join(os.getcwd(), Config.get().videos_dir)
        if os.path.isdir(video_dir):
            for name in os.listdir(video_dir):
                print(f"[-] {name}")
    if not args.users and not args.videos:
        print("No flag provided. Use -u (show all cookies) or -v (show all videos).")


def do_schedule(args) -> None:
    try:
        from api.db import init_db, engine
        from api.models import ScheduledUpload
        from sqlmodel import Session, select
    except ImportError:
        eprint("Schedule commands require the API package + sqlmodel installed.")
        sys.exit(1)

    init_db()
    if args.schedule_action == "list":
        with Session(engine) as s:
            rows = s.exec(select(ScheduledUpload).order_by(ScheduledUpload.scheduled_for)).all()
            if not rows:
                print("(no scheduled uploads)")
                return
            for r in rows:
                print(f"#{r.id:<4} {r.status:<10} {r.scheduled_for.isoformat()}  {r.title[:50]}")
    elif args.schedule_action == "cancel":
        with Session(engine) as s:
            row = s.get(ScheduledUpload, args.id)
            if not row:
                eprint(f"No schedule row with id={args.id}")
                sys.exit(1)
            if row.status not in ("pending", "failed"):
                eprint(f"Cannot cancel row in status '{row.status}'")
                sys.exit(1)
            row.status = "cancelled"
            s.add(row)
            s.commit()
            print(f"Cancelled #{row.id}")
    else:
        eprint("Usage: schedule list | schedule cancel <id>")


def dispatch(args) -> None:
    if args.subcommand == "login":
        do_login(args)
    elif args.subcommand == "upload":
        do_upload(args)
    elif args.subcommand == "show":
        do_show(args)
    elif args.subcommand == "schedule":
        do_schedule(args)
    else:
        eprint(f"Unknown subcommand: {args.subcommand}")


# ---------- REPL ------------------------------------------------------------


REPL_INTRO = (
    "TikTokAutoUpload interactive shell.\n"
    "Commands: login, upload, show, schedule, help, exit.\n"
    "Run any command with --help for its flags. Ctrl-D or 'exit' to quit.\n"
)


def _repl() -> None:
    # prompt_toolkit is optional — fall back to plain input() if missing so
    # the CLI still works in minimal environments.
    try:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.completion import WordCompleter
        from prompt_toolkit.history import FileHistory
    except ImportError:
        print("[prompt_toolkit not installed — using basic input()]")
        _repl_basic()
        return

    parser = build_parser()
    completer = WordCompleter(
        ["login", "upload", "show", "schedule", "help", "exit",
         "-n", "-u", "-v", "-yt", "-t", "-sc", "-ct", "-d", "-st", "-vi",
         "-bo", "-bc", "-ai", "-p", "--name", "--users", "--video",
         "--youtube", "--title", "--schedule", "list", "cancel"],
        ignore_case=True,
    )
    history_path = os.path.expanduser("~/.tiktok_uploader_history")
    session = PromptSession(history=FileHistory(history_path), completer=completer)
    print(REPL_INTRO)

    while True:
        try:
            line = session.prompt("tiktok> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not line:
            continue
        if line in ("exit", "quit"):
            return
        if line == "help":
            parser.print_help()
            continue
        try:
            tokens = shlex.split(line)
            args = parser.parse_args(tokens)
        except SystemExit:
            # argparse calls sys.exit on --help or errors; catch so the REPL
            # doesn't die.
            continue
        try:
            dispatch(args)
        except SystemExit:
            continue
        except Exception as e:  # pragma: no cover - interactive feedback
            eprint(f"[-] {type(e).__name__}: {e}")


def _repl_basic() -> None:
    parser = build_parser()
    print(REPL_INTRO)
    while True:
        try:
            line = input("tiktok> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not line or line in ("exit", "quit"):
            return
        try:
            args = parser.parse_args(shlex.split(line))
            dispatch(args)
        except SystemExit:
            continue


def main() -> None:
    # Match historical behavior: load config.txt on startup.
    if os.path.exists("./config.txt"):
        Config.load("./config.txt")

    if len(sys.argv) == 1:
        _repl()
        return

    parser = build_parser()
    args = parser.parse_args()
    if args.subcommand is None:
        parser.print_help()
        sys.exit(1)
    dispatch(args)


if __name__ == "__main__":
    main()
