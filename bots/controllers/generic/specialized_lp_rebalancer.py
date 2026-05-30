import logging
from decimal import Decimal
from typing import List, Optional
import math

from hummingbot.core.data_type.common import TradeType
from hummingbot.strategy_v2.models.executor_actions import (
    CreateExecutorAction,
    ExecutorAction,
)
from hummingbot.strategy_v2.models.executors import CloseType
from hummingbot.strategy_v2.models.executors_info import ExecutorInfo
from hummingbot.strategy_v2.executors.lp_executor.data_types import LPExecutorConfig

# Import the base class
from controllers.generic.lp_rebalancer.lp_rebalancer import (
    LPRebalancer,
    LPRebalancerConfig,
)


class SpecializedLPRebalancerConfig(LPRebalancerConfig):
    controller_name: str = "specialized_lp_rebalancer"
    # We can add custom fields here if needed, but the base config has most of what we need
    # like trading_pair, pool_address, total_amount_quote, etc.


class SpecializedLPRebalancer(LPRebalancer):
    """
    Custom Controller implementing the specific strategy:
    - Upward Trailing: Close and reopen tight range.
    - Downward Re-averaging: Close and reopen with dynamic upper bound to maintain geometric mean.
    """

    def __init__(self, config: SpecializedLPRebalancerConfig, *args, **kwargs):
        super().__init__(config, *args, **kwargs)
        self.config: SpecializedLPRebalancerConfig = config

        # Track the average buy price of the current position
        self._average_buy_price: Optional[Decimal] = None

    def determine_executor_actions(self) -> List[ExecutorAction]:
        """
        Overriding the base logic to implement the specific state machine.
        """
        actions = []

        # Get current pool price (updated by base class in update_processed_data)
        current_price = self._pool_price
        if current_price is None or current_price == Decimal("0"):
            self.logger().warning("No pool price available.")
            return actions

        executor = self.active_executor()

        # No active executor - check if we should create one
        if executor is None:
            if not self.is_tracked_executor_terminated():
                self.logger().debug("Waiting for tracked executor to terminate.")
                return actions

            # Previous executor terminated
            terminated_executor = self.get_tracked_executor()

            # Capture closed position bounds for side determination
            closed_lower_price = None
            closed_upper_price = None
            if (
                terminated_executor
                and terminated_executor.close_type != CloseType.FAILED
            ):
                closed_lower_price = Decimal(
                    str(terminated_executor.custom_info.get("lower_price", 0))
                )
                closed_upper_price = Decimal(
                    str(terminated_executor.custom_info.get("upper_price", 0))
                )

            # Clear tracking
            self._current_executor_id = None

            # 1. Initial Position or Upward Breach
            if not self._initial_position_created or (
                closed_upper_price and current_price >= closed_upper_price
            ):
                self.logger().info(
                    "Creating initial position or handling Upward Breach."
                )

                # Range: 0.15 - 0% (one direction)
                # Sets upper price bound to current pool active price.
                # Sets lower price bound to 0.15% below the current active price.
                upper_bound = current_price
                lower_bound = current_price * Decimal("0.9985")  # 1 - 0.0015

                # Side: BUY (USDC only) or RANGE depending on offset
                side = TradeType.BUY

                # Update average buy price (geometric mean)
                self._average_buy_price = Decimal(
                    str(math.sqrt(float(lower_bound * upper_bound)))
                )
                self.logger().info(f"New Average Buy Price: {self._average_buy_price}")

            # 2. Downward Breach
            elif closed_lower_price and current_price < closed_lower_price:
                self.logger().info("Handling Downward Breach.")

                if self._average_buy_price is None:
                    # Fallback if somehow not set
                    self._average_buy_price = current_price

                # Sets the new lower price range to the current active pool price.
                lower_bound = current_price

                # Dynamically computes the maximum (upper) price range such that the
                # geometric average sell price of the range matches the calculated average token buy price.
                # upper = (average_buy_price ^ 2) / lower
                upper_bound = (self._average_buy_price**2) / lower_bound

                self.logger().info(
                    f"Calculated Dynamic Upper Bound: {upper_bound} (to match buy price {self._average_buy_price})"
                )

                # Side: SELL (SOL only)
                side = TradeType.SELL

            else:
                # Fallback or price within old range (should not happen with auto-close)
                self.logger().info("Price within old range or fallback.")
                return actions

            # Create executor config
            # Calculate limit prices for auto-close (using 1% threshold as default or from config)
            threshold = self.config.rebalance_threshold_pct / Decimal("100")
            upper_limit_price = upper_bound * (Decimal("1") + threshold)
            lower_limit_price = lower_bound * (Decimal("1") - threshold)

            base_amt, quote_amt = self._calculate_amounts(side, current_price)

            executor_config = LPExecutorConfig(
                timestamp=self.market_data_provider.time(),
                connector_name=self.config.connector_name,
                lp_provider=self.config.lp_provider,
                trading_pair=self.config.trading_pair,
                pool_address=self.config.pool_address,
                lower_price=lower_bound,
                upper_price=upper_bound,
                base_amount=base_amt,
                quote_amount=quote_amt,
                side=side,
                upper_limit_price=upper_limit_price,
                lower_limit_price=lower_limit_price,
                keep_position=True,
            )

            actions.append(
                CreateExecutorAction(
                    controller_id=self.config.id, executor_config=executor_config
                )
            )

            self._current_executor_id = executor_config.id  # This is usually handled by the system after creation, but we set it or track it!
            # Wait, in the base class it searches for the active executor to set the ID!
            # So we can let it do that in the next tick!

            self._initial_position_created = True

            self.logger().info(
                f"Action created: {side.name} position at [{lower_bound:.4f}, {upper_bound:.4f}]"
            )

        return actions
