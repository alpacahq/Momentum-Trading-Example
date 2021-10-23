import logging
import asyncio

import pytz
import sys
import pandas as pd
import alpaca_trade_api as tradeapi
import requests
import time

import yaml
from alpaca_trade_api.common import URL
from alpaca_trade_api.entity import Order
from alpaca_trade_api.rest import TimeFrame
from alpaca_trade_api.rest_async import gather_with_concurrency
from ta.trend import macd
import numpy as np
from datetime import datetime, timedelta
from pytz import timezone
from loguru import logger
from concurrent.futures import ThreadPoolExecutor


# Replace these with your API connection info from the dashboard
with open("./config.yaml", mode='r') as f:
    o = yaml.safe_load(f)
    api_key_id = o.get("key_id")
    api_secret = o.get("secret")
    base_url = o.get("base_url")
    feed = o.get("feed")

api = tradeapi.REST(
    base_url=base_url,
    key_id=api_key_id,
    secret_key=api_secret
)
api_async = tradeapi.AsyncRest(
    key_id=api_key_id,
    secret_key=api_secret
)
conn: tradeapi.Stream = None

session = requests.session()

# We only consider stocks with per-share prices inside this range
min_share_price = 2.0
max_share_price = 13.0
# Minimum previous-day dollar volume for a stock we might consider
min_last_dv = 500000
daily_pct_change = 3.5
# Stop limit to default to
default_stop = .95
# How much of our portfolio to allocate to any one position
risk = 0.001

if float(api.get_account().cash) < 0:
    # we don't have money left, we'll wait until we liquidate some stocks
    WAIT_FOR_LIQUIDATION = True
else:
    WAIT_FOR_LIQUIDATION = False


async def get_historic_bars(symbols, start, end, timeframe: TimeFrame):
    major = sys.version_info.major
    minor = sys.version_info.minor
    if major < 3 or minor < 6:
        raise Exception('asyncio is not support in your python version')
    msg = f"Getting Bars data for {len(symbols)} symbols"
    msg += f", timeframe: {timeframe}" if timeframe else ""
    msg += f" between dates: start={start}, end={end}"
    logger.info(msg)

    tasks = []

    for symbol in symbols:
        args = [symbol, start, end, timeframe.value, 3000]
        tasks.append(api_async.get_bars_async(*args))
    start_time = time.time()
    if minor >= 8:
        results = await asyncio.gather(*tasks, return_exceptions=True)
    else:
        results = await gather_with_concurrency(500, *tasks)

    logger.info(f"Total of {len(results)} Bars. It took "
                f"{time.time() - start_time} seconds")
    for symbol, df in results:
        df.index = df.index.tz_convert(pytz.timezone('America/New_York'))

    return {symbol: df.iloc[-1000:] for symbol, df in results}


def get_1000m_history_data(symbols, start, end):
    logger.info('Getting historical data...')
    timeframe: TimeFrame = TimeFrame.Minute
    loop = asyncio.get_event_loop()
    minute_history = loop.run_until_complete(
        get_historic_bars(symbols,
                          start.isoformat(),
                          end.isoformat(),
                          timeframe))

    return minute_history


def get_tickers():
    assets = api.list_assets(status="active")

    snapshot = api.get_snapshots([el.symbol for el in assets])
    tickers = {}
    for symbol, data in snapshot.items():
        try:
            if data.latest_trade.p >= min_share_price:
                if data.latest_trade.p <= max_share_price:
                    if data.daily_bar.v * data.latest_trade.p > min_last_dv:
                        if (data.daily_bar.c - data.prev_daily_bar.c) / \
                                data.prev_daily_bar.c * 100 >= daily_pct_change:
                            tickers[symbol] = data
        except:
            logger.warning(f"can't get data for: {symbol}")
    return tickers


def find_stop(current_value, minute_history, now):
    series = minute_history['low'][-100:] \
        .dropna().resample('5min').min()
    series = series[now.floor('1D'):]
    diff = np.diff(series.values)
    low_index = np.where((diff[:-1] <= 0) & (diff[1:] > 0))[0] + 1
    if len(low_index) > 0:
        return series[low_index[-1]] - 0.01
    return current_value * default_stop



