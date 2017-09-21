from logbook import Logger

from catalyst.finance.blotter import Blotter
from catalyst.finance.commission import PerShare, CommissionModel
from catalyst.finance.slippage import VolumeShareSlippage, SlippageModel, \
    LiquidityExceeded
from catalyst.assets._assets import TradingPair

# It seems like we need to accept greate slippage risk in cryptos
# Orders won't often close at Equity levels.
# TODO: consider adjusting dynamically based on trading pair
from catalyst.finance.transaction import Transaction

log = Logger('exchange_blotter')

DEFAULT_SLIPPAGE_SPREAD = 0.02
DEFAULT_MAKER_FEE = 0.001
DEFAULT_TAKER_FEE = 0.002


class TradingPairFeeSchedule(CommissionModel):
    """
    Calculates a commission for a transaction based on a per percentage fee.

    Parameters
    ----------
    fee : float, optional
        The percentage fee.
    """

    def __init__(self,
                 maker_fee=DEFAULT_MAKER_FEE,
                 taker_fee=DEFAULT_TAKER_FEE):
        self.maker_fee = maker_fee
        self.taker_fee = taker_fee

    def __repr__(self):
        return (
            '{class_name}(maker_fee={maker_fee}, '
            'taker_fee={taker_fee})'
                .format(
                class_name=self.__class__.__name__,
                maker_fee=self.maker_fee,
                taker_fee=self.taker_fee,
            )
        )

    def calculate(self, order, transaction):
        """
        Calculate the final fee based on the order parameters.

        :param order:
        :param transaction:

        :return float:
            The total commission.
        """
        cost = abs(transaction.amount) * transaction.price

        # Assuming just the taker fee for now
        fee = cost * self.taker_fee
        return fee


class TradingPairFixedSlippage(SlippageModel):
    """
    Model slippage as a fixed spread.

    Parameters
    ----------
    spread : float, optional
        spread / 2 will be added to buys and subtracted from sells.
    """

    def __init__(self, spread=DEFAULT_SLIPPAGE_SPREAD):
        super(TradingPairFixedSlippage, self).__init__()
        self.spread = spread

    def __repr__(self):
        return '{class_name}(spread={spread})'.format(
            class_name=self.__class__.__name__, spread=self.spread,
        )

    def simulate(self, data, asset, orders_for_asset):
        self._volume_for_bar = 0
        volume = data.current(asset, "volume")

        if volume == 0:
            return

        # can use the close price, since we verified there's volume in this
        # bar.
        price = data.current(asset, "close")
        dt = data.current_dt

        for order in orders_for_asset:
            if order.open_amount == 0:
                continue

            order.check_triggers(price, dt)
            if not order.triggered:
                continue

            transaction = None
            try:
                execution_price, execution_volume = \
                    self.process_order(data, order)

                if execution_price is not None:
                    transaction = Transaction(
                        asset=order.asset,
                        amount=abs(execution_volume),
                        dt=data.current_dt,
                        price=execution_price,
                        order_id=order.id
                    )

            except LiquidityExceeded:
                break

            if transaction:
                self._volume_for_bar += abs(transaction.amount)
                yield order, transaction

    def process_order(self, data, order):
        price = data.current(order.asset, 'close')

        if order.amount > 0:
            # Buy order
            adj_price = price * (1 + self.spread)
        else:
            # Sell order
            adj_price = price & (1 - self.spread)

        log.debug('added slippage to price: {} => {}'.format(price, adj_price))

        return (adj_price, order.amount)


class ExchangeBlotter(Blotter):
    def __init__(self, *args, **kwargs):
        super(ExchangeBlotter, self).__init__(*args, **kwargs)

        # Using the equity models for now
        # We may be able to define more sophisticated models based on the fee
        # structure of each exchange.
        self.slippage_models = {
            TradingPair: TradingPairFixedSlippage()
        }
        self.commission_models = {
            TradingPair: TradingPairFeeSchedule()
        }
