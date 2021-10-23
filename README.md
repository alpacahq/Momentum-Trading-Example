# Momentum-Trading-Example

An example algorithm for a momentum-based day trading strategy. This script uses 
the API provided by [Alpaca](https://alpaca.markets/). A brokerage account with 
Alpaca, is required. to access the 
[Polygon](https://polygon.io/) data stream used by this algorithm.

## Running the Script
You must supply your Alpaca credentials to run this script. <br>
You do this by filling the supplied `config.yaml` file next to this script.<br>
You need to fill your key and secret => get it from the Alpaca Dashboard.<br>
Then, for paper trading leave `base_url` as is. For live trading this needs to be changed to `'https://api.alpaca.markets'`<br>
Lastly, select the data stream feed type:
  * iex for free accounts
  * sip for paid accounts

Now the script is ready
to run with `python algo.py`. <br>
**Please note that running with Python 3.6+ is required.**

## Algorithm Logic

This algorithm may buy stocks during a 45 minute period each day, starting 15
minutes after market open. <br>The first 15 minutes are avoided, as the high 
volatility can lead to poor performance.<br> The signals it uses to determine
if it should buy a stock are: 
  * if a stock has gained 4% from the previous day's close
  * and is above the highest point seen during the first 15 minutes of trading 
  * and if its MACD is positive and increasing. <br>
    (It also checks that the volume is strong 
enough to make trades on reliably.) <br>

It sells when a stock drops to a stop loss level or increases to a target price level.<br>
If there are open positions in your account at the end of the day on a symbol the script is watching, those positions
will be liquidated at market price. <br>
(Potentially holding positions overnight goes against
the concept of risk that the algorithm uses, and must be avoided for it to be
effective.)
