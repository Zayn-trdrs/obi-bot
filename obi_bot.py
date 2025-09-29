#!/usr/bin/env python3
"""
Hasbrouck Order-Flow Trading System
----------------------------------

Usage:
  - Provide historical trade ticks CSV: columns = timestamp, price, size
      timestamp should be unix seconds or ISO format parseable by pandas.
  - Example run:
      python hasbrouck_trading_system.py --data trades.csv --out results

Features:
  - Computes OFI from trades using tick rule (classify trade sign by price movement).
  - Builds lagged OFI features (lags 1..k) and predicts future log-return (horizon).
  - Rolling (walk-forward) training: train on `train_window` samples, predict next `test_step`.
  - Generates signals from predicted return with thresholding.
  - Position sizing: bounded Kelly-like size derived from in-sample win-rate & avg RR, plus scaling.
  - Backtest includes transaction cost and slippage.
  - Outputs metrics (CAGR, Sharpe, Max Drawdown), saves trades.csv and equity.png.

Notes:
  - This is a simplified, practical implementation: tune hyperparams (lags, windows, thresholds).
  - For production/HFT, replace trade-sign classification with full orderbook OFI.
"""

import argparse
import os
import math
from dataclasses import dataclass
import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression
from sklearn.preprocessing import StandardScaler
import matplotlib.pyplot as plt
import warnings
warnings.filterwarnings("ignore")

# ----------------------------
# Utility & feature functions
# ----------------------------

def load_trades(path):
    df = pd.read_csv(path)
    # standardize columns
    assert 'price' in df.columns and 'size' in df.columns, "CSV must contain price and size"
    if 'timestamp' in df.columns:
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='s', errors='coerce').fillna(pd.to_datetime(df['timestamp'], errors='coerce'))
    else:
        # try index as time
        df.index.name = 'timestamp'
    df = df.sort_values('timestamp').reset_index(drop=True)
    return df[['timestamp','price','size']].copy()

def compute_trade_signs(df):
    """
    Very common approach: Tick rule
    sign = +1 if price > previous price
         = -1 if price < previous price
         = prev sign if equal (tick rule extension)
    """
    p = df['price'].values
    signs = np.zeros(len(p), dtype=int)
    prev_sign = 1
    for i in range(1, len(p)):
        if p[i] > p[i-1]:
            s = 1
        elif p[i] < p[i-1]:
            s = -1
        else:
            s = prev_sign
        signs[i] = s
        prev_sign = s
    signs[0] = signs[1] if len(signs) > 1 else 1
    return signs

def compute_ofi_from_trades(df, agg_seconds=60):
    """
    Aggregate trades into time-bars (default 60s) and compute OFI per bar as:
      OFI_bar = sum(sign * size)
    Returns DataFrame with columns: timestamp (bar-end), open, high, low, close, volume, OFI
    """
    df2 = df.copy()
    df2['sign'] = compute_trade_signs(df2)
    df2['side_volume'] = df2['sign'] * df2['size']
    # resample by time
    df2.set_index('timestamp', inplace=True)
    rule = f'{agg_seconds}S'
    ohlcv = df2['price'].resample(rule).ohlc()
    vol = df2['size'].resample(rule).sum().rename('volume')
    ofi = df2['side_volume'].resample(rule).sum().rename('OFI')
    bars = pd.concat([ohlcv, vol, ofi], axis=1).dropna().reset_index()
    # compute log returns on close
    bars['log_ret'] = np.log(bars['close'] / bars['close'].shift(1))
    bars = bars.dropna().reset_index(drop=True)
    return bars

def build_lagged_ofi_features(bars, max_lag=5):
    df = bars.copy()
    for lag in range(1, max_lag+1):
        df[f'OFI_lag_{lag}'] = df['OFI'].shift(lag)
    df = df.dropna().reset_index(drop=True)
    return df

# ----------------------------
# Backtest & model classes
# ----------------------------

@dataclass
class BacktestParams:
    train_window: int = 200       # number of bars for training at each step
    test_step: int = 1            # how many bars to step forward prediction
    max_lag: int = 5              # number of lags for OFI
    horizon: int = 1              # predict return over this many bars
    fee: float = 0.0005           # exchange fee proportion per trade (e.g. 0.0005 = 0.05%)
    slippage: float = 0.0002      # slippage per trade (proportion)
    threshold_z: float = 0.0      # z-score threshold on predicted return to create signal
    kelly_cap: float = 0.05       # maximum fraction of equity allowed by Kelly-like sizing
    verbose: bool = True

