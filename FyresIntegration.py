from __future__ import annotations

from fyers_apiv3 import fyersModel
from fyers_apiv3.FyersWebsocket import data_ws
import webbrowser
from datetime import datetime, timedelta, date
from time import sleep
import os
import pyotp
import requests
import json
import math
import pytz
from urllib.parse import parse_qs, urlparse
import warnings
import pandas as pd
access_token=None
fyers=None
shared_data = {}
shared_data_2 = {}
option_fyers_socket = None
_ws_ssl_patch_installed = False
# Lock to ensure thread-safe access to the shared data


def ensure_websocket_ssl_for_fyers() -> None:
    """
    Fyers WebSocket uses websocket-client without passing CA certs; on some Windows/VPS images
    this raises SSL: CERTIFICATE_VERIFY_FAILED. We inject certifi's bundle into run_forever.

    Environment:
      FYERS_WS_SSL_VERIFY=0  — disable TLS verification (insecure; last resort).
    """
    global _ws_ssl_patch_installed
    if _ws_ssl_patch_installed:
        return
    import ssl
    import websocket

    _orig = websocket.WebSocketApp.run_forever

    def _run_forever(self, *args, **kwargs):
        sslopt = dict(kwargs.pop("sslopt", None) or {})
        verify = (os.environ.get("FYERS_WS_SSL_VERIFY") or "1").strip().lower()
        if verify in ("0", "false", "no", "off"):
            sslopt["cert_reqs"] = ssl.CERT_NONE
            if not getattr(ensure_websocket_ssl_for_fyers, "_warned_insecure", False):
                print(
                    "[Fyers WS] FYERS_WS_SSL_VERIFY=0: TLS verification disabled (insecure).",
                    flush=True,
                )
                setattr(ensure_websocket_ssl_for_fyers, "_warned_insecure", True)
        else:
            try:
                import certifi

                sslopt.setdefault("ca_certs", certifi.where())
            except ImportError:
                pass
        kwargs["sslopt"] = sslopt
        return _orig(self, *args, **kwargs)

    websocket.WebSocketApp.run_forever = _run_forever
    _ws_ssl_patch_installed = True


def _redact_for_log(obj):
    """Best-effort redaction for sensitive auth payloads before printing."""
    try:
        if isinstance(obj, dict):
            out = {}
            for k, v in obj.items():
                lk = str(k).lower().replace("-", "_")
                if lk in (
                    "access_token",
                    "refresh_token",
                    "request_key",
                    "auth_code",
                    "code",
                    "pin",
                    "otp",
                    "identifier",
                    "cookie",
                    "details",
                    "auth",
                ):
                    out[k] = "***"
                elif isinstance(v, (dict, list)):
                    out[k] = _redact_for_log(v)
                else:
                    out[k] = v
            return out
        if isinstance(obj, list):
            return [_redact_for_log(x) for x in obj[:20]]
    except Exception:
        pass
    return obj
def apiactivation(client_id, redirect_uri, response_type, state, secret_key, grant_type):
    from fyers_apiv3 import fyersModel
    import webbrowser

    appSession = fyersModel.SessionModel(
        client_id=client_id,
        redirect_uri=redirect_uri,
        response_type=response_type,
        state=state,
        secret_key=secret_key,
        grant_type=grant_type
    )

    try:
        generateTokenUrl = appSession.generate_authcode()
        print("generateTokenUrl:", generateTokenUrl)

        # If it's a full URL, open browser (manual login)
        if generateTokenUrl.startswith("https://"):
            print("Opening browser for manual login...")
            webbrowser.open(generateTokenUrl, new=1)
            return generateTokenUrl

        # Else, assume it's an auth code directly
        elif isinstance(generateTokenUrl, dict) and "data" in generateTokenUrl and "auth" in generateTokenUrl["data"]:
            print("Auth code obtained directly:", generateTokenUrl["data"]["auth"])
            return generateTokenUrl["data"]["auth"]

        else:
            print("Unexpected response format:", generateTokenUrl)
            return None

    except Exception as e:
        print("Error during auth code generation:", e)
        return None


