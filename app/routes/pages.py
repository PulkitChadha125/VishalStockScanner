from flask import Blueprint, render_template

pages_bp = Blueprint("pages", __name__)


@pages_bp.route("/")
def symbol_settings():
    return render_template(
        "symbol_settings.html",
        active_page="symbols",
    )


@pages_bp.route("/order-logs")
def order_logs():
    return render_template(
        "order_logs.html",
        active_page="orders",
    )


@pages_bp.route("/app-logs")
def app_logs():
    return render_template(
        "app_logs.html",
        active_page="app_logs",
    )