class HasbrouckSystem:
    def __init__(self, bars, params: BacktestParams):
        self.bars = bars.copy().reset_index(drop=True)
        self.params = params
        self.model = LinearRegression()
        self.scaler = StandardScaler()

    def prepare_features_targets(self):
        p = self.params
        df = build_lagged_ofi_features(self.bars, p.max_lag)
        # create target: horizon log return forward
        df['target'] = df['log_ret'].shift(-p.horizon).rolling(window=p.horizon).sum()
        df = df.dropna().reset_index(drop=True)
        feature_cols = [f'OFI_lag_{i}' for i in range(1, p.max_lag+1)]
        X = df[feature_cols].values
        y = df['target'].values
        return df, X, y

    def rolling_walkforward(self):
        p = self.params
        df, X_all, y_all = self.prepare_features_targets()
        n = len(df)
        idx = 0
        equity = 1.0
        equity_curve = []
        trades = []
        positions = []
        # We'll step through time; for each step, train on previous train_window, predict next `test_step`
        step = p.test_step
        while True:
            train_start = idx
            train_end = idx + p.train_window
            test_idx = train_end
            if test_idx + p.horizon - 1 >= n:
                break
            X_train = X_all[train_start:train_end]
            y_train = y_all[train_start:train_end]
            X_test = X_all[test_idx:test_idx+step]
            # fit scaler & model
            self.scaler.fit(X_train)
            Xtr_s = self.scaler.transform(X_train)
            self.model.fit(Xtr_s, y_train)
            # predict
            Xt_s = self.scaler.transform(X_test)
            preds = self.model.predict(Xt_s)
            # create signals from preds (z-score threshold)
            pred_mean = np.mean(preds)
            pred_std = np.std(preds) if np.std(preds) > 0 else 1.0
            signals = np.where(preds > p.threshold_z * pred_std, 1,
                       np.where(preds < -p.threshold_z * pred_std, -1, 0))
            # For each predicted bar, execute trade at close price of that bar, hold for horizon bars
            for j, sig in enumerate(signals):
                i_bar = test_idx + j
                entry_price = df.loc[i_bar, 'close']
                exit_bar = i_bar + p.horizon
                if exit_bar >= n: 
                    continue
                exit_price = df.loc[exit_bar, 'close']
                ret = None
                if sig == 1:
                    gross_ret = math.log(exit_price / entry_price)  # approximated log-return
                elif sig == -1:
                    gross_ret = math.log(entry_price / exit_price)
                else:
                    gross_ret = 0.0
                # approximate transaction costs: two sides (entry + exit)
                cost = p.fee*2 + p.slippage*2
                net_ret = gross_ret - cost
                # position sizing using in-sample simplified Kelly:
                # estimate p_win and avg_win/avg_loss from past predictions on train set
                in_sample_preds = self.model.predict(self.scaler.transform(X_train))
                in_sample_signs = np.where(in_sample_preds > 0, 1, np.where(in_sample_preds < 0, -1, 0))
                # compute in-sample realized returns
                in_sample_realized = y_train * in_sample_signs
                wins = in_sample_realized[in_sample_realized > 0]
                losses = in_sample_realized[in_sample_realized <= 0]
                p_win = len(wins) / max(1, len(in_sample_realized))
                avg_win = wins.mean() if len(wins)>0 else 0.0
                avg_loss = -losses.mean() if len(losses)>0 else 0.0
                # b = avg_win / avg_loss  (win/loss ratio)
                if avg_loss == 0 or (avg_win == 0 and avg_loss == 0):
                    kelly = 0.0
                else:
                    b = (avg_win / avg_loss) if avg_loss>0 else 0.0
                    kelly = max(0.0, (b * p_win - (1 - p_win)) / b) if b>0 else 0.0
                # clamp/scale kelly
                kelly = min(kelly, p.kelly_cap)
                # we can also scale the position by predicted strength:
                strength = min(3.0, abs(preds[j]) / (pred_std if pred_std>0 else 1.0))
                position_size = kelly * strength
                # equity update
                equity *= (1.0 + net_ret * position_size)
                equity_curve.append({'bar_idx': i_bar, 'timestamp': df.loc[i_bar,'timestamp'], 'equity': equity})
                trades.append({
                    'entry_bar': i_bar,
                    'exit_bar': exit_bar,
                    'timestamp_entry': df.loc[i_bar,'timestamp'],
                    'timestamp_exit': df.loc[exit_bar,'timestamp'],
                    'signal': int(sig),
                    'entry_price': entry_price,
                    'exit_price': exit_price,
                    'gross_ret': gross_ret,
                    'net_ret': net_ret,
                    'position': position_size,
                    'equity_after': equity
                })
            # advance window
            idx += step
        trades_df = pd.DataFrame(trades)
        equity_df = pd.DataFrame(equity_curve).drop_duplicates('bar_idx').reset_index(drop=True)
        return trades_df, equity_df