def automated_login(client_id, secret_key, FY_ID, TOTP_KEY, PIN, redirect_uri):
    """Fyers app login (OTP + PIN) then exchange for API access_token via SessionModel.generate_token."""
    pd.set_option("display.max_columns", None)
    warnings.filterwarnings("ignore")

    import base64

    def getEncodedString(string):
        string = str(string)
        base64_bytes = base64.b64encode(string.encode("ascii"))
        return base64_bytes.decode("ascii")

    def _split_client_id(value: str) -> tuple[str, str]:
        raw = str(value or "").strip()
        if "-" in raw:
            app_id, app_type = raw.rsplit("-", 1)
            return app_id.strip(), app_type.strip()
        return raw, "100"

    def _require_ok(payload: dict, step_name: str, required_keys: list[str] | None = None) -> None:
        if not isinstance(payload, dict):
            raise RuntimeError(f"{step_name} failed: invalid response type")
        if str(payload.get("s", "")).lower() != "ok":
            msg = payload.get("message") or payload.get("error") or payload
            code = payload.get("code")
            raise RuntimeError(f"{step_name} failed (code={code}): {msg}")
        if required_keys:
            for key in required_keys:
                if key not in payload:
                    raise RuntimeError(f"{step_name} failed: missing key '{key}' in response")

    def _auth_code_from_token_response(token_resp: dict[str, object]) -> str:
        """Older API returns redirect Url with auth_code; newer returns data.auth JWT."""
        url = token_resp.get("Url") or token_resp.get("url")
        if url:
            parsed = urlparse(str(url))
            vals = parse_qs(parsed.query).get("auth_code") or []
            if vals:
                return vals[0]
        data = token_resp.get("data")
        if isinstance(data, dict):
            for key in ("auth", "auth_code"):
                v = data.get(key)
                if isinstance(v, str) and v.strip():
                    return v.strip()
        v = token_resp.get("auth_code")
        if isinstance(v, str) and v.strip():
            return v.strip()
        raise RuntimeError(
            "api/v3/token: could not get auth code (no Url/auth_code and no data.auth). "
            f"Keys: {list(token_resp.keys())}"
        )

    global fyers, access_token

    app_id, app_type = _split_client_id(client_id)

    URL_SEND_LOGIN_OTP = "https://api-t2.fyers.in/vagator/v2/send_login_otp_v2"
    response = requests.post(url=URL_SEND_LOGIN_OTP, json={"fy_id": getEncodedString(FY_ID), "app_id": "2"})
    print("Status code:", response.status_code)
    try:
        print("OTP step response:", _redact_for_log(response.json()))
    except Exception:
        print("OTP step response: <non-json>")
    res = response.json()
    _require_ok(res, "send_login_otp_v2", required_keys=["request_key"])

    if datetime.now().second % 30 > 27:
        sleep(5)
    URL_VERIFY_OTP = "https://api-t2.fyers.in/vagator/v2/verify_otp"
    res2 = requests.post(
        url=URL_VERIFY_OTP,
        json={"request_key": res["request_key"], "otp": pyotp.TOTP(TOTP_KEY).now()},
    ).json()
    print("verify_otp:", _redact_for_log(res2))
    _require_ok(res2, "verify_otp", required_keys=["request_key"])

    ses = requests.Session()
    URL_VERIFY_PIN = "https://api-t2.fyers.in/vagator/v2/verify_pin_v2"
    payload_pin = {"request_key": res2["request_key"], "identity_type": "pin", "identifier": getEncodedString(PIN)}
    pin_resp = ses.post(url=URL_VERIFY_PIN, json=payload_pin).json()
    print("verify_pin_v2:", _redact_for_log(pin_resp))
    _require_ok(pin_resp, "verify_pin_v2", required_keys=["data"])
    if not isinstance(pin_resp.get("data"), dict) or "access_token" not in pin_resp["data"]:
        raise RuntimeError("verify_pin_v2 failed: access_token missing in data")

    ses.headers.update({"authorization": f"Bearer {pin_resp['data']['access_token']}"})

    TOKENURL = "https://api-t1.fyers.in/api/v3/token"
    payload3 = {
        "fyers_id": FY_ID,
        "app_id": app_id,
        "redirect_uri": redirect_uri,
        "appType": app_type,
        "code_challenge": "",
        "state": "None",
        "scope": "",
        "nonce": "",
        "response_type": "code",
        "create_cookie": True,
    }

    token_resp = ses.post(url=TOKENURL, json=payload3).json()
    print("token redirect response:", _redact_for_log(token_resp))
    _require_ok(token_resp, "api/v3/token")

    auth_code = _auth_code_from_token_response(token_resp)
    grant_type = "authorization_code"
    response_type = "code"

    session = fyersModel.SessionModel(
        client_id=client_id,
        secret_key=secret_key,
        redirect_uri=redirect_uri,
        response_type=response_type,
        grant_type=grant_type,
    )
    session.set_token(auth_code)
    gen = session.generate_token()
    if not isinstance(gen, dict) or not gen.get("access_token"):
        raise RuntimeError(f"generate_token failed: {gen}")
    access_token = gen["access_token"]
    print("access_token obtained (length %d)" % len(str(access_token)))
    fyers = fyersModel.FyersModel(client_id=client_id, is_async=False, token=access_token, log_path=os.getcwd())
    prof = fyers.get_profile()
    if isinstance(prof, dict):
        print("profile status:", {"s": prof.get("s"), "code": prof.get("code"), "message": prof.get("message")})
    else:
        print("profile status: <unknown>")