def run(tickers, market_open_dt, market_close_dt):
    # Establish streaming connection
    feed = 'iex'  # <- replace to sip if you have PRO subscription
    # feed = 'sip'  # <- replace to sip if you have PRO subscription
    global conn
    conn = tradeapi.Stream(key_id=api_key_id,
                           secret_key=api_secret,
                           base_url=URL('https://paper-api.alpaca.markets'),
                           data_feed=feed)

    # Update initial state with information from tickers
    volume_today = {}
    prev_closes = {}
    for symbol, data in tickers.items():
        prev_closes[symbol] = data.prev_daily_bar.c
        volume_today[symbol] = data.daily_bar.v

    symbols = tickers.keys()
    logger.info('Tracking {} symbols.'.format(len(symbols)))
    end_date = list(tickers.values())[0].daily_bar.t + timedelta(days=1)
    start_date = end_date - timedelta(days=5)
    minute_history = get_1000m_history_data(symbols, start_date, end_date)

    portfolio_value = float(api.get_account().portfolio_value)

    open_orders = {}
    positions = {}

    # Cancel any existing open orders on watched symbols
    existing_orders = api.list_orders(limit=500)
    for order in existing_orders:
        if order.symbol in symbols:
            logger.info(f"Cancelling open order for {order.symbol} on startup")
            api.cancel_order(order.id)

    stop_prices = {}
    latest_cost_basis = {}

    # Track any positions bought during previous executions
    existing_positions = api.list_positions()
    for position in existing_positions:
        if position.symbol in symbols:
            positions[position.symbol] = float(position.qty)
            # Recalculate cost basis and stop price
            latest_cost_basis[position.symbol] = float(position.cost_basis)
            stop_prices[position.symbol] = (
                    float(position.cost_basis) * default_stop
            )

    # Keep track of what we're buying/selling
    target_prices = {}
    partial_fills = {}

    # Use trade updates to keep track of our portfolio
    async def handle_trade_update(data):
        symbol = data.order['symbol']
        last_order = open_orders.get(symbol)
        if last_order is not None:
            event = data.event
            if event == 'partial_fill':
                qty = int(data.order['filled_qty'])
                if data.order['side'] == 'sell':
                    qty = qty * -1
                positions[symbol] = (
                        positions.get(symbol, 0) - partial_fills.get(symbol, 0)
                )
                partial_fills[symbol] = qty
                positions[symbol] += qty
                open_orders[symbol] = Order(data.order)
            elif event == 'fill':
                qty = int(data.order['filled_qty'])
                if data.order['side'] == 'sell':
                    qty = qty * -1
                positions[symbol] = (
                        positions.get(symbol, 0) - partial_fills.get(symbol, 0)
                )
                partial_fills[symbol] = 0
                positions[symbol] += qty
                open_orders[symbol] = None
            elif event == 'canceled' or event == 'rejected':
                partial_fills[symbol] = 0
                open_orders[symbol] = None

    conn.subscribe_trade_updates(handle_trade_update)

    async def handle_second_bar(data):
        try:
            global WAIT_FOR_LIQUIDATION

            symbol = data.symbol
            if symbol not in symbols:
                return

            # First, aggregate 1s bars for up-to-date MACD calculations
            ts = data.timestamp.replace(second=0, microsecond=0, nanosecond=0)
            # ts -= timedelta(minutes=1)
            try:
                current = minute_history[data.symbol].loc[ts]
            except KeyError:
                current = None
            if current is None:
                new_data = [
                    data.bid_price,  # open
                    data.ask_price,  # high
                    data.bid_price,  # low
                    data.ask_price,  # close
                    data.ask_size + data.bid_size,  # volume,
                    0,  # trade_count
                    0,  # vwap
                ]
            else:
                new_data = [
                    current.open,
                    data.ask_price if data.ask_price > current.high else current.high,
                    data.bid_price if data.bid_price < current.low else current.low,
                    data.ask_price,
                    current.volume + data.ask_size + data.bid_size,
                    0,
                    0,
                ]
            minute_history[symbol].loc[ts] = new_data

            # Next, check for existing orders for the stock
            existing_order = open_orders.get(symbol)
            if existing_order is not None:
                # Make sure the order's not too old
                if isinstance(existing_order, dict):
                    print("sdf")
                    existing_order = Order(existing_order)
                submission_ts = existing_order.submitted_at.astimezone(
                    timezone('America/New_York')
                )
                order_lifetime = abs(ts - submission_ts)
                if order_lifetime.seconds // 60 > 60:
                    # Cancel it so we can try again for a fill
                    logger.info(
                        f"Cancelling order for {existing_order.symbol} due to not filling in time")
                    api.cancel_order(existing_order.id)
                return

            # Now we check to see if it might be time to buy or sell
            since_market_open = ts - market_open_dt
            until_market_close = market_close_dt - ts
            if (
                    since_market_open.seconds // 60 > 15 and
                    since_market_open.seconds // 60 < 60
            ):

                # Check for buy signals
                if not WAIT_FOR_LIQUIDATION:

                    # See if we've already bought in first
                    position = positions.get(symbol, 0)
                    if position > 0:
                        return

                    # See how high the price went during the first 15 minutes
                    lbound = market_open_dt
                    ubound = lbound + timedelta(minutes=15)
                    high_15m = 0
                    try:
                        high_15m = minute_history[symbol][lbound:ubound][
                            'high'].max()
                    except Exception as e:
                        # Because we're aggregating on the fly, sometimes the datetime
                        # index can get messy until it's healed by the minute bars
                        return

                    # Get the change since yesterday's market close
                    daily_pct_change = (data.ask_price - prev_closes[symbol]) / \
                                       prev_closes[symbol]

                    if (
                            daily_pct_change > .04 and
                            data.ask_price > high_15m and
                            volume_today[symbol] > 30000
                    ):
                        # check for a positive, increasing MACD
                        hist = macd(
                            minute_history[symbol]['close'].dropna(),
                            window_fast=12,
                            window_slow=26
                        )
                        if (
                                hist[-1] < 0 or
                                not (hist[-3] < hist[-2] < hist[-1])
                        ):
                            return
                        hist = macd(
                            minute_history[symbol]['close'].dropna(),
                            window_fast=40,
                            window_slow=60
                        )
                        if hist[-1] < 0 or np.diff(hist)[-1] < 0:
                            return

                        # Stock has passed all checks; figure out how much to buy
                        stop_price = find_stop(
                            data.ask_price, minute_history[symbol], ts
                        )
                        stop_prices[symbol] = stop_price
                        target_prices[symbol] = data.ask_price + (
                                (data.ask_price - stop_price) * 3
                        )
                        shares_to_buy = portfolio_value * risk // (
                                data.ask_price - stop_price
                        )
                        if shares_to_buy == 0:
                            shares_to_buy = 1
                        shares_to_buy -= positions.get(symbol, 0)
                        if shares_to_buy <= 0:
                            return
                        if shares_to_buy * data.ask_price > float(
                                api.get_account().cash):
                            # logger.debug(f"not enough cash to buy {symbol}.")
                            return
                        logger.info(
                            'Submitting buy for {} shares of {} at {}'.format(
                                shares_to_buy, symbol, data.ask_price
                            ))
                        try:
                            o = api.submit_order(
                                symbol=symbol, qty=str(shares_to_buy),
                                side='buy',
                                type='limit', time_in_force='day',
                                limit_price=str(data.ask_price)
                            )
                            open_orders[symbol] = o
                            latest_cost_basis[symbol] = data.ask_price
                            if float(api.get_account().cash) < 0:
                                # if float(api.get_account().cash) > 0:
                                # we don't have money left, we'll wait until we liquidate some stocks
                                WAIT_FOR_LIQUIDATION = True
                        except Exception as e:
                            logger.error(e)
                        return
            if (
                    since_market_open.seconds // 60 >= 24 and
                    until_market_close.seconds // 60 > 15
            ):
                # Check for liquidation signals

                # We can't liquidate if there's no position
                position = positions.get(symbol, 0)
                if position == 0:
                    return

                # Sell for a loss if it's fallen below our stop price
                # Sell for a loss if it's below our cost basis and MACD < 0
                # Sell for a profit if it's above our target price
                hist = macd(
                    minute_history[symbol]['close'].dropna(),
                    window_fast=13,
                    window_slow=21
                )
                if (
                        data.ask_price <= stop_prices[symbol] or
                        (data.ask_price >= target_prices[symbol] and hist[
                            -1] <= 0) or
                        (data.ask_price <= latest_cost_basis[symbol] and hist[
                            -1] <= 0)
                ):
                    logger.info(
                        'Submitting sell for {} shares of {} at {}'.format(
                            position, symbol, data.ask_price
                        ))
                    try:
                        o = api.submit_order(
                            symbol=symbol, qty=str(position), side='sell',
                            type='market', time_in_force='day'
                        )
                        WAIT_FOR_LIQUIDATION = False
                        open_orders[symbol] = o
                        latest_cost_basis[symbol] = data.ask_price
                    except Exception as e:
                        logger.error(e)
                return
            elif (
                    until_market_close.seconds // 60 <= 15
            ):
                # Liquidate remaining positions on watched symbols at market
                try:
                    position = api.get_position(symbol)
                except Exception as e:
                    # Exception here indicates that we have no position
                    return
                logger.info(
                    'Trading over, liquidating remaining position in {}'.format(
                        symbol)
                )
                api.submit_order(
                    symbol=symbol, qty=position.qty, side='sell',
                    type='market', time_in_force='day'
                )
                WAIT_FOR_LIQUIDATION = False
                symbols.remove(symbol)
                if len(symbols) <= 0:
                    conn.stop_ws()
                conn.deregister([
                    'A.{}'.format(symbol),
                    'AM.{}'.format(symbol)
                ])
        except Exception as e:
            print(e)

    # conn.subscribe_quotes(handle_second_bar, *symbols)
    conn.subscribe_quotes(handle_second_bar, "*")

    # Replace aggregated 1s bars with incoming 1m bars
    async def handle_minute_bar(data):
        ts = pd.Timestamp(data.timestamp).tz_localize(pytz.utc).tz_convert(
            pytz.timezone('America/New_York'))
        minute_history[data.symbol].loc[ts] = [
            data.open,
            data.high,
            data.low,
            data.close,
            data.volume,
            data.trade_count,
            data.vwap
        ]
        volume_today[data.symbol] += data.volume

    conn.subscribe_bars(handle_minute_bar, *symbols)

    logger.info('Watching {} symbols.'.format(len(symbols)))

    watchdog()