# ----------------------------
# Metrics & plotting
# ----------------------------

def compute_performance(equity_df):
    if equity_df.empty:
        return {}
    eq = equity_df['equity'].values
    # dailyization factor based on bar frequency
    # We can't reliably detect frequency here; approximate using difference of timestamps
    ts = pd.to_datetime(equity_df['timestamp'])
    if len(ts) < 2:
        return {}
    avg_seconds = (ts.diff().dt.total_seconds().dropna().mean())
    bars_per_day = max(1, int(round(86400 / avg_seconds)))
    total_days = (ts.iloc[-1] - ts.iloc[0]).total_seconds() / 86400.0
    total_return = eq[-1] / eq[0] - 1.0
    cagr = (eq[-1] / eq[0]) ** (1 / max(1/365, total_days/365)) - 1.0
    # compute daily returns for sharpe
    pct = pd.Series(eq).pct_change().fillna(0)
    if pct.std() == 0:
        sharpe = 0.0
    else:
        sharpe = (pct.mean() / pct.std()) * math.sqrt(bars_per_day)
    # max drawdown
    roll_max = np.maximum.accumulate(eq)
    drawdown = (eq - roll_max) / roll_max
    max_dd = drawdown.min()
    return {
        'total_return': total_return,
        'cagr': cagr,
        'sharpe_approx': sharpe,
        'max_drawdown': max_dd,
        'bars_per_day_approx': bars_per_day
    }

def plot_equity(equity_df, outpath):
    if equity_df.empty:
        print("No equity to plot")
        return
    plt.figure(figsize=(10,6))
    plt.plot(pd.to_datetime(equity_df['timestamp']), equity_df['equity'])
    plt.title('Equity curve')
    plt.xlabel('Time')
    plt.ylabel('Equity')
    plt.grid(True)
    plt.tight_layout()
    plt.savefig(outpath)
    plt.close()

# ----------------------------
# CLI & main
# ----------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data', required=True, help='path to trades CSV (timestamp,price,size)')
    parser.add_argument('--agg', type=int, default=60, help='aggregation seconds for bars (default 60)')
    parser.add_argument('--out', default='results', help='output folder')
    parser.add_argument('--train_window', type=int, default=500, help='train window bars')
    parser.add_argument('--max_lag', type=int, default=5, help='max lag for OFI features')
    parser.add_argument('--horizon', type=int, default=1, help='prediction horizon (bars)')
    parser.add_argument('--fee', type=float, default=0.0005, help='fee proportion per trade side')
    parser.add_argument('--slippage', type=float, default=0.0002, help='slippage proportion per trade side')
    parser.add_argument('--kelly_cap', type=float, default=0.05, help='max kelly fraction')
    args = parser.parse_args()

    os.makedirs(args.out, exist_ok=True)
    df_trades = load_trades(args.data)
    bars = compute_ofi_from_trades(df_trades, agg_seconds=args.agg)
    params = BacktestParams(
        train_window=args.train_window,
        test_step=1,
        max_lag=args.max_lag,
        horizon=args.horizon,
        fee=args.fee,
        slippage=args.slippage,
        threshold_z=0.0,
        kelly_cap=args.kelly_cap,
        verbose=True
    )

    system = HasbrouckSystem(bars, params)
    trades_df, equity_df = system.rolling_walkforward()
    perf = compute_performance(equity_df)

    trades_out = os.path.join(args.out, 'trades.csv')
    equity_out = os.path.join(args.out, 'equity.csv')
    plot_out = os.path.join(args.out, 'equity.png')

    trades_df.to_csv(trades_out, index=False)
    equity_df.to_csv(equity_out, index=False)
    plot_equity(equity_df, plot_out)

    print("Backtest complete. Results saved to:", args.out)
    print("Performance summary:")
    for k,v in perf.items():
        print(f"  {k}: {v}")

if __name__ == '__main__':
    main()