def ensure_fyers_session(client_id: str, token: str) -> tuple[bool, str]:
    """Build global FyersModel from client_id + access_token; verify with get_profile."""
    global fyers, access_token
    if not (client_id or "").strip() or not (token or "").strip():
        return False, "Missing client_id or access_token"
    try:
        access_token = token.strip()
        fyers = fyersModel.FyersModel(
            client_id=client_id.strip(),
            is_async=False,
            token=access_token,
            log_path=os.getcwd(),
        )
        prof = fyers.get_profile()
        if isinstance(prof, dict) and prof.get("s") == "ok":
            return True, ""
        return False, str(prof.get("message") or prof.get("msg") or prof)[:400]
    except Exception as e:
        return False, str(e)


def verify_profile_ok() -> tuple[bool, str]:
    """Use when session already exists (e.g. refresh positions)."""
    global fyers
    if fyers is None:
        return False, "Fyers client not initialized"
    try:
        prof = fyers.get_profile()
        if isinstance(prof, dict) and prof.get("s") == "ok":
            return True, ""
        return False, str(prof.get("message") or prof.get("msg") or prof)[:400]
    except Exception as e:
        return False, str(e)


def run_automated_login_from_store(store: dict) -> tuple[str | None, str]:
    """
    CSV keys (lowercase): fy_id, pin, totpkey, client_id, secret_key, redirect_uri.
    On success returns (access_token, ""); updates globals fyers / access_token.
    """
    need = ("fy_id", "pin", "totpkey", "client_id", "secret_key", "redirect_uri")
    missing = [k for k in need if not (store.get(k) or "").strip()]
    if missing:
        return None, "Missing Fyers CSV fields: " + ", ".join(missing)
    try:
        automated_login(
            store["client_id"].strip(),
            store["secret_key"].strip(),
            store["fy_id"].strip(),
            store["totpkey"].strip(),
            store["pin"].strip(),
            store["redirect_uri"].strip(),
        )
        tok = access_token
        if tok:
            return str(tok), ""
        return None, "automated_login did not set access_token"
    except Exception as e:
        return None, str(e)

def get_ltp(SYMBOL):
    global fyers
    data={"symbols":f"{SYMBOL}"}
    res=fyers.quotes(data)
    if 'd' in res and len(res['d']) > 0:
        lp = res['d'][0]['v']['lp']
        return lp

    else:
        print("Last Price (lp) not found in the response.")




def get_position():
    global fyers
      ## This will provide all the trade related information
    res=fyers.positions()
    return res

def get_orderbook():
    global fyers
    res = fyers.orderbook()
    return res
      ## This will provide the user with all the order realted information

def get_tradebook():
    global fyers
    res = fyers.tradebook()
    return res


def fetchOHLC_Scanner(symbol):
    dat =str(datetime.now().date())
    dat1 = str((datetime.now() - timedelta(5)).date())
    data = {
        "symbol": symbol,
        "resolution": "1D",
        "date_format": "1",
        "range_from": dat1,
        "range_to": dat ,
        "cont_flag": "1"
    }
    response = fyers.history(data=data)
    cl = ['date', 'open', 'high', 'low', 'close', 'volume']
    df = pd.DataFrame(response['candles'], columns=cl)
    df['date']=df['date'].apply(pd.Timestamp,unit='s',tzinfo=pytz.timezone('Asia/Kolkata'))
    return df.tail(5)

