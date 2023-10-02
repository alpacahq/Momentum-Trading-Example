# Momentum-Trading-Example

An example algorithm for a momentum-based day trading strategy. This script uses the API provided by [Alpaca](https://alpaca.markets/). A brokerage account with Alpaca, available to US customers, is required to access the [Polygon](https://polygon.io/) data stream used by this algorithm.

## Project Description

This example code is meant to illustrate the use of Alpaca's API when used with Polygon's live data streaming services to create a momentum-based day trading algorithm.
Note that the timeframe may be changed to suit your trading style and preferences. While it is currently set to trade at a high frequency, it can be modified to pull data on a higher timeframe, such as hourly or even daily, and still be effective.

We strongly recommend you learn more about the algorithm and the accomanying strategy [here](https://www.investopedia.com/terms/m/momentum.asp#:~:text=Momentum%20trading%20is%20a%20strategy,both%20price%20and%20volume%20information.).

DISCLAIMER: This algorithm is an educational foundation for you to build upon. There are no guarantees of profit or loss if used in a live trading environment. Furthermore, it is strongly recommended to store your API keys in a secure manner, such as with a config.env file, and not directly in the code as plain text.

For testing, we recommend using paper market keys. They can be safely loaded into the script and used to test the algorithm without risking any real money. You can get paper market keys from the Alpaca dashboard. Replace the following lines of code with your keys:

```py
    base_url = 'Your API URL'
    api_key_id = 'Your API Key'
    api_secret = 'Your API Secret'
```

## Installation

First clone the repository with

> git clone git@github.com:alpacahq/Momentum-Trading-Example.git
> Then, install the required packages with `pip install -r requirements.txt`. It is recommended to use a virtual environment for this.

## Running the Script

Note that near the top of the file, there are placeholders for your API information - your key ID, your secret key, and the URL you want to connect to. You can get all that information from the Alpaca dashboard. Replace the placeholder strings with your own information, and the script is ready to run with `python algo.py`. **Please note that running with Python 3.6 is required.**

## Algorithm Logic and Breakdown

There are parameters to be set in the beginning which are defined by your personal risk tolerance/aversion:

```py
    # We only consider stocks with per-share prices inside this range
    min_share_price = 2.0
    max_share_price = 13.0
    # Minimum previous-day dollar volume for a stock we might consider
    min_last_dv = 500000
    # Stop limit to default to
    default_stop = .95
    # How much of our portfolio to allocate to any one position
    risk = 0.001
```

This algorithm may buy stocks during a 45 minute period each day, starting 15 minutes after market open. (The first 15 minutes are avoided, as the high volatility can lead to poor performance.)

The signals it uses to determine if it should buy a stock are if it has gained 4% from the previous day's close, is above the highest point seen during the first 15 minutes of trading, and if its MACD is positive and increasing. (It also checks that the volume is strong enough to make trades on reliably.) It sells when a stock drops to a stop loss level or increases to a target price level.

Both sell triggers can be modified in the global parameters mentioned before. The stop loss is currently set to 5% below the purchase price, but will automatically update as the price of the security changes. The target price is set to 2% above the purchase price and will also update as the price changes. 

The portfolio allocation percentage should also be changed to suit your risk tolerance. As a general principle, no more than 1% of your portfolio should be risked on any trade. So, given a stop loss of 5%, the portfolio allocation percentage should be set to 20% or less.

If there are open positions in your account at the end of the day on a symbol the script is watching, those positions will be liquidated at market. (Potentially holding positions overnight goes against the concept of risk that the algorithm uses, and must be avoided for it to be effective.) However, if you would like to enable overnight positions, you can simply comment out the following lines:

```py
    api.submit_order(
                    symbol=symbol, qty=position.qty, side='sell',
                    type='market', time_in_force='day'
                )
                symbols.remove(symbol)
                if len(symbols) <= 0:
                    conn.close()
                conn.deregister([
                    'A.{}'.format(symbol),
                    'AM.{}'.format(symbol)
```
