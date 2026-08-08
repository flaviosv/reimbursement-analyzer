class PayloadTooLarge(Exception):
    """The request body exceeded MAX_BODY_BYTES while streaming."""


class BatchInvalid(Exception):
    """The batch failed schema validation. The message names the offending
    item index and field only — never the value."""


class PublishFailed(Exception):
    """The broker returned a delivery error, or no delivery report arrived
    within the publish timeout."""