def fetchOHLC_Weekly(symbol):
    from datetime import datetime, timedelta
    import pandas as pd
    import numpy as np

    # Extended range for full candle history
    today = datetime.now()
    dat = str((today + timedelta(days=1)).date())
    dat1 = str((today - timedelta(days=160)).date())

    data = {
        "symbol": symbol,
        "resolution": "1D",
        "date_format": "1",
        "range_from": dat1,
        "range_to": dat,
        "cont_flag": "1"
    }

    response = fyers.history(data=data)

    cl = ['date', 'open', 'high', 'low', 'close', 'volume']
    df = pd.DataFrame(response['candles'], columns=cl)

    # Convert timestamp to datetime in IST
    df['date'] = pd.to_datetime(df['date'], unit='s').dt.tz_localize('UTC').dt.tz_convert('Asia/Kolkata')
    df.set_index('date', inplace=True)


    # ============ Weekly OHLC ============
    df_weekly = df.resample('W-FRI').agg({
        'open': 'first',
        'high': 'max',
        'low': 'min',
        'close': 'last',
        'volume': 'sum'
    }).dropna()

    # ============ Monthly OHLC with actual last available dates ============

    df['year'] = df.index.year
    df['month'] = df.index.month

    # Group by (year, month)
    grouped = df.groupby(['year', 'month'])

    records = []
    index_dates = []

    for (y, m), group in grouped:
        open_price = group['open'].iloc[0]
        high_price = group['high'].max()
        low_price = group['low'].min()
        close_price = group['close'].iloc[-1]
        volume_sum = group['volume'].sum()

        # Use the actual last trading day in the group as index
        last_date = group.index[-1]
        index_dates.append(last_date)

        records.append([open_price, high_price, low_price, close_price, volume_sum])

    df_monthly = pd.DataFrame(records, columns=['open', 'high', 'low', 'close', 'volume'], index=index_dates)

    # Ensure index is sorted
    df_monthly.sort_index(inplace=True)



    return df_weekly, df_monthly





# def fetchOHLC_Weekly(symbol):
#     # Approx 140 days for 20 weeks of daily data
#     dat = str(datetime.now().date())
#     dat1 = str((datetime.now() - timedelta(days=140)).date())

#     data = {
#         "symbol": symbol,
#         "resolution": "1D",
#         "date_format": "1",
#         "range_from": dat1,
#         "range_to": dat,
#         "cont_flag": "1"
#     }

#     response = fyers.history(data=data)
#     # print("response weekly:", response)

#     cl = ['date', 'open', 'high', 'low', 'close', 'volume']
#     df = pd.DataFrame(response['candles'], columns=cl)

#     # Convert Unix timestamp to datetime in IST
#     df['date'] = pd.to_datetime(df['date'], unit='s').dt.tz_localize('UTC').dt.tz_convert('Asia/Kolkata')
#     df.set_index('date', inplace=True)

#     # Resample to weekly candles, week ending on Friday
#     df_weekly = df.resample('W-FRI').agg({
#         'open': 'first',
#         'high': 'max',
#         'low': 'min',
#         'close': 'last',
#         'volume': 'sum'
#     })

#     # Drop incomplete weeks
#     df_weekly.dropna(inplace=True)

#     return df_weekly  # Return last 20 weeks

def fetchOHLC(symbol,tf):
    print("symbol: ",symbol)
    dat =str(datetime.now().date())
    dat1 = str((datetime.now() - timedelta(17)).date())
    data = {
        "symbol": symbol,
        "resolution":str(tf),
        "date_format": "1",
        "range_from": dat1,
        "range_to": dat,
        "cont_flag": "1"
    }
    response = fyers.history(data=data)
    # print("response: ",response)
    cl = ['date', 'open', 'high', 'low', 'close', 'volume']
    df = pd.DataFrame(response['candles'], columns=cl)
    df['date']=df['date'].apply(pd.Timestamp,unit='s',tzinfo=pytz.timezone('Asia/Kolkata'))
    return df