def conn_thread():
    global conn
    try:
        # make sure we have an event loop, if not create a new one
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())
    conn.run()


def watchdog():
    global conn
    pool = ThreadPoolExecutor(1)

    # time.sleep(5)  # give the ws time to open
    while True:
        if api.get_clock().is_open:
            if not conn.is_open():
                logger.info("Market is open, restarting websocket connection")
                pool.submit(conn_thread)
        else:
            if conn:
                if conn.is_open():
                    logger.info('waiting for market open')
                    loop = asyncio.get_event_loop()
                    loop.run_until_complete(conn.stop_ws())
                else:
                    time.sleep(30)
        time.sleep(15)


if __name__ == "__main__":
    # Get when the market opens or opened today
    alpaca_logger = logging.getLogger('alpaca_trade_api')
    alpaca_logger.setLevel(logging.INFO)
    alpaca_logger.addHandler(logging.StreamHandler())

    nyc = timezone('America/New_York')
    today = datetime.today().astimezone(nyc)
    today_str = datetime.today().astimezone(nyc).strftime('%Y-%m-%d')
    calendar = api.get_calendar(start=today_str, end=today_str)[0]
    market_open = today.replace(
        hour=calendar.open.hour,
        minute=calendar.open.minute,
        second=0
    )
    market_open = market_open.astimezone(nyc)
    market_close = today.replace(
        hour=calendar.close.hour,
        minute=calendar.close.minute,
        second=0
    )
    market_close = market_close.astimezone(nyc)

    # Wait until just before we might want to trade
    current_dt = datetime.today().astimezone(nyc)
    since_market_open = current_dt - market_open
    while since_market_open.seconds // 60 <= 14:
        time.sleep(1)
        since_market_open = current_dt - market_open

    run(get_tickers(), market_open, market_close)
