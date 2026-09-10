    )
    ENGINE_PID_FILE.write_text(str(_engine_process.pid), encoding="utf-8")
    _write_runtime_manifest(engine_module)
    print(
        f"OTR strategy engine started (PID {_engine_process.pid}, module {engine_module})",
        flush=True,
    )
    print("Engine logs stream directly into Railway deploy logs", flush=True)

    watcher = threading.Thread(
        target=_monitor_engine,
        args=(_engine_process,),
        name="otr-engine-watchdog",
        daemon=True,
    )
    watcher.start()


def _handle_shutdown(_signum, _frame) -> None:
    _stop_engine()
    raise KeyboardInterrupt


if __name__ == "__main__":
    atexit.register(_stop_engine)
    signal.signal(signal.SIGTERM, _handle_shutdown)
    signal.signal(signal.SIGINT, _handle_shutdown)

    _reset_evaluation_history_if_requested()
    _start_engine()
    os.environ["OTR_REQUIRE_ENGINE_HEALTH"] = "1"