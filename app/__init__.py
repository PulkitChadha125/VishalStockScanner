from flask import Flask

from app.config import Config
from app.database import init_db
from app.strategy_scheduler import start_scheduler


def create_app(config_class=Config):
    app = Flask(
        __name__,
        template_folder="../templates",
        static_folder="../static",
    )
    app.config.from_object(config_class)

    init_db(app.config["DATABASE_PATH"])

    from app.config import Config
    from app import repository

    imported = repository.bootstrap_scanner_from_csv(Config.SCANNER_CSV_PATH)
    if imported:
        print(f"[Scanner] Bootstrapped {imported} symbol(s) from scanner.csv", flush=True)
    repaired = repository.repair_zero_scanner_volume_from_csv(Config.SCANNER_CSV_PATH)
    if repaired:
        print(
            f"[Scanner] Restored volume difference on {repaired} symbol(s) from scanner.csv",
            flush=True,
        )

    from app.routes.symbols import symbols_bp
    from app.routes.scanner import scanner_bp
    from app.routes.pages import pages_bp
    from app.routes.logs import logs_bp
    from app.routes.strategy import strategy_bp

    app.register_blueprint(symbols_bp, url_prefix="/api/symbols")
    app.register_blueprint(scanner_bp, url_prefix="/api/scanner")
    app.register_blueprint(logs_bp, url_prefix="/api/logs")
    app.register_blueprint(strategy_bp, url_prefix="/api/strategy")
    app.register_blueprint(pages_bp)

    # If app restarted, clear stale "running" when engine thread is gone
    from app import strategy_engine

    strategy_engine.get_engine_status()

    # Background scheduler: auto-login at 9:00 and auto-start at configured start_time.
    start_scheduler()

    # WebSocket fetch + console printing run on a dedicated worker thread (not Flask).
    from app import fyers_market_ws

    fyers_market_ws.ensure_worker()

    return app
