# Momentum-Trading-Example

An example algorithm for a momentum-based day trading strategy. This script uses the API provided by [Alpaca](https://alpaca.markets/). A brokerage account with Alpaca, available to US customers, is required to access the [Polygon](https://polygon.io/) data stream used by this algorithm.

## Running the Script

Note that near the top of the file, there are placeholders for your API information - your key ID, your secret key, and the URL you want to connect to. You can get all that information from the Alpaca dashboard. Replace the placeholder strings with your own information, and the script is ready to run with `python algo.py`. **Please note that running with Python 3.6 is required.**

## Algorithm Logic

This algorithm may buy stocks during a 45 minute period each day, starting 15 minutes after market open. (The first 15 minutes are avoided, as the high volatility can lead to poor performance.) The signals it uses to determine if it should buy a stock are if it has gained 4% from the previous day's close, is above the highest point seen during the first 15 minutes of trading, and if its MACD is positive and increasing. (It also checks that the volume is strong enough to make trades on reliably.) It sells when a stock drops to a stop loss level or increases to a target price level. If there are open positions in your account at the end of the day on a symbol the script is watching, those positions will be liquidated at market. (Potentially holding positions overnight goes against the concept of risk that the algorithm uses, and must be avoided for it to be effective.)