def fetchOHLC_get_selected_price(symbol, date):

    print("option symbol :",symbol)
    print("option symbol date :", date)
    dat = str(datetime.now().date())
    dat1 = str((datetime.now() - timedelta(25)).date())
    data = {
        "symbol": symbol,
        "resolution": "1D",
        "date_format": "1",
        "range_from": dat1,
        "range_to": dat,
        "cont_flag": "1"
    }
    response = fyers.history(data=data)
    cl = ['date', 'open', 'high', 'low', 'close', 'volume']
    df = pd.DataFrame(response['candles'], columns=cl)
    df['date'] = pd.to_datetime(df['date'], unit='s', utc=True).dt.tz_convert('Asia/Kolkata').dt.date
    target_date = pd.to_datetime(date).date()
    matching_row = df[df['date'] == target_date]
    if matching_row.empty:
        return 0
    else:
        close_price = matching_row.iloc[0]['close']
        return close_price
    



def fyres_websocket(symbollist):
    from fyers_apiv3.FyersWebsocket import data_ws
    global access_token

    def onmessage(message):
        """
        Callback function to handle incoming messages from the FyersDataSocket WebSocket.

        Parameters:
            message (dict): The received message from the WebSocket.

        """
        # print("Response:", message)
        try:
            if 'symbol' in message:
                symbol = message.get('symbol')
                ltp = message.get('ltp')
                last_traded_qty = message.get('last_traded_qty')
                vol_traded_today = message.get('vol_traded_today')
                ts = message.get('exch_feed_time') or message.get('last_traded_time')
                shared_data[symbol] = {
                    'ltp': ltp,
                    'last_traded_qty': last_traded_qty,
                    'vol_traded_today': vol_traded_today,
                    'timestamp': ts
                }
        except Exception as e:
            print("onmessage parse error:", e)




    def onerror(message):
        """
        Callback function to handle WebSocket errors.

        Parameters:
            message (dict): The error message received from the WebSocket.


        """
        print("Error:", message)


    def onclose(message):
        """
        Callback function to handle WebSocket connection close events.
        """
        print("Connection closed:", message)


    def onopen():
        """
        Callback function to subscribe to data type and symbols upon WebSocket connection.

        """
        # Specify the data type and symbols you want to subscribe to
        data_type = "SymbolUpdate"

        # Subscribe to the specified symbols and data type
        symbols = symbollist
        # ['NSE:LTIM24JULFUT', 'NSE:BHARTIARTL24JULFUT']
        fyers.subscribe(symbols=symbols, data_type=data_type)

        # Keep the socket running to receive real-time data
        fyers.keep_running()


    # Replace the sample access token with your actual access token obtained from Fyers
    # access_token = "XC4XXXXXXM-100:eXXXXXXXXXXXXfZNSBoLo"

    ensure_websocket_ssl_for_fyers()

    # Create a FyersDataSocket instance with the provided parameters
    fyers = data_ws.FyersDataSocket(
        access_token=access_token,  # Access token in the format "appid:accesstoken"
        log_path="",  # Path to save logs. Leave empty to auto-create logs in the current directory.
        litemode=False,  # Lite mode disabled. Set to True if you want a lite response.
        write_to_file=False,  # Save response in a log file instead of printing it.
        reconnect=True,  # Enable auto-reconnection to WebSocket on disconnection.
        on_connect=onopen,  # Callback function to subscribe to data upon connection.
        on_close=onclose,  # Callback function to handle WebSocket connection close events.
        on_error=onerror,  # Callback function to handle WebSocket errors.
        on_message=onmessage  # Callback function to handle incoming messages from the WebSocket.
    )

    # Establish a connection to the Fyers WebSocket
    fyers.connect()

def fyres_quote(symbol):
    data = {
        "symbols": f"{symbol}"
    }

    response = fyers.quotes(data=data)
    return response





