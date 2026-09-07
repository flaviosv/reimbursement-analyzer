import logging
import socket

import pytest
from shared.metrics import reimbursement_status_transitions_total, start_metrics_server


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class DescribeReimbursementStatusTransitionsTotal:
    def it_is_constructed_with_from_and_to_labelnames(self) -> None:
        assert reimbursement_status_transitions_total._labelnames == ("from", "to")

    def it_accepts_positional_labels_in_from_to_order(self) -> None:
        # `from` is a Python reserved word — every call site must use
        # positional .labels(value, value), never .labels(from=..., to=...).
        before = reimbursement_status_transitions_total.labels("pending", "auto-approved")._value.get()

        reimbursement_status_transitions_total.labels("pending", "auto-approved").inc()

        after = reimbursement_status_transitions_total.labels("pending", "auto-approved")._value.get()
        assert after == before + 1

    def it_increments_on_inc(self) -> None:
        before = reimbursement_status_transitions_total.labels("pending", "auto-rejected")._value.get()

        reimbursement_status_transitions_total.labels("pending", "auto-rejected").inc()

        after = reimbursement_status_transitions_total.labels("pending", "auto-rejected")._value.get()
        assert after == before + 1


class DescribeStartMetricsServer:
    def it_logs_one_info_line_naming_the_port_and_does_not_raise(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        port = _free_port()

        with caplog.at_level(logging.INFO, logger="shared.metrics"):
            server, thread = start_metrics_server(port)
        try:
            assert any(
                record.levelno == logging.INFO and str(port) in record.getMessage()
                for record in caplog.records
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=1)

    def it_raises_when_the_port_is_already_in_use(self) -> None:
        # A plain bound socket already occupies the port — this exercises
        # the same OSError-on-bind-failure path without starting a second
        # real prometheus_client HTTP server (and its background thread)
        # just to make it collide with the first.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as blocker:
            blocker.bind(("0.0.0.0", 0))
            blocker.listen(1)
            port = blocker.getsockname()[1]

            with pytest.raises(OSError):
                start_metrics_server(port)
