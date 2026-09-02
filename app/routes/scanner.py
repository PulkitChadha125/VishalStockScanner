from datetime import datetime

from flask import Blueprint, Response, jsonify, request

from app import fyers_service, market_tz, repository, scanner_service
from app.scanner_csv import parse_scanner_csv, scanner_to_csv
from app.timeframes import SCANNER_VALID_TIMEFRAMES

scanner_bp = Blueprint("scanner", __name__)


def _log_server_activity(description: str, details: dict | None = None):
    repository.create_app_log(
        activity_type="api",
        description=description,
        page_path=request.path,
        details=details,
    )


def _parse_payload():
    data = request.get_json(silent=True) or {}
    symbol_name = str(data.get("symbol_name", "")).strip()
    time_frame = str(data.get("time_frame", "")).strip().lower()

    if not symbol_name or not time_frame:
        return None, jsonify({"error": "Symbol name and timeframe are required."}), 400

    if time_frame not in SCANNER_VALID_TIMEFRAMES:
        return (
            None,
            jsonify({"error": f"Invalid timeframe '{time_frame}'."}),
            400,
        )

    try:
        volume_difference = float(data.get("volume_difference", 0))
    except (TypeError, ValueError):
        return None, jsonify({"error": "Volume difference must be a number."}), 400

    if volume_difference < 0:
        return None, jsonify({"error": "Volume difference cannot be negative."}), 400

    return (symbol_name.upper(), time_frame, volume_difference), None, None


@scanner_bp.route("", methods=["GET"])
def list_scanner():
    return jsonify(repository.list_scanner_symbols())


@scanner_bp.route("/status", methods=["GET"])
def scanner_status():
    session = market_tz.session_status()
    summary = scanner_service.scanner_status_summary()

    return jsonify(
        {
            "connected": fyers_service.is_connected(),
            "market_open": session.get("market_open"),
            "updated_at": market_tz.now_ist().strftime("%H:%M:%S"),
            "summary": summary,
            "symbols": summary.get("symbols") or [],
            "majority": summary.get("majority", 0),
        }
    )


@scanner_bp.route("/export.csv", methods=["GET"])
def export_scanner_csv():
    symbols = repository.list_scanner_symbols()
    csv_text = scanner_to_csv(symbols)
    filename = f"scanner_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return Response(
        csv_text,
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@scanner_bp.route("/import.csv", methods=["POST"])
def import_scanner_csv():
    if repository.get_strategy_settings().get("is_running"):
        return jsonify({"error": "Stop the strategy before loading a CSV file."}), 400

    upload = request.files.get("file")
    if not upload or not upload.filename:
        return jsonify({"error": "No CSV file selected."}), 400

    if not upload.filename.lower().endswith(".csv"):
        return jsonify({"error": "Please upload a .csv file."}), 400

    rows, errors = parse_scanner_csv(upload.read())
    if errors:
        return jsonify({"error": "; ".join(errors), "errors": errors}), 400
    if not rows:
        return jsonify({"error": "No valid scanner rows in CSV."}), 400

    symbols = repository.replace_all_scanner_symbols(rows)
    fyers_service.sync_market_websocket()
    _log_server_activity(
        f"Loaded {len(symbols)} scanner symbol(s) from CSV",
        {"filename": upload.filename, "count": len(symbols)},
    )
    return jsonify(
        {
            "message": f"Loaded {len(symbols)} scanner symbol(s) from CSV.",
            "symbols": symbols,
            "count": len(symbols),
        }
    )


@scanner_bp.route("/<int:scanner_id>", methods=["GET"])
def get_scanner(scanner_id):
    symbol = repository.get_scanner_symbol(scanner_id)
    if not symbol:
        return jsonify({"error": "Scanner symbol not found."}), 404
    return jsonify(symbol)


@scanner_bp.route("", methods=["POST"])
def create_scanner():
    parsed, err_response, status = _parse_payload()
    if err_response is not None:
        return err_response, status

    symbol_name, time_frame, volume_difference = parsed
    symbol = repository.create_scanner_symbol(
        symbol_name, time_frame, volume_difference
    )
    fyers_service.sync_market_websocket()
    _log_server_activity(
        f"Scanner symbol created: {symbol_name}",
        {
            "scanner_id": symbol["id"],
            "time_frame": time_frame,
            "volume_difference": volume_difference,
        },
    )
    return jsonify(symbol), 201


@scanner_bp.route("/<int:scanner_id>", methods=["PUT"])
def update_scanner(scanner_id):
    parsed, err_response, status = _parse_payload()
    if err_response is not None:
        return err_response, status

    symbol_name, time_frame, volume_difference = parsed
    symbol = repository.update_scanner_symbol(
        scanner_id, symbol_name, time_frame, volume_difference
    )
    if not symbol:
        return jsonify({"error": "Scanner symbol not found."}), 404
    fyers_service.sync_market_websocket()
    _log_server_activity(
        f"Scanner symbol updated: {symbol_name}",
        {
            "scanner_id": scanner_id,
            "volume_difference": volume_difference,
        },
    )
    return jsonify(symbol)


@scanner_bp.route("/<int:scanner_id>", methods=["DELETE"])
def delete_scanner(scanner_id):
    symbol = repository.get_scanner_symbol(scanner_id)
    if not repository.delete_scanner_symbol(scanner_id):
        return jsonify({"error": "Scanner symbol not found."}), 404
    fyers_service.sync_market_websocket()
    name = symbol["symbol_name"] if symbol else str(scanner_id)
    _log_server_activity(f"Scanner symbol deleted: {name}", {"scanner_id": scanner_id})
    return jsonify({"message": "Deleted successfully."})