def fyres_websocket_option(symbollist):
    from fyers_apiv3.FyersWebsocket import data_ws
    global access_token, option_fyers_socket

    def onmessage(message):
        """
        Callback function to handle incoming messages from the FyersDataSocket WebSocket.

        Parameters:
            message (dict): The received message from the WebSocket.

        """
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        print(f"{timestamp} - {message}\n")
        if 'symbol' in message and 'ltp' in message:
            shared_data_2[message['symbol']] = message['ltp']




    def onerror(message):
        """
        Callback function to handle WebSocket errors.

        Parameters:
            message (dict): The error message received from the WebSocket.


        """
        print("Error:", message)


    def onclose(message):
        """
        Callback function to handle WebSocket connection close events.
        """
        print("Connection closed:", message)


    def onopen():
        """
        Callback function to subscribe to data type and symbols upon WebSocket connection.

        """
        # Specify the data type and symbols you want to subscribe to
        data_type = "SymbolUpdate"

        # Subscribe to the specified symbols and data type
        symbols = symbollist
        # ['NSE:LTIM24JULFUT', 'NSE:BHARTIARTL24JULFUT']
        fyers.subscribe(symbols=symbols, data_type=data_type)

        # Keep the socket running to receive real-time data
        fyers.keep_running()


    # Replace the sample access token with your actual access token obtained from Fyers
    # access_token = "XC4XXXXXXM-100:eXXXXXXXXXXXXfZNSBoLo"

    # If an older option socket exists, close it before starting a new one.
    stop_option_websocket(clear_ltp=False)

    ensure_websocket_ssl_for_fyers()

    # Create a FyersDataSocket instance with the provided parameters
    fyers = data_ws.FyersDataSocket(
        access_token=access_token,  # Access token in the format "appid:accesstoken"
        log_path="",  # Path to save logs. Leave empty to auto-create logs in the current directory.
        litemode=True,  # Lite mode disabled. Set to True if you want a lite response.
        write_to_file=False,  # Save response in a log file instead of printing it.
        reconnect=True,  # Enable auto-reconnection to WebSocket on disconnection.
        on_connect=onopen,  # Callback function to subscribe to data upon connection.
        on_close=onclose,  # Callback function to handle WebSocket connection close events.
        on_error=onerror,  # Callback function to handle WebSocket errors.
        on_message=onmessage  # Callback function to handle incoming messages from the WebSocket.
    )
    option_fyers_socket = fyers

    # Establish a connection to the Fyers WebSocket
    fyers.connect()


def stop_option_websocket(clear_ltp: bool = True):
    global option_fyers_socket
    sock = option_fyers_socket
    option_fyers_socket = None
    if sock is not None:
        try:
            if hasattr(sock, "disconnect"):
                sock.disconnect()
            elif hasattr(sock, "close_connection"):
                sock.close_connection()
            elif hasattr(sock, "close"):
                sock.close()
        except Exception as e:
            print("Error while closing option websocket:", e)
    if clear_ltp:
        shared_data_2.clear()



def place_order(symbol,quantity,type,side,price):
    # Set quantity to 1 by default if not provided
    if quantity is None or quantity == 0:
        quantity = 1
    quantity = int(quantity)
    price = float(price)
    
    # Keep type as integer (1=Limit, 2=Market)
    order_type = int(type)
    
    # Keep side as integer (1=Buy, -1=Sell)
    order_side = int(side)
    
    print("quantity: ",quantity)
    print("price: ",price)
    print("type: ",order_type)
    print("side: ",order_side)
    
    # For market orders (type=2), set limitPrice to 0
    limit_price = 0 if order_type == 2 else price
    
    # Use the exact field names and data types from Fyers API documentation
    data = {
        "symbol": symbol,
        "qty": quantity,
        "type": order_type,
        "side": order_side,
        "productType": "INTRADAY",
        "limitPrice": limit_price,
        "stopPrice": 0,
        "validity": "DAY",
        "disclosedQty": 0,
        "offlineOrder": False,
        "stopLoss": 0,
        "takeProfit": 0,
        "orderTag": "tag1"
    }
    
    print("Order data: ", data)
    response = fyers.place_order(data=data)
    print("response: ", response)
    if isinstance(response, dict) and response.get("s") == "error":
        msg = str(response.get("message") or "")
        code = response.get("code")
        if code == -50 or "algo" in msg.lower():
            print(
                "[Fyers] If you see 'Algo orders are not allowed': enable Algo / API trading "
                "for this app in Fyers My API (developer portal), then retry.",
                flush=True,
            )
    return response

