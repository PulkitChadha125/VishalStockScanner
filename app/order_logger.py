"""Helper for the trading strategy to record placed orders."""

from app import repository


def log_order(
    symbol_name: str,
    side: str,
    order_type: str,
    quantity: float,
    status: str,
    price: float | None = None,
    stop_loss: float | None = None,
    target: float | None = None,
) -> dict:
    return repository.create_order_log(
        symbol_name=symbol_name,
        side=side,
        order_type=order_type,
        quantity=quantity,
        status=status,
        price=price,
        stop_loss=stop_loss,
        target=target,
    )
