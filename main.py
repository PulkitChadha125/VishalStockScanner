"""
Entry point for the trading strategy symbol settings application.
Run: python main.py
"""

from app import create_app

app = create_app()

if __name__ == "__main__":
    # threaded=True: Flask handles HTTP on worker threads; WebSocket runs on fyers-ws-worker.
    # use_reloader=False: avoids a second process duplicating background threads.
    app.run(
        debug=True,
        host="0.0.0.0",
        port=5000,
        threaded=True,
        use_reloader=False,
    )
