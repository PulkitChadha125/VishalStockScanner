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

    from app.routes.symbols import symbols_bp
    from app.routes.pages import pages_bp
    from app.routes.logs import logs_bp
    from app.routes.strategy import strategy_bp

    app.register_blueprint(symbols_bp, url_prefix="/api/symbols")
    app.register_blueprint(logs_bp, url_prefix="/api/logs")
    app.register_blueprint(strategy_bp, url_prefix="/api/strategy")
    app.register_blueprint(pages_bp)

    # Background scheduler: auto-login at 9:00 and auto-start at configured start_time.
    start_scheduler()

    return app
