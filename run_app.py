"""Entry point for the console-free Windows build."""
if __name__ == "__main__":
    import sys
    import traceback
    from pathlib import Path
    try:
        from alienape.app import main
        raise SystemExit(main())
    except Exception:
        destination = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
        (destination / "startup-error.log").write_text(traceback.format_exc(), encoding="utf-8")
        raise
